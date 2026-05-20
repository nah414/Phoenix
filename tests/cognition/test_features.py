"""Tests for the cognition classifier feature extractors."""

from __future__ import annotations

import pytest

from cognition_wobble.features import (
    FEATURE_NAMES,
    extract_features,
    factual_claim_heuristic,
    length_ratio,
    refusal_pattern_match,
    tool_call_equality,
)
from phoenix.providers.cognition.types import (
    CognitionResult,
    Prompt,
    TokenUsage,
    ToolCall,
)


def _result(text: str, *, tool_calls: list[ToolCall] | None = None) -> CognitionResult:
    return CognitionResult(
        text=text,
        tool_calls=tool_calls or [],
        usage=TokenUsage(input_tokens=1, output_tokens=1),
        latency_ms=1.0,
        provider_fingerprint="test|fixture",
    )


# ---------------------------------------------------------------------------
# length_ratio


def test_length_ratio_equal_length() -> None:
    assert length_ratio("abcde", "fghij") == pytest.approx(1.0)


def test_length_ratio_one_much_longer() -> None:
    """5 chars vs 50 chars → 0.1."""
    assert length_ratio("abcde", "x" * 50) == pytest.approx(0.1)


def test_length_ratio_both_empty() -> None:
    assert length_ratio("", "") == 1.0


def test_length_ratio_one_empty() -> None:
    assert length_ratio("", "hello") == 0.0


# ---------------------------------------------------------------------------
# refusal_pattern_match


def test_refusal_match_neither_refused() -> None:
    assert refusal_pattern_match("the capital is paris", "paris.") == -1.0


def test_refusal_match_both_refused() -> None:
    a = "I can't help with that."
    b = "As an AI, I won't provide such instructions."
    assert refusal_pattern_match(a, b) == 0.0


def test_refusal_match_asymmetric() -> None:
    a = "I can't help with that."
    b = "Sure, here's how to do it: ..."
    assert refusal_pattern_match(a, b) == 1.0


def test_refusal_match_empty_text() -> None:
    """Empty text doesn't match any refusal pattern."""
    assert refusal_pattern_match("", "") == -1.0


# ---------------------------------------------------------------------------
# tool_call_equality


def test_tool_call_both_empty() -> None:
    a = _result("a", tool_calls=[])
    b = _result("b", tool_calls=[])
    assert tool_call_equality(a, b) == 1.0


def test_tool_call_same_tool() -> None:
    tc = ToolCall(call_id="c1", name="search", arguments={"q": "x"})
    a = _result("", tool_calls=[tc])
    b = _result("", tool_calls=[ToolCall(call_id="c2", name="search", arguments={"q": "y"})])
    assert tool_call_equality(a, b) == 1.0


def test_tool_call_different_tools() -> None:
    a = _result("", tool_calls=[ToolCall(call_id="c1", name="search", arguments={})])
    b = _result("", tool_calls=[ToolCall(call_id="c2", name="weather", arguments={})])
    assert tool_call_equality(a, b) == 0.5


def test_tool_call_asymmetric_use() -> None:
    a = _result("just answering")
    b = _result("", tool_calls=[ToolCall(call_id="c1", name="search", arguments={})])
    assert tool_call_equality(a, b) == 0.0


# ---------------------------------------------------------------------------
# factual_claim_heuristic


def test_factual_claim_empty() -> None:
    assert factual_claim_heuristic("") == 0.0


def test_factual_claim_no_factual_signal() -> None:
    """Plain conversational text with no caps/dates/numbers."""
    assert factual_claim_heuristic("yes maybe so") < 0.1


def test_factual_claim_date_year() -> None:
    """A year like '1945' bumps the heuristic."""
    score = factual_claim_heuristic("ended in 1945.")
    assert score > 0.0


def test_factual_claim_proper_noun() -> None:
    """Capitalized words like 'Paris' bump the heuristic."""
    score = factual_claim_heuristic("Paris is the capital.")
    assert score > 0.0


# ---------------------------------------------------------------------------
# extract_features


def test_extract_features_returns_expected_keys() -> None:
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "q"}])
    features = extract_features(prompt, _result("a"), _result("b"))
    for name in FEATURE_NAMES:
        assert name in features


def test_extract_features_returns_floats() -> None:
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "q"}])
    features = extract_features(prompt, _result("a"), _result("b"))
    for v in features.values():
        assert isinstance(v, float)
