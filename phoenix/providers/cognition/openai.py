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

    async def astream(
        self,
        prompt: Prompt,
        *,
        max_tokens: int,
        temperature: float,
        tools: list[Tool] | None = None,
    ) -> Any:  # AsyncIterator[StreamEvent]; relaxed for mypy on lazy SDK
        """Stream completion events for ``prompt`` (Phase 13.x.2).

        Yields :class:`StreamTokenDelta` / :class:`StreamToolCallStart`
        / :class:`StreamFinal` events as the OpenAI SDK reports them.
        Mirrors :meth:`AnthropicProvider.astream` shipped at Phase 13
        Step 7; production injects an :class:`openai.AsyncOpenAI` client
        for the streaming path.

        OpenAI's streaming chunks differ from Anthropic's in two ways:

        1. Each chunk carries a per-choice ``delta`` with optional
           ``content`` (text fragment) + optional ``tool_calls`` array.
           Tool-call arguments arrive as concatenated JSON fragments
           across chunks — we accumulate per ``index`` and parse at
           the final aggregate step.
        2. Final usage info arrives on the LAST chunk (one with empty
           ``choices``) when ``stream_options={"include_usage": True}``
           is set. We always set that option so the aggregate
           :class:`CognitionResult` has accurate token counts.
        """
        from phoenix.providers.cognition.streaming import (
            StreamFinal,
            StreamTokenDelta,
            StreamToolCallStart,
        )

        # Build the request payload (mirrors _do_complete's prep).
        messages: list[dict[str, Any]] = []
        if prompt.system is not None:
            messages.append({"role": "system", "content": prompt.system})
        messages.extend(prompt.messages)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = [self._tool_to_openai(t) for t in tools]

        t0 = time.perf_counter()
        cumulative_tokens = 0
        text_parts: list[str] = []
        # Per-index accumulator for tool-call fragments.
        tool_calls_acc: dict[int, dict[str, Any]] = {}
        final_usage: Any = None

        # Cast to Any at the stream-call seam: production injects
        # AsyncOpenAI; tests inject duck-typed fakes. The cast isolates
        # the type-loose seam to this one line (matches AnthropicProvider).
        async_client: Any = self._client
        try:
            stream = await async_client.chat.completions.create(**kwargs)
            async for chunk in stream:
                # Last chunk: empty choices, populated usage (when
                # stream_options.include_usage=True).
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    usage_obj = getattr(chunk, "usage", None)
                    if usage_obj is not None:
                        final_usage = usage_obj
                    continue

                delta = getattr(choices[0], "delta", None)
                if delta is None:
                    continue

                # Text deltas.
                text = getattr(delta, "content", None)
                if text:
                    text_parts.append(text)
                    cumulative_tokens += max(1, len(text) // 4)
                    yield StreamTokenDelta(
                        delta_text=text,
                        cumulative_tokens=cumulative_tokens,
                    )

                # Tool-call announcements + argument fragments.
                tc_deltas = getattr(delta, "tool_calls", None) or []
                for tc in tc_deltas:
                    idx = getattr(tc, "index", 0)
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": getattr(tc, "id", "") or "",
                            "name": "",
                            "args_chunks": [],
                            "announced": False,
                        }
                    # Update id if it arrives in a later chunk.
                    if not tool_calls_acc[idx]["id"]:
                        new_id = getattr(tc, "id", "") or ""
                        if new_id:
                            tool_calls_acc[idx]["id"] = new_id
                    fn = getattr(tc, "function", None)
                    if fn is not None:
                        name = getattr(fn, "name", None)
                        if name and not tool_calls_acc[idx]["name"]:
                            tool_calls_acc[idx]["name"] = name
                        args_chunk = getattr(fn, "arguments", None)
                        if args_chunk:
                            tool_calls_acc[idx]["args_chunks"].append(args_chunk)
                    # Announce once we have BOTH an id and a name.
                    if (
                        not tool_calls_acc[idx]["announced"]
                        and tool_calls_acc[idx]["id"]
                        and tool_calls_acc[idx]["name"]
                    ):
                        tool_calls_acc[idx]["announced"] = True
                        yield StreamToolCallStart(
                            call_id=tool_calls_acc[idx]["id"],
                            tool_name=tool_calls_acc[idx]["name"],
                            partial_args={},
                        )
        except Exception as exc:
            mapped = self._map_sdk_exception(exc)
            if mapped is not None:
                raise mapped from exc
            raise

        latency_ms = (time.perf_counter() - t0) * 1000.0

        # Assemble final tool calls from accumulated fragments.
        tool_calls: list[ToolCall] = []
        for idx in sorted(tool_calls_acc.keys()):
            acc = tool_calls_acc[idx]
            args_raw = "".join(acc["args_chunks"])
            try:
                args = json.loads(args_raw) if args_raw else {}
            except json.JSONDecodeError:
                args = {"_raw_arguments": args_raw}
            tool_calls.append(ToolCall(call_id=acc["id"], name=acc["name"], arguments=args))

        prompt_tokens = int(getattr(final_usage, "prompt_tokens", 0)) if final_usage else 0
        completion_tokens = int(getattr(final_usage, "completion_tokens", 0)) if final_usage else 0
        cached_tokens = 0
        if final_usage is not None:
            details = getattr(final_usage, "prompt_tokens_details", None)
            if details is not None:
                cached_tokens = int(getattr(details, "cached_tokens", 0) or 0)

        result = CognitionResult(
            text="".join(text_parts),
            tool_calls=tool_calls,
            usage=TokenUsage(
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
                cached_input_tokens=cached_tokens,
            ),
            latency_ms=latency_ms,
            provider_fingerprint=self.fingerprint(),
            prompt_cache_hit=(cached_tokens > 0) if final_usage is not None else None,
        )
        yield StreamFinal(result=result)

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
