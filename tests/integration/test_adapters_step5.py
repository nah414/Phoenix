"""Phase 9 Step 5 -- POST /v1/identity/enroll integration tests.

Verifies the admin-only actor enrollment endpoint per architecture
v1 Section 5.2 + 7.3:

- Admin (bootstrap "adam") enrolls "bob" with explicit permissions;
  the response carries the assigned permissions + the ledger entry
  hash; a follow-up :func:`PermissionsRegistry.get` returns the
  new permissions.
- Re-enrolling the same actor_name overwrites permissions BUT
  appends a fresh ledger entry (operator history matters).
- An :class:`EnrollmentEntry` lands in the Omega Ledger with
  ``entry_kind="enrollment"`` and the admin actor as ``actor_id``.
- Non-admin "alice" gets 403 even though the safety gate would
  otherwise pass (alice's defaults include
  ``can_submit_tasks=True``).
- Invalid actor names (uppercase, special characters,
  oversize) return 400.
- Invalid rate_limit_tier returns 400.
- Empty permissions payload uses :class:`ActorPermissions` defaults.

This Step 5 test file is named ``test_adapters_step5`` only because
that's the Phase 9 step naming pattern -- the file is the
single canonical integration suite for the enroll endpoint.
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
    # Pin the permissions JSON to the tmp dir so we don't trample
    # ~/.phoenix/runtime when tests run on a developer machine.
    monkeypatch.setenv("PHOENIX_KEYSTORE_DIR", str(runtime / "keystore"))
    (runtime / "keystore").mkdir(parents=True, exist_ok=True)

    from phoenix.audit import reset_emitter
    from phoenix.ledger import reset_ledger
    from phoenix.safety import kill_switch as ks_module
    from phoenix.safety import permissions as permissions_module
    from phoenix.safety.rate_limiter import get_limiter
    from phoenix.state import reset_state_backend

    reset_emitter()
    reset_ledger()
    reset_state_backend()
    ks_module._STORE = None
    get_limiter().reset_all()
    # Reset the permissions registry singleton so the tmp keystore takes effect.
    permissions_module._REGISTRY = None
    try:
        yield runtime
    finally:
        reset_emitter()
        reset_ledger()
        reset_state_backend()
        ks_module._STORE = None
        get_limiter().reset_all()
        permissions_module._REGISTRY = None


def _alice_header() -> str:
    from phoenix.identity.bootstrap import mint_bootstrap_actor

    alice = mint_bootstrap_actor("alice")
    payload = alice.to_payload()
    return "Phoenix-Actor " + base64.b64encode(json.dumps(payload).encode()).decode("ascii")


# ---------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------


class TestEnrollHappyPath:
    def test_admin_enrolls_actor_with_explicit_permissions(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/identity/enroll",
                json={
                    "actor_name": "bob",
                    "permissions": {
                        "can_submit_tasks": True,
                        "can_load_adapter": True,
                        "rate_limit_tier": "elevated",
                    },
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["enrolled_actor_name"] == "bob"
        assert body["enrolled_by"] == "adam"
        # The full ActorPermissions payload comes back so the caller
        # can confirm what landed.
        assert body["permissions"]["can_submit_tasks"] is True
        assert body["permissions"]["can_load_adapter"] is True
        assert body["permissions"]["rate_limit_tier"] == "elevated"
        # Defaults applied where the payload was silent
        assert body["permissions"]["is_admin"] is False
        # Ledger hash is non-empty
        assert isinstance(body["ledger_entry_hash"], str)
        assert len(body["ledger_entry_hash"]) > 0

    def test_enroll_persists_in_permissions_registry(self, isolated_runtime: Path) -> None:
        from phoenix.safety.permissions import get_registry

        with TestClient(app) as client:
            client.post(
                "/v1/identity/enroll",
                json={
                    "actor_name": "bob",
                    "permissions": {"can_load_adapter": True},
                },
            )
        perms = get_registry().get("bob")
        assert perms.can_load_adapter is True
        assert perms.can_submit_tasks is True  # default

    def test_enroll_with_empty_permissions_uses_defaults(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/identity/enroll",
                json={"actor_name": "charlie", "permissions": {}},
            )
        assert resp.status_code == 200
        body = resp.json()
        # ActorPermissions defaults: submit/replay True; everything
        # else False; default rate tier.
        assert body["permissions"]["can_submit_tasks"] is True
        assert body["permissions"]["can_replay_tasks"] is True
        assert body["permissions"]["can_load_adapter"] is False
        assert body["permissions"]["is_admin"] is False
        assert body["permissions"]["rate_limit_tier"] == "default"

    def test_enroll_missing_permissions_field_works(self, isolated_runtime: Path) -> None:
        """``permissions`` defaults to {} when omitted from the body."""
        with TestClient(app) as client:
            resp = client.post(
                "/v1/identity/enroll",
                json={"actor_name": "diana"},
            )
        assert resp.status_code == 200
        assert resp.json()["permissions"]["rate_limit_tier"] == "default"


# ---------------------------------------------------------------------
# Idempotency + ledger history
# ---------------------------------------------------------------------


class TestEnrollIdempotency:
    def test_re_enrolling_overwrites_permissions(self, isolated_runtime: Path) -> None:
        from phoenix.safety.permissions import get_registry

        with TestClient(app) as client:
            client.post(
                "/v1/identity/enroll",
                json={
                    "actor_name": "bob",
                    "permissions": {"can_load_adapter": False},
                },
            )
            client.post(
                "/v1/identity/enroll",
                json={
                    "actor_name": "bob",
                    "permissions": {"can_load_adapter": True},
                },
            )
        perms = get_registry().get("bob")
        assert perms.can_load_adapter is True

    def test_re_enrolling_appends_second_ledger_entry(self, isolated_runtime: Path) -> None:
        """Operator history matters: two enrollments -> two ledger entries."""
        from phoenix.state import get_state_backend

        with TestClient(app) as client:
            client.post(
                "/v1/identity/enroll",
                json={
                    "actor_name": "bob",
                    "permissions": {"can_load_adapter": False},
                },
            )
            client.post(
                "/v1/identity/enroll",
                json={
                    "actor_name": "bob",
                    "permissions": {"can_load_adapter": True},
                },
            )
        rows = get_state_backend().list_ledger_entries(since_unix=0.0, limit=10_000)
        enrollment_rows = [r for r in rows if r["entry_kind"] == "enrollment"]
        assert len(enrollment_rows) == 2
        # Both name "bob" as the enrolled actor
        for row in enrollment_rows:
            payload = json.loads(row["payload_json"])
            assert payload["enrolled_actor_name"] == "bob"
            assert payload["enrolled_by"] == "adam"


# ---------------------------------------------------------------------
# Ledger entry shape
# ---------------------------------------------------------------------


def test_enrollment_entry_in_ledger(isolated_runtime: Path) -> None:
    """The enrollment lands as an EnrollmentEntry payload on the chain."""
    from phoenix.state import get_state_backend

    with TestClient(app) as client:
        resp = client.post(
            "/v1/identity/enroll",
            json={
                "actor_name": "bob",
                "permissions": {
                    "can_submit_tasks": True,
                    "can_load_adapter": True,
                    "is_admin": False,
                    "rate_limit_tier": "elevated",
                },
            },
        )
    ledger_hash = resp.json()["ledger_entry_hash"]

    rows = get_state_backend().list_ledger_entries(since_unix=0.0, limit=10_000)
    enrollment_rows = [r for r in rows if r["entry_kind"] == "enrollment"]
    assert len(enrollment_rows) == 1
    row = enrollment_rows[0]
    assert row["entry_hash"] == ledger_hash
    assert row["actor_id"] == "adam"
    payload = json.loads(row["payload_json"])
    assert payload["enrolled_actor_name"] == "bob"
    assert payload["enrolled_by"] == "adam"
    assert payload["permissions"]["can_load_adapter"] is True
    assert payload["permissions"]["rate_limit_tier"] == "elevated"


# ---------------------------------------------------------------------
# Permission gating
# ---------------------------------------------------------------------


class TestEnrollPermissions:
    def test_non_admin_actor_gets_403(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/identity/enroll",
                json={"actor_name": "bob", "permissions": {}},
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------


class TestEnrollValidation:
    @pytest.mark.parametrize(
        "bad_name",
        [
            "UPPERCASE",
            "with space",
            "special!char",
            "x" * 65,
        ],
    )
    def test_invalid_actor_name_returns_400(self, isolated_runtime: Path, bad_name: str) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/identity/enroll",
                json={"actor_name": bad_name, "permissions": {}},
            )
        # Either 400 (regex check) or 422 (pydantic length check) is acceptable;
        # both signal "bad request" to the caller without recording an enrollment.
        assert resp.status_code in (400, 422)

    def test_invalid_rate_limit_tier_returns_400(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/identity/enroll",
                json={
                    "actor_name": "bob",
                    "permissions": {"rate_limit_tier": "nonexistent"},
                },
            )
        assert resp.status_code == 400
        assert "rate_limit_tier" in resp.json()["detail"]

    def test_missing_actor_name_returns_422(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/identity/enroll",
                json={"permissions": {}},
            )
        # Pydantic validation -- 422 before the handler runs
        assert resp.status_code == 422


# ---------------------------------------------------------------------
# Audit emit
# ---------------------------------------------------------------------


def test_enroll_emits_audit_event(isolated_runtime: Path) -> None:
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
            client.post(
                "/v1/identity/enroll",
                json={"actor_name": "bob", "permissions": {}},
            )
        success_events = [e for e in sink.events if e.event_type == "api.identity.enroll.success"]
        assert len(success_events) == 1
        assert success_events[0].parameters["enrolled_actor_name"] == "bob"
        assert success_events[0].parameters["enrolled_by"] == "adam"
    finally:
        reset_emitter()


# ---------------------------------------------------------------------
# OpenAPI shape
# ---------------------------------------------------------------------


def test_openapi_advertises_enroll(isolated_runtime: Path) -> None:
    with TestClient(app) as client:
        schema = client.get("/v1/openapi.json").json()
    assert "/v1/identity/enroll" in schema["paths"]
    assert "post" in schema["paths"]["/v1/identity/enroll"]
