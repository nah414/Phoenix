"""Cross-provider wobble logic for Trinity Core's Orchestrate subsystem.

Per architecture v1 Section 6.3 (Axis 3): submit the verified bundle to
two providers (typically the chosen primary plus the local simulator)
and compare the two :class:`Result` candidates. Disagreement between the
two outcomes is the cross-provider error bar -- it surfaces backend-
specific physics that single-provider runs miss.

**Disagreement metric (Phase 5 default):** absolute difference between
the two providers' returned ``value`` field, scaled to the units of
``error_bar_solver`` directly (Result.value already lives in the same
energy scale as the solver's eigenvalue). The full distance matrix row
is preserved per Section 6.2 DO-NOT-COLLAPSE.

This module is a pure-function helper. The :class:`CrossProviderAxis`
Protocol implementation in ``phoenix/verification/wobble_axis.py``
composes this helper with two :class:`Result` envelopes that the
:class:`VerificationGate` (Step 6) produces by invoking
:func:`orchestrate.engine.orchestrate` twice with two different
:class:`ProviderSelection`.

The gate, not the axis, owns the Router-second-call + double-orchestrate
mechanics per Section 6.10 ("the gate issues a *second* `RoutingRequest`
with `excluded_providers` including the primary's choice"). The axis is
a thin disagreement composer over the two pre-computed Result envelopes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phoenix.trinity.data_model import Result


@dataclass(frozen=True)
class CrossProviderDisagreement:
    """Structured disagreement between two provider runs of the same bundle.

    Fields:
        error_bar_scalar: Absolute |value_primary - value_alternate|.
            Already in the energy scale of the solver eigenvalue (Phase
            5 keeps the placeholder identity scaling; Phase 6+ may
            normalize against expected provider variance).
        distance_matrix_row: ``[error_bar_scalar]`` (single-element row
            in Phase 5, matching Phase 3's Axis 2 shape). Preserved per
            DO-NOT-COLLAPSE; the gate's distance matrix concatenates
            this with Axis 1 + Axis 2 rows.
        primary_provider_id / alternate_provider_id: For audit log
            join. Lands in :class:`AxisResult.metadata`.
        primary_value / alternate_value: The two values that produced
            the disagreement. Audit-friendly so log readers see what
            each provider returned.
        metric: Literal ``"absolute_value_difference"`` in Phase 5. Phase
            6+ may switch (relative difference, KL divergence on shot
            histograms, etc.).
    """

    error_bar_scalar: float
    distance_matrix_row: list[float]
    primary_provider_id: str
    alternate_provider_id: str
    primary_value: float
    alternate_value: float
    metric: str  # "absolute_value_difference" in Phase 5


def compute_cross_provider_disagreement(
    primary: Result,
    alternate: Result,
    *,
    primary_provider_id: str,
    alternate_provider_id: str,
) -> CrossProviderDisagreement:
    """Compute disagreement between two :class:`Result` envelopes.

    Phase 5 metric: absolute difference between the two ``value`` fields.

    Raises:
        ValueError: either ``Result.value`` is not a real-valued scalar
            (Phase 5 surface; future phases may normalize complex /
            distribution-valued observables).
    """
    primary_val = _coerce_scalar(primary.value, role="primary")
    alternate_val = _coerce_scalar(alternate.value, role="alternate")
    diff = abs(primary_val - alternate_val)
    return CrossProviderDisagreement(
        error_bar_scalar=diff,
        distance_matrix_row=[diff],
        primary_provider_id=primary_provider_id,
        alternate_provider_id=alternate_provider_id,
        primary_value=primary_val,
        alternate_value=alternate_val,
        metric="absolute_value_difference",
    )


def _coerce_scalar(value: object, *, role: str) -> float:
    """Defensive scalar coercion. Phase 5 surface; Phase 6+ tightens."""
    if isinstance(value, (int, float)):
        return float(value)
    if hasattr(value, "real"):
        return float(getattr(value, "real"))  # complex-like
    raise ValueError(
        f"CrossProviderAxis: {role} Result.value is not a real scalar "
        f"(got {type(value).__name__}={value!r}). Phase 5 only handles "
        f"scalar observables; complex / distribution-valued results "
        f"will be supported in a future phase."
    )
