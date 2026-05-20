"""Integration tests for axes + classifier (Step 5b)."""

from __future__ import annotations

from typing import Any

from cognition_wobble.classifier import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    ClassificationResult,
)
from cognition_wobble.disagreement_types import CognitionDisagreementType
from phoenix.providers.cognition.capabilities import CognitionCapabilities
from phoenix.providers.cognition.types import (
    CognitionResult,
    Prompt,
    TokenUsage,
    Tool,
)
from phoenix.verification.axes.cross_model import CrossModelAxis
from phoenix.verification.axes.self_consistency import SelfConsistencyAxis


class _StubProvider:
    def __init__(self, *, provider_id: str, text: str) -> None:
        self.provider_id = provider_id
        self.model = "fake-model"
        self._text = text

    def complete(
        self,
        prompt: Prompt,
        *,
        max_tokens: int,
        temperature: float,
        tools: list[Tool] | None = None,
        stream: bool = False,
    ) -> CognitionResult:
        del prompt, max_tokens, temperature, tools, stream
        return CognitionResult(
            text=self._text,
            tool_calls=[],
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            latency_ms=1.0,
            provider_fingerprint=f"{self.provider_id}|{self.model}|test",
        )

    def capabilities(self) -> CognitionCapabilities:
        return CognitionCapabilities(
            streaming=False,
            tool_use=False,
            vision=False,
            max_context_tokens=4096,
            supports_prompt_cache=False,
            supports_batch=False,
        )

    def fingerprint(self) -> str:
        return f"{self.provider_id}|{self.model}|test"


class _ConstantClassifier:
    """Returns a fixed ClassificationResult on every call."""

    def __init__(
        self,
        *,
        version: str,
        cls: CognitionDisagreementType,
        confidence: float,
    ) -> None:
        self.version = version
        self._cls = cls
        self._confidence = confidence

    def classify(
        self,
        prompt: Prompt,
        responses: list[CognitionResult],
        *,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> ClassificationResult:
        del prompt, responses, confidence_threshold
        return ClassificationResult(
            disagreement_type=self._cls,
            confidence=self._confidence,
            classifier_version=self.version,
        )


def _prompt() -> Prompt:
    return Prompt(system=None, messages=[{"role": "user", "content": "q"}])


def test_cross_model_without_classifier_emits_unclassified() -> None:
    providers: list[Any] = [
        _StubProvider(provider_id="a", text="hello"),
        _StubProvider(provider_id="b", text="hello"),
    ]
    axis = CrossModelAxis(providers=providers)
    metric = axis.run(_prompt())
    assert metric.disagreement_type is CognitionDisagreementType.UNCLASSIFIED
    assert metric.classifier_confidence is None
    assert metric.classifier_version is None


def test_cross_model_with_classifier_populates_class() -> None:
    providers: list[Any] = [
        _StubProvider(provider_id="a", text="The capital is Paris."),
        _StubProvider(provider_id="b", text="Paris is the capital."),
    ]
    classifier: Any = _ConstantClassifier(
        version="test-classifier-v1",
        cls=CognitionDisagreementType.FACTUAL_AGREEMENT,
        confidence=0.91,
    )
    axis = CrossModelAxis(providers=providers, classifier=classifier)
    metric = axis.run(_prompt())
    assert metric.disagreement_type is CognitionDisagreementType.FACTUAL_AGREEMENT
    assert metric.classifier_confidence == 0.91
    assert metric.classifier_version == "test-classifier-v1"
    assert metric.metadata["classifier_attached"] is True


def test_self_consistency_with_classifier_populates_class() -> None:
    provider: Any = _StubProvider(provider_id="a", text="answer")
    classifier: Any = _ConstantClassifier(
        version="test-classifier-v2",
        cls=CognitionDisagreementType.STYLISTIC_DIVERGENCE,
        confidence=0.78,
    )
    axis = SelfConsistencyAxis(
        provider=provider,
        temperatures=[0.0, 0.5],
        classifier=classifier,
    )
    metric = axis.run(_prompt())
    assert metric.disagreement_type is CognitionDisagreementType.STYLISTIC_DIVERGENCE
    assert metric.classifier_confidence == 0.78


def test_self_consistency_n1_skips_classifier() -> None:
    """With only 1 dispatch, classifier needs >= 2 responses; axis emits UNCLASSIFIED."""
    provider: Any = _StubProvider(provider_id="a", text="x")
    classifier: Any = _ConstantClassifier(
        version="ignored",
        cls=CognitionDisagreementType.FACTUAL_AGREEMENT,
        confidence=1.0,
    )
    axis = SelfConsistencyAxis(provider=provider, temperatures=[0.0], classifier=classifier)
    metric = axis.run(_prompt())
    # N=1 → no pairwise comparison → classifier not invoked
    assert metric.disagreement_type is CognitionDisagreementType.UNCLASSIFIED


def test_metadata_distance_metric_label_updated() -> None:
    """Step 5b updates the metadata label from 'exact_string' to 'semantic_with_fallback'."""
    providers: list[Any] = [
        _StubProvider(provider_id="a", text="x"),
        _StubProvider(provider_id="b", text="x"),
    ]
    axis = CrossModelAxis(providers=providers)
    metric = axis.run(_prompt())
    assert metric.metadata["distance_metric"] == "semantic_with_fallback"
