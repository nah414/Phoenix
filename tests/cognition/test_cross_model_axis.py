"""Tests for ``CrossModelAxis``."""

from __future__ import annotations

from typing import Any

import pytest

from phoenix.providers.cognition.capabilities import CognitionCapabilities
from phoenix.providers.cognition.types import (
    CognitionResult,
    Prompt,
    TokenUsage,
    Tool,
)
from cognition_wobble.disagreement_types import CognitionDisagreementType
from phoenix.verification.axes.cross_model import CrossModelAxis


class _StubProvider:
    """Returns a configurable text on each ``complete()`` call."""

    def __init__(
        self,
        *,
        provider_id: str,
        model: str,
        text: str,
        latency_ms: float = 10.0,
    ) -> None:
        self.provider_id = provider_id
        self.model = model
        self._text = text
        self._latency_ms = latency_ms

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
            usage=TokenUsage(input_tokens=5, output_tokens=10),
            latency_ms=self._latency_ms,
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


def _make_prompt() -> Prompt:
    return Prompt(system=None, messages=[{"role": "user", "content": "what is 2+2?"}])


def test_all_responses_agree_yields_zero_distance() -> None:
    """Identical responses → distance 0.0."""
    providers: list[Any] = [
        _StubProvider(provider_id="anthropic", model="claude-sonnet", text="4"),
        _StubProvider(provider_id="openai", model="gpt-4o", text="4"),
        _StubProvider(provider_id="google", model="gemini-2.5-pro", text="4"),
    ]
    axis = CrossModelAxis(providers=providers)

    metric = axis.run(_make_prompt())

    assert metric.distance == 0.0
    assert metric.disagreement_type == CognitionDisagreementType.UNCLASSIFIED
    assert len(metric.responses) == 3
    assert len(metric.provenance) == 3
    assert metric.metadata["n_providers"] == 3
    assert metric.metadata["distance_metric"] == "semantic_with_fallback"


def test_all_responses_differ_yields_max_distance() -> None:
    """Three distinct responses → all pairwise distances 1.0; mean 1.0."""
    providers: list[Any] = [
        _StubProvider(provider_id="anthropic", model="claude-sonnet", text="four"),
        _StubProvider(provider_id="openai", model="gpt-4o", text="4"),
        _StubProvider(provider_id="google", model="gemini-2.5-pro", text="2+2=4"),
    ]
    axis = CrossModelAxis(providers=providers)
    metric = axis.run(_make_prompt())
    assert metric.distance == pytest.approx(1.0)


def test_provenance_carries_per_provider_info() -> None:
    providers: list[Any] = [
        _StubProvider(provider_id="anthropic", model="claude-sonnet", text="x", latency_ms=12.5),
        _StubProvider(provider_id="openai", model="gpt-4o", text="x", latency_ms=8.0),
    ]
    axis = CrossModelAxis(providers=providers)
    metric = axis.run(_make_prompt())
    assert metric.provenance[0].provider_id == "anthropic"
    assert metric.provenance[0].latency_ms == 12.5
    assert metric.provenance[1].provider_id == "openai"
    assert metric.provenance[1].latency_ms == 8.0


def test_pairwise_matrix_correct_shape() -> None:
    providers: list[Any] = [
        _StubProvider(provider_id="anthropic", model="claude", text="a"),
        _StubProvider(provider_id="openai", model="gpt", text="b"),
        _StubProvider(provider_id="google", model="gemini", text="a"),
    ]
    axis = CrossModelAxis(providers=providers)
    metric = axis.run(_make_prompt())

    matrix = metric.pairwise_distance_matrix
    assert len(matrix) == 3
    assert all(len(row) == 3 for row in matrix)
    # texts: ["a", "b", "a"] → (0,2)=0, (0,1)=1, (1,2)=1
    assert matrix[0][2] == 0.0
    assert matrix[0][1] == 1.0
    assert matrix[1][2] == 1.0


def test_fewer_than_two_providers_raises() -> None:
    """A single-provider configuration raises at run() time."""
    providers: list[Any] = [
        _StubProvider(provider_id="anthropic", model="claude", text="x"),
    ]
    axis = CrossModelAxis(providers=providers)
    assert axis.applies_to(_make_prompt()) is False
    with pytest.raises(ValueError):
        axis.run(_make_prompt())


def test_temperature_threaded_through() -> None:
    """The temperature kwarg propagates to provenance entries."""
    providers: list[Any] = [
        _StubProvider(provider_id="a", model="m1", text="x"),
        _StubProvider(provider_id="b", model="m2", text="x"),
    ]
    axis = CrossModelAxis(providers=providers)
    metric = axis.run(_make_prompt(), temperature=0.7)
    for p in metric.provenance:
        assert p.temperature == 0.7
