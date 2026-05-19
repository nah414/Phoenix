"""``OpenAIProvider`` — adapter for the OpenAI Chat Completions API.

Wraps the ``openai`` Python SDK (pinned ``>=1.50, <2.0``). Maps
Phoenix's :class:`Prompt` + :class:`Tool` to OpenAI's chat-completions
format; handles tool-use schema differences from Anthropic's;
exposes the Batch API via ``CognitionCapabilities.supports_batch=True``.

Canonical models (as of 2026-05-18):

- ``gpt-5``
- ``gpt-4o``
- ``gpt-4o-mini``

**SAFETY:** ``OPENAI_API_KEY`` is read from the environment only.
:class:`CognitionAuthError` is raised at construction if it is missing.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from phoenix.providers.cognition._base import _CognitionAdapterBase
from phoenix.providers.cognition.capabilities import CognitionCapabilities
from phoenix.providers.cognition.errors import (
    CognitionAuthError,
    CognitionContentPolicyError,
    CognitionContextLengthError,
    CognitionError,
    CognitionRateLimitError,
    CognitionTimeoutError,
    CognitionUnavailable,
)
from phoenix.providers.cognition.types import (
    CognitionResult,
    Prompt,
    TokenUsage,
    Tool,
    ToolCall,
)

if TYPE_CHECKING:
    import openai

_STATIC_CAPABILITIES: dict[str, CognitionCapabilities] = {
    "gpt-5": CognitionCapabilities(
        streaming=True,
        tool_use=True,
        vision=True,
        max_context_tokens=400_000,
        supports_prompt_cache=True,
        supports_batch=True,
    ),
    "gpt-4o": CognitionCapabilities(
        streaming=True,
        tool_use=True,
        vision=True,
        max_context_tokens=128_000,
        supports_prompt_cache=True,
        supports_batch=True,
    ),
    "gpt-4o-mini": CognitionCapabilities(
        streaming=True,
        tool_use=True,
        vision=True,
        max_context_tokens=128_000,
        supports_prompt_cache=True,
        supports_batch=True,
    ),
}


class OpenAIProvider(_CognitionAdapterBase):
    """Phoenix adapter for the OpenAI Chat Completions API."""

    provider_id = "openai"
    _api_key_env_var = "OPENAI_API_KEY"

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        client: openai.OpenAI | None = None,
        timeout_sec: float = 60.0,
        max_retries: int = 3,
    ) -> None:
        super().__init__(model=model, api_key=api_key, max_retries=max_retries)
        self._timeout_sec = timeout_sec
        if client is not None:
            self._client = client
        else:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise CognitionError(
                    "openai SDK not installed; "
                    "install via `pip install phoenix-middleware[openai]`."
                ) from exc
            self._client = OpenAI(api_key=self._api_key, timeout=timeout_sec)

    def _do_complete(
        self,
        prompt: Prompt,
        *,
        max_tokens: int,
        temperature: float,
        tools: list[Tool] | None,
        stream: bool,
    ) -> CognitionResult:
        del stream  # Step 7 lands streaming.

        # OpenAI flat-messages: system is just the first message.
        messages: list[dict[str, Any]] = []
        if prompt.system is not None:
            messages.append({"role": "system", "content": prompt.system})
        messages.extend(prompt.messages)

        def _call() -> Any:
            kwargs: dict[str, Any] = {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": messages,
            }
            if tools:
                kwargs["tools"] = [self._tool_to_openai(t) for t in tools]
            return self._client.chat.completions.create(**kwargs)

        t0 = time.perf_counter()
        response = self._wrap_sdk_call(_call)
        latency_ms = (time.perf_counter() - t0) * 1000.0

        if not getattr(response, "choices", None):
            # OpenAI should always return at least one choice; defend
            # against an empty response shape just in case.
            return CognitionResult(
                text="",
                tool_calls=[],
                usage=TokenUsage(input_tokens=0, output_tokens=0),
                latency_ms=latency_ms,
                provider_fingerprint=self.fingerprint(),
            )

        choice = response.choices[0]
        message = getattr(choice, "message", None)
        text = getattr(message, "content", None) or ""
        raw_tool_calls = getattr(message, "tool_calls", None) or []

        tool_calls: list[ToolCall] = []
        for tc in raw_tool_calls:
            fn = getattr(tc, "function", None)
            name = getattr(fn, "name", "") if fn is not None else ""
            args_raw = getattr(fn, "arguments", "{}") if fn is not None else "{}"
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
            except json.JSONDecodeError:
                args = {"_raw_arguments": args_raw}
            tool_calls.append(ToolCall(call_id=getattr(tc, "id", ""), name=name, arguments=args))

        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0)) if usage else 0
        completion_tokens = int(getattr(usage, "completion_tokens", 0)) if usage else 0
        cached_tokens = 0
        if usage is not None:
            details = getattr(usage, "prompt_tokens_details", None)
            if details is not None:
                cached_tokens = int(getattr(details, "cached_tokens", 0) or 0)

        return CognitionResult(
            text=text,
            tool_calls=tool_calls,
            usage=TokenUsage(
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
                cached_input_tokens=cached_tokens,
            ),
            latency_ms=latency_ms,
            provider_fingerprint=self.fingerprint(),
            prompt_cache_hit=(cached_tokens > 0) if usage is not None else None,
        )

    def capabilities(self) -> CognitionCapabilities:
        caps = _STATIC_CAPABILITIES.get(self.model)
        if caps is None:
            return CognitionCapabilities(
                streaming=False,
                tool_use=False,
                vision=False,
                max_context_tokens=8192,
                supports_prompt_cache=False,
                supports_batch=False,
            )
        return caps

    def fingerprint(self) -> str:
        return f"{self.provider_id}|{self.model}|step-2"

    def _map_sdk_exception(self, exc: Exception) -> CognitionError | None:
        try:
            from openai import (
                APIConnectionError,
                APITimeoutError,
                AuthenticationError,
                BadRequestError,
                InternalServerError,
                RateLimitError,
            )
        except ImportError:
            return None

        if isinstance(exc, AuthenticationError):
            return CognitionAuthError(f"{self.provider_id}: authentication rejected.")
        if isinstance(exc, RateLimitError):
            retry_after = _extract_retry_after(exc)
            return CognitionRateLimitError(
                f"{self.provider_id}: rate-limited.", retry_after_seconds=retry_after
            )
        if isinstance(exc, APITimeoutError):
            return CognitionTimeoutError(f"{self.provider_id}: request timed out.")
        if isinstance(exc, APIConnectionError):
            return CognitionUnavailable(f"{self.provider_id}: connection error: {exc}")
        if isinstance(exc, InternalServerError):
            return CognitionUnavailable(f"{self.provider_id}: provider 5xx: {exc}")
        if isinstance(exc, BadRequestError):
            msg = str(exc).lower()
            code = _extract_error_code(exc) or ""
            if "context_length_exceeded" in code or "context" in msg or "max_tokens" in msg:
                return CognitionContextLengthError(f"{self.provider_id}: context length exceeded.")
            if (
                code in {"content_policy_violation", "moderation_blocked"}
                or "policy" in msg
                or "refused" in msg
            ):
                return CognitionContentPolicyError(
                    f"{self.provider_id}: content policy refusal.", reason_code=code or None
                )
            return CognitionError(f"{self.provider_id}: bad request: {exc}")
        return None

    @staticmethod
    def _tool_to_openai(t: Tool) -> dict[str, Any]:
        """Translate a Phoenix :class:`Tool` to OpenAI's tool shape."""
        return {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }


def _extract_retry_after(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    if response is None:
        return None
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _extract_error_code(exc: Exception) -> str | None:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return str(err.get("code") or err.get("type") or "")
    return None
