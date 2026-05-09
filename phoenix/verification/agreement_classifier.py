"""Agreement classifier: extends :class:`DisagreementType` with Phoenix-native values.

Per architecture v1 Section 6.2: Phase 5 extends the vendored
:class:`wobble.disagreement_types.DisagreementType` enum with Phoenix
physics-wobble specializations:

- ``CONVERGED`` -- all axes agreed within tolerance; the most common
  Phoenix outcome.
- ``NUMERICAL_DRIFT`` -- Axis 1 (cross-precision) dominated the
  disagreement. The grid resolution didn't converge.
- ``BACKACTION_SENSITIVE`` -- Axis 2 (cross-control) dominated. The
  measurement backaction is sensitive to probe strength.
- ``PROVIDER_DIVERGENT`` -- Axis 3 (cross-provider) dominated. Different
  providers gave materially different answers.
- ``DEGRADED`` -- multiple axes hit budget OR drift detector flagged
  warning state. The result is provisional.
- ``DEGRADED_BUDGET_BOUND`` -- the gate's pre-promotion cost check
  refused to promote because the user's cost ceiling can't accommodate
  the next rung. The result ships at reduced verification depth.

Python enums can't be extended at the language level. Phoenix introduces
:class:`PhoenixDisagreementType` -- a new enum that mirrors the vendored
values plus the Phase 5 specializations. Result.agreement_type's type
changes to this Phoenix enum; the vendored :class:`DisagreementType`
remains in :mod:`wobble.disagreement_types` for vendored code.

Phase 3 used :attr:`DisagreementType.HEDGED_CONSENSUS` as a placeholder
mapping for "all axes agree within tolerance" -- Phase 5 replaces with
:attr:`PhoenixDisagreementType.CONVERGED` for that case.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phoenix.verification.drift_state import DriftState
    from phoenix.verification.wobble_axis import AxisResult


class PhoenixDisagreementType(Enum):
    """Phoenix-native disagreement classification (Section 6.2 extension).

    Mirrors the vendored :class:`wobble.disagreement_types.DisagreementType`
    values plus the Phoenix physics-wobble specializations. Stable string
    values; new values append.
    """

    # Vendored mirror values (string values match the vendored enum so
    # audit logs and JSON payloads carry the same identifiers).
    CONTRADICTION = "contradiction"
    TEMPORAL_DRIFT = "temporal_drift"
    FRAME_MISMATCH = "frame_mismatch"
    HEDGED_CONSENSUS = "hedged_consensus"
    UNKNOWN = "unknown"

    # Phoenix physics-wobble specializations (Section 6.2).
    CONVERGED = "converged"
    """All axes converged within tolerance. Phase 5's default for
    successful solves; replaces Phase 3's HEDGED_CONSENSUS placeholder."""

    NUMERICAL_DRIFT = "numerical_drift"
    """Axis 1 (cross-precision) dominated the combined disagreement.
    Grid resolution insufficient; user can re-submit with finer grid."""

    BACKACTION_SENSITIVE = "backaction_sensitive"
    """Axis 2 (cross-control) dominated. The measurement backaction is
    sensitive to probe strength; the verified state depends meaningfully
    on the chosen probe."""

    PROVIDER_DIVERGENT = "provider_divergent"
    """Axis 3 (cross-provider) dominated. Different providers gave
    materially different answers; the bundle's interpretation differs
    across hardware."""

    DEGRADED = "degraded"
    """Multiple axes hit error budget OR drift detector flagged
    ``warning`` state. The result is provisional pending operator
    review."""

    DEGRADED_BUDGET_BOUND = "degraded_budget_bound"
    """The pre-promotion cost check refused to promote (Section 4.7
    Stage 5). Result ships at reduced verification depth; user can
    re-submit with a higher cost_ceiling_usd."""


def classify(
    axis_results: list[AxisResult],
    *,
    max_error_bar: float,
    drift_state: DriftState | None = None,
    budget_bound: bool = False,
) -> PhoenixDisagreementType:
    """Classify a verification run's outcome.

    Per Section 6.2 the classification logic walks per-axis disagreement
    contributions and identifies whether (and which) axis dominated.
    Drift state and budget bound override individual axis classifications
    when set.

    Decision tree (Section 6.2):

    1. Budget-bound override: if ``budget_bound=True``,
       :attr:`DEGRADED_BUDGET_BOUND` regardless of axis disagreement.
    2. Drift-warning override: if ``drift_state.is_warning``,
       :attr:`DEGRADED` regardless of axis disagreement.
    3. No axes ran or all skipped: :attr:`UNKNOWN`.
    4. All axes' contributions < 10% of ``max_error_bar``:
       :attr:`CONVERGED`.
    5. Multi-axis simultaneous breach (>=2 axes contribute > 50% of
       ``max_error_bar``): :attr:`DEGRADED`.
    6. Single-axis dominator (one axis contributes > 50% of
       ``max_error_bar`` and others < 25%): name the dominator's
       specific specialization (cross_precision -> NUMERICAL_DRIFT,
       cross_control -> BACKACTION_SENSITIVE, cross_provider ->
       PROVIDER_DIVERGENT).
    7. Otherwise: :attr:`HEDGED_CONSENSUS` (axes don't converge cleanly
       but no single dominator; matches the vendored enum's "soft
       agreement" value).
    """
    if budget_bound:
        return PhoenixDisagreementType.DEGRADED_BUDGET_BOUND
    if drift_state is not None and drift_state.is_warning:
        return PhoenixDisagreementType.DEGRADED

    # Filter out skipped axes (zero contribution and skipped=True in metadata).
    active = [
        a
        for a in axis_results
        if not a.metadata.get("skipped", False) and a.error_bar_contribution >= 0.0
    ]
    if not active:
        return PhoenixDisagreementType.UNKNOWN

    # Per-axis dominance analysis.
    breach_threshold = 0.50 * max_error_bar
    minor_threshold = 0.25 * max_error_bar
    converged_threshold = 0.10 * max_error_bar

    breaches = [a for a in active if a.error_bar_contribution > breach_threshold]
    if len(breaches) >= 2:
        return PhoenixDisagreementType.DEGRADED

    if len(breaches) == 1:
        dominator = breaches[0]
        others = [a for a in active if a is not dominator]
        if all(a.error_bar_contribution < minor_threshold for a in others):
            # Single dominator; classify by axis name.
            return _axis_to_specialization(dominator.axis_name)
        # Dominator + at least one significant runner-up: degraded.
        return PhoenixDisagreementType.DEGRADED

    # No single axis dominates.
    if all(a.error_bar_contribution < converged_threshold for a in active):
        return PhoenixDisagreementType.CONVERGED

    return PhoenixDisagreementType.HEDGED_CONSENSUS


def _axis_to_specialization(axis_name: str) -> PhoenixDisagreementType:
    """Map an axis name to its Phoenix-native specialization."""
    return {
        "cross_precision": PhoenixDisagreementType.NUMERICAL_DRIFT,
        "cross_control": PhoenixDisagreementType.BACKACTION_SENSITIVE,
        "cross_provider": PhoenixDisagreementType.PROVIDER_DIVERGENT,
    }.get(axis_name, PhoenixDisagreementType.UNKNOWN)
