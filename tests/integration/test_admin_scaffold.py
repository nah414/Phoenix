"""Phase 8 Step 1 -- admin scaffolding tests.

Covers the four-layer composition every admin handler uses:

1. :func:`extract_or_bootstrap` parses the Actor header.
2. :func:`verify_request` runs the 9-stage safety gate.
3. :func:`require_admin` checks the ``is_admin`` privilege class.
4. :func:`emit_admin_audit` writes a top-priority audit event.

Step 1 ships the sanity-check `/v1/admin/_ping` endpoint as the
test vehicle for this composition. Steps 2-9 will register real
handlers using the same pattern.

What this file proves:

- The admin router is mounted under ``/v1/admin/`` with the
  ``Admin`` OpenAPI tag.
- An admin actor (bootstrap adam) gets HTTP 200 from
  ``/v1/admin/_ping``.
- A non-admin actor (alice with default permissions) gets
  HTTP 403 ``AdminPrivilegeRequired``.
- Every admin call -- success or failure -- emits an audit event
  with the ``admin`` layer + the appropriate ``admin.<endpoint>.<outcome>``
  event_type.
- The audit event records the operator's actor identity + the
  request_id from the front-door HTTP middleware.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix.admin import AdminPrivilegeRequired, require_admin
from phoenix.api.routes import app
from phoenix.audit import AuditEvent, get_emitter, reset_emitter


class _RecordingSink:
    """Test double -- captures every emitted audit event."""

    def __init__(self) -> None:
        self.received: list[AuditEvent] = []
        self.closed = False

    def emit(self, event: AuditEvent) -> None:
        self.received.append(event)

    def close(self, drain_timeout_seconds: float = 5.0) -> None:
        self.closed = True


@pytest.fixture
def isolated_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[Path]:
    """Per-test fresh Phoenix runtime."""
    runtime = tmp_path / "phoenix_runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PHOENIX_SQLITE_DB_PATH", str(runtime / "state.db"))
    monkeypatch.setenv("PHOENIX_AUDIT_DIR", str(runtime / "audit"))

    from phoenix.audit import reset_emitter as reset_em
    from phoenix.ledger import reset_ledger
    from phoenix.safety.rate_limiter import get_limiter
    from phoenix.state import reset_state_backend

    reset_em()
    reset_ledger()
    reset_state_backend()
    get_limiter().reset_all()
    try:
        yield runtime
    finally:
        reset_em()
        reset_ledger()
        reset_state_backend()
        get_limiter().reset_all()


@pytest.fixture
def audit_recorder(isolated_runtime: Path) -> Iterator[_RecordingSink]:
    """Attach a recording sink to the singleton audit emitter."""
    reset_emitter()
    emitter = get_emitter()
    sink = _RecordingSink()
    emitter.add_sink(sink)
    yield sink
    reset_emitter()


# ---------------------------------------------------------------------------
# require_admin (unit-level)


class TestRequireAdmin:
    def test_admin_actor_passes(self) -> None:
        """Bootstrap adam is admin (per Phase 6a default permissions)."""
        from phoenix.identity.bootstrap import mint_bootstrap_actor

        adam = mint_bootstrap_actor("adam")
        # No exception raised.
        require_admin(adam)

    def test_non_admin_actor_raises(self) -> None:
        """Alice (default permissions) is not admin."""
        from phoenix.identity.bootstrap import mint_bootstrap_actor

        alice = mint_bootstrap_actor("alice")
        with pytest.raises(AdminPrivilegeRequired) as exc_info:
            require_admin(alice)
        assert exc_info.value.actor_name == "alice"
        assert "is_admin" in str(exc_info.value)


# ---------------------------------------------------------------------------
# /v1/admin/_ping integration


class TestAdminPingEndpoint:
    def test_admin_ping_returns_200_for_bootstrap_adam(
        self, audit_recorder: _RecordingSink
    ) -> None:
        """Bootstrap fallback actor (adam) is admin → 200."""
        with TestClient(app) as client:
            resp = client.get("/v1/admin/_ping")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert body["actor"] == "adam"

    def test_admin_ping_returns_403_for_non_admin_actor(
        self, audit_recorder: _RecordingSink
    ) -> None:
        """Alice has can_submit_tasks=True but is_admin=False → 403."""
        from phoenix.identity.bootstrap import mint_bootstrap_actor

        alice = mint_bootstrap_actor("alice")
        payload = alice.to_payload()
        header = "Phoenix-Actor " + base64.b64encode(json.dumps(payload).encode()).decode("ascii")

        with TestClient(app) as client:
            resp = client.get(
                "/v1/admin/_ping",
                headers={"Authorization": header},
            )
        assert resp.status_code == 403
        assert "is_admin" in resp.json()["detail"]

    def test_admin_ping_success_emits_audit_event(self, audit_recorder: _RecordingSink) -> None:
        """Successful admin call emits admin.ping.success event."""
        with TestClient(app) as client:
            resp = client.get("/v1/admin/_ping")
        assert resp.status_code == 200

        admin_events = [e for e in audit_recorder.received if e.layer == "admin"]
        assert any(e.event_type == "admin.ping.success" for e in admin_events)
        success = next(e for e in admin_events if e.event_type == "admin.ping.success")
        assert success.actor_id == "adam"
        assert success.request_id is not None
        assert success.request_id.startswith("req_")

    def test_admin_ping_403_emits_privilege_error_audit_event(
        self, audit_recorder: _RecordingSink
    ) -> None:
        """Permission denial emits admin.ping.error.privilege event."""
        from phoenix.identity.bootstrap import mint_bootstrap_actor

        alice = mint_bootstrap_actor("alice")
        payload = alice.to_payload()
        header = "Phoenix-Actor " + base64.b64encode(json.dumps(payload).encode()).decode("ascii")

        with TestClient(app) as client:
            resp = client.get(
                "/v1/admin/_ping",
                headers={"Authorization": header},
            )
        assert resp.status_code == 403

        admin_events = [e for e in audit_recorder.received if e.layer == "admin"]
        privilege_events = [e for e in admin_events if e.event_type == "admin.ping.error.privilege"]
        assert len(privilege_events) == 1
        assert privilege_events[0].actor_id == "alice"


# ---------------------------------------------------------------------------
# OpenAPI schema


class TestAdminOpenAPI:
    def test_admin_router_mounted_under_v1_admin(self) -> None:
        with TestClient(app) as client:
            schema = client.get("/v1/openapi.json").json()
        admin_paths = [path for path in schema["paths"] if path.startswith("/v1/admin")]
        # Step 1 only registers _ping; Steps 2-9 grow this list.
        assert "/v1/admin/_ping" in admin_paths

    def test_admin_routes_carry_admin_tag(self) -> None:
        with TestClient(app) as client:
            schema = client.get("/v1/openapi.json").json()
        ping_op = schema["paths"]["/v1/admin/_ping"]["get"]
        assert "Admin" in ping_op["tags"]
