"""Actor permissions registry: who can do what.

Per architecture v1 Section 7.3: every Actor carries an
:class:`ActorPermissions` record with eight capability flags. The
registry is queried by the safety gate at every request to authorize
the Actor's action.

Per Phase 6a scope (2026-05-08): JSON-file-backed registry. v1.x's
Phase 6b will swap the JSON-file backend for SQLite via the abstract
:class:`StateBackend` Protocol (Step 4).

The registry is append-only: permission grants and revocations are
events, not in-place edits. Phase 6a stores the latest permission
snapshot per actor in JSON; Phase 6b extends with the audit trail.

Bootstrap actors (``"adam"`` and ``"ash"`` per Section 7.3) default to
all True + ``"admin"`` tier. Any other actor defaults to safe minimum:
``can_submit_tasks=True``, ``can_replay_tasks=True``, all others False,
``"default"`` rate-limit tier.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from phoenix.identity.keystore import _keystore_dir

_PERMISSIONS_FILENAME = "actor_permissions.json"
_BOOTSTRAP_ACTORS = frozenset({"adam", "ash"})

# Valid rate-limit tier names per Section 1 Decision 23.
_VALID_RATE_TIERS = frozenset({"default", "elevated", "admin"})


@dataclass(frozen=True)
class ActorPermissions:
    """Capability flags carried per Actor (Section 7.3 schema).

    The eight fields are the v1 contract; new flags append in v1.x. The
    rate_limit_tier string lookup happens against
    :data:`_VALID_RATE_TIERS`; unknown values fall back to
    ``"default"`` defensively.

    Phase 4's frontier-physics gate already exists (Phase 2 engine
    boundary + Phase 4 router defense-in-depth); the
    :attr:`frontier_physics` flag here is the Section 7-layer policy
    version (authority check above capability check).

    **Phase 13 Step 9 extensions** (per 13-D2 + 13-D4 locked):

    - :attr:`can_call_cognition` — gate for cognition-task dispatch.
      Default ``True`` for ``default``-tier and above (cognition is a
      first-class Phoenix capability); an admin can revoke per-actor
      for compliance + cost-isolation scenarios.
    - :attr:`can_call_mcp_server` — base capability; per-server access
      controlled by Step 6's :class:`MCPServerRegistry` (specific
      server name lookup happens in the dispatch helper). Default
      ``False`` — the conservative posture: no MCP access without
      admin grant.
    - :attr:`can_register_mcp_server` — admin-only; required for
      ``POST /v1/admin/mcp-servers/{name}``. Default ``False``.
    - :attr:`can_store_prompt_verbatim` — required to dispatch a
      cognition task with ``prompt_disposition=VERBATIM`` (13-D2).
      Default ``False`` — the load-bearing privacy guarantee.
    - :attr:`can_store_prompt_encrypted` — required for
      ``ENCRYPTED_OPT_IN`` disposition. Default ``False``. Phase 13
      ships the column + Protocol; the customer-key-management
      ceremony lands later (see
      :mod:`phoenix.ledger.encryption`).
    - :attr:`can_store_raw_provider_body` — gates the
      :attr:`CognitionResult.raw_provider_body` storage path
      (P13-1 default). Default ``False``.
    - :attr:`can_receive_token_stream` — required to subscribe to
      ``token.delta`` WebSocket events (Phase 13 Step 7 streaming
      surface). Default ``True`` — token streams are functional, not
      privacy-bearing, so the conservative-stance is relaxed here.

    **Phase 13.x.7 extension** (encryption admin):

    - :attr:`can_rotate_encryption_key` — gate on
      ``POST /v1/admin/encryption/rotate-key`` admin endpoint. Default
      ``False`` (deny); admin-tier construction grants ``True``.
    """

    can_submit_tasks: bool = True
    can_replay_tasks: bool = True
    can_load_adapter: bool = False
    can_unload_adapter: bool = False
    frontier_physics: bool = False
    can_override_human_review: bool = False
    is_admin: bool = False
    rate_limit_tier: str = "default"
    # ----- Phase 13 Step 9 extensions (cognition + MCP) -----
    can_call_cognition: bool = True
    can_call_mcp_server: bool = False
    can_register_mcp_server: bool = False
    can_store_prompt_verbatim: bool = False
    can_store_prompt_encrypted: bool = False
    can_store_raw_provider_body: bool = False
    can_receive_token_stream: bool = True
    # ----- Phase 13.x.7 extension (encryption admin) -----
    can_rotate_encryption_key: bool = False


def _default_permissions_for(actor_name: str) -> ActorPermissions:
    """Return the default :class:`ActorPermissions` for a freshly-seen actor.

    Bootstrap actors (``adam``, ``ash``) get all-True + admin tier
    per Section 7.3 — including the Phase 13 cognition + MCP grants
    so the development experience isn't gated on a permissions dance.
    Any other actor gets the safe minimum (defaults documented on the
    dataclass).
    """
    if actor_name.lower() in _BOOTSTRAP_ACTORS:
        return ActorPermissions(
            can_submit_tasks=True,
            can_replay_tasks=True,
            can_load_adapter=True,
            can_unload_adapter=True,
            frontier_physics=True,
            can_override_human_review=True,
            is_admin=True,
            rate_limit_tier="admin",
            # ----- Phase 13 Step 9 bootstrap grants -----
            can_call_cognition=True,
            can_call_mcp_server=True,
            can_register_mcp_server=True,
            can_store_prompt_verbatim=True,
            can_store_prompt_encrypted=True,
            can_store_raw_provider_body=True,
            can_receive_token_stream=True,
            # ----- Phase 13.x.7 bootstrap grant -----
            can_rotate_encryption_key=True,
        )
    return ActorPermissions()


def _permissions_path() -> Path:
    return _keystore_dir() / _PERMISSIONS_FILENAME


class PermissionsRegistry:
    """JSON-file-backed lookup of :class:`ActorPermissions` by actor name.

    Phase 6a writes ``~/.phoenix/runtime/actor_permissions.json``
    on every grant/revoke; reads happen against an in-memory cache
    populated lazily on first access. Phase 6b replaces the JSON
    backend with SQLite via the :class:`StateBackend` Protocol.

    Thread-safety: a single lock guards the cache + file writes. Phase
    6a's daemon is single-process; Phase 6b's persistent backend
    handles cross-process locking.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else _permissions_path()
        self._cache: dict[str, ActorPermissions] | None = None
        # RLock so set() -> _ensure_loaded -> with self._lock doesn't
        # deadlock against the outer set()'s acquire.
        self._lock = threading.RLock()

    def _ensure_loaded(self) -> dict[str, ActorPermissions]:
        if self._cache is not None:
            return self._cache
        with self._lock:
            if self._cache is not None:
                return self._cache
            cache: dict[str, ActorPermissions] = {}
            if self._path.exists():
                try:
                    raw = json.loads(self._path.read_text(encoding="utf-8"))
                    for name, fields in raw.items():
                        cache[name] = ActorPermissions(**fields)
                except (OSError, ValueError, TypeError):
                    # Corrupted file: fall back to empty cache. Phase 6b
                    # logs a structured warning here; Phase 6a stays
                    # silent (the audit logger lands in 6b).
                    cache = {}
            self._cache = cache
            return cache

    def get(self, actor_name: str) -> ActorPermissions:
        """Return permissions for ``actor_name``.

        First-time lookups populate the cache with defaults
        (bootstrap-tier for ``adam``/``ash``; safe minimum otherwise)
        WITHOUT writing to disk. Disk write happens on explicit
        :meth:`grant` / :meth:`revoke`.
        """
        cache = self._ensure_loaded()
        if actor_name not in cache:
            return _default_permissions_for(actor_name)
        return cache[actor_name]

    def set(self, actor_name: str, permissions: ActorPermissions) -> None:
        """Persist new permissions for ``actor_name``. Validates rate
        tier; raises :class:`ValueError` on invalid string.
        """
        if permissions.rate_limit_tier not in _VALID_RATE_TIERS:
            raise ValueError(
                f"rate_limit_tier {permissions.rate_limit_tier!r} not in "
                f"{sorted(_VALID_RATE_TIERS)}"
            )
        with self._lock:
            cache = self._ensure_loaded()
            cache[actor_name] = permissions
            self._write_through(cache)

    def all_actors(self) -> list[str]:
        """List all known actor names. Phase 8 admin uses this."""
        return sorted(self._ensure_loaded().keys())

    def _write_through(self, cache: dict[str, ActorPermissions]) -> None:
        """Atomically write the cache to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        serialized = {name: asdict(p) for name, p in cache.items()}
        # Write to a tmp + rename for atomic replacement.
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(serialized, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(self._path)


# Module-level singleton; tests can swap via monkey-patch.
_REGISTRY: PermissionsRegistry | None = None


def get_registry() -> PermissionsRegistry:
    """Lazy module-level :class:`PermissionsRegistry` singleton."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = PermissionsRegistry()
    return _REGISTRY
