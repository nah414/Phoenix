"""Phase 8 Step 5 -- admin verification + pending-review override tests.

Coverage:

- ``GET /v1/admin/tasks-pending-review`` lists rows from the
  ``pending_review_queue`` table.
- ``POST /v1/admin/tasks-pending-review/{task_id}/override`` with a
  valid disposition: resolves the queue row, emits the audit event,
  appends an ``OverrideByOperatorEntry`` ledger entry.
- Override with an invalid disposition string returns HTTP 400.
- Override of an unknown task_id returns HTTP 409
  ``TaskNotPendingReview``.
- Override requires ``can_override_human_review`` on top of
  ``is_admin``: an actor with ``is_admin=True`` but missing the
  capability gets HTTP 403 ``PermissionDenied``.
- ``GET /v1/admin/verification/rung-distribution`` aggregates the
  ``initial_rung`` field from ``verification.gate.started`` audit
  events into a histogram.
- All three endpoints carry the ``Admin`` OpenAPI tag.

Plus the architecturally-consequential Phase 8 Step 5 wiring
verification: a DEGRADED solve (drift detector in warning state)
auto-enqueues into ``pending_review_queue`` so the admin endpoint
sees real data without manual seeding.
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


def _seed_pending_review(task_id: str, actor_id: str = "adam") -> str:
    """Manually enqueue a pending-review row via the state backend.

    Returns the review_id.
    """
    from phoenix.state import get_state_backend

    review_id = f"review_{task_id}"
    get_state_backend().enqueue_pending_review(
        {
            "review_id": review_id,
            "task_id": task_id,
            "actor_id": actor_id,
            "result": {
                "value": 1.0,
                "error_bar": 0.05,
                "sigma": 0.5,
                "phoenix_agreement_type": "degraded",
            },
            "agreement_type": "degraded",
            "queued_at_unix": time.time(),
        }
    )
    return review_id


# ---------------------------------------------------------------------------
# GET /v1/admin/tasks-pending-review


class TestListPendingReview:
    def test_empty_queue_returns_zero(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.get("/v1/admin/tasks-pending-review")
        assert resp.status_code == 200
        assert resp.json() == {"reviews": [], "count": 0}

    def test_returns_seeded_rows(self, isolated_runtime: Path) -> None:
        _seed_pending_review("task_a")
        _seed_pending_review("task_b")
        with TestClient(app) as client:
            resp = client.get("/v1/admin/tasks-pending-review")
        body = resp.json()
        assert body["count"] == 2
        task_ids = [r["task_id"] for r in body["reviews"]]
        assert "task_a" in task_ids
        assert "task_b" in task_ids

    def test_403_for_non_admin(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.get(
                "/v1/admin/tasks-pending-review",
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /v1/admin/tasks-pending-review/{task_id}/override


class TestOverridePendingReview:
    def test_valid_override_resolves_row(self, isolated_runtime: Path) -> None:
        _seed_pending_review("task_valid")
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/tasks-pending-review/task_valid/override",
                json={"disposition": "ship-as-degraded", "reason": "good enough"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["task_id"] == "task_valid"
        assert body["disposition"] == "ship-as-degraded"
        assert body["resolved_by"] == "adam"

        # The row is no longer in the unresolved list.
        from phoenix.state import get_state_backend

        rows = get_state_backend().list_pending_reviews()
        assert rows == []

    def test_appends_override_ledger_entry(self, isolated_runtime: Path) -> None:
        _seed_pending_review("task_ledger")
        with TestClient(app) as client:
            client.post(
                "/v1/admin/tasks-pending-review/task_ledger/override",
                json={"disposition": "reject", "reason": "bad data"},
            )

        from phoenix.state import get_state_backend

        rows = get_state_backend().list_ledger_entries(since_unix=0, limit=10)
        override_rows = [r for r in rows if r["entry_kind"] == "override_by_operator"]
        assert len(override_rows) == 1
        payload = json.loads(override_rows[0]["payload_json"])
        assert payload["affected_task_id"] == "task_ledger"
        assert payload["override_disposition"] == "reject"
        assert payload["operator_id"] == "adam"

    def test_invalid_disposition_returns_400(self, isolated_runtime: Path) -> None:
        _seed_pending_review("task_bad")
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/tasks-pending-review/task_bad/override",
                json={"disposition": "definitely-not-valid", "reason": "x"},
            )
        assert resp.status_code == 400
        assert "Invalid disposition" in resp.json()["detail"]

    def test_unknown_task_returns_409(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/tasks-pending-review/never_queued/override",
                json={"disposition": "ship-as-degraded", "reason": "x"},
            )
        assert resp.status_code == 409

    def test_override_requires_can_override_human_review(
        self,
        isolated_runtime: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """An is_admin actor without ``can_override_human_review`` gets 403."""
        from phoenix.identity.bootstrap import mint_bootstrap_actor
        from phoenix.safety import permissions as perms_module
        from phoenix.safety.permissions import ActorPermissions, PermissionsRegistry

        # Build a registry where "lab" is admin but lacks the override capability.
        isolated_registry = PermissionsRegistry(path=tmp_path / "perms_step5.json")
        isolated_registry.set(
            "lab",
            ActorPermissions(
                can_submit_tasks=True,
                can_replay_tasks=True,
                can_load_adapter=True,
                can_unload_adapter=True,
                frontier_physics=True,
                can_override_human_review=False,  # explicitly NOT granted
                is_admin=True,
                rate_limit_tier="admin",
            ),
        )
        monkeypatch.setattr(perms_module, "_REGISTRY", isolated_registry)

        _seed_pending_review("task_cap_check")

        lab = mint_bootstrap_actor("lab")
        payload = lab.to_payload()
        header = "Phoenix-Actor " + base64.b64encode(json.dumps(payload).encode()).decode("ascii")

        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/tasks-pending-review/task_cap_check/override",
                json={"disposition": "ship-as-degraded", "reason": "x"},
                headers={"Authorization": header},
            )
        assert resp.status_code == 403
        assert "can_override_human_review" in resp.json()["detail"]

    def test_override_403_for_non_admin(self, isolated_runtime: Path) -> None:
        _seed_pending_review("task_non_admin")
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/tasks-pending-review/task_non_admin/override",
                json={"disposition": "ship-as-degraded", "reason": "x"},
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /v1/admin/verification/rung-distribution


class TestRungDistribution:
    def test_empty_when_no_solves(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.get("/v1/admin/verification/rung-distribution")
        assert resp.status_code == 200
        body = resp.json()
        assert body["rung_counts"] == {}
        assert body["total_solves_in_window"] == 0

    def test_aggregates_initial_rung_from_audit_log(self, isolated_runtime: Path) -> None:
        """Seed audit_events with verification.gate.started rows and
        verify the histogram tallies them."""
        from phoenix.state import get_state_backend

        backend = get_state_backend()
        for rung, n in [("R1_FLOOR", 3), ("R3_TWO_AXES", 5), ("R5_REPLICATED", 2)]:
            for i in range(n):
                backend.append_audit_event(
                    {
                        "timestamp_unix": time.time() + i,
                        "actor_id": "adam",
                        "layer": "verification_gate",
                        "event_type": "verification.gate.started",
                        "parameters": {"initial_rung": rung},
                        "result_hash": "",
                    }
                )

        with TestClient(app) as client:
            resp = client.get("/v1/admin/verification/rung-distribution")
        body = resp.json()
        assert body["rung_counts"]["R1_FLOOR"] == 3
        assert body["rung_counts"]["R3_TWO_AXES"] == 5
        assert body["rung_counts"]["R5_REPLICATED"] == 2
        assert body["total_solves_in_window"] == 10

    def test_403_for_non_admin(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.get(
                "/v1/admin/verification/rung-distribution",
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Verification gate auto-enqueue (the architecturally-consequential
# OPEN-3 wiring)


class TestVerificationGateAutoEnqueue:
    def test_real_solve_does_not_enqueue_when_converged(self, isolated_runtime: Path) -> None:
        """A normal QHO solve produces CONVERGED, not DEGRADED, so it
        should NOT enqueue. This guards the gate against over-enqueueing."""
        with TestClient(app) as client:
            body = {
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
            client.post("/v1/tasks", json=body)
            resp = client.get("/v1/admin/tasks-pending-review")
        assert resp.json()["count"] == 0

    def test_degraded_via_drift_warning_enqueues(self, isolated_runtime: Path) -> None:
        """Force drift state to warning so the verification gate
        classifies the result as DEGRADED, then verify the auto-enqueue
        fired (Phase 8 Step 5 OPEN-3 LOCKED wiring)."""
        from phoenix.verification import gate as gate_module
        from phoenix.verification.drift_state import DriftState

        # Patch the gate's already-bound read_drift_state reference
        # (the gate does ``from phoenix.verification.drift_state import
        # read_drift_state`` at module load, so the binding lives in
        # gate's namespace -- patching the source module doesn't affect
        # the gate's local).
        def _warning_drift_state() -> DriftState:
            return DriftState(state="warning", detector_summaries={"tier1": "synthetic"})

        original = gate_module.read_drift_state
        gate_module.read_drift_state = _warning_drift_state  # type: ignore[assignment]
        try:
            with TestClient(app) as client:
                body = {
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
                solve_resp = client.post("/v1/tasks", json=body)
                resp = client.get("/v1/admin/tasks-pending-review")
        finally:
            gate_module.read_drift_state = original  # type: ignore[assignment]

        assert solve_resp.status_code == 200
        # The user got their result (queue is informational; solve still
        # returns synchronously).
        # But the queue should have one row.
        assert resp.json()["count"] == 1
        row = resp.json()["reviews"][0]
        assert row["task_id"] == solve_resp.json()["task_id"]
        assert row["agreement_type"] == "degraded"
        assert row["result"]["phoenix_agreement_type"] == "degraded"
        # Cross-reference to the ledger entry the verification gate wrote.
        assert (
            row["result"]["omega_ledger_entry_id"]
            == solve_resp.json()["provenance"]["omega_ledger_entry_id"]
        )


# ---------------------------------------------------------------------------
# OpenAPI registration


def test_step5_routes_registered_with_admin_tag(
    isolated_runtime: Path,
) -> None:
    with TestClient(app) as client:
        schema = client.get("/v1/openapi.json").json()
    expected = {
        ("/v1/admin/tasks-pending-review", "get"),
        ("/v1/admin/tasks-pending-review/{task_id}/override", "post"),
        ("/v1/admin/verification/rung-distribution", "get"),
    }
    for path, verb in expected:
        assert path in schema["paths"], f"missing {path}"
        op = schema["paths"][path][verb]
        assert "Admin" in op["tags"], f"{path} missing Admin tag"
