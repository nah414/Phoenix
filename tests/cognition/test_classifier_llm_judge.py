"""Tests for the LLMJudgeClassifier (mocked CognitionProvider)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from cognition_wobble.classifier_llm_judge import (
    LLMJudgeClassifier,
    _parse_judge_output,
)
from cognition_wobble.disagreement_types import CognitionDisagreementType
from phoenix.providers.cognition.capabilities import CognitionCapabilities
from phoenix.providers.cognition.types import (
    CognitionResult,
    Prompt,
    TokenUsage,
    Tool,
)


class _ScriptedProvider:
    """Returns a configurable response text on each ``complete()`` call."""

    provider_id = "test.scripted-judge"
    model = "fake-model"

    def __init__(self, *, response_text: str) -> None:
        self._response_text = response_text
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
            text=self._response_text,
            tool_calls=[],
            usage=TokenUsage(input_tokens=10, output_tokens=10),
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


def _r(text: str) -> CognitionResult:
    return CognitionResult(
        text=text,
        tool_calls=[],
        usage=TokenUsage(input_tokens=1, output_tokens=1),
        latency_ms=1.0,
        provider_fingerprint="test|fixture",
    )


def _p() -> Prompt:
    return Prompt(system=None, messages=[{"role": "user", "content": "q"}])


def test_happy_path_factual_agreement() -> None:
    """Judge returns FACTUAL_AGREEMENT JSON → classifier extracts it."""
    provider: Any = _ScriptedProvider(
        response_text=json.dumps(
            {"class": "FACTUAL_AGREEMENT", "confidence": 0.92, "reasoning": "Both say 4."}
        )
    )
    judge = LLMJudgeClassifier(provider=provider)
    result = judge.classify(_p(), [_r("4"), _r("Four.")])
    assert result.disagreement_type is CognitionDisagreementType.FACTUAL_AGREEMENT
    assert result.confidence == pytest.approx(0.92)


def test_low_confidence_returns_unclassified() -> None:
    """Judge says FACTUAL_AGREEMENT but confidence=0.3 < threshold → UNCLASSIFIED."""
    provider: Any = _ScriptedProvider(
        response_text=json.dumps(
            {"class": "FACTUAL_AGREEMENT", "confidence": 0.3, "reasoning": "Unsure."}
        )
    )
    judge = LLMJudgeClassifier(provider=provider)
    result = judge.classify(_p(), [_r("a"), _r("b")], confidence_threshold=0.5)
    assert result.disagreement_type is CognitionDisagreementType.UNCLASSIFIED
    assert result.confidence == pytest.approx(0.3)


def test_malformed_json_returns_unclassified() -> None:
    provider: Any = _ScriptedProvider(response_text="not valid json")
    judge = LLMJudgeClassifier(provider=provider)
    result = judge.classify(_p(), [_r("a"), _r("b")])
    assert result.disagreement_type is CognitionDisagreementType.UNCLASSIFIED
    assert result.feature_values.get("judge_parse_failed") == 1.0


def test_unknown_class_returns_unclassified() -> None:
    """Judge emits an unrecognized class name → UNCLASSIFIED."""
    provider: Any = _ScriptedProvider(
        response_text=json.dumps({"class": "BANANAS", "confidence": 0.99, "reasoning": "??"})
    )
    judge = LLMJudgeClassifier(provider=provider)
    result = judge.classify(_p(), [_r("a"), _r("b")])
    assert result.disagreement_type is CognitionDisagreementType.UNCLASSIFIED


def test_fewer_than_two_responses_raises() -> None:
    provider: Any = _ScriptedProvider(response_text=json.dumps({}))
    judge = LLMJudgeClassifier(provider=provider)
    with pytest.raises(ValueError):
        judge.classify(_p(), [_r("only-one")])


def test_meta_prompt_contains_both_responses() -> None:
    """Sanity-check: the dispatched meta-prompt mentions both response texts."""
    provider = _ScriptedProvider(
        response_text=json.dumps(
            {"class": "FACTUAL_AGREEMENT", "confidence": 0.9, "reasoning": "ok"}
        )
    )
    judge = LLMJudgeClassifier(provider=provider)
    judge.classify(_p(), [_r("alpha-response"), _r("beta-response")])
    assert len(provider.calls) == 1
    meta_user = provider.calls[0].messages[0]["content"]
    assert "alpha-response" in meta_user
    assert "beta-response" in meta_user


# ---------------------------------------------------------------------------
# _parse_judge_output direct coverage


def test_parse_judge_plain_json() -> None:
    text = '{"class": "FACTUAL_AGREEMENT", "confidence": 0.8, "reasoning": "ok"}'
    parsed = _parse_judge_output(text)
    assert parsed == ("FACTUAL_AGREEMENT", 0.8, "ok")


def test_parse_judge_json_in_markdown_fence() -> None:
    text = '```json\n{"class": "FACTUAL_DISAGREEMENT", "confidence": 0.6}\n```'
    parsed = _parse_judge_output(text)
    assert parsed is not None
    assert parsed[0] == "FACTUAL_DISAGREEMENT"
    assert parsed[1] == 0.6


def test_parse_judge_confidence_clamped_to_unit() -> None:
    """Confidence outside [0, 1] is clamped."""
    text = '{"class": "X", "confidence": 1.5}'
    parsed = _parse_judge_output(text)
    assert parsed is not None
    assert parsed[1] == 1.0


def test_parse_judge_missing_class() -> None:
    text = '{"confidence": 0.8, "reasoning": "ok"}'
    assert _parse_judge_output(text) is None


def test_parse_judge_invalid_confidence_type() -> None:
    text = '{"class": "X", "confidence": "high"}'
    assert _parse_judge_output(text) is None
