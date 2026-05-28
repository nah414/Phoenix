"""Tests for ``LiteLLMPassthroughProvider.astream`` (Phase 13.x.2).

LiteLLM normalizes streaming chunks to OpenAI's ChatCompletionChunk
shape, so this file mirrors :mod:`test_openai_streaming` with the
``litellm.acompletion`` async entry point as the seam.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from phoenix.providers.cognition.streaming import (
    StreamFinal,
    StreamingCognitionProvider,
    StreamToolCallStart,
    StreamTokenDelta,
)
from phoenix.providers.cognition.types import Prompt


@pytest.fixture
def fake_litellm_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Install a fake ``litellm`` module + ``litellm.exceptions``."""
    fake = types.ModuleType("litellm")
    exceptions_sub = types.ModuleType("litellm.exceptions")

    class AuthenticationError(Exception):
        pass

    class RateLimitError(Exception):
        pass

    class Timeout(Exception):
        pass

    class APIConnectionError(Exception):
        pass

    class ContextWindowExceededError(Exception):
        pass

    class ContentPolicyViolationError(Exception):
        pass

    class ServiceUnavailableError(Exception):
        pass

    class APIError(Exception):
        pass

    for cls in (
        AuthenticationError,
        RateLimitError,
        Timeout,
        APIConnectionError,
        ContextWindowExceededError,
        ContentPolicyViolationError,
        ServiceUnavailableError,
        APIError,
    ):
        setattr(exceptions_sub, cls.__name__, cls)
    fake.exceptions = exceptions_sub
    fake.completion = lambda **kw: None  # placeholder
    fake.acompletion = AsyncMock()
    fake.get_model_info = lambda *a, **kw: {}

    monkeypatch.setitem(sys.modules, "litellm", fake)
    monkeypatch.setitem(sys.modules, "litellm.exceptions", exceptions_sub)
    return fake


def _text_chunk(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text, tool_calls=None))],
        usage=None,
    )


def _final_usage_chunk(*, prompt_tokens: int, completion_tokens: int) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            prompt_tokens_details=None,
        ),
    )


def _make_async_iter(chunks: list[SimpleNamespace]):
    async def _iter():
        for c in chunks:
            yield c

    return _iter()


@pytest.mark.asyncio
async def test_astream_emits_token_deltas(fake_litellm_module: Any) -> None:
    from phoenix.providers.cognition.litellm_passthrough import LiteLLMPassthroughProvider

    chunks = [
        _text_chunk("Hel"),
        _text_chunk("lo"),
        _text_chunk(" world"),
        _final_usage_chunk(prompt_tokens=5, completion_tokens=2),
    ]
    fake_litellm_module.acompletion = AsyncMock(return_value=_make_async_iter(chunks))

    adapter = LiteLLMPassthroughProvider(
        litellm_model="openai/gpt-4o-mini", client=fake_litellm_module
    )
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])

    events = [e async for e in adapter.astream(prompt, max_tokens=100, temperature=0.0)]
    assert len(events) == 4
    assert isinstance(events[0], StreamTokenDelta)
    assert events[0].delta_text == "Hel"
    assert isinstance(events[3], StreamFinal)
    assert events[3].result.text == "Hello world"
    assert events[3].result.usage.input_tokens == 5
    assert events[3].result.usage.output_tokens == 2


@pytest.mark.asyncio
async def test_astream_cumulative_tokens_monotonic(fake_litellm_module: Any) -> None:
    from phoenix.providers.cognition.litellm_passthrough import LiteLLMPassthroughProvider

    chunks = [_text_chunk("aaaa"), _text_chunk("bbbb"), _text_chunk("cccc")]
    fake_litellm_module.acompletion = AsyncMock(return_value=_make_async_iter(chunks))

    adapter = LiteLLMPassthroughProvider(
        litellm_model="openai/gpt-4o-mini", client=fake_litellm_module
    )
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
async def test_astream_emits_tool_call_start(fake_litellm_module: Any) -> None:
    from phoenix.providers.cognition.litellm_passthrough import LiteLLMPassthroughProvider

    chunks = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="tc_1",
                                function=SimpleNamespace(name="search", arguments='{"q":"x"}'),
                            )
                        ],
                    )
                )
            ],
            usage=None,
        ),
        _final_usage_chunk(prompt_tokens=2, completion_tokens=3),
    ]
    fake_litellm_module.acompletion = AsyncMock(return_value=_make_async_iter(chunks))

    adapter = LiteLLMPassthroughProvider(
        litellm_model="openai/gpt-4o-mini", client=fake_litellm_module
    )
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "search"}])

    events = [e async for e in adapter.astream(prompt, max_tokens=100, temperature=0.0)]
    starts = [e for e in events if isinstance(e, StreamToolCallStart)]
    assert len(starts) == 1
    assert starts[0].call_id == "tc_1"
    assert starts[0].tool_name == "search"
    final = events[-1]
    assert isinstance(final, StreamFinal)
    assert final.result.tool_calls[0].arguments == {"q": "x"}


@pytest.mark.asyncio
async def test_astream_satisfies_streaming_protocol(fake_litellm_module: Any) -> None:
    from phoenix.providers.cognition.litellm_passthrough import LiteLLMPassthroughProvider

    adapter = LiteLLMPassthroughProvider(
        litellm_model="openai/gpt-4o-mini", client=fake_litellm_module
    )
    assert isinstance(adapter, StreamingCognitionProvider)
