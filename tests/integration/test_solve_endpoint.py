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


def test_solve_endpoint_qho_ground_state() -> None:
    """A QHO solve over HTTP returns a sane ground-state energy."""
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

    assert payload["status"] == "completed_solver_only"
    assert payload["phase"] == "phase_2_solver_only"
    assert payload["task_id"].startswith("req_")
    assert "reproducibility_asterisk" in payload

    cand = payload["candidate_answer"]
    expected_e0 = HBAR * 1e15 * 0.5
    assert (
        abs(cand["value"] - expected_e0) / expected_e0 < 0.02
    ), f"QHO ground state: expected {expected_e0:.4e}, got {cand['value']:.4e}"
    assert cand["error_bar_solver"] > 0
    assert cand["error_bar_solver"] < 1e-3  # within max_error_bar in absolute J
    assert cand["sigma_solver"] == cand["error_bar_solver"]  # Phase 2 placeholder

    bundle = cand["solver_kpi_bundle"]
    assert bundle["n_grid_low"] == 200
    assert bundle["n_grid_high"] == 400
    assert bundle["rung_depth"] == "R2_CROSS_PRECISION"
    assert "/" in cand["solver_id"]  # "<regime>/<solver_class>"

    prov = payload["provenance"]
    assert prov["phase"] == "phase_2_solver_only"
    assert prov["request_id"] == payload["task_id"]
    assert prov["n_grid_low"] == 200
    assert prov["n_grid_high"] == 400


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
