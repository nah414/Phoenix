"""Sanity tests for the bootstrap calibration eval set."""

from __future__ import annotations

from collections import Counter

from cognition_wobble.calibration.bootstrap import BOOTSTRAP_EXAMPLES
from cognition_wobble.disagreement_types import GRADED_CLASSES, CognitionDisagreementType


def test_bootstrap_has_examples() -> None:
    assert len(BOOTSTRAP_EXAMPLES) > 0


def test_bootstrap_covers_every_graded_class() -> None:
    """Every graded class appears at least twice in the bootstrap set."""
    counts = Counter(e.gold_class for e in BOOTSTRAP_EXAMPLES)
    for cls in GRADED_CLASSES:
        assert counts[cls] >= 2, f"Bootstrap missing examples for {cls.name}"


def test_bootstrap_includes_unclassified_examples() -> None:
    """Bootstrap includes at least one gold-UNCLASSIFIED example so the
    eval framework's abstention path is exercised."""
    counts = Counter(e.gold_class for e in BOOTSTRAP_EXAMPLES)
    assert counts[CognitionDisagreementType.UNCLASSIFIED] >= 1


def test_bootstrap_source_dataset_tag() -> None:
    """Every example tags its source dataset."""
    for example in BOOTSTRAP_EXAMPLES:
        assert example.source_dataset != ""


def test_bootstrap_examples_are_immutable() -> None:
    """CalibrationExample is frozen."""
    import dataclasses
    import pytest

    example = BOOTSTRAP_EXAMPLES[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        example.gold_class = CognitionDisagreementType.UNCLASSIFIED  # type: ignore[misc]
