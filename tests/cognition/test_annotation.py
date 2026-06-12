"""Tests for the Step 5c LLM-judge labeler (scripted classifier; no API keys)."""

from __future__ import annotations

from cognition_wobble.annotation import (
    label_pairs,
    labeled_examples,
    verify_summary,
)
from cognition_wobble.classifier import DEFAULT_CONFIDENCE_THRESHOLD, ClassificationResult
from cognition_wobble.disagreement_types import CognitionDisagreementType as C
from cognition_wobble.eval import CalibrationExample
from phoenix.providers.cognition.types import CognitionResult, Prompt, TokenUsage


class _ScriptedJudge:
    """Returns a pre-scripted (class, confidence) sequence."""

    version = "scripted-judge-v1"

    def __init__(self, results: list[tuple[C, float]]) -> None:
        self._results = list(results)
        self._i = 0

    def classify(self, prompt, responses, *, confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD):  # noqa: ANN001, ANN002, ANN003
        cls, conf = self._results[self._i]
        self._i += 1
        return ClassificationResult(
            disagreement_type=cls, confidence=conf, classifier_version=self.version
        )


def _result(text: str) -> CognitionResult:
    return CognitionResult(
        text=text,
        tool_calls=[],
        usage=TokenUsage(input_tokens=1, output_tokens=1),
        latency_ms=0.0,
        provider_fingerprint="test|fixture",
    )


def _unlabeled(n: int) -> list[CalibrationExample]:
    return [
        CalibrationExample(
            prompt=Prompt(system=None, messages=[{"role": "user", "content": f"q{i}"}]),
            response_a=_result(f"a{i}"),
            response_b=_result(f"b{i}"),
            gold_class=C.UNCLASSIFIED,  # placeholder
            source_dataset="phoenix-generated",
            annotation_notes="[target=x] UNLABELED",
        )
        for i in range(n)
    ]


def test_label_assigns_judge_class_and_records_provenance() -> None:
    judge = _ScriptedJudge([(C.FACTUAL_AGREEMENT, 0.9)])
    labeled = label_pairs(_unlabeled(1), judge)
    lp = labeled[0]
    assert lp.judge_class is C.FACTUAL_AGREEMENT
    assert lp.example.gold_class is C.FACTUAL_AGREEMENT
    assert "judge=factual_agreement" in lp.example.annotation_notes
    assert "conf=0.90" in lp.example.annotation_notes
    assert "scripted-judge-v1" in lp.example.annotation_notes


def test_confident_factual_class_does_not_need_verify() -> None:
    judge = _ScriptedJudge([(C.FACTUAL_AGREEMENT, 0.95)])
    labeled = label_pairs(_unlabeled(1), judge, min_confidence=0.5)
    assert labeled[0].needs_verify is False
    assert "NEEDS-VERIFY" not in labeled[0].example.annotation_notes


def test_hard_class_always_needs_verify_even_when_confident() -> None:
    judge = _ScriptedJudge([(C.REFUSAL_DIVERGENCE, 0.99)])
    labeled = label_pairs(_unlabeled(1), judge, min_confidence=0.5)
    assert labeled[0].needs_verify is True
    assert "NEEDS-VERIFY" in labeled[0].example.annotation_notes


def test_low_confidence_needs_verify() -> None:
    judge = _ScriptedJudge([(C.FACTUAL_AGREEMENT, 0.3)])
    labeled = label_pairs(_unlabeled(1), judge, min_confidence=0.5)
    assert labeled[0].needs_verify is True


def test_unclassified_needs_verify() -> None:
    judge = _ScriptedJudge([(C.UNCLASSIFIED, 0.8)])
    labeled = label_pairs(_unlabeled(1), judge)
    assert labeled[0].needs_verify is True


def test_verify_summary_and_extraction() -> None:
    judge = _ScriptedJudge(
        [
            (C.FACTUAL_AGREEMENT, 0.9),  # no verify
            (C.REFUSAL_DIVERGENCE, 0.9),  # hard -> verify
            (C.FACTUAL_DISAGREEMENT, 0.2),  # low conf -> verify
        ]
    )
    labeled = label_pairs(_unlabeled(3), judge, min_confidence=0.5)
    summary = verify_summary(labeled)
    assert summary["total"] == 3
    assert summary["needs_verify"] == 2
    assert summary["factual_agreement"] == 1
    assert summary["refusal_divergence"] == 1
    examples = labeled_examples(labeled)
    assert len(examples) == 3
    assert all(isinstance(e, CalibrationExample) for e in examples)


def test_verify_summary_includes_all_classes_even_when_absent() -> None:
    judge = _ScriptedJudge([(C.FACTUAL_AGREEMENT, 0.9)])
    summary = verify_summary(label_pairs(_unlabeled(1), judge))
    # every taxonomy class is present (0 when absent) -> no KeyError for callers
    for cls in C:
        assert cls.value in summary
    assert summary["tool_choice_divergence"] == 0
    assert summary["factual_agreement"] == 1
