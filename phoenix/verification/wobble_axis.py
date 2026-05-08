"""WobbleAxis Protocol + the verification rung-depth enum + concrete axis impls.

Per architecture v1.1 follow-up (Section 6.3, locked 2026-05-08): the
verification gate is parameterized by a list of :class:`WobbleAxis` Protocol
implementations rather than hardcoding three named methods. v1 ships three
concrete impls (``CrossPrecisionAxis``, ``CrossControlAxis``,
``CrossProviderAxis``); v1.x extensions register their own axes against the
same Protocol contract without forking the gate.

Phase 2 ships:

- The :class:`WobbleAxis` Protocol contract.
- :class:`AxisResult` (the row each axis adds to the distance matrix plus
  the axis's contribution to the combined error bar).
- :class:`RungDepth` enum (R1 through R5; full rung-table semantics land in
  Phase 5, but the enum is needed now so axis impls can accept a depth
  parameter).
- :class:`CrossPrecisionAxis` -- skeleton (Axis 1 dispatch contract; full
  implementation lands in Phase 2 Step 4).

Phase 3 adds :class:`CrossControlAxis` (Axis 2) and :class:`CrossProviderAxis`
(Axis 3) following the same Protocol contract.

Phase 5 adds the verification gate orchestrator that registers axis impls
and exercises them per the rung table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from phoenix.trinity.data_model import PhysicsTask


# ---------------------------------------------------------------------------
# Verification depth dial


class RungDepth(Enum):
    """Verification depth tiers per architecture Section 6.4.

    Phase 2 routes R1 (no axes) and R2 (Axis 1 only). Phases 3 + 5 fill in
    R3 (two axes), R4 (three axes default for instrument-grade), and R5
    (three axes plus replication). The enum integer values are stable across
    releases; new rungs append.
    """

    R1_FLOOR = 1
    R2_CROSS_PRECISION = 2
    R3_TWO_AXES = 3
    R4_THREE_AXES = 4
    R5_REPLICATED = 5


# ---------------------------------------------------------------------------
# Per-axis run result


@dataclass(frozen=True)
class AxisResult:
    """One :class:`WobbleAxis` invocation's structured output.

    Each axis returns its row of the distance matrix (preserved alongside
    the scalar ``error_bar_contribution`` per the vendored ``DO NOT COLLAPSE``
    invariant from architecture Section 6.2) plus axis-specific metadata
    that the verification gate stitches into ``ProvenanceTrace``.
    """

    axis_name: str
    error_bar_contribution: float
    distance_matrix_row: list[float] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# WobbleAxis Protocol contract


class WobbleAxis(Protocol):
    """A single disagreement axis the verification gate can orchestrate.

    v1 ships three concrete axis impls (cross-precision, cross-control,
    cross-provider). The perception harness extension at Phase 20 adds three
    more (cross-modality, cross-frame, cross-canonical) implementing the same
    Protocol. Same gate, same machinery, different axes.

    The Protocol is structural: any class exposing the ``name`` attribute and
    the two methods satisfies it. Concrete impls register themselves with the
    verification gate at startup; the gate orchestrates whichever axes the
    active domain has registered.
    """

    name: str
    """Stable identifier (e.g. ``"cross_precision"``). Used in
    :class:`AxisResult.axis_name`, in ``ProvenanceTrace`` rows, and as the
    distance-matrix axis label."""

    def applies_to(self, task: PhysicsTask) -> bool:
        """Whether this axis exercises meaningfully on the given task.

        e.g. ``CrossProviderAxis`` returns ``False`` when the task's
        reproducibility mode forces a single-provider replay; the gate skips
        that axis for that task and records the skip in provenance.
        """
        ...

    def run(self, task: PhysicsTask, depth: RungDepth) -> AxisResult:
        """Run the axis at the requested depth.

        Returns the row that lands in the distance matrix plus the axis's
        contribution to the combined error bar. Raises a typed exception
        (per architecture Section 3.7 discipline) if the axis cannot run --
        the gate catches and records the failure rather than propagating it
        as a successful zero-contribution result.
        """
        ...


# ---------------------------------------------------------------------------
# Concrete axis impls -- v1 quantum wobble axes


class CrossPrecisionAxis:
    """Axis 1 -- cross-precision verification inside the Solver subsystem.

    Runs the dispatched solver at two grid resolutions (``N`` from
    ``task.physics_context.metadata["n_grid_points"]``, default 400, and
    ``2N``). Disagreement between the two eigenvalue spectra is the
    cross-precision error bar; the full pairwise difference row is preserved
    in :class:`AxisResult` per architecture Section 6.2's DO-NOT-COLLAPSE
    invariant.

    Always applies in v1: all twelve vendored solvers use numerical grids.

    Depth handling:
        - :attr:`RungDepth.R1_FLOOR`: skip cross-precision; return a
          zero-contribution :class:`AxisResult` with ``skipped=True`` in
          metadata. Used by the verification gate when ``max_error_bar``
          is loose enough that single-precision is acceptable.
        - :attr:`RungDepth.R2_CROSS_PRECISION` and higher: run the full
          ``N + 2N`` comparison. The high-grid :class:`SolverRunResult` is
          stashed in ``AxisResult.metadata["high_grid_result"]`` so the
          pipeline orchestrator (Step 5) can extract the canonical
          ``CandidateAnswer.value`` without re-running the solver. **PERF:**
          saves one solver invocation per solve (~10-30 ms on Phase 2's
          QHO benchmark).
    """

    name: str = "cross_precision"

    def applies_to(self, task: PhysicsTask) -> bool:
        # Cross-precision applies to any task with a numerical-grid solver.
        # All twelve vendored solvers use grids, so always True in v1.
        # The signature-formal `task` parameter is unused at v1; future
        # axis impls (e.g. one that skips cross-precision for analytical
        # solvers) will inspect the task's regime.
        del task  # explicit acknowledgement of the unused-parameter pattern
        return True

    def run(self, task: PhysicsTask, depth: RungDepth) -> AxisResult:
        if depth == RungDepth.R1_FLOOR:
            return AxisResult(
                axis_name=self.name,
                error_bar_contribution=0.0,
                distance_matrix_row=[],
                metadata={"skipped": True, "depth": depth.name},
            )

        # Lazy imports to avoid circular concerns at module load (engine.py
        # imports PhysicsTask from data_model; data_model has TYPE_CHECKING
        # import of AxisResult from this module). Function-local imports
        # break the runtime cycle while preserving the typing-level cycle
        # which `from __future__ import annotations` resolves.
        from phoenix.trinity.solver.cross_precision import (
            compute_cross_precision_disagreement,
        )
        from phoenix.trinity.solver.engine import run_solver

        raw_n_grid_default = task.physics_context.metadata.get("n_grid_points", 400)
        n_grid_low = int(raw_n_grid_default)
        n_grid_high = 2 * n_grid_low

        result_low = run_solver(task, n_grid=n_grid_low)
        result_high = run_solver(task, n_grid=n_grid_high)

        disagreement = compute_cross_precision_disagreement(result_low, result_high)

        return AxisResult(
            axis_name=self.name,
            error_bar_contribution=disagreement.error_bar_scalar,
            distance_matrix_row=disagreement.distance_matrix_row,
            metadata={
                "depth": depth.name,
                "n_grid_low": disagreement.n_grid_low,
                "n_grid_high": disagreement.n_grid_high,
                "eig_low_count": disagreement.eig_low_count,
                "eig_high_count": disagreement.eig_high_count,
                "wall_clock_ms_low": result_low.wall_clock_ms,
                "wall_clock_ms_high": result_high.wall_clock_ms,
                "regime": result_high.regime.name,
                "solver_class_name": result_high.solver_class_name,
                "classifier_confidence": result_high.classifier_confidence,
                "classifier_reasoning": result_high.classifier_reasoning,
                # The high-grid SolverRunResult is preserved for the pipeline
                # orchestrator to extract CandidateAnswer.value without
                # re-running the solver. PERF win per Phase 2 docstring.
                "high_grid_result": result_high,
            },
        )


class CrossControlAxis:
    """Axis 2 -- cross-control verification inside the Control subsystem.

    Runs the dispatched DPD control sequence at two probe strengths
    (Phase 3 defaults: weak ε₁ = 0.1 and strong ε₂ = 0.5). Disagreement
    between the two post-DPD density matrices is the cross-control error
    bar; the metric is **trace distance** per architecture Section 6.3 and
    the Phase 3 plan's R1 risk decision.

    Always applies in v1: the vendored DPD substrate handles all twelve
    Hamiltonian regimes uniformly. Future axis impls (e.g. one that skips
    cross-control for analytical-only solver paths) will inspect the
    task's regime via ``applies_to``.

    Depth handling:
        - :attr:`RungDepth.R1_FLOOR` and :attr:`RungDepth.R2_CROSS_PRECISION`:
          skip cross-control; return a zero-contribution :class:`AxisResult`
          with ``skipped=True`` in metadata. Axis 2 fires only at
          :attr:`RungDepth.R3_TWO_AXES` and higher per Section 6.4's rung
          table.
        - :attr:`RungDepth.R3_TWO_AXES` and higher: run the full ε=0.1 +
          ε=0.5 sweep. The weak-probe :class:`ControlRunResult` is stashed
          in ``AxisResult.metadata["weak_control_result"]`` so the pipeline
          orchestrator (Step 8) can use it as the canonical
          ``VerifiedAnswer.rho_verified`` without a third DPD invocation.
          **PERF:** saves one full DPD execution per solve (~1-2 s on the
          QHO benchmark with the vendored Lindblad RK4 propagator at
          ``dt=1e-12``).

    The optional ``prior_high_grid_result`` constructor parameter lets the
    pipeline inject Axis 1's high-grid :class:`SolverRunResult` so this
    axis avoids a redundant solver invocation. When ``None`` (the standalone
    case, e.g. unit tests), Axis 2 re-runs the solver to build its own
    :class:`CandidateAnswer`. The plan's R3 risk acknowledges this Protocol
    signature stress; Phase 5's gate orchestrator may extend the
    :class:`WobbleAxis` Protocol to plumb prior axis results explicitly.
    """

    name: str = "cross_control"

    def __init__(
        self,
        *,
        prior_high_grid_result: Any = None,
        epsilon_weak: float = 0.1,
        epsilon_strong: float = 0.5,
    ) -> None:
        # ``prior_high_grid_result`` typed Any to avoid a runtime import
        # of SolverRunResult from phoenix.trinity.solver.engine; the pipeline
        # passes the real type. Standalone callers pass None.
        self._prior_high_grid_result = prior_high_grid_result
        self._epsilon_weak = epsilon_weak
        self._epsilon_strong = epsilon_strong

    def applies_to(self, task: PhysicsTask) -> bool:
        # Cross-control applies to any task with a DPD-capable substrate.
        # All twelve vendored regimes pair with the vendored DPDScheduler;
        # always True in v1. Future: returns False for analytical-only
        # solver paths Phase 5+ may instrument.
        del task  # explicit acknowledgement of unused-parameter pattern
        return True

    def run(self, task: PhysicsTask, depth: RungDepth) -> AxisResult:
        # Axis 2 fires at R3+. R1 (floor) and R2 (cross-precision only)
        # both skip; the gate uses the zero-contribution result without
        # invoking the DPD substrate.
        if depth in (RungDepth.R1_FLOOR, RungDepth.R2_CROSS_PRECISION):
            return AxisResult(
                axis_name=self.name,
                error_bar_contribution=0.0,
                distance_matrix_row=[],
                metadata={
                    "skipped": True,
                    "depth": depth.name,
                    "reason": (
                        "Axis 2 (cross-control) fires only at R3+ per "
                        "architecture Section 6.4 rung table."
                    ),
                },
            )

        # Lazy imports break the runtime cycle: phoenix.trinity.control.engine
        # imports CandidateAnswer from data_model; data_model has TYPE_CHECKING
        # import of AxisResult from this module. Function-local imports mirror
        # the CrossPrecisionAxis pattern.
        from phoenix.trinity.control.cross_probe import (
            compute_cross_control_disagreement,
        )
        from phoenix.trinity.control.engine import run_dpd
        from phoenix.trinity.data_model import CandidateAnswer
        from phoenix.trinity.solver.engine import run_solver

        # Resolve the high-grid solver result -- either injected by the
        # pipeline (the cheap path) or re-run here (the standalone path).
        if self._prior_high_grid_result is not None:
            high_grid_result = self._prior_high_grid_result
        else:
            raw_n_grid_default = task.physics_context.metadata.get("n_grid_points", 400)
            n_grid_high = 2 * int(raw_n_grid_default)
            high_grid_result = run_solver(task, n_grid=n_grid_high)

        # Build a CandidateAnswer from the high-grid result. Phase 3 keeps
        # this minimal -- the canonical observable comes from the
        # ground-state eigenvalue (or .energy fallback). Phase 5's gate
        # orchestrator will plumb the full eigenstate through.
        if high_grid_result.eigenvalues:
            value = float(high_grid_result.eigenvalues[0])
        elif high_grid_result.energy is not None:
            value = float(high_grid_result.energy)
        else:
            value = 0.0
        solver_id = f"{high_grid_result.regime.name}/{high_grid_result.solver_class_name}"
        candidate = CandidateAnswer(
            solver_id=solver_id,
            value=value,
            error_bar_solver=0.0,  # Phase 3 placeholder; Axis 1 already ran
            sigma_solver=0.0,
        )

        # Run DPD at both probe strengths. Lindblad RK4 is the slow path
        # (~1-2 s per leg on the default 10-ns drive at dt=1e-12), so this
        # adds ~2-4 s to the R3+ solve. PERF callout in module docstring.
        weak = run_dpd(candidate, probe_strength=self._epsilon_weak)
        strong = run_dpd(candidate, probe_strength=self._epsilon_strong)

        disagreement = compute_cross_control_disagreement(weak, strong)

        # Translate the dimensionless trace distance into the same units as
        # error_bar_solver (joules in v1's QHO benchmark). Architecture
        # Section 11.1.1 flags the quadrature combiner as an OPEN tension;
        # Phase 3 ships the placeholder energy-scale conversion. Phase 5's
        # gate may revise.
        energy_scale = abs(value)
        error_bar_contribution = disagreement.error_bar_scalar * energy_scale

        return AxisResult(
            axis_name=self.name,
            error_bar_contribution=error_bar_contribution,
            distance_matrix_row=disagreement.distance_matrix_row,
            metadata={
                "depth": depth.name,
                "epsilon_weak": disagreement.epsilon_weak,
                "epsilon_strong": disagreement.epsilon_strong,
                "metric": disagreement.metric,
                "trace_distance": disagreement.error_bar_scalar,
                "energy_scale": energy_scale,
                "trace_preservation_weak": weak.dpd_result.trace_preservation,
                "trace_preservation_strong": strong.dpd_result.trace_preservation,
                "positivity_weak": weak.dpd_result.positivity_check,
                "positivity_strong": strong.dpd_result.positivity_check,
                "wall_clock_ms_weak": weak.wall_clock_ms,
                "wall_clock_ms_strong": strong.wall_clock_ms,
                # The weak-probe ControlRunResult is preserved for the
                # pipeline orchestrator to extract canonical
                # VerifiedAnswer.rho_verified without a third DPD invocation.
                # PERF win analogous to Axis 1's high_grid_result caching.
                "weak_control_result": weak,
                "strong_control_result": strong,
            },
        )
