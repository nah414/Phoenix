"""Phase 8 Step 7 -- admin provider manual-quarantine + manual-restore tests.

Coverage:

- ``POST /v1/admin/providers/{id}/manual-quarantine`` marks the
  registry entry DEGRADED and emits a ``provider.health.manual_quarantine``
  audit event.
- ``POST /v1/admin/providers/{id}/manual-restore`` flips it back to
  HEALTHY and emits ``provider.health.manual_restore``.
- Duration over the 24h cap returns HTTP 400
  ``QuarantineDurationExceeded``.
- Unknown provider_id returns HTTP 404.
- Both endpoints are admin-only (403 for non-admin).
- The two audit events land in the state_backend.audit_events table
  so the /v1/admin/providers/health-history endpoint surfaces them.
- Per locked OPEN-5: NEITHER mutation appends an Omega Ledger entry
  (provider state is operational, not audit-grade).
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
    from phoenix.router.decision import clear_decision_log
    from phoenix.safety import kill_switch as ks_module
    from phoenix.safety.rate_limiter import get_limiter
    from phoenix.state import reset_state_backend
    from phoenix.trinity import pipeline as pipe_module

    reset_emitter()
    reset_ledger()
    reset_state_backend()
    clear_decision_log()
    ks_module._STORE = None
    pipe_module._ROUTER = None
    pipe_module._GATE = None
    get_limiter().reset_all()
    try:
        yield runtime
    finally:
        reset_emitter()
        reset_ledger()
        reset_state_backend()
        clear_decision_log()
        ks_module._STORE = None
        pipe_module._ROUTER = None
        pipe_module._GATE = None
        get_limiter().reset_all()


def _alice_header() -> str:
    from phoenix.identity.bootstrap import mint_bootstrap_actor

    alice = mint_bootstrap_actor("alice")
    payload = alice.to_payload()
    return "Phoenix-Actor " + base64.b64encode(json.dumps(payload).encode()).decode("ascii")


# ---------------------------------------------------------------------------
# POST /v1/admin/providers/{id}/manual-quarantine


class TestManualQuarantine:
    def test_marks_provider_degraded(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/providers/ibm_quantum/manual-quarantine",
                json={"duration_seconds": 3600, "reason": "scheduled maintenance"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["provider_id"] == "ibm_quantum"
        assert body["health"] == "degraded"
        assert body["duration_seconds"] == 3600
        assert body["quarantined_by"] == "adam"
        assert body["reason"] == "scheduled maintenance"
        # ISO 8601 timestamp.
        assert "T" in body["degraded_until_utc"]

    def test_registry_state_actually_changes(self, isolated_runtime: Path) -> None:
        from phoenix.router.provider_registry import ProviderHealth
        from phoenix.trinity.pipeline import _get_router

        with TestClient(app) as client:
            client.post(
                "/v1/admin/providers/ibm_quantum/manual-quarantine",
                json={"duration_seconds": 60, "reason": "test"},
            )
        entry = _get_router().registry.get("ibm_quantum")
        assert entry is not None
        assert entry.health is ProviderHealth.DEGRADED

    def test_duration_over_24h_returns_400(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/providers/ibm_quantum/manual-quarantine",
                json={"duration_seconds": 86401, "reason": "way too long"},
            )
        assert resp.status_code == 400
        assert "policy cap" in resp.json()["detail"].lower() or "86400" in resp.json()["detail"]

    def test_unknown_provider_returns_404(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/providers/never_heard_of/manual-quarantine",
                json={"duration_seconds": 60, "reason": "x"},
            )
        assert resp.status_code == 404

    def test_403_for_non_admin(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/providers/ibm_quantum/manual-quarantine",
                json={"duration_seconds": 60, "reason": "x"},
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403

    def test_audit_event_surfaces_in_health_history(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            client.post(
                "/v1/admin/providers/ibm_quantum/manual-quarantine",
                json={"duration_seconds": 60, "reason": "smoke"},
            )
            resp = client.get("/v1/admin/providers/health-history?provider_id=ibm_quantum")
        body = resp.json()
        assert body["count"] >= 1
        event_types = [e["event_type"] for e in body["events"]]
        assert "provider.health.manual_quarantine" in event_types


# ---------------------------------------------------------------------------
# POST /v1/admin/providers/{id}/manual-restore


class TestManualRestore:
    def test_restores_degraded_provider(self, isolated_runtime: Path) -> None:
        from phoenix.router.provider_registry import ProviderHealth
        from phoenix.trinity.pipeline import _get_router

        with TestClient(app) as client:
            client.post(
                "/v1/admin/providers/ibm_quantum/manual-quarantine",
                json={"duration_seconds": 60, "reason": "x"},
            )
            resp = client.post(
                "/v1/admin/providers/ibm_quantum/manual-restore",
                json={"reason": "all clear"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["health"] == "healthy"
        entry = _get_router().registry.get("ibm_quantum")
        assert entry is not None
        assert entry.health is ProviderHealth.HEALTHY

    def test_unknown_provider_returns_404(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/providers/never_heard_of/manual-restore",
                json={"reason": "x"},
            )
        assert resp.status_code == 404

    def test_403_for_non_admin(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/providers/ibm_quantum/manual-restore",
                json={"reason": "x"},
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403

    def test_restore_audit_event_surfaces_in_history(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            client.post(
                "/v1/admin/providers/ibm_quantum/manual-quarantine",
                json={"duration_seconds": 60, "reason": "x"},
            )
            client.post(
                "/v1/admin/providers/ibm_quantum/manual-restore",
                json={"reason": "all clear"},
            )
            resp = client.get("/v1/admin/providers/health-history?provider_id=ibm_quantum")
        event_types = [e["event_type"] for e in resp.json()["events"]]
        assert "provider.health.manual_quarantine" in event_types
        assert "provider.health.manual_restore" in event_types


# ---------------------------------------------------------------------------
# OPEN-5 locked behavior: NO ledger entry for manual provider state


def test_quarantine_does_not_append_ledger_entry(
    isolated_runtime: Path,
) -> None:
    """Per locked OPEN-5 (2026-05-11): manual provider state is
    operational, not audit-grade. Section 8.4's "entire admin
    mutation surface" reserves ledger entries for kill switch +
    HUMAN_REVIEW override only."""
    from phoenix.state import get_state_backend

    with TestClient(app) as client:
        client.post(
            "/v1/admin/providers/ibm_quantum/manual-quarantine",
            json={"duration_seconds": 60, "reason": "x"},
        )
        client.post(
            "/v1/admin/providers/ibm_quantum/manual-restore",
            json={"reason": "y"},
        )

    rows = get_state_backend().list_ledger_entries(since_unix=0, limit=100)
    # No 'provider_state' or 'manual_*' entry_kinds; per OPEN-5 the
    # ledger is reserved for solve + override_by_operator + kill_switch
    # + enrollment.
    kinds = {r["entry_kind"] for r in rows}
    assert "manual_quarantine" not in kinds
    assert "manual_restore" not in kinds
    assert "provider_state" not in kinds


# ---------------------------------------------------------------------------
# OpenAPI registration


def test_step7_routes_registered_with_admin_tag(
    isolated_runtime: Path,
) -> None:
    with TestClient(app) as client:
        schema = client.get("/v1/openapi.json").json()
    for path in (
        "/v1/admin/providers/{provider_id}/manual-quarantine",
        "/v1/admin/providers/{provider_id}/manual-restore",
    ):
        assert path in schema["paths"]
        assert "Admin" in schema["paths"][path]["post"]["tags"]
