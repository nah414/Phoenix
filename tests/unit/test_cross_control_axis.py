"""Phase 3 Step 4 -- CrossControlAxis (Axis 2).

Mirrors tests/unit/test_cross_precision_axis.py shape:
- applies_to returns True for v1 task surface.
- R2_CROSS_PRECISION returns zero-contribution skipped result.
- R3_TWO_AXES runs the full eps=0.1 + eps=0.5 sweep on QHO; trace_distance
  >= 0; metric metadata == "trace_distance"; weak_control_result preserved
  for pipeline reuse.
"""

from __future__ import annotations

import phoenix  # noqa: F401  -- triggers sys.path injection for vendored modules


def _qho_task():
    from synthesis.equations.base import PhysicsContext

    from phoenix.trinity.data_model import PhysicsTask, ToleranceSpec

    ctx = PhysicsContext(
        mass_kg=9.1093837015e-31,
        length_scale_m=4e-9,
        metadata={"omega": 1e15, "n_grid_points": 200},
    )
    return PhysicsTask(
        physics_context=ctx,
        tolerance=ToleranceSpec(),
        actor=None,
        request_id="test-axis-2",
    )


def test_cross_control_applies_to_returns_true() -> None:
    from phoenix.verification.wobble_axis import CrossControlAxis

    axis = CrossControlAxis()
    assert axis.applies_to(_qho_task()) is True


def test_cross_control_r2_returns_zero_contribution_skipped() -> None:
    """R2 (cross-precision only) is below Axis 2's firing threshold (R3+)."""
    from phoenix.verification.wobble_axis import (
        AxisResult,
        CrossControlAxis,
        RungDepth,
    )

    axis = CrossControlAxis()
    res = axis.run(_qho_task(), RungDepth.R2_CROSS_PRECISION)
    assert isinstance(res, AxisResult)
    assert res.axis_name == "cross_control"
    assert res.error_bar_contribution == 0.0
    assert res.distance_matrix_row == []
    assert res.metadata.get("skipped") is True
    assert res.metadata["depth"] == "R2_CROSS_PRECISION"


def test_cross_control_r3_runs_full_probe_sweep_on_qho() -> None:
    """R3 runs eps=0.1 + eps=0.5; produces sane disagreement signal."""
    from phoenix.trinity.control.engine import ControlRunResult
    from phoenix.verification.wobble_axis import (
        AxisResult,
        CrossControlAxis,
        RungDepth,
    )

    axis = CrossControlAxis()
    res = axis.run(_qho_task(), RungDepth.R3_TWO_AXES)
    assert isinstance(res, AxisResult)
    assert res.axis_name == "cross_control"

    # Trace distance is in [0, 1]; for the |0><0| placeholder rho it's 0
    # (sigma-z eigenbasis is unchanged by sigma-z weak measurement at any
    # strength). Phase 5 wires the real eigenstate, surfaces non-zero signal.
    assert 0.0 <= res.metadata["trace_distance"] <= 1.0
    assert len(res.distance_matrix_row) == 1
    assert res.metadata["metric"] == "trace_distance"
    assert res.metadata["epsilon_weak"] == 0.1
    assert res.metadata["epsilon_strong"] == 0.5
    assert res.metadata["depth"] == "R3_TWO_AXES"

    # PERF win: weak-probe ControlRunResult preserved for the pipeline.
    weak = res.metadata["weak_control_result"]
    strong = res.metadata["strong_control_result"]
    assert isinstance(weak, ControlRunResult)
    assert isinstance(strong, ControlRunResult)
    assert weak.probe_strength_used == 0.1
    assert strong.probe_strength_used == 0.5


def test_cross_control_uses_injected_prior_high_grid_result() -> None:
    """Pipeline-style injection: passing prior_high_grid_result skips the
    redundant solver call inside Axis 2's run."""
    from phoenix.trinity.solver.engine import run_solver
    from phoenix.verification.wobble_axis import (
        CrossControlAxis,
        RungDepth,
    )

    task = _qho_task()
    high_grid = run_solver(task, n_grid=400)

    axis = CrossControlAxis(prior_high_grid_result=high_grid)
    res = axis.run(task, RungDepth.R3_TWO_AXES)
    # Same fields as the standalone path; the injection is just a PERF
    # optimization that elides one extra solver invocation.
    assert res.metadata["epsilon_weak"] == 0.1
    assert res.metadata["epsilon_strong"] == 0.5
