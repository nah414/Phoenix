"""Phase 2 Step 6 -- POST /v1/tasks integration tests.

Exercises the front-door REST surface end-to-end via FastAPI's
:class:`TestClient`. The endpoint constructs a :class:`PhysicsTask` from
the JSON body, runs it through Trinity Core's Solver-only pipeline, and
returns the :class:`CandidateAnswer` plus the
``reproducibility_asterisk`` honesty marker.

Status-code mapping under test (architecture v1 Section 5.2):

- 200 -- successful Solver-only solve.
- 400 -- invalid ``latency_tier`` string from the caller.
- 403 -- frontier-physics regime requested without ``frontier_physics=True``.
- 501 -- ``latency_tier`` defined-but-not-routable in v1.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import phoenix  # noqa: F401  -- triggers sys.path injection for vendored modules
from phoenix.api.routes import app

HBAR = 1.054571817e-34


def test_solve_endpoint_qho_returns_full_result_envelope() -> None:
    """A QHO solve over HTTP returns the full Phase 3 Result envelope.

    Phase 2 wrapped the solver-only output in a ``candidate_answer``
    sub-block; Phase 3 promotes to top-level ``value`` / ``error_bar`` /
    ``sigma`` / ``agreement_type`` / ``kpi_bundle_orchestrate`` plus a
    flattened ``provenance`` carrying solver + control + orchestrate.
    """
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
    payload = response.json()

    assert payload["status"] == "completed"
    assert payload["phase"] == "phase_3_solver_control_orchestrate"
    assert payload["task_id"].startswith("req_")
    assert "reproducibility_asterisk" in payload

    # Result envelope: top-level value, error_bar, sigma, agreement_type.
    # Phase 3's local-simulator path returns trace expectation ~1.0.
    assert abs(payload["value"] - 1.0) < 1e-6
    # error_bar quadrature-combined from cross-precision (Phase 3 only
    # contributing axis); sigma tracks error_bar (Phase 3 placeholder).
    assert payload["error_bar"] > 0
    assert payload["sigma"] == payload["error_bar"]
    assert payload["agreement_type"] in {"hedged_consensus", "unknown"}

    # Typed KPIBundle fields.
    kpi = payload["kpi_bundle_orchestrate"]
    assert "fidelity" in kpi
    assert "latency_us" in kpi
    assert "backaction" in kpi
    assert "shots_used" in kpi
    assert "shot_budget" in kpi
    assert kpi["status"] in {"ok", "warn", "fail"}

    # Provenance carries all three sub-blocks.
    prov = payload["provenance"]
    assert prov["request_id"] == payload["task_id"]
    assert prov["cloud_shots_recorded"] is False  # local-simulator path

    assert "solver" in prov
    assert prov["solver"]["phase"] == "phase_3_solver_control_orchestrate"
    assert "/" in prov["solver"]["dispatched_solver"]
    assert prov["solver"]["n_grid_low"] == 200
    assert prov["solver"]["n_grid_high"] == 400
    assert prov["solver"]["axis_1_error_bar_contribution"] is not None

    assert "control" in prov
    assert prov["control"]["phase"] == "phase_3_solver_control_orchestrate"
    assert prov["control"]["dpd_n_blocks"] >= 1
    assert prov["control"]["axis_2_metric"] == "trace_distance"

    assert "orchestrate" in prov
    assert prov["orchestrate"]["phase"] == "phase_3_solver_control_orchestrate"
    assert prov["orchestrate"]["provider_id"] == "phoenix.local_simulator"
    assert prov["orchestrate"]["quantum_technology"] == "simulation"
    assert len(prov["orchestrate"]["bundle_hash"]) == 16
    assert prov["orchestrate"]["cloud_shots_recorded"] is False


def test_solve_endpoint_streaming_returns_501() -> None:
    """``streaming_realtime`` is defined-but-not-routable -> 501."""
    client = TestClient(app)
    body = {
        "physics_context": {
            "mass_kg": 9.1e-31,
            "length_scale_m": 4e-9,
            "metadata": {"omega": 1e15, "n_grid_points": 200},
        },
        "tolerance": {
            "max_error_bar": 1e-3,
            "reproducibility_mode": "default",
            "latency_tier": "streaming_realtime",
            "frontier_physics": False,
        },
        "metadata": {},
    }
    response = client.post("/v1/tasks", json=body)
    assert response.status_code == 501
    assert "streaming_realtime" in response.json()["detail"]


def test_solve_endpoint_perception_returns_501() -> None:
    """``perception_realtime`` is routed only by Phase 12+ -> 501."""
    client = TestClient(app)
    body = {
        "physics_context": {
            "mass_kg": 9.1e-31,
            "length_scale_m": 4e-9,
            "metadata": {"omega": 1e15, "n_grid_points": 200},
        },
        "tolerance": {
            "max_error_bar": 1e-3,
            "reproducibility_mode": "default",
            "latency_tier": "perception_realtime",
            "frontier_physics": False,
        },
        "metadata": {},
    }
    response = client.post("/v1/tasks", json=body)
    assert response.status_code == 501
    assert "perception_realtime" in response.json()["detail"]


def test_solve_endpoint_unknown_latency_tier_returns_400() -> None:
    """Unknown ``latency_tier`` string -> 400 with a helpful detail."""
    client = TestClient(app)
    body = {
        "physics_context": {
            "mass_kg": 9.1e-31,
            "length_scale_m": 4e-9,
            "metadata": {"omega": 1e15, "n_grid_points": 200},
        },
        "tolerance": {
            "max_error_bar": 1e-3,
            "reproducibility_mode": "default",
            "latency_tier": "warp_drive_realtime",
            "frontier_physics": False,
        },
        "metadata": {},
    }
    response = client.post("/v1/tasks", json=body)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "warp_drive_realtime" in detail
    # The error lists the valid values so users can self-correct.
    assert "batch_realtime" in detail


def test_solve_endpoint_frontier_physics_refused_returns_403() -> None:
    """Frontier-physics regime without opt-in -> 403."""
    client = TestClient(app)
    body = {
        "physics_context": {
            "mass_kg": 9.1093837015e-31,
            "length_scale_m": 4e-9,
            "metadata": {
                "omega": 1e15,
                "n_grid_points": 200,
                "include_gravity": True,
                "gravitational_regime": "semiclassical",
            },
        },
        "tolerance": {
            "max_error_bar": 1e-3,
            "reproducibility_mode": "default",
            "latency_tier": "batch_realtime",
            "frontier_physics": False,  # explicit refusal
        },
        "metadata": {"regime_hint": "SEMICLASSICAL_GRAVITY"},
    }
    response = client.post("/v1/tasks", json=body)
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert "SEMICLASSICAL_GRAVITY" in detail


def test_solve_endpoint_invalid_regime_hint_returns_400() -> None:
    """An unknown ``regime_hint`` -> 400 NoEligibleSolver."""
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
        "metadata": {"regime_hint": "PHLOGISTON_THEORY"},
    }
    response = client.post("/v1/tasks", json=body)
    assert response.status_code == 400
    assert "PHLOGISTON_THEORY" in response.json()["detail"]


def test_solve_endpoint_health_still_works() -> None:
    """Phase 2's additions don't break the Phase 0 health probe."""
    client = TestClient(app)
    response = client.get("/v1/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "phoenix_version" in payload
