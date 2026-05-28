"""``LiteLLMPassthroughProvider`` — passthrough adapter via the LiteLLM unified API.

Wraps :func:`litellm.completion` to enable Phoenix to dispatch to the
~130 providers supported by LiteLLM (Ollama, vLLM, AWS Bedrock, Azure,
Mistral, Cohere, xAI, Perplexity, etc.) without writing a Phoenix-
specific adapter for each.

Per Phase 13 build guide Step 3, the ``[litellm]`` pip extra is opt-in
deliberately — LiteLLM is a large dependency (~50 MB installed). Users
who only need Anthropic + OpenAI + Google should not be forced to
pull it in. **LiteLLM is MIT-licensed (Apache-2.0 compatible)** per
Step 3's license note.

Construction takes a ``litellm_model`` string identifying both
provider and model:

- ``"ollama/llama3:8b"``
- ``"bedrock/anthropic.claude-3-sonnet"``
- ``"openai/gpt-5"``
- ``"cohere/command-r-plus"``

Pricing is delegated through a fallback chain (see
:meth:`LiteLLMPassthroughProvider.estimated_cost_usd`):

1. LiteLLM's built-in cost catalogue (``litellm.cost_per_token``).
2. Phoenix v2 pricing table entry for
   ``("litellm/<model>", <model>)``.
3. :class:`PricingUnavailable` — the router skips the candidate.

**SAFETY:** LiteLLM reads provider-specific keys from environment
variables (``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``,
``COHERE_API_KEY``, ``AWS_ACCESS_KEY_ID``, etc.). The adapter does NOT
read or store any key directly; ``_requires_api_key = False`` on the
base class. Phoenix's API-key SAFETY contract holds transitively
because LiteLLM's auth happens out-of-band, not through our
:attr:`_CognitionAdapterBase._api_key` slot.
"""

from __future__ import annotations

import json
import time
from typing import Any

from phoenix.pricing.v2 import load_cognition_pricing
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
    MissingOptionalDependency,
    PricingUnavailable,
)
from phoenix.providers.cognition.types import (
    CognitionResult,
    Prompt,
    TokenUsage,
    Tool,
    ToolCall,
)


class LiteLLMPassthroughProvider(_CognitionAdapterBase):
    """Passthrough adapter via LiteLLM's unified API.

    Conforms structurally to :class:`CognitionProvider` (inherits from
    :class:`_CognitionAdapterBase`). The ``provider_id`` is set per-
    instance as ``"litellm/<litellm_model>"`` so each concrete model
    registered as a LiteLLM passthrough has its own identity in the
    ProviderRegistry.
    """

    _api_key_env_var = "LITELLM_API_KEY"  # unused; see _requires_api_key
    _requires_api_key = False

    def __init__(
        self,
        *,
        litellm_model: str,
        client: Any = None,
        timeout_sec: float = 60.0,
        max_retries: int = 3,
    ) -> None:
        """Construct.

        Args:
            litellm_model: LiteLLM-style model identifier such as
                ``"ollama/llama3:8b"`` or
                ``"bedrock/anthropic.claude-3-sonnet"``.
            client: Optional pre-imported ``litellm`` module (tests
                inject a fake here). When ``None``, the adapter imports
                ``litellm`` lazily and raises
                :class:`MissingOptionalDependency` if the SDK is not
                installed.
            timeout_sec: Per-call timeout passed through to LiteLLM.
            max_retries: Inherited from :class:`_CognitionAdapterBase`.

        Raises:
            MissingOptionalDependency: If ``litellm`` is not installed
                and no ``client`` is injected.
        """
        # provider_id is per-instance for LiteLLM (one adapter per
        # registered model). Set BEFORE super().__init__ so the base's
        # error messages use the correct identifier.
        self.provider_id = f"litellm/{litellm_model}"
        self._litellm_model = litellm_model
        super().__init__(model=litellm_model, max_retries=max_retries)
        self._timeout_sec = timeout_sec
        if client is not None:
            self._litellm = client
        else:
            try:
                import litellm
            except ImportError as exc:
                raise MissingOptionalDependency(
                    "litellm SDK not installed; "
                    "install via `pip install phoenix-middleware[litellm]`."
                ) from exc
            self._litellm = litellm

    def _do_complete(
        self,
        prompt: Prompt,
        *,
        max_tokens: int,
        temperature: float,
        tools: list[Tool] | None,
        stream: bool,
    ) -> CognitionResult:
        del stream  # Step 7 lands streaming for passthrough.

        # LiteLLM normalizes to OpenAI's flat-messages format.
        messages: list[dict[str, Any]] = []
        if prompt.system is not None:
            messages.append({"role": "system", "content": prompt.system})
        messages.extend(prompt.messages)

        def _call() -> Any:
            kwargs: dict[str, Any] = {
                "model": self._litellm_model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "timeout": self._timeout_sec,
            }
            if tools:
                kwargs["tools"] = [self._tool_to_openai_shape(t) for t in tools]
            return self._litellm.completion(**kwargs)

        t0 = time.perf_counter()
        response = self._wrap_sdk_call(_call)
        latency_ms = (time.perf_counter() - t0) * 1000.0

        # LiteLLM returns an OpenAI-compatible ChatCompletion.
        choices = getattr(response, "choices", None) or []
        if not choices:
            return CognitionResult(
                text="",
                tool_calls=[],
                usage=TokenUsage(input_tokens=0, output_tokens=0),
                latency_ms=latency_ms,
                provider_fingerprint=self.fingerprint(),
            )

        choice = choices[0]
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
            except (json.JSONDecodeError, TypeError):
                args = {"_raw_arguments": args_raw}
            tool_calls.append(ToolCall(call_id=getattr(tc, "id", ""), name=name, arguments=args))

        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0)) if usage else 0
        completion_tokens = int(getattr(usage, "completion_tokens", 0)) if usage else 0
        cached_tokens = 0
        if usage is not None:
            # LiteLLM surfaces cache info via OpenAI's prompt_tokens_details
            # when the underlying provider supports it.
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

        LiteLLM normalizes streaming chunks to OpenAI's
        ChatCompletionChunk shape, so this implementation mirrors
        :meth:`OpenAIProvider.astream` with one substitution:
        ``litellm.acompletion(stream=True)`` is the async streaming
        entry point. The chunk-handling logic is identical because
        the normalization is the whole point of LiteLLM.

        **PERF:** :func:`litellm.acompletion` is itself a thin wrapper;
        the heavy lifting happens in whichever provider-side SDK
        litellm dispatches to. Streaming latency reflects the
        underlying provider, not LiteLLM overhead.
        """
        from phoenix.providers.cognition.streaming import (
            StreamFinal,
            StreamTokenDelta,
            StreamToolCallStart,
        )

        # LiteLLM uses OpenAI's flat-messages format.
        messages: list[dict[str, Any]] = []
        if prompt.system is not None:
            messages.append({"role": "system", "content": prompt.system})
        messages.extend(prompt.messages)

        kwargs: dict[str, Any] = {
            "model": self._litellm_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "timeout": self._timeout_sec,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = [self._tool_to_openai_shape(t) for t in tools]

        t0 = time.perf_counter()
        cumulative_tokens = 0
        text_parts: list[str] = []
        tool_calls_acc: dict[int, dict[str, Any]] = {}
        final_usage: Any = None

        # Cast the litellm module to Any at the stream-call seam —
        # the async streaming surface (litellm.acompletion) returns an
        # async generator that the SDK doesn't expose a stable type for.
        litellm_mod: Any = self._litellm
        try:
            stream = await litellm_mod.acompletion(**kwargs)
            async for chunk in stream:
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    usage_obj = getattr(chunk, "usage", None)
                    if usage_obj is not None:
                        final_usage = usage_obj
                    continue

                delta = getattr(choices[0], "delta", None)
                if delta is None:
                    continue

                text = getattr(delta, "content", None)
                if text:
                    text_parts.append(text)
                    cumulative_tokens += max(1, len(text) // 4)
                    yield StreamTokenDelta(
                        delta_text=text,
                        cumulative_tokens=cumulative_tokens,
                    )

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
        """Capabilities from ``litellm.get_model_info`` with conservative
        defaults on miss."""
        info: Any = None
        try:
            info = self._litellm.get_model_info(self._litellm_model)
        except Exception:
            # LiteLLM raises various exceptions for unknown models;
            # treat all as "no info available".
            info = None

        if not isinstance(info, dict):
            return CognitionCapabilities(
                streaming=False,
                tool_use=False,
                vision=False,
                max_context_tokens=8192,
                supports_prompt_cache=False,
                supports_batch=False,
            )

        return CognitionCapabilities(
            streaming=bool(info.get("supports_streaming", False)),
            tool_use=bool(info.get("supports_function_calling", False)),
            vision=bool(info.get("supports_vision", False)),
            max_context_tokens=int(info.get("max_input_tokens", 8192) or 8192),
            supports_prompt_cache=bool(info.get("supports_prompt_caching", False)),
            supports_batch=False,  # LiteLLM doesn't surface Batch API as a flag.
        )

    def fingerprint(self) -> str:
        return f"{self.provider_id}|{self._litellm_model}|step-3"

    def estimated_cost_usd(self, usage: TokenUsage) -> float:
        """Estimate cost in USD for the given token usage.

        Fallback chain (per Phase 13 build guide Step 3):

        1. LiteLLM's ``cost_per_token`` catalogue.
        2. Phoenix v2 pricing table entry for
           ``(self.provider_id, self._litellm_model)``.
        3. :class:`PricingUnavailable` — the router's Stage 2 check
           skips the candidate rather than failing the solve.
        """
        # 1) LiteLLM's catalog.
        try:
            cost_input, cost_output = self._litellm.cost_per_token(
                model=self._litellm_model,
                prompt_tokens=usage.input_tokens,
                completion_tokens=usage.output_tokens,
            )
            return float(cost_input) + float(cost_output)
        except Exception:
            pass

        # 2) Phoenix v2 pricing.
        try:
            pricing = load_cognition_pricing()
        except Exception:
            pricing = {}
        key = (self.provider_id, self._litellm_model)
        if key in pricing:
            rec = pricing[key]
            fresh_input = max(0, usage.input_tokens - usage.cached_input_tokens)
            input_cost = fresh_input / 1e6 * rec.usd_per_1m_input_tokens
            cached_cost = (
                usage.cached_input_tokens
                / 1e6
                * rec.usd_per_1m_input_tokens
                * rec.prompt_cache_discount_factor
            )
            output_cost = usage.output_tokens / 1e6 * rec.usd_per_1m_output_tokens
            return float(input_cost + cached_cost + output_cost)

        # 3) Unavailable.
        raise PricingUnavailable(
            f"No pricing for {self._litellm_model} (tried LiteLLM catalogue + Phoenix v2)."
        )

    def _map_sdk_exception(self, exc: Exception) -> CognitionError | None:
        try:
            from litellm import exceptions as lexc
        except ImportError:
            return None

        if isinstance(exc, lexc.AuthenticationError):
            return CognitionAuthError(
                f"litellm: authentication rejected for {self._litellm_model}."
            )
        if isinstance(exc, lexc.RateLimitError):
            return CognitionRateLimitError(f"litellm: rate-limited for {self._litellm_model}.")
        if isinstance(exc, lexc.Timeout):
            return CognitionTimeoutError(f"litellm: request timed out for {self._litellm_model}.")
        if isinstance(exc, lexc.ServiceUnavailableError):
            return CognitionUnavailable(f"litellm: provider unavailable for {self._litellm_model}.")
        if isinstance(exc, lexc.ContextWindowExceededError):
            return CognitionContextLengthError(
                f"litellm: context length exceeded for {self._litellm_model}."
            )
        if isinstance(exc, lexc.ContentPolicyViolationError):
            return CognitionContentPolicyError(
                f"litellm: content policy refusal for {self._litellm_model}."
            )
        return None

    @staticmethod
    def _tool_to_openai_shape(t: Tool) -> dict[str, Any]:
        """Translate a Phoenix :class:`Tool` to LiteLLM's expected shape.

        LiteLLM normalizes to OpenAI's function-calling shape regardless
        of the underlying provider.
        """
        return {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
