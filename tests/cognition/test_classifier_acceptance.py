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
