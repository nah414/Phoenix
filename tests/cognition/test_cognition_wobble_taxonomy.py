"""Tests for the CognitionDisagreementType taxonomy."""

from __future__ import annotations

from cognition_wobble.disagreement_types import GRADED_CLASSES, CognitionDisagreementType


def test_taxonomy_has_seven_values() -> None:
    """Six graded classes + UNCLASSIFIED."""
    assert len(CognitionDisagreementType) == 7


def test_graded_classes_excludes_unclassified() -> None:
    """UNCLASSIFIED is the escape valve, never in GRADED_CLASSES."""
    assert CognitionDisagreementType.UNCLASSIFIED not in GRADED_CLASSES
    assert len(GRADED_CLASSES) == 6


def test_graded_classes_has_each_classified_value() -> None:
    """All six graded classes are in the GRADED_CLASSES set."""
    expected = {
        CognitionDisagreementType.FACTUAL_AGREEMENT,
        CognitionDisagreementType.STYLISTIC_DIVERGENCE,
        CognitionDisagreementType.FACTUAL_DISAGREEMENT,
        CognitionDisagreementType.INTERPRETIVE_DIVERGENCE,
        CognitionDisagreementType.REFUSAL_DIVERGENCE,
        CognitionDisagreementType.TOOL_CHOICE_DIVERGENCE,
    }
    assert GRADED_CLASSES == expected


def test_values_are_stable_strings() -> None:
    """Enum values are stable strings for ledger/JSON serialization."""
    assert CognitionDisagreementType.FACTUAL_AGREEMENT.value == "factual_agreement"
    assert CognitionDisagreementType.UNCLASSIFIED.value == "unclassified"
