"""Tests for the cognition classifier calibration eval framework."""

from __future__ import annotations

import pytest

from cognition_wobble.classifier import (
    AlwaysUnclassifiedClassifier,
    ClassificationResult,
    DEFAULT_CONFIDENCE_THRESHOLD,
)
from cognition_wobble.disagreement_types import GRADED_CLASSES, CognitionDisagreementType
from cognition_wobble.eval import (
    CalibrationExample,
    ClassMetrics,
    evaluate,
)
from phoenix.providers.cognition.types import CognitionResult, Prompt, TokenUsage


def _result(text: str) -> CognitionResult:
    return CognitionResult(
        text=text,
        tool_calls=[],
        usage=TokenUsage(input_tokens=1, output_tokens=1),
        latency_ms=1.0,
        provider_fingerprint="test|fixture",
    )


def _prompt(text: str) -> Prompt:
    return Prompt(system=None, messages=[{"role": "user", "content": text}])


def _example(gold: CognitionDisagreementType, *, tag: str = "") -> CalibrationExample:
    return CalibrationExample(
        prompt=_prompt(f"q-{tag}"),
        response_a=_result(f"a-{tag}"),
        response_b=_result(f"b-{tag}"),
        gold_class=gold,
        source_dataset="test",
    )


class _ScriptedClassifier:
    """Returns a pre-scripted sequence of predictions; for testing
    the eval framework with known classifier outputs."""

    version: str = "scripted-test-v1"

    def __init__(self, predictions: list[CognitionDisagreementType]) -> None:
        self._predictions = list(predictions)
        self._call = 0

    def classify(
        self,
        prompt: Prompt,
        responses: list[CognitionResult],
        *,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> ClassificationResult:
        del prompt, responses, confidence_threshold
        pred = self._predictions[self._call]
        self._call += 1
        return ClassificationResult(
            disagreement_type=pred,
            confidence=0.9,
            classifier_version=self.version,
        )


def test_perfect_classifier_yields_f1_one() -> None:
    """A classifier that predicts every gold class correctly: macro-F1 = 1.0."""
    examples = [
        _example(CognitionDisagreementType.FACTUAL_AGREEMENT, tag="fa"),
        _example(CognitionDisagreementType.STYLISTIC_DIVERGENCE, tag="sd"),
        _example(CognitionDisagreementType.FACTUAL_DISAGREEMENT, tag="fd"),
        _example(CognitionDisagreementType.INTERPRETIVE_DIVERGENCE, tag="id"),
        _example(CognitionDisagreementType.REFUSAL_DIVERGENCE, tag="rd"),
        _example(CognitionDisagreementType.TOOL_CHOICE_DIVERGENCE, tag="td"),
    ]
    predictions = [e.gold_class for e in examples]
    classifier = _ScriptedClassifier(predictions)

    report = evaluate(classifier, examples)
    assert report.macro_f1 == pytest.approx(1.0)
    assert report.macro_precision == pytest.approx(1.0)
    assert report.macro_recall == pytest.approx(1.0)
    for metrics in report.per_class.values():
        assert metrics.f1 == pytest.approx(1.0)


def test_always_unclassified_yields_zero_f1() -> None:
    """The always-UNCLASSIFIED stub gets macro-F1 = 0.0 on a graded set."""
    examples = [_example(c, tag=str(i)) for i, c in enumerate(GRADED_CLASSES)]
    report = evaluate(AlwaysUnclassifiedClassifier(), examples)
    assert report.macro_f1 == 0.0
    assert report.n_unclassified_predictions == len(examples)


def test_per_class_support_matches_gold_counts() -> None:
    """Support = number of gold examples of that class."""
    examples = [
        _example(CognitionDisagreementType.FACTUAL_AGREEMENT, tag="1"),
        _example(CognitionDisagreementType.FACTUAL_AGREEMENT, tag="2"),
        _example(CognitionDisagreementType.STYLISTIC_DIVERGENCE, tag="3"),
    ]
    predictions = [e.gold_class for e in examples]
    report = evaluate(_ScriptedClassifier(predictions), examples)
    assert report.per_class[CognitionDisagreementType.FACTUAL_AGREEMENT].support == 2
    assert report.per_class[CognitionDisagreementType.STYLISTIC_DIVERGENCE].support == 1


def test_unclassified_gold_excluded_from_grading() -> None:
    """Gold-UNCLASSIFIED rows don't contribute to any graded class's F1."""
    examples = [
        _example(CognitionDisagreementType.FACTUAL_AGREEMENT, tag="real"),
        _example(CognitionDisagreementType.UNCLASSIFIED, tag="amb"),
    ]
    predictions = [
        CognitionDisagreementType.FACTUAL_AGREEMENT,
        CognitionDisagreementType.UNCLASSIFIED,  # classifier abstained on the gold-UNCL row
    ]
    report = evaluate(_ScriptedClassifier(predictions), examples)
    # FACTUAL_AGREEMENT: TP=1, FP=0, FN=0 → F1=1.0
    assert report.per_class[CognitionDisagreementType.FACTUAL_AGREEMENT].f1 == pytest.approx(1.0)
    # Other graded classes: zero support; F1=0.0 (degenerate but stable)
    # Macro across 6 graded: 1.0/6 ≈ 0.1667
    assert report.macro_f1 == pytest.approx(1.0 / 6)
    assert report.n_examples == 2
    assert report.n_graded_examples == 1


def test_confusion_matrix_records_mismatches() -> None:
    """Confusion matrix entries are (gold, predicted)."""
    examples = [
        _example(CognitionDisagreementType.FACTUAL_AGREEMENT, tag="1"),
        _example(CognitionDisagreementType.FACTUAL_AGREEMENT, tag="2"),
    ]
    predictions = [
        CognitionDisagreementType.FACTUAL_AGREEMENT,
        CognitionDisagreementType.STYLISTIC_DIVERGENCE,  # misclassified
    ]
    report = evaluate(_ScriptedClassifier(predictions), examples)
    assert (
        report.confusion_matrix[
            (
                CognitionDisagreementType.FACTUAL_AGREEMENT,
                CognitionDisagreementType.FACTUAL_AGREEMENT,
            )
        ]
        == 1
    )
    assert (
        report.confusion_matrix[
            (
                CognitionDisagreementType.FACTUAL_AGREEMENT,
                CognitionDisagreementType.STYLISTIC_DIVERGENCE,
            )
        ]
        == 1
    )


def test_class_metrics_precision_recall_handcalculated() -> None:
    """Spot-check metrics computation on a known small case.

    Gold:    [FA, FA, SD]
    Pred:    [FA, SD, SD]
    For FACTUAL_AGREEMENT:
        TP = 1 (gold FA, pred FA)
        FP = 0 (no other class predicted as FA)
        FN = 1 (gold FA, pred SD)
        precision = 1/1 = 1.0
        recall = 1/2 = 0.5
        F1 = 2 * 1.0 * 0.5 / 1.5 = 0.6667
    For STYLISTIC_DIVERGENCE:
        TP = 1 (gold SD, pred SD)
        FP = 1 (gold FA, pred SD)
        FN = 0
        precision = 1/2 = 0.5
        recall = 1/1 = 1.0
        F1 = 2 * 0.5 * 1.0 / 1.5 = 0.6667
    """
    examples = [
        _example(CognitionDisagreementType.FACTUAL_AGREEMENT, tag="1"),
        _example(CognitionDisagreementType.FACTUAL_AGREEMENT, tag="2"),
        _example(CognitionDisagreementType.STYLISTIC_DIVERGENCE, tag="3"),
    ]
    predictions = [
        CognitionDisagreementType.FACTUAL_AGREEMENT,
        CognitionDisagreementType.STYLISTIC_DIVERGENCE,
        CognitionDisagreementType.STYLISTIC_DIVERGENCE,
    ]
    report = evaluate(_ScriptedClassifier(predictions), examples)

    fa = report.per_class[CognitionDisagreementType.FACTUAL_AGREEMENT]
    assert fa.precision == pytest.approx(1.0)
    assert fa.recall == pytest.approx(0.5)
    assert fa.f1 == pytest.approx(2.0 / 3.0)

    sd = report.per_class[CognitionDisagreementType.STYLISTIC_DIVERGENCE]
    assert sd.precision == pytest.approx(0.5)
    assert sd.recall == pytest.approx(1.0)
    assert sd.f1 == pytest.approx(2.0 / 3.0)


def test_classifier_version_propagated_to_report() -> None:
    """Report carries the classifier's version string at evaluation time."""
    classifier = _ScriptedClassifier([CognitionDisagreementType.FACTUAL_AGREEMENT])
    examples = [_example(CognitionDisagreementType.FACTUAL_AGREEMENT)]
    report = evaluate(classifier, examples)
    assert report.classifier_version == "scripted-test-v1"


def test_class_metrics_frozen() -> None:
    """ClassMetrics is frozen."""
    import dataclasses

    m = ClassMetrics(
        precision=0.5,
        recall=0.5,
        f1=0.5,
        support=1,
        true_positives=1,
        false_positives=1,
        false_negatives=1,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.precision = 1.0  # type: ignore[misc]
