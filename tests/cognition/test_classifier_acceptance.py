"""Tests for the Step 5c acceptance gate, report formatter, and the
dependency-free training-matrix builder.

No lightgbm required; always runs. The lightgbm-gated training itself is
covered in ``test_train_cognition_classifier.py``.
"""

from __future__ import annotations

import pytest

from cognition_wobble.acceptance import (
    ACCEPTANCE_MACRO_F1,
    GateResult,
    check_gate,
    format_confusion_matrix,
    format_report,
)
from cognition_wobble.calibration.synthetic import generate_synthetic_corpus
from cognition_wobble.classifier import AlwaysUnclassifiedClassifier, DEFAULT_CONFIDENCE_THRESHOLD
from cognition_wobble.classifier_gbm import _GBM_CLASS_ORDER
from cognition_wobble.disagreement_types import GRADED_CLASSES, CognitionDisagreementType
from cognition_wobble.eval import CalibrationExample, evaluate
from cognition_wobble.features import FEATURE_NAMES
from cognition_wobble.training import build_training_matrix
from phoenix.providers.cognition.types import CognitionResult, Prompt, TokenUsage


# ---------------------------------------------------------------------------
# helpers


def _result(text: str) -> CognitionResult:
    return CognitionResult(
        text=text,
        tool_calls=[],
        usage=TokenUsage(input_tokens=1, output_tokens=1),
        latency_ms=0.0,
        provider_fingerprint="test|fixture",
    )


def _prompt() -> Prompt:
    return Prompt(system=None, messages=[{"role": "user", "content": "q"}])


def _example(gold: CognitionDisagreementType) -> CalibrationExample:
    return CalibrationExample(
        prompt=_prompt(), response_a=_result("a"), response_b=_result("b"), gold_class=gold
    )


class _OracleClassifier:
    """Perfect classifier: returns each example's gold class in order.

    Lets us exercise the gate-PASS path without lightgbm — a perfect
    classifier on any graded set yields macro-F1 == 1.0.
    """

    version = "oracle-test-v1"

    def __init__(self, golds: list[CognitionDisagreementType]) -> None:
        self._golds = list(golds)
        self._i = 0

    def classify(self, prompt, responses, *, confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD):
        from cognition_wobble.classifier import ClassificationResult

        gold = self._golds[self._i]
        self._i += 1
        return ClassificationResult(
            disagreement_type=gold, confidence=1.0, classifier_version=self.version
        )


# ---------------------------------------------------------------------------
# gate threshold + check_gate


def test_acceptance_threshold_is_070() -> None:
    assert ACCEPTANCE_MACRO_F1 == 0.70


def test_gate_fails_for_stub() -> None:
    examples = [_example(c) for c in _GBM_CLASS_ORDER]
    report = evaluate(AlwaysUnclassifiedClassifier(), examples)
    gate = check_gate(report)
    assert isinstance(gate, GateResult)
    assert gate.passed is False
    assert gate.macro_f1 == 0.0
    assert gate.threshold == ACCEPTANCE_MACRO_F1
    assert gate.n_graded_examples == len(_GBM_CLASS_ORDER)


def test_gate_passes_for_perfect_classifier() -> None:
    golds = [c for c in _GBM_CLASS_ORDER for _ in range(3)]
    examples = [_example(g) for g in golds]
    report = evaluate(_OracleClassifier(golds), examples)
    gate = check_gate(report)
    assert gate.passed is True
    assert gate.macro_f1 == pytest.approx(1.0)


def test_gate_respects_custom_threshold() -> None:
    golds = [c for c in _GBM_CLASS_ORDER]
    examples = [_example(g) for g in golds]
    report = evaluate(_OracleClassifier(golds), examples)  # macro_f1 == 1.0
    assert check_gate(report, threshold=0.99).passed is True
    assert check_gate(report, threshold=1.01).passed is False


# ---------------------------------------------------------------------------
# format_report


def test_format_report_shows_fail_verdict() -> None:
    examples = [_example(c) for c in _GBM_CLASS_ORDER]
    text = format_report(evaluate(AlwaysUnclassifiedClassifier(), examples))
    assert "FAIL" in text
    assert "MACRO" in text
    for cls in GRADED_CLASSES:
        assert cls.value in text


def test_format_report_shows_pass_verdict() -> None:
    golds = [c for c in _GBM_CLASS_ORDER]
    text = format_report(evaluate(_OracleClassifier(golds), [_example(g) for g in golds]))
    assert "PASS" in text


# ---------------------------------------------------------------------------
# build_training_matrix (dependency-free)


def test_training_matrix_drops_unclassified() -> None:
    examples = generate_synthetic_corpus(n_per_class=2, n_unclassified=5)
    data = build_training_matrix(examples)
    assert data.n_dropped_unclassified == 5
    assert len(data.x) == len(data.y) == 12  # 6 graded * 2
    # feature rows are pinned-order, full-width
    assert all(len(row) == len(FEATURE_NAMES) for row in data.x)
    # labels index into the GBM class order
    assert set(data.y) <= set(range(len(_GBM_CLASS_ORDER)))
    assert data.feature_names == FEATURE_NAMES
    assert data.class_order == tuple(c.value for c in _GBM_CLASS_ORDER)


def test_training_matrix_label_mapping_matches_class_order() -> None:
    # A single example per class, in class order -> labels 0..5 in order.
    examples = [_example(c) for c in _GBM_CLASS_ORDER]
    data = build_training_matrix(examples)
    assert data.y == list(range(len(_GBM_CLASS_ORDER)))


def test_training_matrix_raises_when_all_unclassified() -> None:
    examples = generate_synthetic_corpus(n_per_class=0, n_unclassified=4)
    with pytest.raises(ValueError, match="no graded training examples"):
        build_training_matrix(examples)


# ---------------------------------------------------------------------------
# format_confusion_matrix


def test_confusion_matrix_perfect_classifier_is_diagonal() -> None:
    golds = [c for c in _GBM_CLASS_ORDER for _ in range(3)]
    examples = [_example(g) for g in golds]
    report = evaluate(_OracleClassifier(golds), examples)
    text = format_confusion_matrix(report)
    assert "confusion matrix" in text
    assert "legend:" in text
    for cls in GRADED_CLASSES:
        assert cls.value in text
    # A perfect classifier puts each gold row's mass (3) on the diagonal; every
    # row's trailing UNCL column is therefore 0.
    fa_row = next(li for li in text.splitlines() if li.startswith("factual_agreement"))
    assert fa_row.split()[-1] == "0"  # UNCL column
    assert "3" in fa_row  # the diagonal cell


def test_confusion_matrix_stub_lands_in_unclassified_column() -> None:
    examples = [_example(c) for c in _GBM_CLASS_ORDER]
    report = evaluate(AlwaysUnclassifiedClassifier(), examples)
    text = format_confusion_matrix(report)
    # Every gold row's single example is predicted UNCLASSIFIED -> last column.
    fa_row = next(li for li in text.splitlines() if li.startswith("factual_agreement"))
    assert fa_row.split()[-1] == "1"


def test_confusion_matrix_skips_empty_gold_rows() -> None:
    # Only FACTUAL_AGREEMENT golds -> only that gold row should appear.
    examples = [_example(CognitionDisagreementType.FACTUAL_AGREEMENT) for _ in range(3)]
    report = evaluate(
        _OracleClassifier([CognitionDisagreementType.FACTUAL_AGREEMENT] * 3), examples
    )
    text = format_confusion_matrix(report)
    assert "factual_agreement" in text
    # a gold row is a line that STARTS with the class name; only FA has golds, so
    # no other class should appear as a row (the legend mentions them mid-line).
    assert any(li.startswith("factual_agreement") for li in text.splitlines())
    assert not any(li.startswith("refusal_divergence") for li in text.splitlines())
