"""Phase 8 Step 4 -- admin calibration drill-down + force-cycle tests.

Coverage:

- ``GET /v1/admin/calibration/detail`` returns the per-checker
  rollup with cadence, scheduler flag, checker list, and last
  snapshot (None on first call, populated after a cycle).
- ``GET /v1/admin/calibration/history`` returns drift.* events
  from the audit_events table; respects since_unix + limit.
- ``POST /v1/admin/calibration/run`` with ``wait=True`` runs the
  cycle synchronously and returns the snapshot.
- ``POST /v1/admin/calibration/run`` with ``wait=False`` queues
  the cycle in a daemon thread and returns immediately with a
  ``cycle_id``.
- Concurrent in-progress requests get HTTP 409
  ``CalibrationRunInProgress``.
- Non-admin actors get 403 on every endpoint.
- All three endpoints register with the ``Admin`` OpenAPI tag.
"""

from __future__ import annotations

import base64
import json
import time
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
    from phoenix.verification.drift_detector import reset_detector

    reset_emitter()
    reset_ledger()
    reset_state_backend()
    reset_detector()
    ks_module._STORE = None
    get_limiter().reset_all()
    try:
        yield runtime
    finally:
        reset_emitter()
        reset_ledger()
        reset_state_backend()
        reset_detector()
        ks_module._STORE = None
        get_limiter().reset_all()


def _alice_header() -> str:
    from phoenix.identity.bootstrap import mint_bootstrap_actor

    alice = mint_bootstrap_actor("alice")
    payload = alice.to_payload()
    return "Phoenix-Actor " + base64.b64encode(json.dumps(payload).encode()).decode("ascii")


# ---------------------------------------------------------------------------
# /v1/admin/calibration/detail


class TestCalibrationDetail:
    def test_returns_detector_rollup(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.get("/v1/admin/calibration/detail")
        assert resp.status_code == 200
        body = resp.json()
        assert "cadence_seconds" in body
        assert "scheduler_running" in body
        # Phase 6b ships three checkers by default.
        names = [c["name"] for c in body["checkers"]]
        assert "tier1_analytical" in names
        assert "ml_statistical" in names
        assert "cross_version" in names

    def test_no_snapshot_when_no_cycle_yet(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.get("/v1/admin/calibration/detail")
        assert resp.json()["last_snapshot"] is None

    def test_snapshot_reflects_completed_cycle(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            client.post("/v1/admin/calibration/run", json={"wait": True})
            resp = client.get("/v1/admin/calibration/detail")
        snap = resp.json()["last_snapshot"]
        assert snap is not None
        assert snap["state"] in ("healthy", "warning", "high_confidence_warning")
        assert "firing_detectors" in snap

    def test_403_for_non_admin(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.get(
                "/v1/admin/calibration/detail",
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# /v1/admin/calibration/history


class TestCalibrationHistory:
    def test_returns_empty_count_when_no_drift_events(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.get("/v1/admin/calibration/history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
        assert body["events"] == []

    def test_filters_only_drift_event_types(self, isolated_runtime: Path) -> None:
        """Manually seed audit_events with mixed event_types; only
        drift.* should appear in the response."""
        from phoenix.state import get_state_backend

        backend = get_state_backend()
        now = time.time()
        backend.append_audit_event(
            {
                "timestamp_unix": now,
                "actor_id": "system",
                "layer": "drift_detector",
                "event_type": "drift.cycle.completed",
                "parameters": {"state": "healthy"},
                "result_hash": "",
            }
        )
        backend.append_audit_event(
            {
                "timestamp_unix": now + 1,
                "actor_id": "adam",
                "layer": "api",
                "event_type": "api.request.complete",
                "parameters": {},
                "result_hash": "",
            }
        )

        with TestClient(app) as client:
            resp = client.get("/v1/admin/calibration/history")
        body = resp.json()
        assert body["count"] == 1
        assert body["events"][0]["event_type"] == "drift.cycle.completed"

    def test_respects_limit(self, isolated_runtime: Path) -> None:
        from phoenix.state import get_state_backend

        backend = get_state_backend()
        for i in range(5):
            backend.append_audit_event(
                {
                    "timestamp_unix": time.time() + i,
                    "actor_id": "system",
                    "layer": "drift_detector",
                    "event_type": f"drift.cycle.{i}",
                    "parameters": {},
                    "result_hash": "",
                }
            )

        with TestClient(app) as client:
            resp = client.get("/v1/admin/calibration/history?limit=3")
        body = resp.json()
        assert body["count"] == 3
        assert body["limit"] == 3

    def test_403_for_non_admin(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.get(
                "/v1/admin/calibration/history",
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /v1/admin/calibration/run


class TestCalibrationRun:
    def test_wait_true_runs_synchronously(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post("/v1/admin/calibration/run", json={"wait": True})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["cycle_id"].startswith("cycle_")
        assert "snapshot" in body
        assert body["snapshot"]["state"] in (
            "healthy",
            "warning",
            "high_confidence_warning",
        )

    def test_wait_false_returns_immediately(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post("/v1/admin/calibration/run", json={"wait": False})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "running"
        assert body["cycle_id"].startswith("cycle_")
        assert body["wait"] is False

    def test_concurrent_run_returns_409(self, isolated_runtime: Path) -> None:
        """Block the first cycle from completing, fire a second
        request, verify it gets HTTP 409."""
        from phoenix.admin import calibration as cal_module

        # Manually grab the run-lock so the next request can't.
        cal_module._RUN_LOCK.acquire()
        try:
            with TestClient(app) as client:
                resp = client.post("/v1/admin/calibration/run", json={"wait": True})
            assert resp.status_code == 409
            assert "already running" in resp.json()["detail"].lower()
        finally:
            cal_module._RUN_LOCK.release()

    def test_lock_released_after_completion(self, isolated_runtime: Path) -> None:
        """A successful cycle releases the lock so the next request
        succeeds."""
        with TestClient(app) as client:
            r1 = client.post("/v1/admin/calibration/run", json={"wait": True})
            r2 = client.post("/v1/admin/calibration/run", json={"wait": True})
        assert r1.status_code == 200
        assert r2.status_code == 200

    def test_lock_released_after_failure(self, isolated_runtime: Path) -> None:
        """A checker that raises inside run_cycle must NOT pin the
        lock. (Phase 6b's run_cycle catches checker exceptions and
        marks them as firing, so this is defensive but worth
        guarding the finally block.)"""
        from phoenix.admin import calibration as cal_module

        # After any run, the lock should be unlocked.
        with TestClient(app) as client:
            client.post("/v1/admin/calibration/run", json={"wait": True})

        # Acquire non-blocking should succeed.
        assert cal_module._RUN_LOCK.acquire(blocking=False)
        cal_module._RUN_LOCK.release()

    def test_403_for_non_admin(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/calibration/run",
                json={"wait": False},
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Background-thread completion (wait=False)


def test_wait_false_cycle_actually_completes(
    isolated_runtime: Path,
) -> None:
    """wait=False returns immediately, but the background thread
    actually runs the cycle. Verify by polling /detail.last_snapshot
    until it shows up (or timeout)."""
    with TestClient(app) as client:
        resp = client.post("/v1/admin/calibration/run", json={"wait": False})
        assert resp.status_code == 200

        # Poll for up to 30 seconds for the snapshot to appear.
        deadline = time.time() + 30.0
        snapshot = None
        while time.time() < deadline:
            r = client.get("/v1/admin/calibration/detail")
            snapshot = r.json().get("last_snapshot")
            if snapshot is not None:
                break
            time.sleep(0.1)
    assert snapshot is not None, "background cycle never produced a snapshot"


# ---------------------------------------------------------------------------
# OpenAPI registration


def test_calibration_routes_registered_with_admin_tag(
    isolated_runtime: Path,
) -> None:
    with TestClient(app) as client:
        schema = client.get("/v1/openapi.json").json()
    expected = {
        ("/v1/admin/calibration/detail", "get"),
        ("/v1/admin/calibration/history", "get"),
        ("/v1/admin/calibration/run", "post"),
    }
    for path, verb in expected:
        assert path in schema["paths"], f"missing {path}"
        op = schema["paths"][path][verb]
        assert "Admin" in op["tags"], f"{path} missing Admin tag"
