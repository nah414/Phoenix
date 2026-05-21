"""Tests for ``GoogleProvider.astream`` (Phase 13.x.2)."""

from __future__ import annotations

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


def _text_chunk(text: str) -> SimpleNamespace:
    """Gemini chunk shape: candidates[0].content.parts[*].text."""
    return SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(parts=[SimpleNamespace(text=text, function_call=None)]),
                finish_reason=None,
            )
        ],
        usage_metadata=None,
    )


def _function_call_chunk(*, call_id: str, name: str, args: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(
                            text=None,
                            function_call=SimpleNamespace(id=call_id, name=name, args=args),
                        )
                    ]
                ),
                finish_reason=None,
            )
        ],
        usage_metadata=None,
    )


def _final_usage_chunk(*, prompt_tokens: int, completion_tokens: int) -> SimpleNamespace:
    """Gemini's last chunk carries usage_metadata + finish_reason=STOP."""
    return SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(parts=[]),
                finish_reason="STOP",
            )
        ],
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt_tokens,
            candidates_token_count=completion_tokens,
        ),
    )


def _make_async_iter(chunks: list[SimpleNamespace]):
    async def _iter():
        for c in chunks:
            yield c

    return _iter()


@pytest.mark.asyncio
async def test_astream_emits_token_deltas() -> None:
    from phoenix.providers.cognition.google import GoogleGeminiProvider

    chunks = [
        _text_chunk("Hel"),
        _text_chunk("lo"),
        _text_chunk(" world"),
        _final_usage_chunk(prompt_tokens=4, completion_tokens=2),
    ]
    fake_client = SimpleNamespace(
        generate_content_async=AsyncMock(return_value=_make_async_iter(chunks))
    )
    adapter = GoogleGeminiProvider(model="gemini-2.5-pro", api_key="test", client=fake_client)
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])

    events = [e async for e in adapter.astream(prompt, max_tokens=100, temperature=0.0)]

    # Three text deltas + final.
    assert len(events) == 4
    assert isinstance(events[0], StreamTokenDelta)
    assert events[0].delta_text == "Hel"
    assert isinstance(events[3], StreamFinal)
    assert events[3].result.text == "Hello world"
    assert events[3].result.usage.input_tokens == 4
    assert events[3].result.usage.output_tokens == 2


@pytest.mark.asyncio
async def test_astream_emits_tool_call_start() -> None:
    from phoenix.providers.cognition.google import GoogleGeminiProvider

    chunks = [
        _function_call_chunk(call_id="fn_1", name="search", args={"q": "phoenix"}),
        _final_usage_chunk(prompt_tokens=2, completion_tokens=5),
    ]
    fake_client = SimpleNamespace(
        generate_content_async=AsyncMock(return_value=_make_async_iter(chunks))
    )
    adapter = GoogleGeminiProvider(model="gemini-2.5-pro", api_key="test", client=fake_client)
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "search"}])

    events = [e async for e in adapter.astream(prompt, max_tokens=100, temperature=0.0)]

    starts = [e for e in events if isinstance(e, StreamToolCallStart)]
    assert len(starts) == 1
    assert starts[0].call_id == "fn_1"
    assert starts[0].tool_name == "search"
    # Gemini delivers args as a complete dict on the function_call part.
    assert starts[0].partial_args == {"q": "phoenix"}

    final = events[-1]
    assert isinstance(final, StreamFinal)
    assert len(final.result.tool_calls) == 1
    assert final.result.tool_calls[0].arguments == {"q": "phoenix"}


@pytest.mark.asyncio
async def test_astream_cumulative_tokens_monotonic() -> None:
    from phoenix.providers.cognition.google import GoogleGeminiProvider

    chunks = [_text_chunk("aaaa"), _text_chunk("bbbb"), _text_chunk("cccc")]
    fake_client = SimpleNamespace(
        generate_content_async=AsyncMock(return_value=_make_async_iter(chunks))
    )
    adapter = GoogleGeminiProvider(model="gemini-2.5-pro", api_key="test", client=fake_client)
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
async def test_astream_safety_block_raises() -> None:
    """A final-chunk finish_reason of SAFETY surfaces CognitionContentPolicyError."""
    from phoenix.providers.cognition.errors import CognitionContentPolicyError
    from phoenix.providers.cognition.google import GoogleGeminiProvider

    chunks = [
        _text_chunk("partial unsafe output"),
        SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=SimpleNamespace(parts=[]),
                    finish_reason="SAFETY",
                )
            ],
            usage_metadata=SimpleNamespace(
                prompt_token_count=3,
                candidates_token_count=3,
            ),
        ),
    ]
    fake_client = SimpleNamespace(
        generate_content_async=AsyncMock(return_value=_make_async_iter(chunks))
    )
    adapter = GoogleGeminiProvider(model="gemini-2.5-pro", api_key="test", client=fake_client)
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "x"}])

    with pytest.raises(CognitionContentPolicyError) as exc_info:
        async for _ in adapter.astream(prompt, max_tokens=100, temperature=0.0):
            pass
    # The structured error carries the finish_reason on .reason_code;
    # the message says "content policy refusal" (matches sync path).
    assert exc_info.value.reason_code == "SAFETY"
    assert "content policy" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_astream_satisfies_streaming_protocol() -> None:
    from phoenix.providers.cognition.google import GoogleGeminiProvider

    fake_client = SimpleNamespace(generate_content_async=AsyncMock())
    adapter = GoogleGeminiProvider(model="gemini-2.5-pro", api_key="test", client=fake_client)
    assert isinstance(adapter, StreamingCognitionProvider)
