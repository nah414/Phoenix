"""Phase 8 Step 2 -- admin kill-switch endpoint tests.

Verifies the three /v1/admin/kill-switch/{engage,release,status}
endpoints per architecture v1 Section 8.3:

- Engage flips the persisted state, audit-logs the operator action,
  AND appends a ``KillSwitchEntry`` to the Omega Ledger with
  ``transition="engaged"``.
- Subsequent ``POST /v1/tasks`` returns HTTP 503 ``KillSwitchEngaged``
  (the safety gate Stage 0 enforces this; the admin endpoint
  bypasses it via the ``skip_kill_switch_check=True`` flag).
- Status remains readable while engaged.
- Release mirrors engage with ``transition="released"`` and a cross-
  reference to the prior ``engaged_at_unix``.
- Non-admin actors get HTTP 403 on every endpoint.
- The ledger end-to-end chain is valid after engage+release.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix.api.routes import app


@pytest.fixture
def isolated_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[Path]:
    """Per-test fresh Phoenix runtime so the kill switch starts disengaged."""
    runtime = tmp_path / "phoenix_runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PHOENIX_SQLITE_DB_PATH", str(runtime / "state.db"))
    monkeypatch.setenv("PHOENIX_AUDIT_DIR", str(runtime / "audit"))
    monkeypatch.setenv("PHOENIX_KILL_SWITCH_PATH", str(runtime / "kill_switch.json"))

    from phoenix.audit import reset_emitter
    from phoenix.ledger import reset_ledger
    from phoenix.safety import kill_switch as ks_module
    from phoenix.safety.rate_limiter import get_limiter
    from phoenix.state import reset_state_backend

    reset_emitter()
    reset_ledger()
    reset_state_backend()
    # Reset the kill-switch singleton so it picks up the env override.
    ks_module._STORE = None
    get_limiter().reset_all()
    try:
        yield runtime
    finally:
        # Always release the switch so leakage to other tests is impossible.
        try:
            ks_module.get_store().release()
        except Exception:
            pass
        reset_emitter()
        reset_ledger()
        reset_state_backend()
        ks_module._STORE = None
        get_limiter().reset_all()


def _qho_body() -> dict:
    return {
        "physics_context": {
            "mass_kg": 9.1093837015e-31,
            "length_scale_m": 4e-9,
            "metadata": {"omega": 1e15, "n_grid_points": 200},
        },
        "tolerance": {
            "max_error_bar": 1e-3,
            "reproducibility_mode": "default",
            "latency_tier": "batch_realtime",
            "frontier_physics": False,
        },
        "metadata": {},
    }


def _alice_header() -> str:
    """Build a signed Actor header for non-admin alice."""
    from phoenix.identity.bootstrap import mint_bootstrap_actor

    alice = mint_bootstrap_actor("alice")
    payload = alice.to_payload()
    return "Phoenix-Actor " + base64.b64encode(json.dumps(payload).encode()).decode("ascii")


# ---------------------------------------------------------------------------
# Status (read-only)


class TestKillSwitchStatus:
    def test_initial_state_is_disengaged(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.get("/v1/admin/kill-switch/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["engaged"] is False
        assert body["engaged_at_utc"] is None
        assert body["engaged_by"] is None
        assert body["reason"] is None

    def test_status_403_for_non_admin(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.get(
                "/v1/admin/kill-switch/status",
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Engage


class TestKillSwitchEngage:
    def test_engage_flips_state(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/kill-switch/engage",
                json={"rationale": "smoke test"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["engaged"] is True
        assert body["engaged_by"] == "adam"
        assert body["reason"] == "smoke test"
        assert body["engaged_at_utc"] is not None

    def test_engage_blocks_subsequent_task_submit(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            client.post(
                "/v1/admin/kill-switch/engage",
                json={"rationale": "blocking tasks"},
            )
            resp = client.post("/v1/tasks", json=_qho_body())
        # /v1/tasks goes through the safety gate which DOES check
        # the kill switch -> 503.
        assert resp.status_code == 503

    def test_engage_appends_ledger_entry(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            client.post(
                "/v1/admin/kill-switch/engage",
                json={"rationale": "ledger smoke"},
            )

        from phoenix.state import get_state_backend

        rows = get_state_backend().list_ledger_entries(since_unix=0, limit=10)
        kill_rows = [r for r in rows if r["entry_kind"] == "kill_switch"]
        assert len(kill_rows) == 1
        payload = json.loads(kill_rows[0]["payload_json"])
        assert payload["transition"] == "engaged"
        assert payload["by"] == "adam"
        assert payload["reason"] == "ledger smoke"

    def test_engage_emits_admin_audit_event(self, isolated_runtime: Path) -> None:
        from phoenix.audit import AuditEvent, get_emitter, reset_emitter

        class _Recorder:
            def __init__(self) -> None:
                self.events: list[AuditEvent] = []

            def emit(self, event: AuditEvent) -> None:
                self.events.append(event)

            def close(self, drain_timeout_seconds: float = 5.0) -> None:
                pass

        reset_emitter()
        sink = _Recorder()
        get_emitter().add_sink(sink)
        try:
            with TestClient(app) as client:
                resp = client.post(
                    "/v1/admin/kill-switch/engage",
                    json={"rationale": "audit emit test"},
                )
            assert resp.status_code == 200

            admin_events = [e for e in sink.events if e.event_type == "admin.kill_switch.engaged"]
            assert len(admin_events) == 1
            assert admin_events[0].actor_id == "adam"
            assert admin_events[0].parameters["rationale"] == "audit emit test"
        finally:
            reset_emitter()

    def test_engage_403_for_non_admin(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/kill-switch/engage",
                json={"rationale": "alice tries"},
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Release


class TestKillSwitchRelease:
    def test_release_disengages(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            client.post(
                "/v1/admin/kill-switch/engage",
                json={"rationale": "engage"},
            )
            resp = client.post(
                "/v1/admin/kill-switch/release",
                json={"rationale": "release"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["engaged"] is False
        assert body["engaged_by"] is None

    def test_release_unblocks_task_submit(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            client.post(
                "/v1/admin/kill-switch/engage",
                json={"rationale": "block"},
            )
            assert client.post("/v1/tasks", json=_qho_body()).status_code == 503

            client.post(
                "/v1/admin/kill-switch/release",
                json={"rationale": "unblock"},
            )
            resp = client.post("/v1/tasks", json=_qho_body())
        assert resp.status_code == 200

    def test_release_appends_ledger_entry_with_cross_reference(
        self, isolated_runtime: Path
    ) -> None:
        with TestClient(app) as client:
            client.post(
                "/v1/admin/kill-switch/engage",
                json={"rationale": "engage"},
            )
            client.post(
                "/v1/admin/kill-switch/release",
                json={"rationale": "release"},
            )

        from phoenix.state import get_state_backend

        rows = get_state_backend().list_ledger_entries(since_unix=0, limit=10)
        kill_rows = [r for r in rows if r["entry_kind"] == "kill_switch"]
        # Two entries: engaged + released.
        assert len(kill_rows) == 2

        engaged_payload = json.loads(kill_rows[0]["payload_json"])
        released_payload = json.loads(kill_rows[1]["payload_json"])
        assert engaged_payload["transition"] == "engaged"
        assert released_payload["transition"] == "released"
        # Release entry's engaged_at_unix cross-references the prior engage.
        assert released_payload["engaged_at_unix"] is not None
        assert released_payload["engaged_at_unix"] > 0

    def test_release_when_not_engaged_succeeds(self, isolated_runtime: Path) -> None:
        """Idempotency: releasing a never-engaged switch is a no-op
        on state but still emits audit + appends ledger entry."""
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/kill-switch/release",
                json={"rationale": "wasn't engaged but recording the action"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["engaged"] is False

    def test_release_403_for_non_admin(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            client.post(
                "/v1/admin/kill-switch/engage",
                json={"rationale": "engage"},
            )
            resp = client.post(
                "/v1/admin/kill-switch/release",
                json={"rationale": "alice tries"},
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403
        # And the switch is still engaged.
        with TestClient(app) as client:
            check = client.get("/v1/admin/kill-switch/status")
        assert check.json()["engaged"] is True


# ---------------------------------------------------------------------------
# Stage-0 bypass invariant


class TestKillSwitchStage0Bypass:
    def test_release_works_while_engaged(self, isolated_runtime: Path) -> None:
        """The release endpoint MUST be callable while the switch is
        engaged. If the safety gate's Stage-0 check applied to admin
        endpoints, release would be blocked at 503 -- which would
        make the switch un-releasable. This is the canonical regression
        test for the skip_kill_switch_check seam."""
        with TestClient(app) as client:
            client.post(
                "/v1/admin/kill-switch/engage",
                json={"rationale": "engage"},
            )
            # While engaged: /v1/tasks 503s.
            assert client.post("/v1/tasks", json=_qho_body()).status_code == 503
            # While engaged: release MUST succeed (not 503).
            resp = client.post(
                "/v1/admin/kill-switch/release",
                json={"rationale": "release"},
            )
        assert resp.status_code == 200

    def test_status_readable_while_engaged(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            client.post(
                "/v1/admin/kill-switch/engage",
                json={"rationale": "engage"},
            )
            resp = client.get("/v1/admin/kill-switch/status")
        assert resp.status_code == 200
        assert resp.json()["engaged"] is True


# ---------------------------------------------------------------------------
# Ledger chain validity after engage + release


def test_ledger_chain_valid_after_engage_release(
    isolated_runtime: Path,
) -> None:
    """Phase 7's Omega Ledger chain must verify clean after the two
    kill-switch entries land."""
    with TestClient(app) as client:
        client.post("/v1/admin/kill-switch/engage", json={"rationale": "engage"})
        client.post("/v1/admin/kill-switch/release", json={"rationale": "release"})

    from phoenix.ledger import get_ledger
    from phoenix.state import get_state_backend

    sql_report = get_state_backend().verify_ledger_integrity()
    py_report = get_ledger().verify_chain()
    assert sql_report["valid"] is True
    assert sql_report["entries_checked"] == 2
    assert py_report.valid is True
    assert py_report.entries_checked == 2
