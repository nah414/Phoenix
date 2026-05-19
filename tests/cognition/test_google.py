"""Tests for the GoogleGeminiProvider adapter."""

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
def fake_google_modules(monkeypatch: pytest.MonkeyPatch) -> dict[str, types.ModuleType]:
    """Install fake ``google.generativeai`` and ``google.api_core.exceptions`` modules."""
    gpkg = types.ModuleType("google")
    genai = types.ModuleType("google.generativeai")

    class GenerativeModel:
        def __init__(self, model_name: str = "test") -> None:
            self.model_name = model_name
            self.generate_content = MagicMock()

    def configure(**_: Any) -> None:
        return None

    genai.GenerativeModel = GenerativeModel
    genai.configure = configure

    api_core = types.ModuleType("google.api_core")
    gexc = types.ModuleType("google.api_core.exceptions")

    class PermissionDenied(Exception):
        pass

    class Unauthenticated(Exception):
        pass

    class ResourceExhausted(Exception):
        def __init__(self, msg: str = "rl", metadata: Any = None) -> None:
            super().__init__(msg)
            self.metadata = metadata

    class DeadlineExceeded(Exception):
        pass

    class ServiceUnavailable(Exception):
        pass

    class InternalServerError(Exception):
        pass

    class InvalidArgument(Exception):
        pass

    gexc.PermissionDenied = PermissionDenied
    gexc.Unauthenticated = Unauthenticated
    gexc.ResourceExhausted = ResourceExhausted
    gexc.DeadlineExceeded = DeadlineExceeded
    gexc.ServiceUnavailable = ServiceUnavailable
    gexc.InternalServerError = InternalServerError
    gexc.InvalidArgument = InvalidArgument
    api_core.exceptions = gexc

    monkeypatch.setitem(sys.modules, "google", gpkg)
    monkeypatch.setitem(sys.modules, "google.generativeai", genai)
    monkeypatch.setitem(sys.modules, "google.api_core", api_core)
    monkeypatch.setitem(sys.modules, "google.api_core.exceptions", gexc)
    return {"genai": genai, "exceptions": gexc}


def _make_gemini_response(
    *,
    text: str = "hello",
    function_calls: list[dict[str, Any]] | None = None,
    prompt_tokens: int = 10,
    output_tokens: int = 5,
    finish_reason: str = "STOP",
) -> Any:
    parts: list[Any] = []
    if text:
        parts.append(SimpleNamespace(text=text, function_call=None))
    if function_calls:
        for fc in function_calls:
            parts.append(
                SimpleNamespace(
                    text="",
                    function_call=SimpleNamespace(
                        id=fc.get("id", ""),
                        name=fc["name"],
                        args=fc["args"],
                    ),
                )
            )
    content = SimpleNamespace(parts=parts)
    candidate = SimpleNamespace(content=content, finish_reason=finish_reason)
    usage = SimpleNamespace(
        prompt_token_count=prompt_tokens,
        candidates_token_count=output_tokens,
    )
    return SimpleNamespace(candidates=[candidate], usage_metadata=usage)


def test_happy_path_text_response(fake_google_modules: Any) -> None:
    from phoenix.providers.cognition.google import GoogleGeminiProvider

    client = SimpleNamespace(generate_content=MagicMock(return_value=_make_gemini_response()))
    adapter = GoogleGeminiProvider(model="gemini-2.5-pro", api_key="test", client=client)
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])

    result = adapter.complete(prompt, max_tokens=100, temperature=0.0)

    assert result.text == "hello"
    assert result.tool_calls == []
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 5
    assert result.usage.cached_input_tokens == 0  # Gemini caching deferred
    assert result.provider_fingerprint.startswith("google|gemini-2.5-pro")


def test_function_call_canonicalized(fake_google_modules: Any) -> None:
    from phoenix.providers.cognition.google import GoogleGeminiProvider

    response = _make_gemini_response(
        text="",
        function_calls=[{"name": "lookup", "args": {"q": "phoenix"}, "id": "fc_1"}],
    )
    client = SimpleNamespace(generate_content=MagicMock(return_value=response))
    adapter = GoogleGeminiProvider(model="gemini-2.5-pro", api_key="test", client=client)
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "lookup"}])
    tools = [Tool(name="lookup", description="search", input_schema={"type": "object"})]

    result = adapter.complete(prompt, max_tokens=100, temperature=0.0, tools=tools)

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "lookup"
    assert result.tool_calls[0].arguments == {"q": "phoenix"}


def test_safety_block_raises_content_policy(fake_google_modules: Any) -> None:
    """Gemini's SAFETY finish_reason maps to CognitionContentPolicyError."""
    from phoenix.providers.cognition.google import GoogleGeminiProvider

    response = _make_gemini_response(text="", finish_reason="SAFETY")
    client = SimpleNamespace(generate_content=MagicMock(return_value=response))
    adapter = GoogleGeminiProvider(model="gemini-2.5-pro", api_key="test", client=client)
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])

    with pytest.raises(CognitionContentPolicyError) as exc_info:
        adapter.complete(prompt, max_tokens=10, temperature=0.0)
    assert exc_info.value.reason_code == "SAFETY"


@pytest.mark.parametrize(
    "raise_exc_cls_attr,expected_phoenix",
    [
        ("PermissionDenied", CognitionAuthError),
        ("Unauthenticated", CognitionAuthError),
        ("DeadlineExceeded", CognitionTimeoutError),
        ("ServiceUnavailable", CognitionUnavailable),
        ("InternalServerError", CognitionUnavailable),
    ],
)
def test_error_mapping_simple(
    fake_google_modules: Any, raise_exc_cls_attr: str, expected_phoenix: type
) -> None:
    from phoenix.providers.cognition.google import GoogleGeminiProvider

    sdk_cls = getattr(fake_google_modules["exceptions"], raise_exc_cls_attr)
    client = SimpleNamespace(generate_content=MagicMock(side_effect=sdk_cls()))
    adapter = GoogleGeminiProvider(
        model="gemini-2.5-pro", api_key="test", client=client, max_retries=0
    )
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])

    with patch("phoenix.providers.cognition._base.time.sleep"):
        with pytest.raises(expected_phoenix):
            adapter.complete(prompt, max_tokens=10, temperature=0.0)


def test_rate_limit_resource_exhausted(fake_google_modules: Any) -> None:
    from phoenix.providers.cognition.google import GoogleGeminiProvider

    exc = fake_google_modules["exceptions"].ResourceExhausted(
        "quota", metadata=[("retry-after", "0.5")]
    )
    client = SimpleNamespace(generate_content=MagicMock(side_effect=exc))
    adapter = GoogleGeminiProvider(
        model="gemini-2.5-pro", api_key="test", client=client, max_retries=0
    )
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])

    with patch("phoenix.providers.cognition._base.time.sleep"):
        with pytest.raises(CognitionRateLimitError) as exc_info:
            adapter.complete(prompt, max_tokens=10, temperature=0.0)
    assert exc_info.value.retry_after_seconds == 0.5


def test_invalid_argument_context_length(fake_google_modules: Any) -> None:
    from phoenix.providers.cognition.google import GoogleGeminiProvider

    exc = fake_google_modules["exceptions"].InvalidArgument("input token limit exceeded")
    client = SimpleNamespace(generate_content=MagicMock(side_effect=exc))
    adapter = GoogleGeminiProvider(
        model="gemini-2.5-pro", api_key="test", client=client, max_retries=0
    )
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])

    with pytest.raises(CognitionContextLengthError):
        adapter.complete(prompt, max_tokens=10, temperature=0.0)


def test_system_prompt_encoded_as_leading_user_turn(fake_google_modules: Any) -> None:
    """Phoenix's Prompt.system becomes a [System context] leading turn."""
    from phoenix.providers.cognition.google import GoogleGeminiProvider

    create_mock = MagicMock(return_value=_make_gemini_response())
    client = SimpleNamespace(generate_content=create_mock)
    adapter = GoogleGeminiProvider(model="gemini-2.5-pro", api_key="test", client=client)
    prompt = Prompt(system="be brief", messages=[{"role": "user", "content": "hi"}])

    adapter.complete(prompt, max_tokens=10, temperature=0.0)

    create_mock.assert_called_once()
    sent_contents = create_mock.call_args.kwargs["contents"]
    assert sent_contents[0]["role"] == "user"
    assert "System context" in sent_contents[0]["parts"][0]["text"]
    assert "be brief" in sent_contents[0]["parts"][0]["text"]
    assert sent_contents[1]["role"] == "model"  # acknowledgement


def test_role_translation(fake_google_modules: Any) -> None:
    """Phoenix assistant role maps to Gemini model role."""
    from phoenix.providers.cognition.google import GoogleGeminiProvider

    create_mock = MagicMock(return_value=_make_gemini_response())
    client = SimpleNamespace(generate_content=create_mock)
    adapter = GoogleGeminiProvider(model="gemini-2.5-pro", api_key="test", client=client)
    prompt = Prompt(
        system=None,
        messages=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello back"},
            {"role": "user", "content": "how are you"},
        ],
    )

    adapter.complete(prompt, max_tokens=10, temperature=0.0)

    sent_contents = create_mock.call_args.kwargs["contents"]
    assert [c["role"] for c in sent_contents] == ["user", "model", "user"]
