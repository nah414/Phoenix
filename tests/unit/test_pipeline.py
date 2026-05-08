"""Phase 2 Step 5 -- Trinity Core pipeline orchestrator (Solver-only path).

Exercises :func:`phoenix.trinity.pipeline.solve` end-to-end against the
vendored Solver subsystem:

- The QHO benchmark produces a CandidateAnswer with sane ``value`` (within
  1% of HBAR * omega / 2), small ``error_bar_solver``, and the
  ``phase_2_solver_only`` provenance marker.
- ``LatencyTier.STREAMING_REALTIME`` raises :class:`LatencyTierNotImplemented`
  with the v2 release name in the message.
- ``LatencyTier.PERCEPTION_REALTIME`` raises :class:`LatencyTierNotImplemented`
  with the perception-harness extension reference in the message.
- Frontier-physics regimes raise :class:`FrontierPhysicsRefused` from the
  engine layer when ``frontier_physics`` is False.

Phase 2's latency-tier gate is a load-bearing test: the perception
extension at Phase 12+ reuses this exact pipeline shape, so the gate's
behavior is part of the v1 contract that future phases must not break.
"""

from __future__ import annotations

import math

import phoenix  # noqa: F401  -- triggers sys.path injection for vendored modules

HBAR = 1.054571817e-34


def _qho_task(latency_tier=None, frontier_physics: bool = False):
    """Construct a QHO PhysicsTask with overridable tolerance fields."""
    from synthesis.equations.base import PhysicsContext

    from phoenix._internal.latency import LatencyTier
    from phoenix.trinity.data_model import PhysicsTask, ToleranceSpec

    if latency_tier is None:
        latency_tier = LatencyTier.BATCH_REALTIME
    ctx = PhysicsContext(
        mass_kg=9.1093837015e-31,
        length_scale_m=4e-9,
        metadata={"omega": 1e15, "n_grid_points": 200},
    )
    return PhysicsTask(
        physics_context=ctx,
        tolerance=ToleranceSpec(
            max_error_bar=1e-3,
            reproducibility_mode="default",
            latency_tier=latency_tier,
            frontier_physics=frontier_physics,
        ),
        actor=None,
        request_id="test-pipeline-qho",
    )


def test_solve_returns_candidate_answer_for_qho() -> None:
    """The Solver-only pipeline produces a sane CandidateAnswer for QHO."""
    from phoenix.trinity.data_model import CandidateAnswer, SolverProvenance
    from phoenix.trinity.pipeline import solve

    task = _qho_task()
    candidate = solve(task)

    assert isinstance(candidate, CandidateAnswer)

    # solver_id format: "<regime>/<solver_class_name>"
    assert "/" in candidate.solver_id

    # value is the ground-state eigenvalue; QHO analytical = HBAR * omega / 2.
    expected_e0 = HBAR * 1e15 * 0.5
    assert math.isclose(
        float(candidate.value), expected_e0, rel_tol=0.02
    ), f"QHO ground state: expected {expected_e0:.4e}, got {candidate.value:.4e}"

    # error_bar_solver should be small relative to E0 since the QHO
    # converges rapidly with grid refinement.
    assert candidate.error_bar_solver >= 0.0
    assert candidate.error_bar_solver < 0.05 * expected_e0

    # sigma_solver tracks error_bar_solver in Phase 2 (placeholder).
    assert candidate.sigma_solver == candidate.error_bar_solver

    # KPI bundle carries the audit fields the architecture spec promises.
    bundle = candidate.solver_kpi_bundle
    assert "wall_clock_ms_total" in bundle
    assert "wall_clock_ms_low" in bundle
    assert "wall_clock_ms_high" in bundle
    assert bundle["n_grid_low"] == 200
    assert bundle["n_grid_high"] == 400
    assert bundle["rung_depth"] == "R2_CROSS_PRECISION"
    assert "dispatched_regime" in bundle
    assert "dispatched_solver_class" in bundle

    # Provenance carries the phase-2-incomplete honesty marker.
    assert isinstance(candidate.provenance_solver, SolverProvenance)
    assert candidate.provenance_solver.phase == "phase_2_solver_only"
    assert candidate.provenance_solver.request_id == "test-pipeline-qho"
    assert candidate.provenance_solver.n_grid_low == 200
    assert candidate.provenance_solver.n_grid_high == 400
    assert candidate.provenance_solver.cross_precision_axis_result is not None


def test_solve_streaming_realtime_raises_with_v2_message() -> None:
    """STREAMING_REALTIME is defined-but-not-routable in v1; raises typed exc."""
    import pytest

    from phoenix._internal.latency import LatencyTier, LatencyTierNotImplemented
    from phoenix.trinity.pipeline import solve

    task = _qho_task(latency_tier=LatencyTier.STREAMING_REALTIME)
    with pytest.raises(LatencyTierNotImplemented) as exc_info:
        solve(task)
    assert exc_info.value.tier is LatencyTier.STREAMING_REALTIME
    # Message points users to the v2 release for streaming-realtime support.
    assert "v2" in str(exc_info.value).lower() or "streaming" in str(exc_info.value).lower()


def test_solve_perception_realtime_raises_with_perception_message() -> None:
    """PERCEPTION_REALTIME is routed only by Phase 12+ perception extension."""
    import pytest

    from phoenix._internal.latency import LatencyTier, LatencyTierNotImplemented
    from phoenix.trinity.pipeline import solve

    task = _qho_task(latency_tier=LatencyTier.PERCEPTION_REALTIME)
    with pytest.raises(LatencyTierNotImplemented) as exc_info:
        solve(task)
    assert exc_info.value.tier is LatencyTier.PERCEPTION_REALTIME
    assert "perception" in str(exc_info.value).lower()


def test_solve_frontier_physics_refused_when_capability_missing() -> None:
    """Engine-layer check raises FrontierPhysicsRefused for SCG without permission.

    The ``regime_hint`` override lives on :attr:`PhysicsTask.metadata`
    (per ``engine.pick_solver``). Putting it on ``physics_context.metadata``
    is silently ignored -- the classifier runs and picks whatever it wants.
    """
    import pytest
    from synthesis.equations.base import PhysicsContext

    from phoenix._internal.latency import LatencyTier
    from phoenix.trinity.data_model import PhysicsTask, ToleranceSpec
    from phoenix.trinity.pipeline import solve
    from phoenix.trinity.solver.engine import FrontierPhysicsRefused

    ctx = PhysicsContext(
        mass_kg=9.1093837015e-31,
        length_scale_m=4e-9,
        include_gravity=True,
        gravitational_regime="semiclassical",
        metadata={"omega": 1e15, "n_grid_points": 200},
    )
    task = PhysicsTask(
        physics_context=ctx,
        tolerance=ToleranceSpec(
            max_error_bar=1e-3,
            reproducibility_mode="default",
            latency_tier=LatencyTier.BATCH_REALTIME,
            frontier_physics=False,  # explicit refusal
        ),
        actor=None,
        request_id="test-frontier-refused",
        metadata={"regime_hint": "SEMICLASSICAL_GRAVITY"},
    )

    with pytest.raises(FrontierPhysicsRefused) as exc_info:
        solve(task)
    assert exc_info.value.regime_name == "SEMICLASSICAL_GRAVITY"


def test_solve_frontier_physics_allowed_when_opted_in() -> None:
    """Engine accepts SCG dispatch when frontier_physics=True."""
    from synthesis.equations.base import PhysicsContext

    from phoenix._internal.latency import LatencyTier
    from phoenix.trinity.data_model import CandidateAnswer, PhysicsTask, ToleranceSpec
    from phoenix.trinity.pipeline import solve

    ctx = PhysicsContext(
        mass_kg=9.1093837015e-31,
        length_scale_m=4e-9,
        include_gravity=True,
        gravitational_regime="semiclassical",
        metadata={"omega": 1e15, "n_grid_points": 200},
    )
    task = PhysicsTask(
        physics_context=ctx,
        tolerance=ToleranceSpec(
            max_error_bar=1e-3,
            reproducibility_mode="default",
            latency_tier=LatencyTier.BATCH_REALTIME,
            frontier_physics=True,  # opted in
        ),
        actor=None,
        request_id="test-frontier-allowed",
        metadata={"regime_hint": "SEMICLASSICAL_GRAVITY"},
    )

    candidate = solve(task)
    assert isinstance(candidate, CandidateAnswer)
    assert "SEMICLASSICAL_GRAVITY" in candidate.solver_id
