"""Tests for the HybridClassifier (compose primary + LLM-judge)."""

from __future__ import annotations


import pytest

from cognition_wobble.classifier import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    ClassificationResult,
)
from cognition_wobble.classifier_hybrid import HybridClassifier
from cognition_wobble.disagreement_types import CognitionDisagreementType
from phoenix.providers.cognition.types import CognitionResult, Prompt, TokenUsage


class _ScriptedClassifier:
    """Returns a pre-configured ClassificationResult on each call."""

    def __init__(self, *, version: str, result: ClassificationResult) -> None:
        self.version = version
        self._result = result
        self.call_count = 0

    def classify(
        self,
        prompt: Prompt,
        responses: list[CognitionResult],
        *,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> ClassificationResult:
        del prompt, responses, confidence_threshold
        self.call_count += 1
        return self._result


def _r(text: str = "x") -> CognitionResult:
    return CognitionResult(
        text=text,
        tool_calls=[],
        usage=TokenUsage(input_tokens=1, output_tokens=1),
        latency_ms=1.0,
        provider_fingerprint="test|fixture",
    )


def _p() -> Prompt:
    return Prompt(system=None, messages=[{"role": "user", "content": "q"}])


def test_primary_confident_no_escalation() -> None:
    """When primary returns a definite class, judge is NOT called."""
    primary = _ScriptedClassifier(
        version="gbm-test",
        result=ClassificationResult(
            disagreement_type=CognitionDisagreementType.FACTUAL_AGREEMENT,
            confidence=0.92,
            classifier_version="gbm-test",
            feature_values={"semantic_distance": 0.1},
        ),
    )
    judge = _ScriptedClassifier(
        version="judge-test",
        result=ClassificationResult(
            disagreement_type=CognitionDisagreementType.FACTUAL_DISAGREEMENT,
            confidence=0.99,
            classifier_version="judge-test",
        ),
    )
    hybrid = HybridClassifier(primary=primary, judge=judge)
    result = hybrid.classify(_p(), [_r("a"), _r("b")])

    assert result.disagreement_type is CognitionDisagreementType.FACTUAL_AGREEMENT
    assert result.escalated_to_judge is False
    assert primary.call_count == 1
    assert judge.call_count == 0


def test_primary_unclassified_escalates_to_judge() -> None:
    """When primary returns UNCLASSIFIED, judge IS called."""
    primary = _ScriptedClassifier(
        version="gbm-test",
        result=ClassificationResult(
            disagreement_type=CognitionDisagreementType.UNCLASSIFIED,
            confidence=0.4,
            classifier_version="gbm-test",
            feature_values={"semantic_distance": 0.55},
        ),
    )
    judge = _ScriptedClassifier(
        version="judge-test",
        result=ClassificationResult(
            disagreement_type=CognitionDisagreementType.STYLISTIC_DIVERGENCE,
            confidence=0.85,
            classifier_version="judge-test",
        ),
    )
    hybrid = HybridClassifier(primary=primary, judge=judge)
    result = hybrid.classify(_p(), [_r("a"), _r("b")])

    assert result.disagreement_type is CognitionDisagreementType.STYLISTIC_DIVERGENCE
    assert result.escalated_to_judge is True
    assert result.confidence == pytest.approx(0.85)
    # Primary's feature values are preserved for debugging.
    assert result.feature_values.get("semantic_distance") == 0.55
    assert primary.call_count == 1
    assert judge.call_count == 1


def test_judge_also_unclassified() -> None:
    """Both primary and judge return UNCLASSIFIED → result is UNCLASSIFIED."""
    primary = _ScriptedClassifier(
        version="gbm-test",
        result=ClassificationResult(
            disagreement_type=CognitionDisagreementType.UNCLASSIFIED,
            confidence=0.3,
            classifier_version="gbm-test",
        ),
    )
    judge = _ScriptedClassifier(
        version="judge-test",
        result=ClassificationResult(
            disagreement_type=CognitionDisagreementType.UNCLASSIFIED,
            confidence=0.3,
            classifier_version="judge-test",
        ),
    )
    hybrid = HybridClassifier(primary=primary, judge=judge)
    result = hybrid.classify(_p(), [_r("a"), _r("b")])

    assert result.disagreement_type is CognitionDisagreementType.UNCLASSIFIED
    assert result.escalated_to_judge is True


def test_hybrid_version_composes_both() -> None:
    """Hybrid version string contains both wrapped classifiers' versions."""
    primary = _ScriptedClassifier(
        version="gbm-v1",
        result=ClassificationResult(
            disagreement_type=CognitionDisagreementType.UNCLASSIFIED,
            confidence=0.0,
            classifier_version="gbm-v1",
        ),
    )
    judge = _ScriptedClassifier(
        version="judge-v2",
        result=ClassificationResult(
            disagreement_type=CognitionDisagreementType.UNCLASSIFIED,
            confidence=0.0,
            classifier_version="judge-v2",
        ),
    )
    hybrid = HybridClassifier(primary=primary, judge=judge)
    assert "gbm-v1" in hybrid.version
    assert "judge-v2" in hybrid.version
