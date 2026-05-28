"""Tests for ``OpenAIProvider.astream`` (Phase 13.x.2)."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from phoenix.providers.cognition.streaming import (
    StreamFinal,
    StreamingCognitionProvider,
    StreamToolCallStart,
    StreamTokenDelta,
)
from phoenix.providers.cognition.types import Prompt


@pytest.fixture
def fake_openai_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Install a fake ``openai`` module exposing the typed errors + clients."""
    fake = types.ModuleType("openai")

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

    class OpenAI:
        def __init__(self, **_: Any) -> None:
            self.chat = MagicMock()

    fake.OpenAI = OpenAI
    monkeypatch.setitem(sys.modules, "openai", fake)
    return fake


def _text_chunk(text: str) -> SimpleNamespace:
    """Build an OpenAI ChatCompletionChunk-shaped namespace with a text delta."""
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text, tool_calls=None))],
        usage=None,
    )


def _tool_call_chunk(*, index: int, call_id: str, name: str, args: str) -> SimpleNamespace:
    """Build a chunk that announces (or fragments) a tool call."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            index=index,
                            id=call_id,
                            function=SimpleNamespace(name=name, arguments=args),
                        )
                    ],
                )
            )
        ],
        usage=None,
    )


def _final_usage_chunk(*, prompt_tokens: int, completion_tokens: int) -> SimpleNamespace:
    """OpenAI's last chunk: empty choices + populated usage."""
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            prompt_tokens_details=None,
        ),
    )


def _make_async_iter(chunks: list[SimpleNamespace]):
    """Build an async iterator yielding ``chunks`` once on each iteration."""

    async def _iter():
        for c in chunks:
            yield c

    return _iter()


@pytest.mark.asyncio
async def test_astream_emits_token_deltas(fake_openai_module: Any) -> None:
    from phoenix.providers.cognition.openai import OpenAIProvider

    chunks = [
        _text_chunk("Hel"),
        _text_chunk("lo"),
        _text_chunk(" world"),
        _final_usage_chunk(prompt_tokens=5, completion_tokens=2),
    ]
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=_make_async_iter(chunks)))
        )
    )

    adapter = OpenAIProvider(model="gpt-5", api_key="test", client=fake_client)
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])

    events = [e async for e in adapter.astream(prompt, max_tokens=100, temperature=0.0)]

    # Three text deltas + one final.
    assert len(events) == 4
    assert isinstance(events[0], StreamTokenDelta)
    assert events[0].delta_text == "Hel"
    assert isinstance(events[3], StreamFinal)
    assert events[3].result.text == "Hello world"
    assert events[3].result.usage.input_tokens == 5
    assert events[3].result.usage.output_tokens == 2


@pytest.mark.asyncio
async def test_astream_emits_tool_call_start(fake_openai_module: Any) -> None:
    """Tool call announced once its id + name are both known."""
    from phoenix.providers.cognition.openai import OpenAIProvider

    # Two chunks: first delivers id + name + partial args; second delivers
    # the rest of args. The announcement fires after the first chunk.
    chunks = [
        _tool_call_chunk(index=0, call_id="call_1", name="search", args='{"q":'),
        _tool_call_chunk(index=0, call_id="", name="", args=' "phoenix"}'),
        _final_usage_chunk(prompt_tokens=3, completion_tokens=4),
    ]
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=_make_async_iter(chunks)))
        )
    )

    adapter = OpenAIProvider(model="gpt-5", api_key="test", client=fake_client)
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "search"}])

    events = [e async for e in adapter.astream(prompt, max_tokens=100, temperature=0.0)]

    starts = [e for e in events if isinstance(e, StreamToolCallStart)]
    assert len(starts) == 1
    assert starts[0].call_id == "call_1"
    assert starts[0].tool_name == "search"

    final = events[-1]
    assert isinstance(final, StreamFinal)
    assert len(final.result.tool_calls) == 1
    assert final.result.tool_calls[0].arguments == {"q": "phoenix"}


@pytest.mark.asyncio
async def test_astream_cumulative_tokens_monotonic(fake_openai_module: Any) -> None:
    from phoenix.providers.cognition.openai import OpenAIProvider

    chunks = [_text_chunk("aaaa"), _text_chunk("bbbb"), _text_chunk("cccc")]
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=_make_async_iter(chunks)))
        )
    )
    adapter = OpenAIProvider(model="gpt-5", api_key="test", client=fake_client)
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])

    deltas = [
        e
        async for e in adapter.astream(prompt, max_tokens=100, temperature=0.0)
        if isinstance(e, StreamTokenDelta)
    ]
    assert len(deltas) == 3
    assert deltas[0].cumulative_tokens <= deltas[1].cumulative_tokens
    assert deltas[1].cumulative_tokens <= deltas[2].cumulative_tokens


@pytest.mark.asyncio
async def test_astream_satisfies_streaming_protocol() -> None:
    from phoenix.providers.cognition.openai import OpenAIProvider

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock()))
    )
    adapter = OpenAIProvider(model="gpt-5", api_key="test", client=fake_client)
    assert isinstance(adapter, StreamingCognitionProvider)
