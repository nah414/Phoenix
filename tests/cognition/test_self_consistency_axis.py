"""Tests for ``SelfConsistencyAxis``."""

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
from phoenix.verification.agreement_classifier import PhoenixDisagreementType
from phoenix.verification.axes.self_consistency import SelfConsistencyAxis


class _DeterministicProvider:
    """Returns a text computed from the temperature parameter.

    Lets tests deterministically assert which-temperature-produced-which
    response.
    """

    provider_id = "test.deterministic"
    model = "fake-model"

    def __init__(self, *, text_fn: Any) -> None:
        self._text_fn = text_fn
        self._calls: list[float] = []

    def complete(
        self,
        prompt: Prompt,
        *,
        max_tokens: int,
        temperature: float,
        tools: list[Tool] | None = None,
        stream: bool = False,
    ) -> CognitionResult:
        del prompt, max_tokens, tools, stream
        self._calls.append(temperature)
        return CognitionResult(
            text=self._text_fn(temperature),
            tool_calls=[],
            usage=TokenUsage(input_tokens=5, output_tokens=5),
            latency_ms=1.0,
            provider_fingerprint=f"{self.provider_id}|{self.model}|t={temperature}",
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
        return f"{self.provider_id}|{self.model}"


def _prompt() -> Prompt:
    return Prompt(system=None, messages=[{"role": "user", "content": "explain entropy"}])


def test_default_temperatures_used() -> None:
    """No temperatures kwarg → defaults to [0.0, 0.5, 0.7]."""
    provider = _DeterministicProvider(text_fn=lambda t: f"answer@{t}")
    axis = SelfConsistencyAxis(provider=provider)
    axis.run(_prompt())
    assert provider._calls == [0.0, 0.5, 0.7]


def test_custom_temperatures() -> None:
    """Custom temperatures determine the dispatch sequence."""
    provider = _DeterministicProvider(text_fn=lambda t: f"answer@{t}")
    axis = SelfConsistencyAxis(provider=provider, temperatures=[0.0, 0.2, 0.4, 0.6])
    metric = axis.run(_prompt())
    assert len(metric.responses) == 4
    assert provider._calls == [0.0, 0.2, 0.4, 0.6]


def test_all_responses_identical_yields_zero() -> None:
    """Same response across temperatures → distance 0.0."""
    provider = _DeterministicProvider(text_fn=lambda t: "fixed answer")
    axis = SelfConsistencyAxis(provider=provider, temperatures=[0.0, 0.5, 0.7])
    metric = axis.run(_prompt())
    assert metric.distance == 0.0


def test_all_responses_differ_yields_max() -> None:
    """Different response per temperature → distance 1.0."""
    provider = _DeterministicProvider(text_fn=lambda t: f"answer@{t}")
    axis = SelfConsistencyAxis(provider=provider, temperatures=[0.0, 0.5, 0.7])
    metric = axis.run(_prompt())
    assert metric.distance == pytest.approx(1.0)


def test_disagreement_type_unclassified() -> None:
    provider = _DeterministicProvider(text_fn=lambda t: "x")
    axis = SelfConsistencyAxis(provider=provider, temperatures=[0.0, 0.5])
    metric = axis.run(_prompt())
    assert metric.disagreement_type == PhoenixDisagreementType.COGNITION_UNCLASSIFIED


def test_metadata_carries_temperatures() -> None:
    provider = _DeterministicProvider(text_fn=lambda t: "x")
    temps = [0.0, 0.3, 0.6, 0.9]
    axis = SelfConsistencyAxis(provider=provider, temperatures=temps)
    metric = axis.run(_prompt())
    assert metric.metadata["temperatures"] == temps
    assert metric.metadata["n_samples"] == 4


def test_single_temperature_trivial() -> None:
    """N=1 is allowed; distance is 0.0 (no pairwise comparison possible)."""
    provider = _DeterministicProvider(text_fn=lambda t: "x")
    axis = SelfConsistencyAxis(provider=provider, temperatures=[0.0])
    metric = axis.run(_prompt())
    assert metric.distance == 0.0
    assert len(metric.responses) == 1


def test_provenance_per_temperature() -> None:
    """Provenance row's temperature matches the dispatch."""
    provider = _DeterministicProvider(text_fn=lambda t: f"@{t}")
    axis = SelfConsistencyAxis(provider=provider, temperatures=[0.0, 0.7])
    metric = axis.run(_prompt())
    assert metric.provenance[0].temperature == 0.0
    assert metric.provenance[1].temperature == 0.7


def test_empty_temperatures_raises() -> None:
    provider = _DeterministicProvider(text_fn=lambda t: "x")
    axis = SelfConsistencyAxis(provider=provider, temperatures=[])
    assert axis.applies_to(_prompt()) is False
    with pytest.raises(ValueError):
        axis.run(_prompt())
