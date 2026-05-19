"""``AnthropicProvider`` — adapter for the Anthropic Claude API.

Wraps the ``anthropic`` Python SDK (pinned ``>=0.40, <0.60``). Maps
Phoenix's :class:`Prompt` + :class:`Tool` to Anthropic's messages API;
surfaces prompt-cache hits via
``CognitionCapabilities.supports_prompt_cache=True`` and
``CognitionResult.prompt_cache_hit``.

Canonical models (as of 2026-05-18):

- ``claude-opus-4-7-20260315``
- ``claude-sonnet-4-7-20260418``
- ``claude-haiku-4-5-20251001``

The model list is pulled fresh from Anthropic's ``/v1/models`` endpoint
at provider init when reachable; this static fallback ships for
offline bootstrapping and air-gapped deployments. Live model discovery
landed via the build guide's "live reads beat memory" rule but is
deferred to a Step 2 follow-up when the routing surface needs it.

**SAFETY:** ``ANTHROPIC_API_KEY`` is read from the environment only.
:class:`CognitionAuthError` is raised at construction if it is missing.
"""

from __future__ import annotations

import hashlib
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
    import anthropic

# Static fallback model list used when live discovery is unavailable.
_STATIC_MODEL_FALLBACK: tuple[str, ...] = (
    "claude-opus-4-7-20260315",
    "claude-sonnet-4-7-20260418",
    "claude-haiku-4-5-20251001",
)

# Static capabilities by canonical model. Used until live discovery lands.
_STATIC_CAPABILITIES: dict[str, CognitionCapabilities] = {
    "claude-opus-4-7-20260315": CognitionCapabilities(
        streaming=True,
        tool_use=True,
        vision=True,
        max_context_tokens=200_000,
        supports_prompt_cache=True,
        supports_batch=True,
    ),
    "claude-sonnet-4-7-20260418": CognitionCapabilities(
        streaming=True,
        tool_use=True,
        vision=True,
        max_context_tokens=200_000,
        supports_prompt_cache=True,
        supports_batch=True,
    ),
    "claude-haiku-4-5-20251001": CognitionCapabilities(
        streaming=True,
        tool_use=True,
        vision=True,
        max_context_tokens=200_000,
        supports_prompt_cache=True,
        supports_batch=True,
    ),
}


class AnthropicProvider(_CognitionAdapterBase):
    """Phoenix adapter for the Anthropic Claude API."""

    provider_id = "anthropic"
    _api_key_env_var = "ANTHROPIC_API_KEY"

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        client: anthropic.Anthropic | None = None,
        timeout_sec: float = 60.0,
        max_retries: int = 3,
    ) -> None:
        """Construct the adapter.

        Args:
            model: Anthropic model identifier (see ``_STATIC_MODEL_FALLBACK``).
            api_key: Optional explicit key (tests only; ops should set
                ``ANTHROPIC_API_KEY``).
            client: Optional pre-built ``Anthropic`` client (tests
                inject mocks here). When ``None``, the adapter
                constructs one lazily.
            timeout_sec: Per-call timeout (passed through to the SDK).
            max_retries: Inherited from :class:`_CognitionAdapterBase`.
        """
        super().__init__(model=model, api_key=api_key, max_retries=max_retries)
        self._timeout_sec = timeout_sec
        if client is not None:
            self._client = client
        else:
            try:
                from anthropic import Anthropic
            except ImportError as exc:
                raise CognitionError(
                    "anthropic SDK not installed; "
                    "install via `pip install phoenix-middleware[anthropic]`."
                ) from exc
            self._client = Anthropic(api_key=self._api_key, timeout=timeout_sec)

    def _do_complete(
        self,
        prompt: Prompt,
        *,
        max_tokens: int,
        temperature: float,
        tools: list[Tool] | None,
        stream: bool,
    ) -> CognitionResult:
        del stream  # Step 7 lands streaming; Step 2 ignores the flag.

        def _call() -> Any:
            kwargs: dict[str, Any] = {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": list(prompt.messages),
            }
            if prompt.system is not None:
                kwargs["system"] = prompt.system
            if tools:
                kwargs["tools"] = [self._tool_to_anthropic(t) for t in tools]
            return self._client.messages.create(**kwargs)

        t0 = time.perf_counter()
        message = self._wrap_sdk_call(_call)
        latency_ms = (time.perf_counter() - t0) * 1000.0

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in message.content:
            # Anthropic content blocks carry a ``type`` attribute. The SDK
            # exposes typed blocks (TextBlock, ToolUseBlock); we duck-type to
            # stay decoupled from the SDK class hierarchy.
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(getattr(block, "text", ""))
            elif btype == "tool_use":
                tool_calls.append(
                    ToolCall(
                        call_id=getattr(block, "id", ""),
                        name=getattr(block, "name", ""),
                        arguments=dict(getattr(block, "input", {}) or {}),
                    )
                )

        usage_obj = getattr(message, "usage", None)
        input_tokens = int(getattr(usage_obj, "input_tokens", 0)) if usage_obj else 0
        output_tokens = int(getattr(usage_obj, "output_tokens", 0)) if usage_obj else 0
        cache_read = int(getattr(usage_obj, "cache_read_input_tokens", 0) or 0) if usage_obj else 0

        return CognitionResult(
            text="".join(text_parts),
            tool_calls=tool_calls,
            usage=TokenUsage(
                input_tokens=input_tokens + cache_read,  # report total seen
                output_tokens=output_tokens,
                cached_input_tokens=cache_read,
            ),
            latency_ms=latency_ms,
            provider_fingerprint=self.fingerprint(),
            prompt_cache_hit=(cache_read > 0) if usage_obj is not None else None,
        )

    def capabilities(self) -> CognitionCapabilities:
        caps = _STATIC_CAPABILITIES.get(self.model)
        if caps is None:
            # Unknown model: conservative defaults until live discovery lands.
            return CognitionCapabilities(
                streaming=False,
                tool_use=False,
                vision=False,
                max_context_tokens=100_000,
                supports_prompt_cache=False,
                supports_batch=False,
            )
        return caps

    def fingerprint(self) -> str:
        """Stable fingerprint: provider + model + adapter version."""
        # Step 2: the fingerprint is provider + model only. Step 8 (Omega
        # Ledger) extends with canonicalized request shape (temperature
        # bucket, tool list hash, response-format hash).
        return f"{self.provider_id}|{self.model}|step-2"

    def _map_sdk_exception(self, exc: Exception) -> CognitionError | None:
        """Map an :mod:`anthropic` SDK exception to a typed Phoenix exception."""
        try:
            from anthropic import (
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
            if "context" in msg or "max_tokens" in msg or "too long" in msg:
                return CognitionContextLengthError(f"{self.provider_id}: context length exceeded.")
            if "policy" in msg or "refuse" in msg or "blocked" in msg:
                return CognitionContentPolicyError(
                    f"{self.provider_id}: content policy refusal.",
                    reason_code=_extract_reason_code(exc),
                )
            return CognitionError(f"{self.provider_id}: bad request: {exc}")
        return None

    @staticmethod
    def _tool_to_anthropic(t: Tool) -> dict[str, Any]:
        """Translate a Phoenix :class:`Tool` to Anthropic's tool shape."""
        return {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }


def _extract_retry_after(exc: Exception) -> float | None:
    """Pull a ``Retry-After`` header from an Anthropic SDK exception, if present."""
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


def _extract_reason_code(exc: Exception) -> str | None:
    """Best-effort extraction of a refusal reason code from an SDK exception."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return str(err.get("type") or err.get("code") or "")
    return None


def _request_fingerprint(
    *,
    model: str,
    temperature: float,
    tools: list[Tool] | None,
) -> str:
    """Compute a canonical hash of a request shape for ledger provenance.

    Used by Step 8's Omega Ledger entries. Step 2 ships the helper so
    Step 8 can wire it without re-implementing.
    """
    payload = {
        "model": model,
        "temperature_bucket": round(temperature, 2),
        "tools": (
            [(t.name, sorted(t.input_schema.keys()) if t.input_schema else []) for t in tools]
            if tools
            else []
        ),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
