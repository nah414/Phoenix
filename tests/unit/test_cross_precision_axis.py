"""Phase 2 Step 4 -- CrossPrecisionAxis end-to-end exercise.

Tests the WobbleAxis Protocol implementation for Axis 1 (cross-precision):

- ``applies_to`` returns True for the v1 task surface (all twelve vendored
  solvers use numerical grids).
- ``RungDepth.R1_FLOOR`` returns a zero-contribution :class:`AxisResult` with
  ``skipped=True`` in metadata. No solver invocation; the gate uses this
  when ``max_error_bar`` is loose enough that single-precision suffices.
- ``RungDepth.R2_CROSS_PRECISION`` runs the full N + 2N comparison on a real
  QHO PhysicsTask. The error_bar_contribution should be small (below ~5%
  of the ground-state energy) since the QHO ground state converges quickly
  with grid refinement. The high-grid SolverRunResult is preserved in
  ``metadata["high_grid_result"]`` per the Step 4 PERF win.

These exercise the full chain CrossPrecisionAxis.run -> engine.run_solver ->
vendored EquationSolver -> compute_cross_precision_disagreement.
"""

from __future__ import annotations

import phoenix  # noqa: F401  -- triggers sys.path injection for vendored modules


def _build_qho_task():
    """Construct a minimal PhysicsTask for the harmonic oscillator at omega=1e15."""
    from synthesis.equations.base import PhysicsContext

    from phoenix.trinity.data_model import PhysicsTask, ToleranceSpec

    ctx = PhysicsContext(
        mass_kg=9.1093837015e-31,  # electron
        length_scale_m=4e-9,
        metadata={"omega": 1e15, "n_grid_points": 400},
    )
    return PhysicsTask(
        physics_context=ctx,
        tolerance=ToleranceSpec(),
        actor=None,
        request_id="test-cross-precision-axis-qho",
    )


def test_cross_precision_applies_to_returns_true() -> None:
    """v1 always-applies posture: all twelve vendored solvers use grids."""
    from phoenix.verification.wobble_axis import CrossPrecisionAxis

    task = _build_qho_task()
    axis = CrossPrecisionAxis()
    assert axis.applies_to(task) is True


def test_cross_precision_r1_floor_skips_with_zero_contribution() -> None:
    """R1_FLOOR returns AxisResult with error_bar=0.0 and skipped=True metadata.

    The verification gate uses this when max_error_bar is loose enough that a
    single-precision solve suffices; no solver invocation happens.
    """
    from phoenix.verification.wobble_axis import (
        AxisResult,
        CrossPrecisionAxis,
        RungDepth,
    )

    task = _build_qho_task()
    axis = CrossPrecisionAxis()
    result = axis.run(task, RungDepth.R1_FLOOR)

    assert isinstance(result, AxisResult)
    assert result.axis_name == "cross_precision"
    assert result.error_bar_contribution == 0.0
    assert result.distance_matrix_row == []
    assert result.metadata["skipped"] is True
    assert result.metadata["depth"] == "R1_FLOOR"


def test_cross_precision_r2_runs_full_n_plus_2n_comparison_on_qho() -> None:
    """R2_CROSS_PRECISION runs both grids and produces sane disagreement.

    The QHO ground-state energy converges quickly with grid refinement, so
    the cross-precision error bar should be small relative to the ground-state
    energy itself. We sanity-check shape (positive scalar, non-empty distance
    row) and bound (under 5% of ground-state energy).
    """
    from phoenix.verification.wobble_axis import (
        AxisResult,
        CrossPrecisionAxis,
        RungDepth,
    )

    task = _build_qho_task()
    axis = CrossPrecisionAxis()
    result = axis.run(task, RungDepth.R2_CROSS_PRECISION)

    assert isinstance(result, AxisResult)
    assert result.axis_name == "cross_precision"
    # error_bar_contribution should be non-negative; QHO convergence is fast
    # but exact zero is unlikely with float arithmetic across two grids.
    assert result.error_bar_contribution >= 0.0
    # distance row should have one entry per common eigenvalue index.
    assert len(result.distance_matrix_row) > 0
    assert all(d >= 0.0 for d in result.distance_matrix_row)
    # Ground-state cross-precision disagreement should be small relative to
    # the ground-state energy itself (HBAR * omega / 2 ~= 5.27e-20 J).
    expected_e0 = 1.054571817e-34 * 1e15 * 0.5
    assert result.error_bar_contribution < 0.05 * expected_e0, (
        f"QHO cross-precision disagreement {result.error_bar_contribution:.4e} J too large vs E0={expected_e0:.4e} J"
    )

    # PERF win: high_grid_result stashed in metadata so the pipeline orchestrator
    # can extract CandidateAnswer.value without re-running the solver.
    from phoenix.trinity.solver.engine import SolverRunResult

    high = result.metadata["high_grid_result"]
    assert isinstance(high, SolverRunResult)
    assert high.n_grid_points == 800  # 2 * default 400
    assert len(high.eigenvalues) > 0

    # Audit-friendly metadata fields present.
    assert result.metadata["n_grid_low"] == 400
    assert result.metadata["n_grid_high"] == 800
    assert result.metadata["depth"] == "R2_CROSS_PRECISION"
    assert "regime" in result.metadata
    assert "solver_class_name" in result.metadata
    assert "wall_clock_ms_low" in result.metadata
    assert "wall_clock_ms_high" in result.metadata
