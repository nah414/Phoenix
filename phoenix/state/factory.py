"""Backend factory -- selects and caches the configured :class:`StateBackend`.

Per architecture v1 Section 1 Decision 31: the state backend is chosen
at startup and not switchable at runtime. The factory caches the
constructed backend in a module-level singleton so the first call wins
for the remainder of the process lifetime.

**Selection precedence:**

1. ``$PHOENIX_STATE_BACKEND`` env var. Accepted values:
   - ``"sqlite"`` (default; zero-config solo installs).
   - ``"postgres"`` (opt-in; org deployments needing concurrency,
     replication, or audit-grade durability).

2. (Phase 9) ``~/.phoenix/config.yaml`` ``state.backend`` key, surfaced
   by the CLI config loader. Phase 6b Step 4 does not parse YAML --
   when Phase 9 lands, the CLI will translate config to env vars
   before invoking the daemon, so this factory's env-var path remains
   the single source of truth.

3. Default: ``sqlite``.

**Backend-specific configuration:**

- SQLite: ``$PHOENIX_SQLITE_DB_PATH`` (default
  ``~/.phoenix/runtime/state.db``). Resolved inside
  :class:`~phoenix.state.sqlite_backend.SQLiteStateBackend`.
- Postgres: ``$PHOENIX_STATE_BACKEND_DSN`` (required) or
  ``$PHOENIX_POSTGRES_DSN`` (alias). Resolved inside
  :class:`~phoenix.state.postgres_backend.PostgresStateBackend`.

**Singleton semantics:**

- :func:`get_state_backend` constructs the backend lazily on first
  call and caches it. Subsequent calls return the same instance.
- :func:`reset_state_backend` closes the cached backend and clears
  the singleton. Phoenix's FastAPI lifespan shutdown calls this;
  tests call it for isolation between TestClient sessions.

**Wiring into the safety gate:**

Phoenix's FastAPI lifespan (in :mod:`phoenix.api.routes`) calls
:func:`get_state_backend` at startup and then
:func:`phoenix.safety.kill_switch.set_store_backend` to enable
write-through. Phase 6b Step 2 shipped the write-through capability;
Step 4 is what turns it on.
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phoenix.state.backend_protocol import StateBackend


class InvalidBackendConfig(Exception):
    """Raised when ``$PHOENIX_STATE_BACKEND`` is set to an unrecognized
    value or required backend-specific configuration is missing."""


_BACKEND: StateBackend | None = None
_LOCK = threading.Lock()


def get_state_backend() -> StateBackend:
    """Return the singleton configured :class:`StateBackend` instance.

    Constructed lazily on first call from the env-var configuration.
    Subsequent calls return the same instance. Per Decision 31 the
    backend choice is not switchable at runtime -- call
    :func:`reset_state_backend` only at process boundaries (daemon
    shutdown, test fixtures).
    """
    global _BACKEND
    with _LOCK:
        if _BACKEND is None:
            _BACKEND = _construct_backend()
        return _BACKEND


def reset_state_backend() -> None:
    """Close the cached backend and clear the singleton.

    Called by the FastAPI lifespan shutdown handler so the next daemon
    boot constructs a fresh backend. Tests call this in teardown to
    isolate state-backend wiring between :class:`fastapi.testclient.TestClient`
    sessions.
    """
    global _BACKEND
    with _LOCK:
        if _BACKEND is not None:
            close = getattr(_BACKEND, "close", None)
            if callable(close):
                close()
            _BACKEND = None


def _resolve_backend_name() -> str:
    """Resolve the backend name from env var; default ``sqlite``.

    Lower-cased so callers can use ``"SQLite"`` / ``"Postgres"`` etc.
    in env config files without surprise.
    """
    return os.environ.get("PHOENIX_STATE_BACKEND", "sqlite").strip().lower()


def _construct_backend() -> StateBackend:
    """Construct the configured backend from env vars.

    Imports backend classes lazily so SQLite-only installs never touch
    the Postgres backend module (which has its own lazy psycopg
    import). This keeps import time short and the import surface
    minimal on bare installs.
    """
    name = _resolve_backend_name()
    if name == "sqlite":
        from phoenix.state.sqlite_backend import SQLiteStateBackend

        return SQLiteStateBackend()
    if name == "postgres":
        from phoenix.state.postgres_backend import PostgresStateBackend

        return PostgresStateBackend()
    raise InvalidBackendConfig(
        f"PHOENIX_STATE_BACKEND={name!r} is not recognized; "
        f"valid values are 'sqlite' (default) or 'postgres'"
    )
