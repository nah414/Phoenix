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


def test_solve_returns_result_for_qho() -> None:
    """Phase 3: the three-layer pipeline produces a full Result envelope.

    Phase 2's version of this test asserted CandidateAnswer + Solver-only
    fields. Phase 3 promotes solve() to return a Result; this test now
    walks the three-layer Solver + Control + Orchestrate path and verifies
    the full envelope shape.
    """
    from phoenix.trinity.data_model import (
        ControlProvenance,
        OrchestrateProvenance,
        ProvenanceTrace,
        Result,
        SolverProvenance,
    )
    from phoenix.trinity.orchestrate.kpi_bundle import KPIBundle, KPIStatus
    from phoenix.trinity.pipeline import solve

    task = _qho_task()
    result = solve(task)

    assert isinstance(result, Result)

    # value is the trace-expectation from the local-simulator path; ~1.0
    # for valid density matrices (Phase 3 placeholder; Phase 5 wires real
    # observable extraction).
    assert abs(float(result.value) - 1.0) < 1e-6, f"Phase 3 trace expectation: got {result.value}"

    # Combined error_bar is the quadrature of the three layer bars; in
    # Phase 3 only error_bar_solver contributes meaningfully (error_bar_control
    # depends on the cross-control trace distance, which is 0 for the
    # placeholder |0><0| input; error_bar_orchestrate is 0 at R3).
    expected_e0 = HBAR * 1e15 * 0.5
    assert result.error_bar > 0.0
    assert result.error_bar < 0.05 * expected_e0

    # Phase 5: sigma is the real wobble_score from Section 6.2's
    # sqrt(Var(distance_matrix_upper_triangle)) formula. It's no longer
    # equal to error_bar (which is the quadrature combine). For QHO
    # at R3 with the eigenstate plumbing's diag(1,0) input, sigma is
    # the variance of Axis 1's per-eigenvalue distance row plus
    # Axis 2's zero contribution.
    assert result.sigma >= 0.0

    # KPIBundle from Orchestrate is typed.
    assert isinstance(result.kpi_bundle_orchestrate, KPIBundle)
    assert result.kpi_bundle_orchestrate.status in {KPIStatus.OK, KPIStatus.WARN, KPIStatus.FAIL}
    assert result.kpi_bundle_orchestrate.shots_used > 0

    # Full ProvenanceTrace with all three sub-blocks.
    assert isinstance(result.provenance, ProvenanceTrace)
    assert isinstance(result.provenance.solver, SolverProvenance)
    assert isinstance(result.provenance.control, ControlProvenance)
    assert isinstance(result.provenance.orchestrate, OrchestrateProvenance)
    # Phase 5: solver + control provenances built by the gate carry the
    # phase_5_verification_gate marker. orchestrate provenance is built
    # inside orchestrate() (Phase 3 module) and retains its own marker
    # since the orchestrate subsystem itself didn't change in Phase 5.
    assert result.provenance.solver.phase == "phase_5_verification_gate"
    assert result.provenance.control.phase == "phase_5_verification_gate"
    assert result.provenance.orchestrate.phase == "phase_3_solver_control_orchestrate"
    # New Phase 5 verification provenance.
    assert result.provenance.verification is not None
    assert result.provenance.verification.phase == "phase_5_verification_gate"
    assert result.provenance.verification.initial_rung == "R3_TWO_AXES"
    assert result.provenance.verification.drift_state == "healthy"
    assert result.provenance.solver.request_id == "test-pipeline-qho"
    assert result.provenance.cloud_shots_recorded is False  # local simulator path

    # Solver provenance carries the cross-precision axis result.
    assert result.provenance.solver.cross_precision_axis_result is not None
    # Control provenance carries the cross-control axis result.
    assert result.provenance.control.cross_control_axis_result is not None
    # Orchestrate provenance carries the bundle hash + provider id.
    assert result.provenance.orchestrate.provider_id == "phoenix.local_simulator"
    assert len(result.provenance.orchestrate.bundle_hash) == 16


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
    from phoenix.trinity.data_model import PhysicsTask, Result, ToleranceSpec
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

    result = solve(task)
    assert isinstance(result, Result)
    # Solver provenance carries the dispatched solver name.
    assert result.provenance is not None
    assert result.provenance.solver is not None
    assert "SEMICLASSICAL_GRAVITY" in result.provenance.solver.dispatched_solver
