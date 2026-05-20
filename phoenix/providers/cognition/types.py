"""Payload dataclasses for the cognition substrate.

Per architecture v1 Section 4.2 + Phase 13 build guide Step 1: pure
data types consumed and produced by :class:`CognitionProvider`
implementations. All frozen for immutability; all canonical (no
provider-specific shapes leak through these — the adapter does the
translation).

Per [OPEN: P13-1] default (Phase 13 build guide Section 5):
:class:`CognitionResult` ships the optional :attr:`raw_provider_body`
field for the gated raw-body storage path. The default is ``None``;
Step 2's adapters populate it only when
``task.options.preserve_raw_provider_body=True`` and the actor has
``can_store_raw_provider_body``. Step 1 reserves the field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Prompt + Tool inputs


@dataclass(frozen=True)
class Prompt:
    """A canonical prompt for a cognition provider.

    Phoenix's canonical form mirrors the Anthropic messages-API shape
    (system separate; messages list with per-turn roles) since
    Anthropic's format is the most expressive of the major providers.
    Step 2's adapters translate this canonical shape to provider-
    specific forms (OpenAI's flat messages array, Google's
    contents-with-roles).

    Fields:
        system: Optional system prompt. Single string for v1.1;
            structured-content system prompts are a v1.2 extension.
        messages: Ordered list of conversation turns. Each is a dict
            with ``role`` (``"user"`` | ``"assistant"`` | ``"tool_result"``)
            and ``content`` (``str`` or structured-content list).
            Pure data; adapters interpret.
        metadata: Free-form audit dict (e.g. ``actor_id``, ``task_id``).
            Persisted only when ``prompt_disposition=VERBATIM`` per
            13-D2.
    """

    system: str | None
    messages: list[dict[str, Any]]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Tool:
    """A tool the provider may call during completion.

    Phoenix's canonical form uses the OpenAI function-calling shape
    with Anthropic's ``input_schema`` convention (JSON Schema for
    parameters). Step 2's adapters translate to provider-specific tool
    definitions.

    Fields:
        name: Tool identifier (must be unique within the call).
        description: Human-readable description; the model uses this to
            decide whether to call the tool.
        input_schema: JSON Schema for the tool's parameters.
    """

    name: str
    description: str
    input_schema: dict[str, Any]


# ---------------------------------------------------------------------------
# Usage + result outputs


@dataclass(frozen=True)
class TokenUsage:
    """Token-level usage record for cost accounting.

    Populated by adapters from the provider's response usage block.
    Phase 13 Step 7's WebSocket ``token.delta`` events expose
    incremental counts; this final :class:`TokenUsage` is the aggregate.

    Fields:
        input_tokens: Tokens in the prompt portion (system + messages).
        output_tokens: Tokens in the completion.
        cached_input_tokens: Tokens served from the provider's prompt
            cache (subset of ``input_tokens``). Used by Step 10's
            cost-ledger to apply the v2 pricing
            ``prompt_cache_discount_factor``. ``0`` when the provider
            doesn't support caching or no cache hit occurred.
    """

    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0


@dataclass(frozen=True)
class ToolCall:
    """A model-requested tool invocation.

    Fields:
        call_id: Provider-assigned identifier for this call (used to
            match the eventual :class:`ToolResult`).
        name: The :attr:`Tool.name` the model chose to call.
        arguments: Parsed arguments object (provider-specific JSON
            decoded into a dict).
    """

    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    """Phoenix's response to a model tool call, fed back into the next
    completion turn.

    Phase 13 Step 7's WebSocket ``cognition.tool_result`` event carries
    a summary of this object.

    Fields:
        call_id: The matching :attr:`ToolCall.call_id`.
        content: Tool output. ``str`` for text results,
            ``dict[str, Any]`` for structured results.
        is_error: ``True`` when the tool invocation failed; the model
            decides how to handle it.
    """

    call_id: str
    content: str | dict[str, Any]
    is_error: bool = False


@dataclass(frozen=True)
class CognitionResult:
    """Canonical result of a :meth:`CognitionProvider.complete` call.

    Fields:
        text: The model's text response (empty string when the model
            only produced tool calls).
        tool_calls: Any tool invocations the model requested (empty
            list if none).
        usage: Token-level usage for cost accounting.
        latency_ms: Wall-clock latency of the provider call in
            milliseconds.
        provider_fingerprint: Stable fingerprint for ledger provenance
            (provider name + model version + canonicalized request
            shape). Matches the :meth:`CognitionProvider.fingerprint`
            return at call time.
        prompt_cache_hit: ``True`` when the provider served the prompt
            from its cache (subset reflected in
            :attr:`TokenUsage.cached_input_tokens`). ``None`` when the
            provider doesn't support prompt caching or the response
            didn't surface the signal.
        raw_provider_body: Optional raw provider response body. Default
            ``None``; populated only when [OPEN: P13-1] gating allows
            (``task.options.preserve_raw_provider_body=True`` and the
            actor has ``can_store_raw_provider_body``). Step 1 reserves
            the field; Step 2's adapters write to it when gated.
    """

    text: str
    tool_calls: list[ToolCall]
    usage: TokenUsage
    latency_ms: float
    provider_fingerprint: str
    prompt_cache_hit: bool | None = None
    raw_provider_body: dict[str, Any] | None = None
