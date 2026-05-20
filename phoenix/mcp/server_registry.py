"""``MCPServerRegistry`` — per-process MCP-server registration store.

Per Phase 13 build guide Step 6 + 13-D4 (locked): Phoenix-as-MCP-client
dispatches ONLY to MCP servers in this registry. Registration requires
an admin Actor signature; mutations are JSON-file-persisted (matching
the Phase 6a ``ActorPermissions`` pattern; Phase 6b state-backend
persistence is a Step 6 follow-up).

**13-D4 invariants enforced by this module:**

1. **No TOFU.** :meth:`get` returns ``None`` for unregistered names;
   the dispatch helper raises :class:`MCPServerNotRegistered` rather
   than implicitly trusting the caller.
2. **No empty-default-allows-all.** A fresh registry has zero
   entries; default state results in zero possible network calls.
3. **No discovery-based auto-add.** The registry has NO method that
   ingests MCP server lists from external sources. Registration is
   one-server-at-a-time via :meth:`register` with explicit admin
   action.
4. **``["*"]`` is forbidden.** Construction-time validation in
   :class:`MCPServerSpec.__post_init__` rejects ``"*"`` in
   ``allowed_tools``.
5. **Immediate revocation.** :meth:`unregister` clears the entry;
   the dispatch helper consults the registry on every call so
   de-registration takes effect on the next round-trip.

**SAFETY:** :attr:`MCPServerSpec.auth_config` is held in-memory in
the registry but is NEVER written to the JSON persistence layer
in cleartext (per SAFETY contract). On daemon restart, the JSON
file is reloaded but auth_config is empty for each spec; ops must
re-register or the dispatch helper raises a clear "auth config
missing; re-register" error at first call.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from phoenix.mcp.errors import MCPRegistrationError
from phoenix.mcp.server_spec import MCPServerSpec

log = logging.getLogger(__name__)

# Default persistence path matches the Phase 6a ActorPermissions pattern.
_DEFAULT_REGISTRY_PATH: Path = Path.home() / ".phoenix" / "runtime" / "mcp_servers.json"


class MCPServerRegistry:
    """Per-process MCP-server registry.

    Module-level singleton (per Phase 6a pattern); tests construct
    fresh instances to exercise isolation.

    Mutations are thread-safe via an internal lock. The JSON file
    persistence is atomic-write (tmp file + os.replace) to survive
    crashes during write.
    """

    def __init__(self, *, persistence_path: Path | None = None) -> None:
        """Construct.

        Args:
            persistence_path: Optional override for the JSON file
                location. Tests use a tmp_path; ops use the default
                ``~/.phoenix/runtime/mcp_servers.json``.
        """
        self._entries: dict[str, MCPServerSpec] = {}
        self._persistence_path = (
            persistence_path if persistence_path is not None else _DEFAULT_REGISTRY_PATH
        )
        self._lock = threading.Lock()

    def register(self, spec: MCPServerSpec) -> None:
        """Register an MCP server.

        Idempotent on ``spec.name`` — re-registering replaces the
        existing entry. The caller is responsible for the admin
        Actor signature gate (the registry doesn't auth; the admin
        endpoint above it does).

        Persists the spec to JSON immediately on success.
        """
        with self._lock:
            self._entries[spec.name] = spec
            self._persist_unlocked()

    def unregister(self, server_name: str) -> None:
        """Remove a registered MCP server.

        Idempotent on missing names — no error if the server isn't
        registered. Immediate revocation per 13-D4: callers consult
        the registry on every dispatch.

        Persists the deletion to JSON immediately.
        """
        with self._lock:
            self._entries.pop(server_name, None)
            self._persist_unlocked()

    def get(self, server_name: str) -> MCPServerSpec | None:
        """Look up by name. Returns ``None`` when not registered.

        This is the load-bearing query for 13-D4's invariant: the
        dispatch helper calls :meth:`get` BEFORE any network
        operation; ``None`` means "raise :class:`MCPServerNotRegistered`
        and DO NOT touch the network".
        """
        with self._lock:
            return self._entries.get(server_name)

    def list_servers(self) -> list[MCPServerSpec]:
        """Snapshot of all registered servers in insertion order."""
        with self._lock:
            return list(self._entries.values())

    def __contains__(self, server_name: object) -> bool:
        if not isinstance(server_name, str):
            return False
        with self._lock:
            return server_name in self._entries

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    # ------------------------------------------------------------------
    # Persistence — JSON file (Phase 6a pattern)

    def load_from_disk(self) -> None:
        """Reload the registry from the JSON persistence file.

        Per SAFETY: ``auth_config`` is loaded as an empty dict (the
        persistence file never holds auth cleartext). Specs that
        require auth must be re-registered before first dispatch;
        the dispatch helper surfaces a clear error if auth_config
        is empty when a call attempt happens.

        Idempotent: missing file → empty registry, no error.
        """
        with self._lock:
            self._entries.clear()
            if not self._persistence_path.exists():
                return
            try:
                with self._persistence_path.open(encoding="utf-8") as f:
                    data = json.load(f)
            except json.JSONDecodeError as exc:
                raise MCPRegistrationError(
                    f"MCP registry file at {self._persistence_path} is malformed JSON: {exc}"
                ) from exc
            servers = data.get("servers", [])
            for entry in servers:
                spec = MCPServerSpec.from_dict(entry, auth_config={})
                self._entries[spec.name] = spec

    def _persist_unlocked(self) -> None:
        """Write the current registry state to the JSON persistence file.

        Caller must hold ``self._lock``. Atomic-write: write to
        ``<path>.tmp`` then ``os.replace`` to the final path. This
        survives crashes during write — either the old file or the
        new file is on disk, never a half-written one.
        """
        self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "phase-13-step-6",
            "servers": [spec.to_dict() for spec in self._entries.values()],
        }
        tmp_path = self._persistence_path.with_suffix(self._persistence_path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        tmp_path.replace(self._persistence_path)


# Module-level singleton (per Phase 6a pattern).
_REGISTRY_SINGLETON: MCPServerRegistry | None = None


def get_registry() -> MCPServerRegistry:
    """Return the module-level :class:`MCPServerRegistry` singleton.

    Lazy-constructs on first access. Loads from the default JSON file
    location on first construction.

    Tests reset the singleton via :func:`reset_registry`.
    """
    global _REGISTRY_SINGLETON
    if _REGISTRY_SINGLETON is None:
        _REGISTRY_SINGLETON = MCPServerRegistry()
        try:
            _REGISTRY_SINGLETON.load_from_disk()
        except MCPRegistrationError as exc:
            # Malformed JSON in the persistence file shouldn't crash
            # daemon startup. Log loudly and start with an empty registry.
            log.error(
                "MCP registry persistence file is malformed; starting with "
                "empty registry. Reason: %s",
                exc,
            )
    return _REGISTRY_SINGLETON


def reset_registry(*, persistence_path: Path | None = None) -> MCPServerRegistry:
    """Test helper: reset the module-level singleton.

    Returns the fresh :class:`MCPServerRegistry` instance. Optional
    ``persistence_path`` lets tests use a ``tmp_path``-scoped file.
    """
    global _REGISTRY_SINGLETON
    _REGISTRY_SINGLETON = MCPServerRegistry(persistence_path=persistence_path)
    return _REGISTRY_SINGLETON
