"""Tests for the Step 7 streaming-event WebSocket surface."""

from __future__ import annotations

import time

import pytest

from phoenix.api.event_broker import EventBroker
from phoenix.api.streaming_events import (
    EVENT_COGNITION_TOOL_CALL,
    EVENT_COGNITION_TOOL_RESULT,
    EVENT_TOKEN_DELTA,
    EVENT_TOKEN_DROPPED,
    StreamingTokenEmitter,
    event_type_matches_filter,
    parse_event_filter,
)


def _make_broker() -> EventBroker:
    return EventBroker(max_events_per_task=1000)


# ---------------------------------------------------------------------------
# StreamingTokenEmitter — HASH_ONLY suppression (P13-6 load-bearing)


def test_hash_only_suppresses_token_delta() -> None:
    """P13-6 default: HASH_ONLY prompt_disposition → no token.delta emits."""
    broker = _make_broker()
    emitter = StreamingTokenEmitter(broker=broker, task_id="t1", prompt_disposition="HASH_ONLY")

    emitted = emitter.emit_token_delta(
        axis_id="ax1",
        provider="anthropic",
        model="claude-sonnet",
        delta_text="hello",
        cumulative_tokens=1,
        cost_so_far_usd=0.001,
    )

    assert emitted is False
    assert emitter.dropped_hash_only == 1
    assert broker.event_count("t1") == 0


def test_verbatim_emits_token_delta() -> None:
    """VERBATIM disposition: token.delta events flow normally."""
    broker = _make_broker()
    emitter = StreamingTokenEmitter(broker=broker, task_id="t1", prompt_disposition="VERBATIM")

    emitted = emitter.emit_token_delta(
        axis_id="ax1",
        provider="anthropic",
        model="claude-sonnet",
        delta_text="hello",
        cumulative_tokens=1,
        cost_so_far_usd=0.001,
    )

    assert emitted is True
    assert emitter.dropped_hash_only == 0
    assert broker.event_count("t1") == 1
    events = broker.get_events("t1")
    assert events[0].type == EVENT_TOKEN_DELTA
    assert events[0].payload["delta_text"] == "hello"


def test_hash_only_still_emits_tool_call() -> None:
    """tool_call carries no prompt content → emits even under HASH_ONLY."""
    broker = _make_broker()
    emitter = StreamingTokenEmitter(broker=broker, task_id="t1", prompt_disposition="HASH_ONLY")

    emitter.emit_tool_call(
        axis_id="ax1",
        provider="anthropic",
        model="claude-sonnet",
        tool_name="search",
        tool_args={"q": "phoenix"},
    )

    assert broker.event_count("t1") == 1
    events = broker.get_events("t1")
    assert events[0].type == EVENT_COGNITION_TOOL_CALL


def test_hash_only_still_emits_tool_result() -> None:
    """tool_result summary emits even under HASH_ONLY (no prompt content)."""
    broker = _make_broker()
    emitter = StreamingTokenEmitter(broker=broker, task_id="t1", prompt_disposition="HASH_ONLY")

    emitter.emit_tool_result(
        axis_id="ax1",
        tool_name="search",
        result_summary="found 3 results",
        latency_ms=12.5,
    )

    assert broker.event_count("t1") == 1
    events = broker.get_events("t1")
    assert events[0].type == EVENT_COGNITION_TOOL_RESULT
    assert events[0].payload["latency_ms"] == 12.5


# ---------------------------------------------------------------------------
# Rate cap


def test_rate_cap_drops_after_threshold() -> None:
    """When more than cap events/sec emitted, excess drops are counted."""
    broker = _make_broker()
    emitter = StreamingTokenEmitter(
        broker=broker,
        task_id="t1",
        prompt_disposition="VERBATIM",
        rate_cap_per_sec=3,  # tight cap for the test
    )

    # Emit 5 in rapid succession (within one second window).
    results = [
        emitter.emit_token_delta(
            axis_id="ax1",
            provider="p",
            model="m",
            delta_text="x",
            cumulative_tokens=i,
            cost_so_far_usd=0.0,
        )
        for i in range(5)
    ]

    # First 3 emitted; next 2 dropped.
    assert results[:3] == [True, True, True]
    assert results[3:] == [False, False]
    assert emitter.dropped_rate_cap == 2
    assert broker.event_count("t1") == 3


def test_rate_cap_resets_each_second() -> None:
    """The rate-cap counter resets when the second-window changes."""
    broker = _make_broker()
    emitter = StreamingTokenEmitter(
        broker=broker,
        task_id="t1",
        prompt_disposition="VERBATIM",
        rate_cap_per_sec=2,
    )

    # Saturate the current window.
    for i in range(3):
        emitter.emit_token_delta(
            axis_id="ax1",
            provider="p",
            model="m",
            delta_text="x",
            cumulative_tokens=i,
            cost_so_far_usd=0.0,
        )
    assert emitter.dropped_rate_cap == 1

    # Wait > 1 second to enter a new window.
    time.sleep(1.05)

    emitted = emitter.emit_token_delta(
        axis_id="ax1",
        provider="p",
        model="m",
        delta_text="y",
        cumulative_tokens=10,
        cost_so_far_usd=0.0,
    )
    assert emitted is True
    # Drop count from the previous window is preserved (cumulative).
    assert emitter.dropped_rate_cap == 1


# ---------------------------------------------------------------------------
# finalize() emits token.dropped summary


def test_finalize_emits_dropped_summary_when_drops() -> None:
    """finalize() emits token.dropped with the cumulative drop count."""
    broker = _make_broker()
    emitter = StreamingTokenEmitter(broker=broker, task_id="t1", prompt_disposition="HASH_ONLY")

    # Three suppressed token.deltas.
    for i in range(3):
        emitter.emit_token_delta(
            axis_id="ax1",
            provider="p",
            model="m",
            delta_text="x",
            cumulative_tokens=i,
            cost_so_far_usd=0.0,
        )
    emitter.finalize()

    events = broker.get_events("t1")
    assert len(events) == 1  # only the token.dropped summary
    assert events[0].type == EVENT_TOKEN_DROPPED
    assert events[0].payload["total_dropped"] == 3
    assert events[0].payload["dropped_hash_only"] == 3
    assert events[0].payload["dropped_rate_cap"] == 0


def test_finalize_no_emit_when_no_drops() -> None:
    """finalize() is a no-op when no drops occurred."""
    broker = _make_broker()
    emitter = StreamingTokenEmitter(broker=broker, task_id="t1", prompt_disposition="VERBATIM")

    emitter.emit_token_delta(
        axis_id="ax1",
        provider="p",
        model="m",
        delta_text="x",
        cumulative_tokens=1,
        cost_so_far_usd=0.0,
    )
    emitter.finalize()

    events = broker.get_events("t1")
    # Just the token.delta; no token.dropped.
    assert len(events) == 1
    assert events[0].type == EVENT_TOKEN_DELTA


def test_finalize_is_idempotent() -> None:
    """Repeated finalize() calls emit token.dropped only once."""
    broker = _make_broker()
    emitter = StreamingTokenEmitter(broker=broker, task_id="t1", prompt_disposition="HASH_ONLY")
    emitter.emit_token_delta(
        axis_id="ax1",
        provider="p",
        model="m",
        delta_text="x",
        cumulative_tokens=1,
        cost_so_far_usd=0.0,
    )
    emitter.finalize()
    emitter.finalize()  # second call no-op
    emitter.finalize()

    dropped_events = [e for e in broker.get_events("t1") if e.type == EVENT_TOKEN_DROPPED]
    assert len(dropped_events) == 1


def test_finalize_includes_both_drop_reasons() -> None:
    """The token.dropped summary breaks out hash-only vs. rate-cap drops."""
    broker = _make_broker()
    emitter = StreamingTokenEmitter(
        broker=broker,
        task_id="t1",
        prompt_disposition="HASH_ONLY",
        rate_cap_per_sec=100,
    )

    # 2 hash-only drops.
    for i in range(2):
        emitter.emit_token_delta(
            axis_id="ax1",
            provider="p",
            model="m",
            delta_text="x",
            cumulative_tokens=i,
            cost_so_far_usd=0.0,
        )

    emitter.finalize()

    dropped = [e for e in broker.get_events("t1") if e.type == EVENT_TOKEN_DROPPED][0]
    assert dropped.payload["dropped_hash_only"] == 2
    assert dropped.payload["dropped_rate_cap"] == 0
    assert dropped.payload["total_dropped"] == 2


# ---------------------------------------------------------------------------
# Event filter (parse + match)


def test_parse_event_filter_empty() -> None:
    assert parse_event_filter(None) == ()
    assert parse_event_filter("") == ()


def test_parse_event_filter_single() -> None:
    assert parse_event_filter("token.delta") == ("token.delta",)


def test_parse_event_filter_multiple() -> None:
    assert parse_event_filter("token.delta,cognition.*") == (
        "token.delta",
        "cognition.*",
    )


def test_parse_event_filter_strips_whitespace() -> None:
    assert parse_event_filter("  token.delta , cognition.*  ") == (
        "token.delta",
        "cognition.*",
    )


def test_event_type_matches_filter_no_filter() -> None:
    """Empty tuple = match-all."""
    assert event_type_matches_filter("anything", ()) is True
    assert event_type_matches_filter("task.complete", ()) is True


def test_event_type_matches_filter_exact() -> None:
    assert event_type_matches_filter("token.delta", ("token.delta",)) is True
    assert event_type_matches_filter("token.dropped", ("token.delta",)) is False


def test_event_type_matches_filter_wildcard() -> None:
    """``cognition.*`` matches any cognition.X event."""
    assert event_type_matches_filter("cognition.tool_call", ("cognition.*",)) is True
    assert event_type_matches_filter("cognition.tool_result", ("cognition.*",)) is True
    assert event_type_matches_filter("token.delta", ("cognition.*",)) is False
    # Wildcard requires a separator before the rest; ``cognition*`` is NOT
    # what ``cognition.*`` matches.
    assert event_type_matches_filter("cognition", ("cognition.*",)) is False


def test_event_type_matches_filter_mixed() -> None:
    """Multiple patterns: any match wins."""
    patterns = ("token.delta", "cognition.*")
    assert event_type_matches_filter("token.delta", patterns) is True
    assert event_type_matches_filter("cognition.tool_call", patterns) is True
    assert event_type_matches_filter("token.dropped", patterns) is False


def test_rate_cap_override_via_constructor() -> None:
    """The rate_cap_per_sec kwarg takes precedence over the env var."""
    broker = _make_broker()
    emitter = StreamingTokenEmitter(
        broker=broker,
        task_id="t1",
        prompt_disposition="VERBATIM",
        rate_cap_per_sec=42,
    )
    # Property exposes the configured cap.
    # Use the private attribute for the test (the public surface is the
    # behavior, exercised in other tests).
    assert emitter._rate_cap_per_sec == 42


@pytest.mark.parametrize(
    "raw,fallback_default",
    [
        ("", 100),  # empty string → default
        ("not-a-number", 100),  # parse failure → default
        ("0", 100),  # zero → default (rate cap must be >= 1)
        ("-5", 100),  # negative → default
    ],
)
def test_rate_cap_env_var_invalid_falls_back(
    monkeypatch: pytest.MonkeyPatch, raw: str, fallback_default: int
) -> None:
    """Invalid PHOENIX_STREAM_RATE_CAP values fall back to the default."""
    monkeypatch.setenv("PHOENIX_STREAM_RATE_CAP", raw)
    broker = _make_broker()
    emitter = StreamingTokenEmitter(broker=broker, task_id="t1", prompt_disposition="VERBATIM")
    assert emitter._rate_cap_per_sec == fallback_default


def test_rate_cap_env_var_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid PHOENIX_STREAM_RATE_CAP value is read at construction."""
    monkeypatch.setenv("PHOENIX_STREAM_RATE_CAP", "250")
    broker = _make_broker()
    emitter = StreamingTokenEmitter(broker=broker, task_id="t1", prompt_disposition="VERBATIM")
    assert emitter._rate_cap_per_sec == 250
