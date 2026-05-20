"""Tests for ``AnthropicProvider.astream`` (Phase 13 Step 7)."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from phoenix.providers.cognition.streaming import (
    StreamFinal,
    StreamingCognitionProvider,
    StreamToolCallStart,
    StreamTokenDelta,
    stream_event_kind,
)
from phoenix.providers.cognition.types import Prompt


@pytest.fixture
def fake_anthropic_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Install a fake ``anthropic`` module for the error-mapping path."""
    fake = types.ModuleType("anthropic")

    class AuthenticationError(Exception):
        pass

    class RateLimitError(Exception):
        pass

    class APITimeoutError(Exception):
        pass

    class APIConnectionError(Exception):
        pass

    class InternalServerError(Exception):
        pass

    class BadRequestError(Exception):
        def __init__(self, msg: str = "bad", body: Any = None) -> None:
            super().__init__(msg)
            self.body = body

    fake.AuthenticationError = AuthenticationError
    fake.RateLimitError = RateLimitError
    fake.APITimeoutError = APITimeoutError
    fake.APIConnectionError = APIConnectionError
    fake.InternalServerError = InternalServerError
    fake.BadRequestError = BadRequestError

    class Anthropic:
        def __init__(self, **_: Any) -> None:
            self.messages = MagicMock()

    fake.Anthropic = Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    return fake


class _FakeStream:
    """Mimics the Anthropic SDK's async stream context manager."""

    def __init__(self, events: list[Any], final_message: Any) -> None:
        self._events = events
        self._final = final_message

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None

    def __aiter__(self) -> _FakeStream:
        self._iter = iter(self._events)
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration

    async def get_final_message(self) -> Any:
        return self._final


def _text_delta_event(text: str) -> SimpleNamespace:
    """Build an SDK-shaped content_block_delta event with a text delta."""
    return SimpleNamespace(
        type="content_block_delta",
        delta=SimpleNamespace(type="text_delta", text=text),
    )


def _tool_use_start_event(*, id: str, name: str) -> SimpleNamespace:
    """Build an SDK-shaped content_block_start event for a tool_use block."""
    return SimpleNamespace(
        type="content_block_start",
        content_block=SimpleNamespace(type="tool_use", id=id, name=name),
    )


def _final_message(
    *,
    text: str = "",
    tool_uses: list[dict[str, Any]] | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> SimpleNamespace:
    content: list[Any] = []
    if text:
        content.append(SimpleNamespace(type="text", text=text))
    if tool_uses:
        for tu in tool_uses:
            content.append(
                SimpleNamespace(
                    type="tool_use",
                    id=tu["id"],
                    name=tu["name"],
                    input=tu.get("input", {}),
                )
            )
    return SimpleNamespace(
        content=content,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=0,
        ),
    )


@pytest.mark.asyncio
async def test_astream_emits_token_deltas(fake_anthropic_module: Any) -> None:
    """A stream of text-delta events yields StreamTokenDelta + StreamFinal."""
    from phoenix.providers.cognition.anthropic import AnthropicProvider

    stream_events = [
        _text_delta_event("Hel"),
        _text_delta_event("lo"),
        _text_delta_event(" world"),
    ]
    final = _final_message(text="Hello world", output_tokens=2)

    fake_stream = _FakeStream(stream_events, final)
    fake_client = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: fake_stream))

    adapter = AnthropicProvider(
        model="claude-sonnet-4-7-20260418",
        api_key="test",
        client=fake_client,
    )
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])

    events = []
    async for event in adapter.astream(prompt, max_tokens=100, temperature=0.0):
        events.append(event)

    # Three deltas + one final.
    assert len(events) == 4
    assert isinstance(events[0], StreamTokenDelta)
    assert events[0].delta_text == "Hel"
    assert isinstance(events[1], StreamTokenDelta)
    assert events[1].delta_text == "lo"
    assert isinstance(events[2], StreamTokenDelta)
    assert events[2].delta_text == " world"
    assert isinstance(events[3], StreamFinal)
    assert events[3].result.text == "Hello world"


@pytest.mark.asyncio
async def test_astream_emits_tool_call_start(fake_anthropic_module: Any) -> None:
    """A tool_use content_block_start yields StreamToolCallStart."""
    from phoenix.providers.cognition.anthropic import AnthropicProvider

    stream_events = [
        _tool_use_start_event(id="tu_1", name="search"),
        _text_delta_event("searching"),
    ]
    final = _final_message(
        text="searching",
        tool_uses=[{"id": "tu_1", "name": "search", "input": {"q": "phoenix"}}],
        output_tokens=2,
    )

    fake_stream = _FakeStream(stream_events, final)
    fake_client = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: fake_stream))

    adapter = AnthropicProvider(
        model="claude-sonnet-4-7-20260418",
        api_key="test",
        client=fake_client,
    )
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "search"}])

    events = []
    async for event in adapter.astream(prompt, max_tokens=100, temperature=0.0):
        events.append(event)

    # Tool-call announcement → text delta → final.
    assert isinstance(events[0], StreamToolCallStart)
    assert events[0].call_id == "tu_1"
    assert events[0].tool_name == "search"
    assert isinstance(events[1], StreamTokenDelta)
    assert isinstance(events[-1], StreamFinal)
    # Final result carries the full tool_calls (with parsed args).
    final_event = events[-1]
    assert len(final_event.result.tool_calls) == 1
    assert final_event.result.tool_calls[0].arguments == {"q": "phoenix"}


@pytest.mark.asyncio
async def test_astream_cumulative_tokens_increases(fake_anthropic_module: Any) -> None:
    """cumulative_tokens monotonically increases across deltas."""
    from phoenix.providers.cognition.anthropic import AnthropicProvider

    stream_events = [
        _text_delta_event("aaaa"),
        _text_delta_event("bbbb"),
        _text_delta_event("cccc"),
    ]
    final = _final_message(text="aaaabbbbcccc", output_tokens=3)

    fake_stream = _FakeStream(stream_events, final)
    fake_client = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: fake_stream))

    adapter = AnthropicProvider(
        model="claude-sonnet-4-7-20260418",
        api_key="test",
        client=fake_client,
    )
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])

    deltas = []
    async for event in adapter.astream(prompt, max_tokens=100, temperature=0.0):
        if isinstance(event, StreamTokenDelta):
            deltas.append(event)

    assert len(deltas) == 3
    # cumulative_tokens monotonically non-decreasing.
    assert deltas[0].cumulative_tokens <= deltas[1].cumulative_tokens
    assert deltas[1].cumulative_tokens <= deltas[2].cumulative_tokens


@pytest.mark.asyncio
async def test_astream_satisfies_streaming_protocol() -> None:
    """An AnthropicProvider conforms structurally to StreamingCognitionProvider."""
    from phoenix.providers.cognition.anthropic import AnthropicProvider

    fake_client = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: None))
    adapter = AnthropicProvider(
        model="claude-sonnet-4-7-20260418",
        api_key="test",
        client=fake_client,
    )
    assert isinstance(adapter, StreamingCognitionProvider)


def test_stream_event_kind_discriminator() -> None:
    """stream_event_kind() returns the WS-event taxonomy label."""
    from phoenix.providers.cognition.types import (
        CognitionResult,
        TokenUsage,
    )

    assert stream_event_kind(StreamTokenDelta(delta_text="x", cumulative_tokens=1)) == (
        "token.delta"
    )
    assert (
        stream_event_kind(StreamToolCallStart(call_id="c", tool_name="t", partial_args={}))
        == "cognition.tool_call"
    )
    result = CognitionResult(
        text="",
        tool_calls=[],
        usage=TokenUsage(input_tokens=0, output_tokens=0),
        latency_ms=0.0,
        provider_fingerprint="x",
    )
    assert stream_event_kind(StreamFinal(result=result)) == "stream.final"


def test_stream_event_kind_unknown_type_raises() -> None:
    """Defensive check: unknown event types raise TypeError."""
    with pytest.raises(TypeError):
        stream_event_kind("not a stream event")  # type: ignore[arg-type]
