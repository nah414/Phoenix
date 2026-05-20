"""Phase 5 Step 2 -- agreement_classifier extension + decision tree."""

from __future__ import annotations

import phoenix  # noqa: F401  -- triggers sys.path injection for vendored modules


def _axis(name: str, eb: float, *, skipped: bool = False):
    from phoenix.verification.wobble_axis import AxisResult

    return AxisResult(
        axis_name=name,
        error_bar_contribution=eb,
        metadata={"skipped": skipped},
    )


def test_phoenix_disagreement_type_extends_vendored_values() -> None:
    """Section 6.2: PhoenixDisagreementType mirrors vendored values + adds extensions."""
    from phoenix.verification.agreement_classifier import PhoenixDisagreementType

    expected_values = {
        # vendored mirrors
        "contradiction",
        "temporal_drift",
        "frame_mismatch",
        "hedged_consensus",
        "unknown",
        # Phoenix extensions
        "converged",
        "numerical_drift",
        "backaction_sensitive",
        "provider_divergent",
        "degraded",
        "degraded_budget_bound",
        # Phase 13 Step 4 cognition extension (per Phase 13 build guide
        # Section 4.4): cognition wobble axes default to this when the
        # classifier hasn't been wired in yet.
        "cognition_unclassified",
    }
    actual = {m.value for m in PhoenixDisagreementType}
    assert actual == expected_values


def test_classify_budget_bound_overrides_axes() -> None:
    from phoenix.verification.agreement_classifier import (
        PhoenixDisagreementType,
        classify,
    )

    result = classify(
        [_axis("cross_precision", 1e-9)],  # below tolerance
        max_error_bar=1e-3,
        budget_bound=True,
    )
    assert result is PhoenixDisagreementType.DEGRADED_BUDGET_BOUND


def test_classify_drift_warning_overrides_axes() -> None:
    from phoenix.verification.agreement_classifier import (
        PhoenixDisagreementType,
        classify,
    )
    from phoenix.verification.drift_state import DriftState

    result = classify(
        [_axis("cross_precision", 1e-9)],
        max_error_bar=1e-3,
        drift_state=DriftState(state="warning"),
    )
    assert result is PhoenixDisagreementType.DEGRADED


def test_classify_all_axes_below_10_percent_returns_converged() -> None:
    from phoenix.verification.agreement_classifier import (
        PhoenixDisagreementType,
        classify,
    )

    # All axes < 10% of 1e-3 (i.e. < 1e-4)
    result = classify(
        [
            _axis("cross_precision", 5e-5),
            _axis("cross_control", 2e-5),
        ],
        max_error_bar=1e-3,
    )
    assert result is PhoenixDisagreementType.CONVERGED


def test_classify_single_axis_dominator_maps_to_specialization() -> None:
    """Cross-precision dominator -> NUMERICAL_DRIFT."""
    from phoenix.verification.agreement_classifier import (
        PhoenixDisagreementType,
        classify,
    )

    result = classify(
        [
            _axis("cross_precision", 6e-4),  # > 50% of 1e-3
            _axis("cross_control", 1e-5),  # < 25%
        ],
        max_error_bar=1e-3,
    )
    assert result is PhoenixDisagreementType.NUMERICAL_DRIFT


def test_classify_two_dominators_returns_degraded() -> None:
    from phoenix.verification.agreement_classifier import (
        PhoenixDisagreementType,
        classify,
    )

    result = classify(
        [
            _axis("cross_precision", 6e-4),
            _axis("cross_control", 7e-4),
        ],
        max_error_bar=1e-3,
    )
    assert result is PhoenixDisagreementType.DEGRADED


def test_classify_all_skipped_returns_unknown() -> None:
    from phoenix.verification.agreement_classifier import (
        PhoenixDisagreementType,
        classify,
    )

    result = classify(
        [_axis("cross_precision", 0.0, skipped=True)],
        max_error_bar=1e-3,
    )
    assert result is PhoenixDisagreementType.UNKNOWN
