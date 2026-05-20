"""Tests for the CognitionClassifier Protocol + stub impl."""

from __future__ import annotations

from cognition_wobble.classifier import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    AlwaysUnclassifiedClassifier,
    ClassificationResult,
    CognitionClassifier,
)
from cognition_wobble.disagreement_types import CognitionDisagreementType
from phoenix.providers.cognition.types import CognitionResult, Prompt, TokenUsage


def _result(text: str) -> CognitionResult:
    return CognitionResult(
        text=text,
        tool_calls=[],
        usage=TokenUsage(input_tokens=1, output_tokens=1),
        latency_ms=1.0,
        provider_fingerprint="test|fixture",
    )


def test_stub_satisfies_protocol_runtime_checkable() -> None:
    """The stub conforms to the Protocol via @runtime_checkable."""
    stub = AlwaysUnclassifiedClassifier()
    assert isinstance(stub, CognitionClassifier)


def test_stub_always_returns_unclassified() -> None:
    """The stub returns UNCLASSIFIED regardless of inputs."""
    stub = AlwaysUnclassifiedClassifier()
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "anything"}])
    responses = [_result("a"), _result("b")]
    result = stub.classify(prompt, responses)
    assert result.disagreement_type is CognitionDisagreementType.UNCLASSIFIED
    assert result.confidence == 0.0
    assert result.escalated_to_judge is False


def test_stub_version_is_stable() -> None:
    """The stub's version string is stable across instances."""
    assert AlwaysUnclassifiedClassifier().version == "stub-always-unclassified-v1"
    assert AlwaysUnclassifiedClassifier().version == AlwaysUnclassifiedClassifier().version


def test_classification_result_carries_version() -> None:
    """The stub's ClassificationResult.classifier_version matches the stub."""
    stub = AlwaysUnclassifiedClassifier()
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "x"}])
    result = stub.classify(prompt, [_result("a"), _result("b")])
    assert result.classifier_version == stub.version


def test_classification_result_is_frozen() -> None:
    """ClassificationResult is immutable."""
    import dataclasses

    import pytest

    res = ClassificationResult(
        disagreement_type=CognitionDisagreementType.UNCLASSIFIED,
        confidence=0.0,
        classifier_version="test",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        res.confidence = 1.0  # type: ignore[misc]


def test_default_confidence_threshold_is_half() -> None:
    """The module-level default is 0.5 (documented invariant for Step 5b)."""
    assert DEFAULT_CONFIDENCE_THRESHOLD == 0.5
