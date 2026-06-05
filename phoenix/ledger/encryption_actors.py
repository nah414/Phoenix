"""Per-actor prompt-encryptor registry (Phase 13.x.8).

Extends the shared :mod:`phoenix.ledger.encryption` registry with
per-actor isolation: each Phoenix actor (keyed by ``actor.name``) can
have their own age identity + recipients under
``<keys_dir>/actors/<actor_name>/``.

**Fail-closed by directory presence.** If ``actors/<actor_name>/``
EXISTS, that actor is configured for isolation — a load failure
(:class:`AgeKeyLoadError` OR :class:`AgeKeyPermissionError`) raises
:class:`PerActorKeyError` rather than silently downgrading to the
shared key. If the directory is ABSENT, the actor falls back to the
shared :func:`phoenix.ledger.encryption.get_prompt_encryptor`
(13.x.6 behavior preserved).

**Thread-safety.** Unlike the shared registry (startup-time mutation
only), this cache mutates at REQUEST time (the rotate-key admin
endpoint evicts entries). It is guarded by a :class:`threading.Lock`.
In-process eviction is correct only while the daemon runs single-worker
(uvicorn with no ``--workers``); revisit cross-process invalidation
when ``--workers`` ships.

**Scope (Phase 13.x.8 = plumbing).** This module provides the
resolution the future ENCRYPTED_OPT_IN writer will call. The writer
itself + startup ``set_prompt_encryptor()`` activation remain deferred
per design decision 13-D2.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from phoenix.ledger.encryption import PromptEncryptor, get_prompt_encryptor
from phoenix.ledger.encryption_age import (
    AgeKeyLoadError,
    AgeKeyPermissionError,
    AgePromptEncryptor,
    default_keys_dir,
    encryptor_from_keys_dir,
)

# Mirror of phoenix.api.routes._ACTOR_NAME_RE. Defined locally (not
# imported) to avoid importing the heavy FastAPI app module into the
# ledger layer. A pin test (test_encryption_actors.py) asserts the two
# patterns stay identical so they cannot silently diverge.
_ACTOR_NAME_RE = re.compile(r"^[a-z0-9_\-]{1,64}$")


class PerActorKeyError(Exception):
    """Raised when an actor is configured for isolation
    (``actors/<name>/`` exists) but its keys cannot be loaded, OR when
    the supplied ``actor_name`` fails validation.

    The point of this typed error is to make a fail-CLOSED state
    distinguishable from the legitimate shared-key fallback: callers
    must never silently treat an isolation-configured actor as
    shared-key.
    """


# ---------------------------------------------------------------------------
# Per-actor registry. GUARDED BY _lock (request-time mutation).

_per_actor_encryptors: dict[str, PromptEncryptor] = {}
_lock = threading.Lock()


def _validate_actor_name(actor_name: str) -> None:
    if not _ACTOR_NAME_RE.match(actor_name):
        raise PerActorKeyError(
            f"invalid actor_name {actor_name!r}: must match {_ACTOR_NAME_RE.pattern}"
        )


def _actor_keys_dir(actor_name: str) -> Path:
    """Resolve <keys_dir>/actors/<actor_name>, asserting it stays under the
    actors/ root (path-traversal guard)."""
    _validate_actor_name(actor_name)
    actors_root = (default_keys_dir() / "actors").resolve()
    candidate = (actors_root / actor_name).resolve()
    if not candidate.is_relative_to(actors_root):
        raise PerActorKeyError(
            f"invalid actor_name {actor_name!r}: resolved path escapes the actors/ root"
        )
    return candidate


def encryptor_from_actor_default_layout(actor_name: str) -> AgePromptEncryptor:
    """Build an :class:`AgePromptEncryptor` rooted at
    ``<keys_dir>/actors/<actor_name>/``.

    Raises:
        PerActorKeyError: ``actor_name`` invalid / escapes root.
        AgeKeyLoadError: directory structure not as expected.
        AgeKeyPermissionError: identity file too permissive (POSIX).
    """
    return encryptor_from_keys_dir(_actor_keys_dir(actor_name))


def get_prompt_encryptor_for_actor(actor_name: str) -> PromptEncryptor:
    """Resolve the encryptor for ``actor_name``. FAIL-CLOSED by directory
    presence.

    Resolution (cache + load under ``_lock``):
    1. Cache hit → return cached.
    2. ``actors/<actor_name>/`` EXISTS → load; on
       :class:`AgeKeyLoadError`/:class:`AgeKeyPermissionError` raise
       :class:`PerActorKeyError` (never fall back to shared); cache + return.
    3. Directory ABSENT → :func:`get_prompt_encryptor` (shared fallback).

    Raises:
        PerActorKeyError: invalid name, or dir present but keys broken.
    """
    actor_dir = _actor_keys_dir(actor_name)  # validates name (raises before lock)
    with _lock:
        cached = _per_actor_encryptors.get(actor_name)
        if cached is not None:
            return cached
        if actor_dir.is_dir():
            try:
                encryptor: PromptEncryptor = encryptor_from_keys_dir(actor_dir)
            except (AgeKeyLoadError, AgeKeyPermissionError) as exc:
                raise PerActorKeyError(
                    f"actor {actor_name!r} is configured for isolation "
                    f"(actors/{actor_name}/ exists) but its keys could not be "
                    f"loaded: {exc}. Refusing to fall back to the shared key."
                ) from exc
            _per_actor_encryptors[actor_name] = encryptor
            return encryptor
    # Directory absent → shared fallback (outside the lock; shared registry
    # has its own semantics).
    return get_prompt_encryptor()


def set_prompt_encryptor_for_actor(actor_name: str, encryptor: PromptEncryptor) -> None:
    """Register ``encryptor`` for ``actor_name`` (highest precedence;
    tests + explicit startup wiring)."""
    _validate_actor_name(actor_name)
    with _lock:
        _per_actor_encryptors[actor_name] = encryptor


def reset_prompt_encryptor_for_actor(actor_name: str | None = None) -> None:
    """Evict one actor's cached encryptor, or all when ``actor_name`` is
    None. Called by the rotate-key admin endpoint on success (cache
    invalidation) and by test teardown."""
    with _lock:
        if actor_name is None:
            _per_actor_encryptors.clear()
        else:
            _per_actor_encryptors.pop(actor_name, None)


def list_actors_with_keys() -> list[str]:
    """Return ``actor_name``s that have a valid ``actors/<name>/identity.txt``
    on disk. Used by the enumeration admin endpoint."""
    actors_root = default_keys_dir() / "actors"
    if not actors_root.is_dir():
        return []
    names: list[str] = []
    for child in sorted(actors_root.iterdir()):
        if not child.is_dir():
            continue
        if not _ACTOR_NAME_RE.match(child.name):
            continue
        if (child / "identity.txt").is_file():
            names.append(child.name)
    return names


__all__ = [
    "PerActorKeyError",
    "encryptor_from_actor_default_layout",
    "get_prompt_encryptor_for_actor",
    "list_actors_with_keys",
    "reset_prompt_encryptor_for_actor",
    "set_prompt_encryptor_for_actor",
]
