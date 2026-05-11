"""Phase 7 Step 2 -- audit-wiring integration tests.

Verifies that every code path the BUILDGUIDE names as an audit-emit
site actually produces an :class:`AuditEvent` of the expected shape:

- ``phoenix/safety/gate.py`` -- one ``safety.gate.<decision>`` event
  per ``verify_request`` call, pass or fail, with ``request_id``
  threaded through when the caller passes one.
- ``phoenix/verification/gate.py`` -- ``verification.gate.<lifecycle>``
  events at the 7 lifecycle moments the broker stream also covers
  (started, solver_complete, control_complete, orchestrate_progress,
  promoted-after-axis-1, promoted-after-axis-2, completed). Tests
  exercise an end-to-end solve and assert the audit log contains
  the started + completed pair (the other events are conditional on
  axis dispatch).
- ``phoenix/api/routes.py`` -- ``api.request.start`` +
  ``api.request.complete`` (or ``.error``) for every REST request
  via the HTTP middleware. Tests assert the request_id from the
  middleware propagates into downstream safety/verification audits.
- ``phoenix/api/routes.py`` WebSocket handlers --
  ``api.ws.connect_rejected`` (missing/invalid token),
  ``api.ws.connect_accepted`` (post-accept), and ``api.ws.closed``
  (via try/finally so all exit paths emit).

A :class:`_RecordingSink` test double captures every event into an
in-memory list so the assertions can inspect the exact shape without
parsing JSONL files.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import phoenix  # noqa: F401  -- triggers sys.path injection for vendored modules
from phoenix.audit import AuditEmitter, AuditEvent, get_emitter, reset_emitter


class _RecordingSink:
    """Test double -- captures every emitted event in memory."""

    def __init__(self) -> None:
        self.received: list[AuditEvent] = []
        self.closed = False

    def emit(self, event: AuditEvent) -> None:
        self.received.append(event)

    def close(self, drain_timeout_seconds: float = 5.0) -> None:
        self.closed = True


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[_RecordingSink]:
    """Attach a recording sink to the audit emitter for the test.

    Routes the JSONL writer to a tmp_path so the test doesn't litter
    the real ``~/.phoenix/runtime/audit/`` directory, then registers
    a recording sink alongside the default JSONL writer.

    Tears down by closing + clearing the emitter singleton, so each
    test starts fresh.
    """
    monkeypatch.setenv("PHOENIX_AUDIT_DIR", str(tmp_path / "audit"))
    reset_emitter()
    emitter = get_emitter()  # lazily constructs with default JSONL sink
    sink = _RecordingSink()
    emitter.add_sink(sink)
    yield sink
    reset_emitter()


def _reset_safety_global_state() -> None:
    """Mirror tests/unit/test_identity_safety.py::_reset_global_state."""
    from phoenix.safety.kill_switch import get_store as get_ks_store
    from phoenix.safety.rate_limiter import get_limiter

    get_limiter().reset_all()
    get_ks_store().release()


# ---------------------------------------------------------------------------
# Safety gate audit emits


class TestSafetyGateAudit:
    def test_allowed_decision_emits_safety_gate_allowed(self, recorder: _RecordingSink) -> None:
        from phoenix.identity.bootstrap import mint_bootstrap_actor
        from phoenix.safety.gate import verify_request

        _reset_safety_global_state()
        adam = mint_bootstrap_actor()
        decision = verify_request(
            adam,
            action_key="tasks_submit",
            requires_capability="can_submit_tasks",
            request_id="req_safetytest_1",
        )
        assert decision.cost_charged > 0

        gate_events = [e for e in recorder.received if e.event_type.startswith("safety.gate.")]
        assert len(gate_events) == 1
        event = gate_events[0]
        assert event.event_type == "safety.gate.allowed"
        assert event.layer == "safety_gate"
        assert event.actor_id == "adam"
        assert event.request_id == "req_safetytest_1"
        assert event.parameters["action_key"] == "tasks_submit"
        assert event.parameters["requires_capability"] == "can_submit_tasks"
        assert event.parameters["cost_deducted"] > 0

    def test_permission_denied_emits_safety_gate_denied(self, recorder: _RecordingSink) -> None:
        from phoenix.identity.bootstrap import mint_bootstrap_actor
        from phoenix.safety.errors import PermissionDenied
        from phoenix.safety.gate import verify_request

        _reset_safety_global_state()
        # 'alice' has no capabilities by default (see ``_default_permissions_for``).
        alice = mint_bootstrap_actor("alice")
        with pytest.raises(PermissionDenied):
            verify_request(
                alice,
                action_key="adapters_post",
                requires_capability="can_load_adapter",
                request_id="req_perm_denied",
            )
        gate_events = [e for e in recorder.received if e.event_type.startswith("safety.gate.")]
        assert len(gate_events) == 1
        event = gate_events[0]
        assert event.event_type == "safety.gate.denied.permission"
        assert event.actor_id == "alice"
        assert event.request_id == "req_perm_denied"
        assert "error_detail" in event.parameters

    def test_bad_actor_name_emits_safety_gate_denied_auth(self, recorder: _RecordingSink) -> None:
        from phoenix.safety.errors import AuthError
        from phoenix.safety.gate import verify_request

        _reset_safety_global_state()

        class _FakeActor:
            name = "ADAM"  # uppercase rejected per Section 7.3
            identity_fingerprint = "abc"

        with pytest.raises(AuthError):
            verify_request(
                _FakeActor(),  # type: ignore[arg-type]
                action_key="tasks_submit",
                request_id="req_bad_name",
            )
        gate_events = [e for e in recorder.received if e.event_type.startswith("safety.gate.")]
        assert len(gate_events) == 1
        # Stage 1/2 raised AuthError -- before permissions/rate limit.
        assert gate_events[0].event_type == "safety.gate.denied.auth"
        # Bad-shape actor name surfaces as actor_id="ADAM" (best-effort).
        assert gate_events[0].actor_id == "ADAM"

    def test_kill_switch_emits_safety_gate_denied(
        self,
        recorder: _RecordingSink,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from phoenix.identity.bootstrap import mint_bootstrap_actor
        from phoenix.safety import kill_switch as ks_module
        from phoenix.safety.gate import verify_request
        from phoenix.safety.kill_switch import KillSwitchEngaged, KillSwitchStore

        _reset_safety_global_state()
        store = KillSwitchStore(path=tmp_path / "ks.json")
        store.engage(by="test", reason="audit-wiring kill-switch test")
        monkeypatch.setattr(ks_module, "_STORE", store)
        try:
            adam = mint_bootstrap_actor()
            with pytest.raises(KillSwitchEngaged):
                verify_request(
                    adam,
                    action_key="tasks_submit",
                    requires_capability="can_submit_tasks",
                    request_id="req_killswitch",
                )
            gate_events = [e for e in recorder.received if e.event_type.startswith("safety.gate.")]
            assert len(gate_events) == 1
            assert gate_events[0].event_type == "safety.gate.denied.kill_switch"
            assert gate_events[0].request_id == "req_killswitch"
        finally:
            store.release()


# ---------------------------------------------------------------------------
# Verification gate audit emits (end-to-end via /v1/tasks)


class TestVerificationGateAudit:
    def test_solve_emits_verification_started_and_completed(self, recorder: _RecordingSink) -> None:
        from fastapi.testclient import TestClient

        from phoenix.api.routes import app

        _reset_safety_global_state()
        client = TestClient(app)
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
        response = client.post("/v1/tasks", json=body)
        assert response.status_code == 200, response.text
        task_id = response.json()["task_id"]

        ver_events = [e for e in recorder.received if e.event_type.startswith("verification.gate.")]
        # Lifecycle: at minimum started + completed. Solver+control
        # complete + orchestrate_progress also fire on this path.
        event_types = [e.event_type for e in ver_events]
        assert "verification.gate.started" in event_types
        assert "verification.gate.completed" in event_types
        # All verification-gate events from this request share the same
        # request_id as the HTTP request.
        request_ids = {e.request_id for e in ver_events}
        assert request_ids == {task_id}
        # actor_id is the bootstrap actor.
        assert all(e.actor_id == "adam" for e in ver_events)


# ---------------------------------------------------------------------------
# HTTP middleware emits + request_id propagation


class TestHTTPMiddlewareAudit:
    def test_health_request_emits_start_and_complete(self, recorder: _RecordingSink) -> None:
        from fastapi.testclient import TestClient

        from phoenix.api.routes import app

        client = TestClient(app)
        response = client.get("/v1/health")
        assert response.status_code == 200

        api_events = [e for e in recorder.received if e.event_type.startswith("api.request.")]
        assert any(e.event_type == "api.request.start" for e in api_events)
        complete = [e for e in api_events if e.event_type == "api.request.complete"]
        assert len(complete) == 1
        assert complete[0].parameters["method"] == "GET"
        assert complete[0].parameters["path"] == "/v1/health"
        assert complete[0].parameters["status_code"] == 200
        # The middleware mints req_<uuid> and stamps it on every event.
        assert complete[0].request_id is not None
        assert complete[0].request_id.startswith("req_")

    def test_request_id_propagates_to_safety_gate(self, recorder: _RecordingSink) -> None:
        """The middleware's request_id should appear on the safety-gate
        audit event for the same request -- this is the single thread
        admins use to correlate API + safety + verification + ledger."""
        from fastapi.testclient import TestClient

        from phoenix.api.routes import app

        _reset_safety_global_state()
        client = TestClient(app)
        response = client.post("/v1/identity/ws-token")
        assert response.status_code == 200

        api_complete = [e for e in recorder.received if e.event_type == "api.request.complete"]
        safety_events = [e for e in recorder.received if e.event_type.startswith("safety.gate.")]
        assert len(api_complete) == 1
        assert len(safety_events) >= 1
        # The api.request.complete and the safety.gate.allowed for the
        # same request share request_id.
        api_request_id = api_complete[0].request_id
        assert any(e.request_id == api_request_id for e in safety_events)


# ---------------------------------------------------------------------------
# WebSocket audit emits


class TestWebSocketAudit:
    def test_ws_rejects_missing_token_emits_connect_rejected(
        self, recorder: _RecordingSink
    ) -> None:
        from fastapi.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect

        from phoenix.api.routes import app

        client = TestClient(app)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/v1/ws/tasks/req_test/stream"):
                pass

        ws_events = [e for e in recorder.received if e.event_type.startswith("api.ws.")]
        assert len(ws_events) == 1
        event = ws_events[0]
        assert event.event_type == "api.ws.connect_rejected"
        assert event.parameters["reason"] == "missing_token"
        assert event.parameters["task_id"] == "req_test"

    def test_ws_rejects_bad_token_emits_connect_rejected(self, recorder: _RecordingSink) -> None:
        from fastapi.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect

        from phoenix.api.routes import app

        client = TestClient(app)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/v1/ws/tasks/req_test/stream?token=not-a-real-token"):
                pass

        ws_events = [e for e in recorder.received if e.event_type.startswith("api.ws.")]
        assert len(ws_events) == 1
        assert ws_events[0].event_type == "api.ws.connect_rejected"
        assert ws_events[0].parameters["reason"] == "invalid_token"
        assert "error_detail" in ws_events[0].parameters

    def test_ws_accepts_token_and_emits_connect_accepted_and_closed(
        self, recorder: _RecordingSink
    ) -> None:
        from fastapi.testclient import TestClient

        from phoenix.api.event_broker import get_broker
        from phoenix.api.routes import app

        _reset_safety_global_state()
        client = TestClient(app)
        broker = get_broker()
        broker.clear_all()
        task_id = "req_audit_ws_smoke"
        # Pre-populate so the stream sees a task.complete and closes
        # cleanly via the normal path.
        broker.emit(task_id, "task.started", {"initial_rung": "R3_TWO_AXES"})
        broker.emit(task_id, "task.complete", {"value": 1.0, "error_bar": 1e-22})

        token = client.post("/v1/identity/ws-token").json()["token"]
        with client.websocket_connect(f"/v1/ws/tasks/{task_id}/stream?token={token}") as ws:
            received: list[dict] = []
            while True:
                try:
                    ev = ws.receive_json()
                    received.append(ev)
                    if ev["type"] in ("task.complete", "task.failed"):
                        break
                except Exception:
                    break
        assert len(received) == 2

        ws_events = [e for e in recorder.received if e.event_type.startswith("api.ws.")]
        types = [e.event_type for e in ws_events]
        assert "api.ws.connect_accepted" in types
        assert "api.ws.closed" in types
        accepted = next(e for e in ws_events if e.event_type == "api.ws.connect_accepted")
        closed = next(e for e in ws_events if e.event_type == "api.ws.closed")
        assert accepted.actor_id == "adam"
        assert accepted.parameters["task_id"] == task_id
        # The closed event shares the request_id of the connect_accepted
        # event (both minted at handler entry).
        assert accepted.request_id == closed.request_id
        assert closed.parameters["task_id"] == task_id
        assert closed.parameters["events_streamed"] == 2
        broker.clear_all()


# ---------------------------------------------------------------------------
# Cross-cutting -- single request, full chain


def test_full_request_chain_shares_request_id(recorder: _RecordingSink) -> None:
    """A single POST /v1/tasks emits api.request.start, safety.gate.allowed,
    verification.gate.started, verification.gate.completed, and
    api.request.complete -- and all five carry the same request_id."""
    from fastapi.testclient import TestClient

    from phoenix.api.routes import app

    _reset_safety_global_state()
    client = TestClient(app)
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
    response = client.post("/v1/tasks", json=body)
    assert response.status_code == 200, response.text
    task_id = response.json()["task_id"]

    # All events with this request_id should be there.
    matching = [e for e in recorder.received if e.request_id == task_id]
    types_seen = {e.event_type for e in matching}
    assert "api.request.start" in types_seen
    assert "api.request.complete" in types_seen
    assert "safety.gate.allowed" in types_seen
    assert "verification.gate.started" in types_seen
    assert "verification.gate.completed" in types_seen


# ---------------------------------------------------------------------------
# AuditEmitter contract smoke


def test_audit_emitter_used_is_the_singleton(recorder: _RecordingSink) -> None:
    """The recorder fixture attaches to ``get_emitter()`` -- code paths
    that call ``get_emitter()`` independently must hit the same
    instance, otherwise the recorder would miss their events."""
    em = get_emitter()
    assert isinstance(em, AuditEmitter)
    # Issue a synthetic emit; the recorder must see it.
    import time as _time

    em.emit(
        AuditEvent(
            timestamp_unix=_time.time(),
            actor_id="t",
            layer="t",
            event_type="t.singleton.smoke",
            parameters={},
            result_hash="",
        )
    )
    assert any(e.event_type == "t.singleton.smoke" for e in recorder.received)
