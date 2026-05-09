"""Cross-precision wobble logic for Trinity Core's Solver subsystem.

Per architecture v1 Section 6.3 (Axis 1): run the dispatched solver at two
grid resolutions (default ``N`` and ``2N``) and compare the eigenvalue spectra
elementwise. Disagreement between the two solutions is the cross-precision
error bar.

The full pairwise distance row is preserved alongside the scalar
``error_bar_scalar`` per architecture Section 6.2's DO-NOT-COLLAPSE invariant
(vendored from ``wobble/disagreement_types.py``). Phase 5's verification gate
composes this row into the final distance matrix.

This module is a pure-function helper. The :class:`CrossPrecisionAxis`
Protocol implementation in ``phoenix/verification/wobble_axis.py`` composes
this helper with the engine adapter's :func:`run_solver` to produce the
:class:`AxisResult` the verification gate consumes.
"""

from __future__ import annotations

from dataclasses import dataclass

from phoenix.trinity.solver.engine import SolverRunResult


@dataclass(frozen=True)
class CrossPrecisionDisagreement:
    """Structured pairwise disagreement between two grid resolutions.

    Fields:
        error_bar_scalar: Absolute disagreement of the ground-state eigenvalue
            (index 0). When eigenvalue lists are absent, falls back to the
            absolute ``energy`` difference.
        distance_matrix_row: Per-eigenvalue absolute differences up to the
            common length of the two spectra. Preserved per
            DO-NOT-COLLAPSE; the verification gate consumes the full row.
        n_grid_low / n_grid_high: Grid resolutions that produced the two
            spectra.
        eig_low_count / eig_high_count: Lengths of the two spectra (helps
            audit-log readers see if the higher-grid run produced more
            eigenvalues, which can happen when the solver dispatches
            differently at scale).
    """

    error_bar_scalar: float
    distance_matrix_row: list[float]
    n_grid_low: int
    n_grid_high: int
    eig_low_count: int
    eig_high_count: int


def compute_cross_precision_disagreement(
    low: SolverRunResult,
    high: SolverRunResult,
) -> CrossPrecisionDisagreement:
    """Compare two :class:`SolverRunResult` instances at low and high grid.

    Phase 2 returns absolute disagreement (not relative-to-magnitude). The
    verification gate (Phase 5) and the pipeline orchestrator (Step 5)
    normalize relative to ``max_error_bar`` when needed.

    Raises:
        ValueError: Both eigenvalue lists are empty and ``energy`` is ``None``
            on both results -- there is nothing to compare. Surface as a real
            error rather than silently zeroing out.
    """
    n_compare = min(len(low.eigenvalues), len(high.eigenvalues))

    if n_compare == 0:
        if low.energy is not None and high.energy is not None:
            error_bar = abs(float(low.energy) - float(high.energy))
            return CrossPrecisionDisagreement(
                error_bar_scalar=error_bar,
                distance_matrix_row=[error_bar],
                n_grid_low=low.n_grid_points,
                n_grid_high=high.n_grid_points,
                eig_low_count=0,
                eig_high_count=0,
            )
        raise ValueError(
            f"Cross-precision: both eigenvalue lists empty AND energy=None for "
            f"low (regime={low.regime.name}, n_grid={low.n_grid_points}) and "
            f"high (regime={high.regime.name}, n_grid={high.n_grid_points}). "
            f"Cannot compute disagreement."
        )

    distances = [abs(low.eigenvalues[i] - high.eigenvalues[i]) for i in range(n_compare)]
    return CrossPrecisionDisagreement(
        error_bar_scalar=distances[0],
        distance_matrix_row=distances,
        n_grid_low=low.n_grid_points,
        n_grid_high=high.n_grid_points,
        eig_low_count=len(low.eigenvalues),
        eig_high_count=len(high.eigenvalues),
    )
