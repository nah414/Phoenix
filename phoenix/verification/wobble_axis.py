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

    Runs the dispatched solver at two grid resolutions (default ``N`` and
    ``2N``). Disagreement between the two solutions is the cross-precision
    error bar. Always applies in v1 (all twelve vendored solvers use grids).

    **Phase 2 status:** the ``run()`` method is a skeleton. Phase 2 Step 4
    fills in the real cross-precision logic by calling
    ``phoenix/trinity/solver/cross_precision.py``. The ``name`` and
    ``applies_to`` methods are operational from Step 2 so the data model's
    forward references resolve.
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
        raise NotImplementedError(
            "CrossPrecisionAxis.run lands in Phase 2 Step 4. "
            "Step 4 wires phoenix/trinity/solver/cross_precision.py against this skeleton."
        )
