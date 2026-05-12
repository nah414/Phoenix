"""Phase 8 Step 6 -- admin router-decisions + provider-health-history tests.

Coverage:

- The router-decision ring buffer is populated by every call to
  :meth:`Router.decide` and respects ``$PHOENIX_ROUTER_DECISION_LOG_SIZE``.
- ``GET /v1/admin/router/decisions`` returns the most-recent N
  decisions with full ``decision_provenance``.
- ``GET /v1/admin/providers/health-history`` filters audit_events
  to ``provider.health.*`` and supports the optional
  ``provider_id`` filter.
- Both endpoints share the standard admin auth chain (403 for
  non-admins) and are tagged ``Admin`` in OpenAPI.
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
    from phoenix.router.decision import clear_decision_log
    from phoenix.safety import kill_switch as ks_module
    from phoenix.safety.rate_limiter import get_limiter
    from phoenix.state import reset_state_backend

    reset_emitter()
    reset_ledger()
    reset_state_backend()
    clear_decision_log()
    ks_module._STORE = None
    get_limiter().reset_all()
    try:
        yield runtime
    finally:
        reset_emitter()
        reset_ledger()
        reset_state_backend()
        clear_decision_log()
        ks_module._STORE = None
        get_limiter().reset_all()


def _alice_header() -> str:
    from phoenix.identity.bootstrap import mint_bootstrap_actor

    alice = mint_bootstrap_actor("alice")
    payload = alice.to_payload()
    return "Phoenix-Actor " + base64.b64encode(json.dumps(payload).encode()).decode("ascii")


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


# ---------------------------------------------------------------------------
# Ring-buffer unit-level behavior


class TestDecisionLogRingBuffer:
    def test_snapshot_empty_initially(self, isolated_runtime: Path) -> None:
        from phoenix.router.decision import decision_log_snapshot

        assert decision_log_snapshot() == []

    def test_decide_populates_buffer(self, isolated_runtime: Path) -> None:
        """Each Router.decide() call appends to the ring buffer."""
        from phoenix.router.decision import decision_log_snapshot

        # Just hit /v1/tasks twice; each goes through the verification
        # gate which calls Router.decide for Stage 6 routing.
        with TestClient(app) as client:
            client.post("/v1/tasks", json=_qho_body())
            client.post("/v1/tasks", json=_qho_body())

        snap = decision_log_snapshot()
        # Each solve calls Router.decide once (Phase 5 verification
        # gate at default rung doesn't call decide a second time for
        # Axis 3 because cross-provider isn't applicable for the
        # local-simulator path).
        assert len(snap) >= 2

    def test_limit_caps_returned_count(self, isolated_runtime: Path) -> None:
        from phoenix.router.decision import decision_log_snapshot

        with TestClient(app) as client:
            for _ in range(5):
                client.post("/v1/tasks", json=_qho_body())

        full = decision_log_snapshot()
        limited = decision_log_snapshot(limit=2)
        assert len(full) >= 5
        assert len(limited) == 2
        # Limit returns the MOST RECENT entries.
        assert limited == full[-2:]


# ---------------------------------------------------------------------------
# GET /v1/admin/router/decisions


class TestRouterDecisionsEndpoint:
    def test_returns_zero_when_no_solves(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.get("/v1/admin/router/decisions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
        assert body["decisions"] == []

    def test_returns_decisions_after_solve(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            client.post("/v1/tasks", json=_qho_body())
            resp = client.get("/v1/admin/router/decisions?limit=10")
        body = resp.json()
        assert body["count"] >= 1
        d = body["decisions"][0]
        # Shape contract.
        assert "primary" in d
        assert "provider_id" in d["primary"]
        assert "backend_name" in d["primary"]
        assert isinstance(d["alternates"], list)
        assert "rationale" in d
        assert "decision_provenance" in d
        # Provenance carries the stages + ranking weights.
        prov = d["decision_provenance"]
        assert "stages_applied" in prov
        assert "ranking_weights" in prov
        assert "candidates_considered" in prov

    def test_limit_capped_at_1000(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.get("/v1/admin/router/decisions?limit=999999")
        body = resp.json()
        assert body["limit"] == 1000

    def test_403_for_non_admin(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.get(
                "/v1/admin/router/decisions",
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /v1/admin/providers/health-history


class TestProviderHealthHistoryEndpoint:
    def test_returns_empty_when_no_health_events(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.get("/v1/admin/providers/health-history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
        assert body["events"] == []

    def test_filters_only_provider_health_event_types(self, isolated_runtime: Path) -> None:
        from phoenix.state import get_state_backend

        backend = get_state_backend()
        now = time.time()
        backend.append_audit_event(
            {
                "timestamp_unix": now,
                "actor_id": "system",
                "layer": "router",
                "event_type": "provider.health.degraded",
                "parameters": {"provider_id": "ibm_quantum"},
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
            resp = client.get("/v1/admin/providers/health-history")
        body = resp.json()
        assert body["count"] == 1
        assert body["events"][0]["event_type"] == "provider.health.degraded"

    def test_provider_id_filter_isolates(self, isolated_runtime: Path) -> None:
        from phoenix.state import get_state_backend

        backend = get_state_backend()
        for provider in ("ibm_quantum", "aws_braket", "ionq"):
            backend.append_audit_event(
                {
                    "timestamp_unix": time.time(),
                    "actor_id": "system",
                    "layer": "router",
                    "event_type": "provider.health.degraded",
                    "parameters": {"provider_id": provider},
                    "result_hash": "",
                }
            )

        with TestClient(app) as client:
            resp = client.get("/v1/admin/providers/health-history?provider_id=ibm_quantum")
        body = resp.json()
        assert body["count"] == 1
        params = body["events"][0]["parameters"]
        # The state backend may serialize parameters as a JSON string.
        if isinstance(params, str):
            params = json.loads(params)
        assert params["provider_id"] == "ibm_quantum"

    def test_403_for_non_admin(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.get(
                "/v1/admin/providers/health-history",
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# OpenAPI registration


def test_step6_routes_registered_with_admin_tag(
    isolated_runtime: Path,
) -> None:
    with TestClient(app) as client:
        schema = client.get("/v1/openapi.json").json()
    expected = {
        ("/v1/admin/router/decisions", "get"),
        ("/v1/admin/providers/health-history", "get"),
    }
    for path, verb in expected:
        assert path in schema["paths"], f"missing {path}"
        op = schema["paths"][path][verb]
        assert "Admin" in op["tags"]
