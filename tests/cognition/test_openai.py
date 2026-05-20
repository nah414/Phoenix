"""Tests for the OpenAIProvider adapter."""

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
def fake_openai_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Install a fake ``openai`` module in sys.modules for error-mapping tests."""
    fake = types.ModuleType("openai")

    class AuthenticationError(Exception):
        pass

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

    class OpenAI:
        def __init__(self, **_: Any) -> None:
            self.chat = SimpleNamespace(completions=MagicMock())

    fake.AuthenticationError = AuthenticationError
    fake.RateLimitError = RateLimitError
    fake.APITimeoutError = APITimeoutError
    fake.APIConnectionError = APIConnectionError
    fake.InternalServerError = InternalServerError
    fake.BadRequestError = BadRequestError
    fake.OpenAI = OpenAI
    monkeypatch.setitem(sys.modules, "openai", fake)
    return fake


def _make_openai_response(
    *,
    text: str = "hello",
    tool_calls: list[dict[str, Any]] | None = None,
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    cached_tokens: int = 0,
) -> Any:
    """Construct a fake OpenAI ChatCompletion response."""
    raw_tool_calls = []
    if tool_calls:
        for tc in tool_calls:
            raw_tool_calls.append(
                SimpleNamespace(
                    id=tc["id"],
                    function=SimpleNamespace(name=tc["name"], arguments=tc["arguments"]),
                )
            )
    message = SimpleNamespace(content=text, tool_calls=raw_tool_calls or None)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    details = SimpleNamespace(cached_tokens=cached_tokens) if cached_tokens else None
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prompt_tokens_details=details,
    )
    return SimpleNamespace(choices=[choice], usage=usage)


def _make_client(create_mock: MagicMock) -> Any:
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock)))


def test_happy_path_text_response(fake_openai_module: Any) -> None:
    from phoenix.providers.cognition.openai import OpenAIProvider

    client = _make_client(MagicMock(return_value=_make_openai_response()))
    adapter = OpenAIProvider(model="gpt-4o", api_key="test", client=client)
    prompt = Prompt(system="be brief", messages=[{"role": "user", "content": "hi"}])

    result = adapter.complete(prompt, max_tokens=100, temperature=0.0)

    assert result.text == "hello"
    assert result.tool_calls == []
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 5
    assert result.provider_fingerprint.startswith("openai|gpt-4o")
    assert result.prompt_cache_hit is False


def test_tool_use_canonicalized(fake_openai_module: Any) -> None:
    from phoenix.providers.cognition.openai import OpenAIProvider

    response = _make_openai_response(
        text="checking",
        tool_calls=[
            {"id": "call_1", "name": "lookup", "arguments": '{"q": "phoenix"}'},
        ],
    )
    client = _make_client(MagicMock(return_value=response))
    adapter = OpenAIProvider(model="gpt-4o", api_key="test", client=client)
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "lookup"}])
    tools = [Tool(name="lookup", description="search", input_schema={"type": "object"})]

    result = adapter.complete(prompt, max_tokens=100, temperature=0.0, tools=tools)

    assert len(result.tool_calls) == 1
    tc = result.tool_calls[0]
    assert tc.call_id == "call_1"
    assert tc.name == "lookup"
    assert tc.arguments == {"q": "phoenix"}


def test_prompt_cache_hit_surfaced(fake_openai_module: Any) -> None:
    from phoenix.providers.cognition.openai import OpenAIProvider

    response = _make_openai_response(cached_tokens=300, prompt_tokens=1000)
    client = _make_client(MagicMock(return_value=response))
    adapter = OpenAIProvider(model="gpt-4o", api_key="test", client=client)
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])

    result = adapter.complete(prompt, max_tokens=10, temperature=0.0)

    assert result.prompt_cache_hit is True
    assert result.usage.cached_input_tokens == 300


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
    fake_openai_module: Any, raise_exc_cls_attr: str, expected_phoenix: type
) -> None:
    from phoenix.providers.cognition.openai import OpenAIProvider

    sdk_cls = getattr(fake_openai_module, raise_exc_cls_attr)
    client = _make_client(MagicMock(side_effect=sdk_cls()))
    adapter = OpenAIProvider(model="gpt-4o", api_key="test", client=client, max_retries=0)
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])

    with patch("phoenix.providers.cognition._base.time.sleep"):
        with pytest.raises(expected_phoenix):
            adapter.complete(prompt, max_tokens=10, temperature=0.0)


def test_rate_limit_extracts_retry_after(fake_openai_module: Any) -> None:
    from phoenix.providers.cognition.openai import OpenAIProvider

    headers = {"retry-after": "2.0"}
    response = SimpleNamespace(headers=headers)
    exc = fake_openai_module.RateLimitError("rl", response=response)
    client = _make_client(MagicMock(side_effect=exc))
    adapter = OpenAIProvider(model="gpt-4o", api_key="test", client=client, max_retries=0)
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])

    with patch("phoenix.providers.cognition._base.time.sleep"):
        with pytest.raises(CognitionRateLimitError) as exc_info:
            adapter.complete(prompt, max_tokens=10, temperature=0.0)
    assert exc_info.value.retry_after_seconds == 2.0


def test_bad_request_context_length(fake_openai_module: Any) -> None:
    from phoenix.providers.cognition.openai import OpenAIProvider

    body = {"error": {"code": "context_length_exceeded"}}
    exc = fake_openai_module.BadRequestError("context_length_exceeded", body=body)
    client = _make_client(MagicMock(side_effect=exc))
    adapter = OpenAIProvider(model="gpt-4o", api_key="test", client=client, max_retries=0)
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])

    with pytest.raises(CognitionContextLengthError):
        adapter.complete(prompt, max_tokens=10, temperature=0.0)


def test_bad_request_content_policy(fake_openai_module: Any) -> None:
    from phoenix.providers.cognition.openai import OpenAIProvider

    body = {"error": {"code": "content_policy_violation"}}
    exc = fake_openai_module.BadRequestError("blocked", body=body)
    client = _make_client(MagicMock(side_effect=exc))
    adapter = OpenAIProvider(model="gpt-4o", api_key="test", client=client, max_retries=0)
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])

    with pytest.raises(CognitionContentPolicyError) as exc_info:
        adapter.complete(prompt, max_tokens=10, temperature=0.0)
    assert exc_info.value.reason_code == "content_policy_violation"


def test_system_prompt_prepended(fake_openai_module: Any) -> None:
    """Phoenix's Prompt.system becomes the first message with role=system."""
    from phoenix.providers.cognition.openai import OpenAIProvider

    create_mock = MagicMock(return_value=_make_openai_response())
    client = _make_client(create_mock)
    adapter = OpenAIProvider(model="gpt-4o", api_key="test", client=client)
    prompt = Prompt(system="you are brief", messages=[{"role": "user", "content": "hi"}])

    adapter.complete(prompt, max_tokens=10, temperature=0.0)

    create_mock.assert_called_once()
    sent_messages = create_mock.call_args.kwargs["messages"]
    assert sent_messages[0] == {"role": "system", "content": "you are brief"}
    assert sent_messages[1] == {"role": "user", "content": "hi"}
