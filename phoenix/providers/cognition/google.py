"""``GoogleGeminiProvider`` — adapter for the Google Gemini API.

Wraps the ``google-generativeai`` Python SDK. Maps Phoenix's
:class:`Prompt` + :class:`Tool` to Gemini's contents-with-roles format.
Gemini's long-context support is significant: 2M tokens for
Gemini 1.5 Pro, up to 3M for Gemini 2.x; the capability advertise
should be live-read from the model metadata in production. Step 2
ships a static fallback list.

Canonical models (as of 2026-05-18):

- ``gemini-2.5-pro``
- ``gemini-2.0-flash``

**SAFETY:** ``GOOGLE_API_KEY`` is read from the environment only.
:class:`CognitionAuthError` is raised at construction if it is missing.

**Caveat:** Gemini's context-caching API differs from Anthropic's and
OpenAI's (it's separately constructed, not auto-detected). Step 2
ships ``cached_input_tokens=0`` for Gemini results until the explicit
context-caching path lands (Step 7+ or v1.2).
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
    pass

_STATIC_CAPABILITIES: dict[str, CognitionCapabilities] = {
    "gemini-2.5-pro": CognitionCapabilities(
        streaming=True,
        tool_use=True,
        vision=True,
        max_context_tokens=2_000_000,
        supports_prompt_cache=False,  # explicit caching API; Step 2 reports False.
        supports_batch=False,
    ),
    "gemini-2.0-flash": CognitionCapabilities(
        streaming=True,
        tool_use=True,
        vision=True,
        max_context_tokens=1_000_000,
        supports_prompt_cache=False,
        supports_batch=False,
    ),
}

# Gemini roles use "user" / "model" (not "user" / "assistant"). Map Phoenix
# canonical roles to Gemini's roles.
_PHOENIX_TO_GEMINI_ROLE = {
    "user": "user",
    "assistant": "model",
    "tool_result": "user",  # Gemini surfaces tool results as user-side function_response parts.
}


class GoogleGeminiProvider(_CognitionAdapterBase):
    """Phoenix adapter for the Google Gemini API."""

    provider_id = "google"
    _api_key_env_var = "GOOGLE_API_KEY"

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        client: Any = None,
        timeout_sec: float = 60.0,
        max_retries: int = 3,
    ) -> None:
        """Construct the adapter.

        ``client`` is duck-typed (``Any``) because the Gemini SDK's
        :class:`genai.GenerativeModel` does not inherit from a stable
        public base class; tests inject a mock with ``generate_content``
        method.
        """
        super().__init__(model=model, api_key=api_key, max_retries=max_retries)
        self._timeout_sec = timeout_sec
        if client is not None:
            self._client = client
        else:
            try:
                import google.generativeai as genai
            except ImportError as exc:
                raise CognitionError(
                    "google-generativeai SDK not installed; "
                    "install via `pip install phoenix-middleware[google]`."
                ) from exc
            genai.configure(api_key=self._api_key)
            # Phase 13 Step 2 ships without system_instruction support
            # for tools-only flows; system text is passed at generate
            # time via the contents list (Gemini accepts a leading
            # "user" turn that acts as system context). This is a
            # pragmatic Step-2 trade-off; full system_instruction
            # mapping is a Step 2 follow-up.
            self._client = genai.GenerativeModel(model_name=self.model)

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

        contents = self._prompt_to_gemini(prompt)
        generation_config: dict[str, Any] = {
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        }

        def _call() -> Any:
            kwargs: dict[str, Any] = {
                "contents": contents,
                "generation_config": generation_config,
            }
            if tools:
                kwargs["tools"] = [self._tool_to_gemini(t) for t in tools]
            return self._client.generate_content(**kwargs)

        t0 = time.perf_counter()
        response = self._wrap_sdk_call(_call)
        latency_ms = (time.perf_counter() - t0) * 1000.0

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            content = getattr(candidates[0], "content", None)
            parts = getattr(content, "parts", None) or []
            for part in parts:
                if hasattr(part, "text") and getattr(part, "text"):
                    text_parts.append(part.text)
                fn_call = getattr(part, "function_call", None)
                if fn_call is not None:
                    args_raw = getattr(fn_call, "args", {}) or {}
                    # Gemini args is a dict-like (proto Map). Coerce to dict.
                    try:
                        args = dict(args_raw)
                    except (TypeError, ValueError):
                        args = {"_raw_arguments": str(args_raw)}
                    tool_calls.append(
                        ToolCall(
                            call_id=getattr(fn_call, "id", "") or "",
                            name=getattr(fn_call, "name", ""),
                            arguments=args,
                        )
                    )

            finish_reason = getattr(candidates[0], "finish_reason", None)
            if _is_safety_block(finish_reason):
                raise CognitionContentPolicyError(
                    f"{self.provider_id}: content policy refusal.",
                    reason_code=str(finish_reason),
                )

        usage = getattr(response, "usage_metadata", None)
        input_tokens = int(getattr(usage, "prompt_token_count", 0)) if usage else 0
        output_tokens = int(getattr(usage, "candidates_token_count", 0)) if usage else 0

        return CognitionResult(
            text="".join(text_parts),
            tool_calls=tool_calls,
            usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=0,  # See module docstring caveat.
            ),
            latency_ms=latency_ms,
            provider_fingerprint=self.fingerprint(),
            prompt_cache_hit=None,
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
        / :class:`StreamFinal` events as the Gemini SDK reports them.

        Gemini's streaming shape differs from Anthropic's and OpenAI's:

        1. The async streaming entry is
           ``model.generate_content_async(stream=True)`` which returns
           an async iterator over chunks. Each chunk carries
           ``candidates[0].content.parts`` with optional ``text`` or
           ``function_call`` on each part.
        2. Function calls arrive as complete objects on the part (not as
           streamed argument fragments like OpenAI). When a chunk
           contains a ``function_call`` part, we emit the
           :class:`StreamToolCallStart` event with the complete args
           parsed at that moment — no per-fragment accumulation.
        3. Usage metadata arrives on the FINAL chunk's
           ``usage_metadata`` (when the SDK populates it). Cached
           tokens are not surfaced (matches the sync ``_do_complete``
           caveat documented in the module docstring).

        **Production note:** the sync ``GenerativeModel`` client's
        ``generate_content_async`` is itself an async method on the
        same client object, so a single client instance supports both
        sync ``_do_complete`` and async ``astream`` — no separate
        async client construction is needed (contrast with
        Anthropic/OpenAI which have distinct ``Async*`` classes).
        """
        from phoenix.providers.cognition.streaming import (
            StreamFinal,
            StreamTokenDelta,
            StreamToolCallStart,
        )

        contents = self._prompt_to_gemini(prompt)
        generation_config: dict[str, Any] = {
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        }
        kwargs: dict[str, Any] = {
            "contents": contents,
            "generation_config": generation_config,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = [self._tool_to_gemini(t) for t in tools]

        t0 = time.perf_counter()
        cumulative_tokens = 0
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        final_usage: Any = None
        final_finish_reason: Any = None

        async_client: Any = self._client
        try:
            stream = await async_client.generate_content_async(**kwargs)
            async for chunk in stream:
                candidates = getattr(chunk, "candidates", None) or []
                if candidates:
                    content = getattr(candidates[0], "content", None)
                    parts = getattr(content, "parts", None) or []
                    for part in parts:
                        # Text delta.
                        text = getattr(part, "text", None)
                        if text:
                            text_parts.append(text)
                            cumulative_tokens += max(1, len(text) // 4)
                            yield StreamTokenDelta(
                                delta_text=text,
                                cumulative_tokens=cumulative_tokens,
                            )
                        # Function call announcement (Gemini delivers as
                        # a complete part rather than fragmented args).
                        fn_call = getattr(part, "function_call", None)
                        if fn_call is not None:
                            args_raw = getattr(fn_call, "args", {}) or {}
                            try:
                                args = dict(args_raw)
                            except (TypeError, ValueError):
                                args = {"_raw_arguments": str(args_raw)}
                            call_id = getattr(fn_call, "id", "") or ""
                            tool_name = getattr(fn_call, "name", "")
                            yield StreamToolCallStart(
                                call_id=call_id,
                                tool_name=tool_name,
                                partial_args=args,
                            )
                            tool_calls.append(
                                ToolCall(
                                    call_id=call_id,
                                    name=tool_name,
                                    arguments=args,
                                )
                            )
                    # Track the LATEST finish_reason; SDK reports per-chunk.
                    fr = getattr(candidates[0], "finish_reason", None)
                    if fr is not None:
                        final_finish_reason = fr

                # Usage metadata typically arrives on the final chunk.
                usage_chunk = getattr(chunk, "usage_metadata", None)
                if usage_chunk is not None:
                    final_usage = usage_chunk
        except Exception as exc:
            mapped = self._map_sdk_exception(exc)
            if mapped is not None:
                raise mapped from exc
            raise

        # Post-stream safety check — matches the sync path's behavior.
        if _is_safety_block(final_finish_reason):
            raise CognitionContentPolicyError(
                f"{self.provider_id}: content policy refusal.",
                reason_code=str(final_finish_reason),
            )

        latency_ms = (time.perf_counter() - t0) * 1000.0

        input_tokens = int(getattr(final_usage, "prompt_token_count", 0)) if final_usage else 0
        output_tokens = int(getattr(final_usage, "candidates_token_count", 0)) if final_usage else 0

        result = CognitionResult(
            text="".join(text_parts),
            tool_calls=tool_calls,
            usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=0,  # See module docstring caveat.
            ),
            latency_ms=latency_ms,
            provider_fingerprint=self.fingerprint(),
            prompt_cache_hit=None,
        )
        yield StreamFinal(result=result)

    def capabilities(self) -> CognitionCapabilities:
        caps = _STATIC_CAPABILITIES.get(self.model)
        if caps is None:
            return CognitionCapabilities(
                streaming=False,
                tool_use=False,
                vision=False,
                max_context_tokens=32_000,
                supports_prompt_cache=False,
                supports_batch=False,
            )
        return caps

    def fingerprint(self) -> str:
        return f"{self.provider_id}|{self.model}|step-2"

    def _map_sdk_exception(self, exc: Exception) -> CognitionError | None:
        try:
            from google.api_core import exceptions as gexc
        except ImportError:
            return None

        if isinstance(exc, gexc.PermissionDenied) or isinstance(exc, gexc.Unauthenticated):
            return CognitionAuthError(f"{self.provider_id}: authentication rejected.")
        if isinstance(exc, gexc.ResourceExhausted):
            return CognitionRateLimitError(
                f"{self.provider_id}: rate-limited.",
                retry_after_seconds=_extract_retry_delay(exc),
            )
        if isinstance(exc, gexc.DeadlineExceeded):
            return CognitionTimeoutError(f"{self.provider_id}: request timed out.")
        if isinstance(exc, gexc.ServiceUnavailable) or isinstance(exc, gexc.InternalServerError):
            return CognitionUnavailable(f"{self.provider_id}: provider 5xx: {exc}")
        if isinstance(exc, gexc.InvalidArgument):
            msg = str(exc).lower()
            if "context" in msg or "token" in msg or "too long" in msg:
                return CognitionContextLengthError(f"{self.provider_id}: context length exceeded.")
            return CognitionError(f"{self.provider_id}: invalid argument: {exc}")
        return None

    @staticmethod
    def _tool_to_gemini(t: Tool) -> dict[str, Any]:
        """Translate a Phoenix :class:`Tool` to Gemini's tool shape.

        Gemini's tool shape wraps function declarations in a
        ``function_declarations`` list; each declaration carries name +
        description + parameters (JSON Schema-compatible).
        """
        return {
            "function_declarations": [
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                }
            ]
        }

    def _prompt_to_gemini(self, prompt: Prompt) -> list[dict[str, Any]]:
        """Translate Phoenix :class:`Prompt` to Gemini's contents-with-roles.

        Phoenix's system prompt becomes a leading user turn prefixed
        with a system-context marker. This is the pragmatic Step-2
        encoding; full ``system_instruction`` support lands later.
        """
        contents: list[dict[str, Any]] = []
        if prompt.system is not None:
            contents.append(
                {
                    "role": "user",
                    "parts": [{"text": f"[System context]\n{prompt.system}"}],
                }
            )
            contents.append(
                {
                    "role": "model",
                    "parts": [{"text": "Acknowledged."}],
                }
            )
        for msg in prompt.messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            gemini_role = _PHOENIX_TO_GEMINI_ROLE.get(role, "user")
            parts = (
                [{"text": content}] if isinstance(content, str) else [{"text": json.dumps(content)}]
            )
            contents.append({"role": gemini_role, "parts": parts})
        return contents


def _is_safety_block(finish_reason: Any) -> bool:
    """Return True when a Gemini finish_reason indicates a safety block."""
    if finish_reason is None:
        return False
    s = str(finish_reason).upper()
    return "SAFETY" in s or "BLOCKLIST" in s or "RECITATION" in s or "PROHIBITED" in s


def _extract_retry_delay(exc: Exception) -> float | None:
    """Pull a retry-after hint from a Google API exception's metadata."""
    metadata = getattr(exc, "metadata", None)
    if metadata is None:
        return None
    for key, value in metadata:
        if str(key).lower() in {"retry-after", "x-goog-api-client-retry-delay"}:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None
