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
