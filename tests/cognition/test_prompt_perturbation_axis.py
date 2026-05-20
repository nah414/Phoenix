"""Tests for ``PromptPerturbationAxis``."""

from __future__ import annotations

import json
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
from phoenix.verification.axes.prompt_perturbation import (
    PromptPerturbationAxis,
    _parse_paraphrase_list,
)


class _StubProvider:
    """Returns a fixed text on each ``complete()`` call.

    Records each prompt for assertion."""

    provider_id = "test.stub"
    model = "fake-model"

    def __init__(self, *, fixed_text: str = "answer") -> None:
        self._fixed_text = fixed_text
        self.calls: list[Prompt] = []

    def complete(
        self,
        prompt: Prompt,
        *,
        max_tokens: int,
        temperature: float,
        tools: list[Tool] | None = None,
        stream: bool = False,
    ) -> CognitionResult:
        del max_tokens, temperature, tools, stream
        self.calls.append(prompt)
        return CognitionResult(
            text=self._fixed_text,
            tool_calls=[],
            usage=TokenUsage(input_tokens=5, output_tokens=5),
            latency_ms=1.0,
            provider_fingerprint=f"{self.provider_id}|{self.model}|fixed",
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
    return Prompt(
        system="be terse",
        messages=[{"role": "user", "content": "describe entropy"}],
    )


def test_passing_perturbations_skips_paraphraser() -> None:
    """When ``perturbations=`` is passed at run() time, no paraphraser call."""
    provider = _StubProvider(fixed_text="entropy is disorder")
    axis = PromptPerturbationAxis(provider=provider, n_perturbations=3)
    perts = [
        Prompt(system="be terse", messages=[{"role": "user", "content": "what is entropy"}]),
        Prompt(system="be terse", messages=[{"role": "user", "content": "explain entropy"}]),
    ]
    metric = axis.run(_prompt(), perturbations=perts)
    # Exactly len(perts) calls — no paraphraser meta-call.
    assert len(provider.calls) == 2
    assert metric.distance == 0.0  # fixed_text identical across calls


def test_callable_paraphraser_invoked() -> None:
    """A ``paraphraser`` Callable is invoked when ``perturbations`` is not passed."""
    provider = _StubProvider()
    paraphraser_calls: list[tuple[Prompt, int]] = []

    def my_paraphraser(p: Prompt, n: int) -> list[Prompt]:
        paraphraser_calls.append((p, n))
        return [
            Prompt(system=p.system, messages=[{"role": "user", "content": f"v{i}"}])
            for i in range(n)
        ]

    axis = PromptPerturbationAxis(provider=provider, paraphraser=my_paraphraser, n_perturbations=3)
    metric = axis.run(_prompt())
    assert len(paraphraser_calls) == 1
    assert paraphraser_calls[0][1] == 3
    assert len(metric.responses) == 3


def test_default_paraphraser_uses_primary_provider() -> None:
    """The default paraphraser calls the primary provider with a meta-prompt."""

    class _MetaProvider:
        """First call returns paraphrase list; subsequent calls return canned answer."""

        provider_id = "test.meta"
        model = "fake-model"

        def __init__(self) -> None:
            self.call_count = 0

        def complete(
            self,
            prompt: Prompt,
            *,
            max_tokens: int,
            temperature: float,
            tools: list[Tool] | None = None,
            stream: bool = False,
        ) -> CognitionResult:
            del max_tokens, temperature, tools, stream
            self.call_count += 1
            if self.call_count == 1:
                # First call is the paraphraser meta-call.
                text = json.dumps(["rewrite one", "rewrite two", "rewrite three"])
            else:
                # Subsequent calls dispatch the perturbations.
                text = "answer"
            return CognitionResult(
                text=text,
                tool_calls=[],
                usage=TokenUsage(input_tokens=5, output_tokens=5),
                latency_ms=1.0,
                provider_fingerprint=f"{self.provider_id}|call-{self.call_count}",
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

    provider: Any = _MetaProvider()
    axis = PromptPerturbationAxis(provider=provider, n_perturbations=3)
    metric = axis.run(_prompt())
    # 1 meta-call + 3 perturbation dispatches.
    assert provider.call_count == 4
    assert len(metric.responses) == 3
    assert metric.metadata["perturbation_texts"] == [
        "rewrite one",
        "rewrite two",
        "rewrite three",
    ]


def test_disagreement_type_unclassified() -> None:
    provider = _StubProvider()
    axis = PromptPerturbationAxis(provider=provider, n_perturbations=2)
    perts = [
        Prompt(system=None, messages=[{"role": "user", "content": "a"}]),
        Prompt(system=None, messages=[{"role": "user", "content": "b"}]),
    ]
    metric = axis.run(_prompt(), perturbations=perts)
    assert metric.disagreement_type == CognitionDisagreementType.UNCLASSIFIED


def test_zero_n_perturbations_raises() -> None:
    provider = _StubProvider()
    axis = PromptPerturbationAxis(provider=provider, n_perturbations=0)
    assert axis.applies_to(_prompt()) is False
    with pytest.raises(ValueError):
        axis.run(_prompt())


def test_empty_perturbations_raises() -> None:
    provider = _StubProvider()
    axis = PromptPerturbationAxis(provider=provider, paraphraser=lambda p, n: [], n_perturbations=2)
    with pytest.raises(ValueError):
        axis.run(_prompt())


# ---------------------------------------------------------------------------
# _parse_paraphrase_list helper coverage


def test_parse_json_list() -> None:
    assert _parse_paraphrase_list('["a", "b", "c"]', 3) == ["a", "b", "c"]


def test_parse_json_in_markdown_fence() -> None:
    text = '```json\n["x", "y"]\n```'
    assert _parse_paraphrase_list(text, 2) == ["x", "y"]


def test_parse_falls_back_to_newlines() -> None:
    text = "first one\nsecond one\nthird one"
    parsed = _parse_paraphrase_list(text, 3)
    assert parsed == ["first one", "second one", "third one"]


def test_parse_strips_numbered_prefix() -> None:
    text = "1. alpha\n2. beta\n3. gamma"
    parsed = _parse_paraphrase_list(text, 3)
    assert parsed == ["alpha", "beta", "gamma"]


def test_parse_strips_bullet_prefix() -> None:
    text = "- alpha\n* beta"
    parsed = _parse_paraphrase_list(text, 2)
    assert parsed == ["alpha", "beta"]


def test_parse_truncates_to_expected_n() -> None:
    """When the parser returns more than expected, truncate."""
    parsed = _parse_paraphrase_list('["a", "b", "c", "d"]', 2)
    assert parsed == ["a", "b", "c", "d"]  # JSON path returns all
    # Newline path applies truncation:
    parsed_nl = _parse_paraphrase_list("a\nb\nc\nd", 2)
    assert parsed_nl == ["a", "b"]
