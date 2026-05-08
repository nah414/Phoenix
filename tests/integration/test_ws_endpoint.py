"""Phase 6a Step 8 -- WebSocket endpoint integration tests.

Exercises POST /v1/identity/ws-token and the
/v1/ws/tasks/{task_id}/stream WebSocket via FastAPI's TestClient.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import phoenix  # noqa: F401  -- triggers sys.path injection for vendored modules
from phoenix.api.routes import app


def test_ws_token_mints_token() -> None:
    """POST /v1/identity/ws-token returns a token and metadata."""
    client = TestClient(app)
    response = client.post("/v1/identity/ws-token")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "token" in payload
    assert len(payload["token"]) > 20
    assert payload["actor"] == "adam"  # bootstrap actor default
    assert payload["expires_in_seconds"] == 60
    assert payload["single_use"] is True


def test_ws_token_endpoint_subject_to_safety_gate() -> None:
    """The endpoint runs through verify_request -- bootstrap actor passes."""
    client = TestClient(app)
    # Two consecutive mints should both succeed (cost 1 each; tier admin
    # for bootstrap adam is unlimited).
    for _ in range(3):
        response = client.post("/v1/identity/ws-token")
        assert response.status_code == 200


def test_ws_endpoint_rejects_missing_token() -> None:
    """WS connect without ?token=... closes with policy-violation code 1008."""
    from starlette.websockets import WebSocketDisconnect

    client = TestClient(app)
    try:
        with client.websocket_connect("/v1/ws/tasks/req_test/stream"):
            pass
    except WebSocketDisconnect as exc:
        assert exc.code == 1008, f"expected close code 1008, got {exc.code}"
        return
    raise AssertionError("Expected WebSocketDisconnect with code 1008")


def test_ws_endpoint_rejects_bad_token() -> None:
    """WS connect with invalid token closes with code 1008."""
    from starlette.websockets import WebSocketDisconnect

    client = TestClient(app)
    try:
        with client.websocket_connect("/v1/ws/tasks/req_test/stream?token=not-a-real-token"):
            pass
    except WebSocketDisconnect as exc:
        assert exc.code == 1008
        return
    raise AssertionError("Expected WebSocketDisconnect with code 1008")


def test_ws_endpoint_streams_buffered_events() -> None:
    """Pre-populate the broker; WS client sees emitted events."""
    from phoenix.api.event_broker import get_broker

    client = TestClient(app)
    broker = get_broker()
    broker.clear_all()
    task_id = "req_ws_smoke"
    # Emit a complete sequence so the WS handler closes cleanly via
    # the task.complete branch.
    broker.emit(task_id, "task.started", {"initial_rung": "R3_TWO_AXES"})
    broker.emit(task_id, "task.solver.complete", {"error_bar_solver": 1.2e-22})
    broker.emit(task_id, "task.complete", {"value": 1.0, "error_bar": 1.2e-22})

    # Mint a token.
    token_response = client.post("/v1/identity/ws-token")
    token = token_response.json()["token"]

    # Connect + collect events until the server closes.
    received: list[dict] = []
    with client.websocket_connect(f"/v1/ws/tasks/{task_id}/stream?token={token}") as ws:
        while True:
            try:
                ev = ws.receive_json()
                received.append(ev)
                if ev["type"] in ("task.complete", "task.failed"):
                    break
            except Exception:
                break
    assert len(received) == 3
    assert received[0]["type"] == "task.started"
    assert received[1]["type"] == "task.solver.complete"
    assert received[2]["type"] == "task.complete"
    broker.clear_all()
