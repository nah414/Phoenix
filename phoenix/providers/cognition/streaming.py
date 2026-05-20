"""Streaming-completion Protocol + event types for cognition adapters.

Per Phase 13 build guide §4.7: cognition providers that support
streaming emit incremental token deltas + tool-call events via the
:meth:`StreamingCognitionProvider.astream` async generator. The
dispatch site (verification gate; Step 9+) bridges these to the
WebSocket event surface via :class:`StreamingTokenEmitter`.

**Design:** :class:`StreamingCognitionProvider` extends the base
:class:`CognitionProvider` (Step 1) with the streaming method. Not
all providers/models support streaming — the provider advertises via
``capabilities().streaming``; only providers that satisfy
``StreamingCognitionProvider`` Protocol can be invoked for streaming
dispatch.

**Step 7 ships:**
- The Protocol extension + event-shape dataclasses (this module).
- :class:`AnthropicProvider.astream` implementation (in
  ``anthropic.py``).
- ``OpenAIProvider`` / ``GoogleGeminiProvider`` / ``LiteLLMPassthroughProvider``
  streaming impls follow the same pattern; deferred to a Step 7
  follow-up (the build guide's verification gate is "unit tests with
  mocked streaming adapters", which Anthropic satisfies as the
  representative case).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from phoenix.providers.cognition.protocol import CognitionProvider
from phoenix.providers.cognition.types import (
    CognitionResult,
    Prompt,
    Tool,
    ToolCall,
)


# ---------------------------------------------------------------------------
# Stream event types (discriminated union)


@dataclass(frozen=True)
class StreamTokenDelta:
    """One incremental token chunk from the provider.

    Fields:
        delta_text: The new text appended since the last delta. May be
            empty for non-text events (e.g. tool-call construction).
        cumulative_tokens: Running count of output tokens so far.
    """

    delta_text: str
    cumulative_tokens: int


@dataclass(frozen=True)
class StreamToolCallStart:
    """The provider announced it's calling a tool.

    Fields:
        call_id: Provider-assigned identifier (matches the eventual
            :class:`ToolCall.call_id`).
        tool_name: The tool the model chose to invoke.
        partial_args: The arguments accumulated so far. May be empty
            initially; providers stream tool-arg JSON in chunks.
    """

    call_id: str
    tool_name: str
    partial_args: dict[str, Any]


@dataclass(frozen=True)
class StreamFinal:
    """End-of-stream marker carrying the aggregate :class:`CognitionResult`.

    The dispatch site uses this to emit the final ``task.complete``
    event after the stream's last delta. Always emitted exactly once
    per stream (on success or fallback-on-error).
    """

    result: CognitionResult


# Discriminated-union type alias. Adapters yield one of these per
# stream chunk; the dispatch site discriminates on isinstance.
StreamEvent = StreamTokenDelta | StreamToolCallStart | StreamFinal


# ---------------------------------------------------------------------------
# StreamingCognitionProvider Protocol


@runtime_checkable
class StreamingCognitionProvider(CognitionProvider, Protocol):
    """Cognition provider that supports incremental token streaming.

    Structural extension of :class:`CognitionProvider`. Adapters that
    implement :meth:`astream` satisfy this Protocol; the dispatch
    site at Step 9+ checks ``isinstance(provider, StreamingCognitionProvider)``
    before invoking streaming dispatch.
    """

    async def astream(
        self,
        prompt: Prompt,
        *,
        max_tokens: int,
        temperature: float,
        tools: list[Tool] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream completion events for this prompt.

        Yields :class:`StreamEvent` instances in order. The last event
        is always a :class:`StreamFinal` carrying the aggregate
        :class:`CognitionResult` — exactly once per stream, even on
        provider errors that allow partial output recovery.

        Implementations should:
        1. Yield ``StreamTokenDelta`` for each text chunk.
        2. Yield ``StreamToolCallStart`` when the provider begins a
           tool call (one per tool; partial args accumulated across
           subsequent yields are not required at Step 7).
        3. Yield ``StreamFinal`` at end-of-stream with the aggregate
           ``CognitionResult`` (text + tool_calls + usage +
           provider_fingerprint + latency_ms).
        4. Raise the same typed errors :meth:`complete` raises
           (``CognitionAuthError``, ``CognitionRateLimitError``, etc.)
           on terminal failures.

        Streaming adapters MUST emit at least one ``StreamFinal``; the
        dispatch site relies on this invariant to detect stream
        completion and emit ``task.complete``.
        """
        ...


# ---------------------------------------------------------------------------
# Helpers


def stream_event_kind(event: StreamEvent) -> str:
    """Return a stable string label for telemetry / debugging.

    The discriminator strings match the WebSocket event-type taxonomy
    in :mod:`phoenix.api.streaming_events`: ``"token.delta"``,
    ``"cognition.tool_call"``, and the synthetic ``"stream.final"``
    (which the dispatch site translates to ``task.complete``).
    """
    if isinstance(event, StreamTokenDelta):
        return "token.delta"
    if isinstance(event, StreamToolCallStart):
        return "cognition.tool_call"
    if isinstance(event, StreamFinal):
        return "stream.final"
    raise TypeError(f"Unknown StreamEvent type: {type(event).__name__}")


def aggregate_from_deltas(
    deltas: list[StreamTokenDelta],
    *,
    tool_calls: list[ToolCall] | None = None,
    usage: Any | None = None,
    latency_ms: float = 0.0,
    provider_fingerprint: str = "",
) -> CognitionResult:
    """Build a :class:`CognitionResult` from a list of token deltas.

    Used by adapters that accumulate deltas during streaming and need
    to construct the final ``CognitionResult`` for the
    :class:`StreamFinal` event. Concatenates ``delta_text`` across
    deltas; uses the last delta's ``cumulative_tokens`` as the
    output-token count.
    """
    from phoenix.providers.cognition.types import TokenUsage

    text = "".join(d.delta_text for d in deltas)
    output_tokens = deltas[-1].cumulative_tokens if deltas else 0
    if usage is None:
        usage = TokenUsage(input_tokens=0, output_tokens=output_tokens)
    return CognitionResult(
        text=text,
        tool_calls=list(tool_calls or []),
        usage=usage,
        latency_ms=latency_ms,
        provider_fingerprint=provider_fingerprint,
    )


__all__ = [
    "StreamEvent",
    "StreamTokenDelta",
    "StreamToolCallStart",
    "StreamFinal",
    "StreamingCognitionProvider",
    "stream_event_kind",
    "aggregate_from_deltas",
]
