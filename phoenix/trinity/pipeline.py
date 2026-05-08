"""Trinity Core pipeline orchestrator.

Per architecture v1 Section 2.3: every Phoenix solve flows through Trinity
Core's three peer engines (Solver, Control, Orchestrate). Phase 2 ships the
Solver-only path -- :func:`solve` returns a :class:`CandidateAnswer` directly
with a ``phase: phase_2_solver_only`` provenance marker. Phase 3 promotes the
return type to a full :class:`Result` envelope by wiring Control's DPD
scheduling and Orchestrate's verification gate above this layer.

Phase 2 responsibilities (Section 2.3):

1. Latency-tier gate. ``BATCH_REALTIME`` is the only tier v1 routes;
   ``STREAMING_REALTIME`` (v2) and ``PERCEPTION_REALTIME`` (Phase 12+ via the
   perception harness extension) raise :class:`LatencyTierNotImplemented`.
2. Frontier-physics gate is enforced one layer down by
   :func:`phoenix.trinity.solver.engine.pick_solver` (architecture Section 1
   Decision 7); the engine raises :class:`FrontierPhysicsRefused` when
   Wheeler-DeWitt / Gravitational Decoherence / Semiclassical Gravity is
   dispatched without ``task.tolerance.frontier_physics``. This pipeline does
   not re-check; the engine-boundary check is authoritative for Phase 2.
3. Run :class:`CrossPrecisionAxis` at :attr:`RungDepth.R2_CROSS_PRECISION`.
   Phase 2 always exercises Axis 1 at R2 -- adaptive rung selection (R1-R5)
   driven by ``max_error_bar`` lands in Phase 5's verification gate.
4. Extract canonical ``value`` from the axis result's high-grid
   :class:`SolverRunResult` -- the PERF win that avoids a third solver
   invocation. Step 4 stashed the higher-grid run in
   ``AxisResult.metadata["high_grid_result"]`` for exactly this consumer.
5. Build :class:`SolverProvenance` + :class:`CandidateAnswer` with the
   ``phase: phase_2_solver_only`` honesty marker.
"""

from __future__ import annotations

import time

from phoenix._internal.latency import LatencyTier, LatencyTierNotImplemented
from phoenix.trinity.data_model import (
    CandidateAnswer,
    PhysicsTask,
    SolverProvenance,
)
from phoenix.trinity.solver.engine import SolverRunResult
from phoenix.verification.wobble_axis import (
    AxisResult,
    CrossPrecisionAxis,
    RungDepth,
)


def _enforce_latency_tier(task: PhysicsTask) -> None:
    """Refuse non-routable latency tiers.

    v1 (architecture Section 1 Decision 26) routes only ``BATCH_REALTIME``.
    The other two tiers are defined-but-not-routable so v1's pipeline accepts
    the parameter from day one without forcing a churn-the-call-sites
    extension when Phase 12+ adds ``PERCEPTION_REALTIME`` and v2 adds
    ``STREAMING_REALTIME``.
    """
    tier = task.tolerance.latency_tier
    if tier is LatencyTier.BATCH_REALTIME:
        return
    if tier is LatencyTier.STREAMING_REALTIME:
        raise LatencyTierNotImplemented(
            tier=tier,
            reason=(
                f"Latency tier {tier.value!r} is defined-but-not-routable in "
                f"Phoenix v1. Streaming-realtime (sub-millisecond loops, "
                f"standing-computation API) lands in v2 per architecture "
                f"Section 1 Decision 28. Submit this task with "
                f"latency_tier=BATCH_REALTIME or wait for v2."
            ),
        )
    if tier is LatencyTier.PERCEPTION_REALTIME:
        raise LatencyTierNotImplemented(
            tier=tier,
            reason=(
                f"Latency tier {tier.value!r} is routed only by the perception "
                f"harness extension (Phase 12+ per architecture Section "
                f"11.14.7, locked 2026-05-08). Phoenix v1's quantum-solve "
                f"pipeline does not accept this tier. Submit with "
                f"latency_tier=BATCH_REALTIME for v1 quantum work."
            ),
        )
    # Defensive: a future enum value not yet wired into this gate.
    raise LatencyTierNotImplemented(
        tier=tier,
        reason=(
            f"Latency tier {tier!r} is not routable in this Phoenix release. "
            f"Submit with latency_tier=BATCH_REALTIME."
        ),
    )


def _extract_value(high_grid_result: SolverRunResult) -> float:
    """Pull the canonical observable from the high-grid solver result.

    Phase 2 ships ground-state-eigenvalue extraction: ``eigenvalues[0]`` if
    the solver returned a spectrum, else ``energy`` if the solver returned a
    scalar, else 0.0 (defensive -- should be unreachable since Step 4's
    cross-precision logic itself raises ValueError when both are absent).

    Phase 3 expands this when Control's verified observable surface lands;
    the high-grid SolverRunResult will carry richer payload (full state
    vector, occupation distribution, etc.) and the orchestrator will route
    based on what the task asked for.
    """
    if high_grid_result.eigenvalues:
        return float(high_grid_result.eigenvalues[0])
    if high_grid_result.energy is not None:
        return float(high_grid_result.energy)
    return 0.0


def solve(task: PhysicsTask) -> CandidateAnswer:
    """Phase 2 Solver-only pipeline entry point.

    Returns a :class:`CandidateAnswer` carrying the dispatched solver's
    ground-state energy as ``value``, the cross-precision wobble result as
    ``error_bar_solver``, and a :class:`SolverProvenance` with the
    ``phase: phase_2_solver_only`` honesty marker.

    Raises:
        LatencyTierNotImplemented: ``task.tolerance.latency_tier`` is not
            ``BATCH_REALTIME``.
        FrontierPhysicsRefused: dispatched solver is in the frontier-physics
            set and ``task.tolerance.frontier_physics`` is False (raised by
            :func:`engine.pick_solver` one layer down).
        ValueError: cross-precision logic could not compute disagreement
            (both eigenvalue lists empty AND energy is None on both grids).
    """
    _enforce_latency_tier(task)

    t_start = time.perf_counter()

    axis = CrossPrecisionAxis()
    axis_result: AxisResult = axis.run(task, RungDepth.R2_CROSS_PRECISION)

    # The high-grid SolverRunResult was preserved in metadata by Step 4
    # specifically so this orchestrator can extract the canonical value
    # without a third solver invocation. PERF win: ~10-30 ms saved per solve.
    high_grid_result: SolverRunResult = axis_result.metadata["high_grid_result"]
    value = _extract_value(high_grid_result)

    error_bar_solver = axis_result.error_bar_contribution
    # Phase 2 placeholder: sigma_solver tracks error_bar_solver. Phase 5's
    # verification gate will compute the proper standard-deviation form
    # across the full distance matrix (Section 6.2 DO-NOT-COLLAPSE).
    sigma_solver = error_bar_solver

    wall_clock_ms_total = (time.perf_counter() - t_start) * 1000.0

    # solver_id format per architecture Section 2.3: "<regime>/<solver_class>"
    solver_id = f"{high_grid_result.regime.name}/{high_grid_result.solver_class_name}"

    solver_kpi_bundle: dict[str, object] = {
        "wall_clock_ms_total": wall_clock_ms_total,
        "wall_clock_ms_low": axis_result.metadata["wall_clock_ms_low"],
        "wall_clock_ms_high": axis_result.metadata["wall_clock_ms_high"],
        "n_grid_low": axis_result.metadata["n_grid_low"],
        "n_grid_high": axis_result.metadata["n_grid_high"],
        "dispatched_regime": high_grid_result.regime.name,
        "dispatched_solver_class": high_grid_result.solver_class_name,
        "classifier_confidence": high_grid_result.classifier_confidence,
        "classifier_reasoning": high_grid_result.classifier_reasoning,
        "rung_depth": RungDepth.R2_CROSS_PRECISION.name,
    }

    provenance = SolverProvenance(
        request_id=task.request_id,
        dispatched_solver=solver_id,
        n_grid_low=axis_result.metadata["n_grid_low"],
        n_grid_high=axis_result.metadata["n_grid_high"],
        wall_clock_ms_total=wall_clock_ms_total,
        cross_precision_axis_result=axis_result,
        phase="phase_2_solver_only",
    )

    return CandidateAnswer(
        solver_id=solver_id,
        value=value,
        error_bar_solver=error_bar_solver,
        sigma_solver=sigma_solver,
        solver_kpi_bundle=solver_kpi_bundle,
        provenance_solver=provenance,
    )
