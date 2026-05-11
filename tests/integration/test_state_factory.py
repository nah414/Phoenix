"""Integration tests for the state-backend factory (Phase 6b Step 4).

Coverage:

- Default (no ``$PHOENIX_STATE_BACKEND``) resolves to SQLite.
- Explicit ``$PHOENIX_STATE_BACKEND=sqlite`` resolves to SQLite.
- Invalid backend name raises :class:`InvalidBackendConfig`.
- Singleton: two calls return the same instance until reset.
- ``reset_state_backend`` closes and clears the singleton.
- ``$PHOENIX_STATE_BACKEND=postgres`` dispatches to the Postgres
  branch (verified via a monkey-patched constructor; we do not require
  a live Postgres for this test).
- FastAPI lifespan wires the kill-switch store with a backend on
  startup and clears it on shutdown.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from phoenix.safety.kill_switch import KillSwitchStore, get_store
from phoenix.state import (
    InvalidBackendConfig,
    get_state_backend,
    reset_state_backend,
)
from phoenix.state.sqlite_backend import SQLiteStateBackend


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Ensure a clean factory singleton + isolated kill-switch store per test.

    Routes the SQLite default away from ``~/.phoenix/runtime/state.db``
    so tests never touch the user's real home directory.

    Critically: ``monkeypatch.setattr`` resets
    :data:`phoenix.safety.kill_switch._STORE` to ``None`` for the
    duration of the test and restores it after. Without this, a test
    that overrides ``_STORE`` to point at a ``tmp_path``-backed store
    would leak that reference to subsequent tests -- ``tmp_path`` is
    NOT auto-deleted by pytest between tests, so a stale
    ``kill_switch.json`` from the prior test would still satisfy the
    next test's read (and engaged state would leak).
    """
    import phoenix.safety.kill_switch as ks

    reset_state_backend()
    monkeypatch.setattr(ks, "_STORE", None, raising=False)
    monkeypatch.setenv("PHOENIX_SQLITE_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.delenv("PHOENIX_STATE_BACKEND", raising=False)
    yield
    reset_state_backend()


def test_default_resolves_to_sqlite() -> None:
    backend = get_state_backend()
    assert isinstance(backend, SQLiteStateBackend)


def test_explicit_sqlite_resolves_to_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIX_STATE_BACKEND", "sqlite")
    backend = get_state_backend()
    assert isinstance(backend, SQLiteStateBackend)


def test_case_insensitive_backend_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIX_STATE_BACKEND", "SQLite")
    backend = get_state_backend()
    assert isinstance(backend, SQLiteStateBackend)


def test_invalid_backend_name_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIX_STATE_BACKEND", "mongodb")
    with pytest.raises(InvalidBackendConfig, match="mongodb"):
        get_state_backend()


def test_singleton_returns_same_instance() -> None:
    first = get_state_backend()
    second = get_state_backend()
    assert first is second


def test_reset_closes_and_clears() -> None:
    first = get_state_backend()
    assert isinstance(first, SQLiteStateBackend)
    reset_state_backend()
    second = get_state_backend()
    assert isinstance(second, SQLiteStateBackend)
    assert first is not second


def test_postgres_branch_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    """When PHOENIX_STATE_BACKEND=postgres, the factory dispatches to
    :class:`PostgresStateBackend`. We monkey-patch the constructor so
    the test does not require psycopg or a live Postgres."""
    monkeypatch.setenv("PHOENIX_STATE_BACKEND", "postgres")

    constructed: list[Any] = []

    class FakePostgresBackend:
        def __init__(self) -> None:
            constructed.append(self)

        def close(self) -> None:
            pass

    # Inject the fake into the lazy-import path.
    import phoenix.state.postgres_backend as pg_module

    monkeypatch.setattr(pg_module, "PostgresStateBackend", FakePostgresBackend)

    backend = get_state_backend()
    assert len(constructed) == 1
    assert backend is constructed[0]


def test_reset_when_singleton_unset_is_noop() -> None:
    # Ensure the autouse fixture already cleared state.
    reset_state_backend()
    reset_state_backend()  # second call must not raise


def test_lifespan_wires_kill_switch_backend(tmp_path: Path) -> None:
    """The FastAPI lifespan startup must inject the singleton backend
    into the kill-switch store; shutdown must clear it."""
    from fastapi.testclient import TestClient

    from phoenix.api.routes import app

    # Replace the module-level kill-switch store with a fresh one so the
    # write-through is observable independent of prior test state.
    import phoenix.safety.kill_switch as ks

    fresh_store = KillSwitchStore(path=tmp_path / "kill_switch.json")
    ks._STORE = fresh_store  # type: ignore[attr-defined]

    assert get_store()._backend is None  # type: ignore[attr-defined]

    with TestClient(app) as _client:
        # Lifespan startup has fired; backend should be wired.
        assert get_store()._backend is not None  # type: ignore[attr-defined]
        assert isinstance(get_store()._backend, SQLiteStateBackend)  # type: ignore[attr-defined]

    # Lifespan shutdown has fired; backend should be cleared.
    assert get_store()._backend is None  # type: ignore[attr-defined]


def test_lifespan_dual_write_actually_flows(tmp_path: Path) -> None:
    """Under the lifespan, engaging the kill switch via the store
    writes to both JSON and SQLite (the Step 2 write-through path is
    active because Step 4 wired the backend)."""
    from fastapi.testclient import TestClient

    from phoenix.api.routes import app

    import phoenix.safety.kill_switch as ks

    json_path = tmp_path / "kill_switch.json"
    fresh_store = KillSwitchStore(path=json_path)
    ks._STORE = fresh_store  # type: ignore[attr-defined]

    with TestClient(app) as _client:
        store = get_store()
        store.engage(by="ash", reason="step 4 lifespan test")

        # JSON was written.
        assert json_path.exists()

        # SQLite was written through the wired backend.
        backend = store._backend  # type: ignore[attr-defined]
        assert backend is not None
        state = backend.get_kill_switch_state()
        assert state["engaged"] is True
        assert state["engaged_by"] == "ash"
