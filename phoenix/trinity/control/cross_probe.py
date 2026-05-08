"""Cross-control wobble logic for Trinity Core's Control subsystem.

Per architecture v1 Section 6.3 (Axis 2): run the dispatched solver's DPD
control sequence at two probe strengths (typically weak ε₁ = 0.1 and strong
ε₂ = 0.5) and compare the resulting verified density matrices. Disagreement
between the two outcomes is the cross-control error bar -- it surfaces
backaction-driven physics that single-probe-strength runs miss.

**Disagreement metric (Phase 3 default -- TRACE DISTANCE):**

For two density matrices :math:`\\rho_1, \\rho_2`:

.. math::

    T(\\rho_1, \\rho_2) = \\frac{1}{2} \\left\\| \\rho_1 - \\rho_2 \\right\\|_1
                       = \\frac{1}{2} \\sum_i |\\lambda_i(\\rho_1 - \\rho_2)|

This is the standard quantum-information distinguishability metric, bounded
:math:`[0, 1]`, and operationally meaningful: it's the maximum probability of
distinguishing the two states by any single-shot measurement. The metric
name is stored in :class:`CrossControlDisagreement.metric` and on the
:class:`AxisResult.metadata` so Phase 5's verification gate composer can
introspect or override.

Alternatives considered (per the Phase 3 plan's R1 risk):

- **Frobenius distance** :math:`\\|\\rho_1 - \\rho_2\\|_F` -- simpler and
  basis-independent but not as physically meaningful.
- **Bures distance** :math:`\\sqrt{2 - 2\\sqrt{F(\\rho_1, \\rho_2)}}` --
  geodesic on the density-matrix manifold; may give better
  distance-matrix uniformity with Axes 1 + 3 in Phase 5's gate composition.

Phase 3 ships trace distance; Phase 5's gate may switch.

The full pairwise distance row is preserved alongside the scalar per
architecture Section 6.2's DO-NOT-COLLAPSE invariant. Phase 3 ships a
single-element row (trace distance is a scalar metric; per-eigenvalue
expansion comes at Phase 5 if the gate composer needs it).

This module is a pure-function helper. The :class:`CrossControlAxis`
Protocol implementation in ``phoenix/verification/wobble_axis.py`` composes
this helper with two :func:`run_dpd` invocations to produce the
:class:`AxisResult` the verification gate consumes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from phoenix.trinity.control.engine import ControlRunResult


@dataclass(frozen=True)
class CrossControlDisagreement:
    """Structured pairwise disagreement between two probe-strength runs.

    Fields:
        error_bar_scalar: Trace distance between the two verified density
            matrices (dimensionless, in :math:`[0, 1]`). The verification
            gate translates this to an energy-scale error bar by multiplying
            by the candidate's energy scale -- see
            :class:`CrossControlAxis.run` for the conversion.
        distance_matrix_row: ``[error_bar_scalar]`` (single-element row in
            Phase 3). Preserved per DO-NOT-COLLAPSE; the gate consumes the
            full row even when it's one element.
        epsilon_weak / epsilon_strong: The two probe strengths used. Phase
            3 default: 0.1 weak + 0.5 strong.
        metric: Literal ``"trace_distance"`` in Phase 3. Phase 5's gate
            composer can introspect this to know what units the disagreement
            is in (Frobenius, Bures, etc. would have different shapes).
    """

    error_bar_scalar: float
    distance_matrix_row: list[float]
    epsilon_weak: float
    epsilon_strong: float
    metric: str  # "trace_distance" in Phase 3


def compute_cross_control_disagreement(
    weak: ControlRunResult,
    strong: ControlRunResult,
) -> CrossControlDisagreement:
    """Compute trace distance between two :class:`ControlRunResult` outputs.

    .. math::

        T(\\rho_w, \\rho_s) = \\frac{1}{2} \\sum_i |\\lambda_i(\\rho_w - \\rho_s)|

    where :math:`\\rho_w` and :math:`\\rho_s` are the post-DPD density
    matrices from the weak and strong probe runs respectively.

    SAFETY: requires the two ρ to have the same dimension. Raises
    :class:`ValueError` on shape mismatch (a malformed ControlRunResult
    pair, not a normal physics condition). The trace distance is always in
    :math:`[0, 1]` for valid density matrices; Phase 3 clips to that range
    defensively.
    """
    rho_w = np.asarray(weak.rho_verified, dtype=complex)
    rho_s = np.asarray(strong.rho_verified, dtype=complex)
    if rho_w.shape != rho_s.shape:
        raise ValueError(
            f"Cross-control: rho shapes mismatch (weak={rho_w.shape}, "
            f"strong={rho_s.shape}). Cannot compute trace distance."
        )

    diff = rho_w - rho_s
    # Hermitian-eigenvalue path: rho is Hermitian, rho_w - rho_s is Hermitian,
    # use eigvalsh for real eigenvalues. Trace distance is half the L1 norm
    # of the eigenvalue spectrum.
    eigenvalues = np.real(np.linalg.eigvalsh(diff))
    trace_distance = 0.5 * float(np.sum(np.abs(eigenvalues)))
    # Clip to [0, 1] defensively -- valid density matrices always satisfy
    # this bound; numerical noise at machine precision can push slightly out.
    trace_distance = float(np.clip(trace_distance, 0.0, 1.0))

    return CrossControlDisagreement(
        error_bar_scalar=trace_distance,
        distance_matrix_row=[trace_distance],
        epsilon_weak=weak.probe_strength_used,
        epsilon_strong=strong.probe_strength_used,
        metric="trace_distance",
    )
