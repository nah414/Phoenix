"""Tests for the AnthropicProvider adapter."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from phoenix.providers.cognition.errors import (
    CognitionAuthError,
    CognitionContentPolicyError,
    CognitionContextLengthError,
    CognitionRateLimitError,
    CognitionTimeoutError,
    CognitionUnavailable,
)
from phoenix.providers.cognition.types import Prompt, Tool


@pytest.fixture
def fake_anthropic_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Install a fake ``anthropic`` module in sys.modules with the
    exception classes our adapter's ``_map_sdk_exception`` imports.

    Each fake exception class is a plain ``Exception`` subclass so we
    can raise it as a stand-in for the real SDK exception without
    requiring the SDK to be installed.
    """
    fake = types.ModuleType("anthropic")

    class AuthenticationError(Exception):
        def __init__(self, msg: str = "auth", body: Any = None) -> None:
            super().__init__(msg)
            self.body = body

    class RateLimitError(Exception):
        def __init__(self, msg: str = "rl", response: Any = None) -> None:
            super().__init__(msg)
            self.response = response

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

    class Anthropic:
        def __init__(self, **_: Any) -> None:
            self.messages = MagicMock()

    fake.AuthenticationError = AuthenticationError
    fake.RateLimitError = RateLimitError
    fake.APITimeoutError = APITimeoutError
    fake.APIConnectionError = APIConnectionError
    fake.InternalServerError = InternalServerError
    fake.BadRequestError = BadRequestError
    fake.Anthropic = Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    return fake


def _make_anthropic_message(
    *,
    text: str = "hello",
    tool_uses: list[dict[str, Any]] | None = None,
    input_tokens: int = 10,
    output_tokens: int = 5,
    cache_read: int = 0,
) -> Any:
    """Construct a fake Anthropic Message response."""
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
                    input=tu["input"],
                )
            )
    usage = SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read,
    )
    return SimpleNamespace(content=content, usage=usage)


def test_happy_path_text_response(fake_anthropic_module: Any) -> None:
    """Adapter returns canonical CognitionResult on a clean text response."""
    from phoenix.providers.cognition.anthropic import AnthropicProvider

    client = SimpleNamespace(
        messages=SimpleNamespace(create=MagicMock(return_value=_make_anthropic_message()))
    )
    adapter = AnthropicProvider(model="claude-sonnet-4-7-20260418", api_key="test", client=client)
    prompt = Prompt(system="be brief", messages=[{"role": "user", "content": "hi"}])

    result = adapter.complete(prompt, max_tokens=100, temperature=0.0)

    assert result.text == "hello"
    assert result.tool_calls == []
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 5
    assert result.usage.cached_input_tokens == 0
    assert result.provider_fingerprint.startswith("anthropic|claude-sonnet-4-7")
    assert result.prompt_cache_hit is False


def test_tool_use_canonicalized(fake_anthropic_module: Any) -> None:
    """Tool calls from Anthropic map to canonical ToolCall objects."""
    from phoenix.providers.cognition.anthropic import AnthropicProvider

    message = _make_anthropic_message(
        text="checking",
        tool_uses=[
            {"id": "tu_1", "name": "lookup", "input": {"q": "phoenix"}},
        ],
    )
    client = SimpleNamespace(messages=SimpleNamespace(create=MagicMock(return_value=message)))
    adapter = AnthropicProvider(model="claude-sonnet-4-7-20260418", api_key="test", client=client)
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "lookup phoenix"}])
    tools = [Tool(name="lookup", description="search", input_schema={"type": "object"})]

    result = adapter.complete(prompt, max_tokens=100, temperature=0.0, tools=tools)

    assert result.text == "checking"
    assert len(result.tool_calls) == 1
    tc = result.tool_calls[0]
    assert tc.call_id == "tu_1"
    assert tc.name == "lookup"
    assert tc.arguments == {"q": "phoenix"}


def test_prompt_cache_hit_surfaced(fake_anthropic_module: Any) -> None:
    """cache_read_input_tokens > 0 surfaces prompt_cache_hit=True."""
    from phoenix.providers.cognition.anthropic import AnthropicProvider

    message = _make_anthropic_message(cache_read=200, input_tokens=50)
    client = SimpleNamespace(messages=SimpleNamespace(create=MagicMock(return_value=message)))
    adapter = AnthropicProvider(model="claude-sonnet-4-7-20260418", api_key="test", client=client)
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])

    result = adapter.complete(prompt, max_tokens=10, temperature=0.0)

    assert result.prompt_cache_hit is True
    assert result.usage.cached_input_tokens == 200
    assert result.usage.input_tokens == 250  # 50 fresh + 200 cached


@pytest.mark.parametrize(
    "raise_exc_cls_attr,expected_phoenix",
    [
        ("AuthenticationError", CognitionAuthError),
        ("APITimeoutError", CognitionTimeoutError),
        ("APIConnectionError", CognitionUnavailable),
        ("InternalServerError", CognitionUnavailable),
    ],
)
def test_error_mapping_simple(
    fake_anthropic_module: Any, raise_exc_cls_attr: str, expected_phoenix: type
) -> None:
    """Each simple SDK exception maps to the corresponding Phoenix typed error."""
    from phoenix.providers.cognition.anthropic import AnthropicProvider

    sdk_cls = getattr(fake_anthropic_module, raise_exc_cls_attr)
    client = SimpleNamespace(messages=SimpleNamespace(create=MagicMock(side_effect=sdk_cls())))
    adapter = AnthropicProvider(
        model="claude-sonnet-4-7-20260418",
        api_key="test",
        client=client,
        max_retries=0,  # no backoff in tests
    )
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])

    with patch("phoenix.providers.cognition._base.time.sleep"):
        with pytest.raises(expected_phoenix):
            adapter.complete(prompt, max_tokens=10, temperature=0.0)


def test_rate_limit_extracts_retry_after(fake_anthropic_module: Any) -> None:
    """RateLimitError with a Retry-After header surfaces retry_after_seconds."""
    from phoenix.providers.cognition.anthropic import AnthropicProvider

    headers = {"retry-after": "1.5"}
    response = SimpleNamespace(headers=headers)
    exc = fake_anthropic_module.RateLimitError("rl", response=response)
    client = SimpleNamespace(messages=SimpleNamespace(create=MagicMock(side_effect=exc)))
    adapter = AnthropicProvider(
        model="claude-sonnet-4-7-20260418", api_key="test", client=client, max_retries=0
    )
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])

    with patch("phoenix.providers.cognition._base.time.sleep"):
        with pytest.raises(CognitionRateLimitError) as exc_info:
            adapter.complete(prompt, max_tokens=10, temperature=0.0)
    assert exc_info.value.retry_after_seconds == 1.5


def test_bad_request_context_length(fake_anthropic_module: Any) -> None:
    """BadRequestError mentioning context length maps to CognitionContextLengthError."""
    from phoenix.providers.cognition.anthropic import AnthropicProvider

    exc = fake_anthropic_module.BadRequestError("prompt is too long for context window")
    client = SimpleNamespace(messages=SimpleNamespace(create=MagicMock(side_effect=exc)))
    adapter = AnthropicProvider(
        model="claude-sonnet-4-7-20260418", api_key="test", client=client, max_retries=0
    )
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])

    with pytest.raises(CognitionContextLengthError):
        adapter.complete(prompt, max_tokens=10, temperature=0.0)


def test_bad_request_content_policy(fake_anthropic_module: Any) -> None:
    """BadRequestError mentioning policy maps to CognitionContentPolicyError."""
    from phoenix.providers.cognition.anthropic import AnthropicProvider

    body = {"error": {"type": "content_policy_violation"}}
    exc = fake_anthropic_module.BadRequestError("blocked by content policy", body=body)
    client = SimpleNamespace(messages=SimpleNamespace(create=MagicMock(side_effect=exc)))
    adapter = AnthropicProvider(
        model="claude-sonnet-4-7-20260418", api_key="test", client=client, max_retries=0
    )
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])

    with pytest.raises(CognitionContentPolicyError) as exc_info:
        adapter.complete(prompt, max_tokens=10, temperature=0.0)
    assert exc_info.value.reason_code == "content_policy_violation"


def test_capabilities_known_model() -> None:
    """A canonical model returns its static capability set."""
    from phoenix.providers.cognition.anthropic import AnthropicProvider

    adapter = AnthropicProvider(
        model="claude-sonnet-4-7-20260418",
        api_key="test",
        client=SimpleNamespace(messages=SimpleNamespace(create=MagicMock())),
    )
    caps = adapter.capabilities()
    assert caps.supports_prompt_cache is True
    assert caps.tool_use is True
    assert caps.max_context_tokens == 200_000


def test_capabilities_unknown_model() -> None:
    """An unknown model returns conservative defaults."""
    from phoenix.providers.cognition.anthropic import AnthropicProvider

    adapter = AnthropicProvider(
        model="claude-experimental-9000",
        api_key="test",
        client=SimpleNamespace(messages=SimpleNamespace(create=MagicMock())),
    )
    caps = adapter.capabilities()
    assert caps.supports_prompt_cache is False
    assert caps.tool_use is False


def test_fingerprint_stable() -> None:
    """fingerprint() is deterministic for fixed model."""
    from phoenix.providers.cognition.anthropic import AnthropicProvider

    adapter = AnthropicProvider(
        model="claude-sonnet-4-7-20260418",
        api_key="test",
        client=SimpleNamespace(messages=SimpleNamespace(create=MagicMock())),
    )
    assert adapter.fingerprint() == adapter.fingerprint()
    assert "anthropic" in adapter.fingerprint()
    assert "claude-sonnet-4-7-20260418" in adapter.fingerprint()
