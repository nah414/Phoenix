"""Phase 8 Step 9 -- admin adapter force-revalidate (501 stub) tests.

Per locked OPEN-6 (2026-05-11): Phase 8 registers the endpoint as a
501 stub. The real LoRA-adapter-sandbox handler lands in Phase 9.
This test file verifies:

- The endpoint IS registered in OpenAPI with the ``Admin`` tag.
- An admin caller gets HTTP 501 with a clear "Phase 9" message.
- Non-admin callers get HTTP 403 BEFORE the 501 (auth precedes the
  not-implemented response).
- The audit event ``admin.adapters.force_revalidate.not_implemented``
  fires on each call attempt so the audit log records the attempt.
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
    ks_module._STORE = None
    get_limiter().reset_all()
    try:
        yield runtime
    finally:
        reset_emitter()
        reset_ledger()
        reset_state_backend()
        ks_module._STORE = None
        get_limiter().reset_all()


def _alice_header() -> str:
    from phoenix.identity.bootstrap import mint_bootstrap_actor

    alice = mint_bootstrap_actor("alice")
    payload = alice.to_payload()
    return "Phoenix-Actor " + base64.b64encode(json.dumps(payload).encode()).decode("ascii")


class TestAdapterForceRevalidate:
    def test_admin_caller_gets_501(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post("/v1/admin/adapters/my-lora-v1/force-revalidate")
        assert resp.status_code == 501
        detail = resp.json()["detail"]
        assert "Phase 9" in detail
        assert "my-lora-v1" in detail

    def test_non_admin_caller_gets_403_not_501(self, isolated_runtime: Path) -> None:
        """Auth chain runs BEFORE the 501 stub: non-admin should be
        rejected with 403, not see the not-implemented response.
        Without this invariant a malicious actor would discover the
        endpoint exists and exploit the future Phase 9 handler."""
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/adapters/my-lora-v1/force-revalidate",
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403

    def test_endpoint_registered_with_admin_tag(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            schema = client.get("/v1/openapi.json").json()
        path = "/v1/admin/adapters/{adapter_id}/force-revalidate"
        assert path in schema["paths"]
        assert "Admin" in schema["paths"][path]["post"]["tags"]

    def test_round_trip_history_not_registered_in_phase_8(self, isolated_runtime: Path) -> None:
        """Per locked OPEN-6: round-trip-history is DEFERRED ENTIRELY
        to Phase 9; force-revalidate is the only adapter endpoint that
        Phase 8 advertises."""
        with TestClient(app) as client:
            schema = client.get("/v1/openapi.json").json()
        # No round-trip-history path exists.
        round_trip_paths = [p for p in schema["paths"] if "round-trip-history" in p]
        assert round_trip_paths == []

    def test_not_implemented_audit_event_emitted(self, isolated_runtime: Path) -> None:
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
                client.post("/v1/admin/adapters/test-lora/force-revalidate")
            stub_events = [
                e
                for e in sink.events
                if e.event_type == "admin.adapters.force_revalidate.not_implemented"
            ]
            assert len(stub_events) == 1
            assert stub_events[0].parameters["adapter_id"] == "test-lora"
            assert stub_events[0].parameters["phase"] == "phase_8_stub"
        finally:
            reset_emitter()
