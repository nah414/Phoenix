"""Phase 11 Step 3 -- state-backend-down isolation test (§10.7 acceptance).

Per architecture v1 Section 10.7: when the state backend (SQLite or
Postgres) is unreachable, Phoenix must fail-closed with the typed
:class:`StateBackendUnavailable` error rather than silently
degrading to in-memory state or leaking the driver-level
``sqlite3.OperationalError``.

This module pins the state-backend portion of that contract:

1. ``SQLiteStateBackend._require_conn`` raises
   :class:`StateBackendUnavailable` (Step 1) when the connection
   has been closed -- every read/write method goes through this
   chokepoint, so every operation fail-closes deterministically.
2. The ``kill_state_backend`` fixture composes -- when downstream
   code (audit append, kill-switch read, ledger query) tries to
   touch a closed backend, the typed error surfaces.
3. ``isinstance(exc, StateBackendUnavailable)`` passes; pre-
   Phase-11 ``RuntimeError`` pattern-matching no longer works
   (the error class moved up the hierarchy).
4. The ``backend_kind`` attribute names which backend was in use
   ("sqlite" / "postgres") so operators reading the log see
   which dependency to investigate.
5. **No silent fallback.** There is no in-memory ``StateBackend``
   implementation today; this test pins that any future in-memory
   shim would have to be explicitly opted in, not silently
   substituted on backend failure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix.state.errors import StateBackendUnavailable
from tests.integration.test_panic_mode.conftest import kill_state_backend


pytestmark = pytest.mark.acceptance


# ---------------------------------------------------------------------
# 1. SQLite _require_conn surfaces StateBackendUnavailable
# ---------------------------------------------------------------------


class TestSQLiteBackendFailClosed:
    def test_every_method_fails_closed_when_closed(self, tmp_path: Path) -> None:
        """The closed-backend invariant covers every method that touches
        ``_require_conn`` -- not just the kill switch.
        """
        from phoenix.state.sqlite_backend import SQLiteStateBackend

        backend = SQLiteStateBackend(db_path=tmp_path / "state.db")
        backend.close()

        # Each of these methods goes through _require_conn; each must
        # fail-close with the typed error.
        with pytest.raises(StateBackendUnavailable):
            backend.get_kill_switch_state()
        with pytest.raises(StateBackendUnavailable):
            backend.list_audit_events(since_unix=0.0, limit=10)
        with pytest.raises(StateBackendUnavailable):
            backend.list_ledger_entries(since_unix=0.0, limit=10)
        with pytest.raises(StateBackendUnavailable):
            backend.query_actor_24h_spend("adam", as_of_unix=0.0)
        with pytest.raises(StateBackendUnavailable):
            backend.list_active_budget_overrides("adam", as_of_unix=0.0)

    def test_backend_kind_attribute_set(self, tmp_path: Path) -> None:
        """The typed error carries ``backend_kind="sqlite"`` so logs
        identify which backend impl was in use.
        """
        from phoenix.state.sqlite_backend import SQLiteStateBackend

        backend = SQLiteStateBackend(db_path=tmp_path / "state.db")
        backend.close()

        with pytest.raises(StateBackendUnavailable) as excinfo:
            backend.get_kill_switch_state()
        assert excinfo.value.backend_kind == "sqlite"


# ---------------------------------------------------------------------
# 2. kill_state_backend fixture composes through downstream callers
# ---------------------------------------------------------------------


class TestKillStateBackendComposition:
    def test_audit_append_through_killed_backend(self, isolated_runtime: Path) -> None:
        """A higher-level caller (audit emitter writing to state backend)
        must surface the typed error when the backend is down.
        """
        from phoenix.state import get_state_backend

        with kill_state_backend():
            with pytest.raises(StateBackendUnavailable):
                # append_audit_event is the typical write path
                get_state_backend().append_audit_event(
                    {
                        "timestamp_unix": 0.0,
                        "actor_id": "adam",
                        "layer": "test",
                        "event_type": "test.event",
                        "parameters_json": "{}",
                        "result_hash": "",
                    }
                )

    def test_ledger_append_through_killed_backend(self, isolated_runtime: Path) -> None:
        """Ledger writes through a closed backend fail-close."""
        from phoenix.state import get_state_backend

        with kill_state_backend():
            with pytest.raises(StateBackendUnavailable):
                get_state_backend().append_ledger_entry(
                    {
                        "entry_id": "tx-test",
                        "entry_kind": "solve",
                        "timestamp_unix": 0.0,
                        "actor_id": "adam",
                        "parent_hash": "GENESIS",
                        "entry_hash": "abc",
                        "payload_json": "{}",
                    }
                )


# ---------------------------------------------------------------------
# 3. Typed-error vs generic RuntimeError
# ---------------------------------------------------------------------


class TestTypedErrorDiscipline:
    def test_isinstance_state_backend_unavailable_passes(self, isolated_runtime: Path) -> None:
        from phoenix.state import get_state_backend

        with kill_state_backend():
            try:
                get_state_backend().get_kill_switch_state()
            except Exception as exc:
                assert isinstance(exc, StateBackendUnavailable)
                # NOT a bare RuntimeError (pre-Phase-11 behavior)
                assert type(exc) is not RuntimeError
            else:
                pytest.fail("expected StateBackendUnavailable, got nothing")


# ---------------------------------------------------------------------
# 4. No silent in-memory fallback
# ---------------------------------------------------------------------


def test_no_silent_fallback_to_in_memory_state(
    isolated_runtime: Path,
) -> None:
    """There is no in-memory StateBackend impl in Phoenix v1. When the
    SQLite/Postgres backend is unreachable, callers MUST see the typed
    error -- they MUST NOT receive a silent substitute that pretends
    to work but doesn't actually persist.

    This test pins that invariant: if a future v1.x adds an
    InMemoryStateBackend, the migration must NOT make it the silent
    fallback. It must be explicit opt-in via env var or config.
    """
    from phoenix.state import get_state_backend

    # Force a clean state with the backend present
    backend = get_state_backend()
    backend.get_kill_switch_state()  # works

    with kill_state_backend():
        # Operations MUST raise. They MUST NOT return a default empty
        # value silently (e.g., {"engaged": False}) as if the backend
        # were healthy.
        with pytest.raises(StateBackendUnavailable):
            get_state_backend().get_kill_switch_state()


# ---------------------------------------------------------------------
# 5. HTTP integration: admin health endpoint surfaces 500 when backend down
# ---------------------------------------------------------------------


def test_admin_health_endpoint_with_state_backend_down(
    isolated_runtime: Path,
) -> None:
    """A request to an admin endpoint that touches the state backend
    must NOT return 200 with a healthy-looking response when the
    backend is actually down. The endpoint either:

    - Returns 5xx with a clear error message, OR
    - Returns 200 with a 'state_backend' status field marked unhealthy.

    Either is acceptable per §10.7 ("never silently degrade"); what's
    NOT acceptable is returning 200 with all subsystems marked healthy.

    The Phase 8 admin/health/detailed endpoint uses defensive sub-call
    wrapping (per Phase 8 Step 3) so each subsystem reports its own
    health. We assert that when the state backend is down, the
    rollup explicitly notes it.
    """
    from fastapi.testclient import TestClient

    from phoenix.api.routes import app

    with TestClient(app) as client:
        with kill_state_backend():
            resp = client.get("/v1/admin/health/detailed")

    # The endpoint either returns 5xx OR returns 200 with the state
    # backend's status surfaced as not-healthy. The §10.7 invariant
    # is "no silent degrade" -- in either case, the caller can SEE
    # the failure.
    if resp.status_code >= 500:
        # 5xx response is acceptable: the endpoint hit the typed error
        # and surfaced it. NO assertion on shape -- just the bare fact
        # that the failure didn't get swallowed.
        return
    # If 200, the body MUST surface the state-backend unhealth somewhere
    body_text = resp.text.lower()
    assert (
        "state_backend" in body_text
        or "stateBackend" in resp.text
        or "unhealthy" in body_text
        or "unavailable" in body_text
        or "error" in body_text
    ), f"200 response must surface state-backend failure; got: {resp.text[:500]}"
