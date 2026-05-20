"""Streaming-token WebSocket event surface (Phase 13 Step 7).

Per BUILDGUIDE §4.7: cognition providers that support streaming emit
incremental token deltas during a solve. Phoenix's event broker fans
these out to WebSocket subscribers via three new event types
(``token.delta``, ``cognition.tool_call``, ``cognition.tool_result``)
plus a ``token.dropped`` summary that's emitted when the rate-cap or
HASH_ONLY suppression caused any deltas to be dropped.

**Per P13-6 default (locked at Step 7 implementation time):** when
``prompt_disposition == HASH_ONLY``, ``token.delta`` events are NOT
emitted to WS subscribers — the consumer receives only the final
``task.complete`` with the aggregate result. Streaming requires
``VERBATIM`` (or a future ``STREAM_ONLY`` disposition that streams
to one subscriber without persisting to the ledger).

**Rate cap:** the default 100 token.delta events/second per task
is configurable via the ``PHOENIX_STREAM_RATE_CAP`` env var. Above
that cap, deltas are dropped and counted toward the
``token.dropped`` summary. Verification-gate inputs (the existing
``task.*`` events) are NEVER subject to drops — only ``token.delta``
falls back when the consumer is slow.
"""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING, Any, Literal, TypedDict

if TYPE_CHECKING:
    from phoenix.api.event_broker import BrokerProtocol

log = logging.getLogger(__name__)

# Event type discriminator strings (stable across releases).
EVENT_TOKEN_DELTA: str = "token.delta"
EVENT_COGNITION_TOOL_CALL: str = "cognition.tool_call"
EVENT_COGNITION_TOOL_RESULT: str = "cognition.tool_result"
EVENT_TOKEN_DROPPED: str = "token.dropped"

# Prompt-disposition values per 13-D2 (locked 2026-05-18).
PromptDisposition = Literal["HASH_ONLY", "VERBATIM", "ENCRYPTED_OPT_IN"]


# Default rate cap per build guide §4.7 PERF callout. Configurable via
# PHOENIX_STREAM_RATE_CAP env var.
_DEFAULT_RATE_CAP_PER_SEC: int = 100


def _read_rate_cap() -> int:
    """Read PHOENIX_STREAM_RATE_CAP from env, with sane fallback."""
    raw = os.environ.get("PHOENIX_STREAM_RATE_CAP")
    if not raw:
        return _DEFAULT_RATE_CAP_PER_SEC
    try:
        parsed = int(raw)
    except ValueError:
        log.warning(
            "PHOENIX_STREAM_RATE_CAP=%r is not an integer; using default %d events/sec",
            raw,
            _DEFAULT_RATE_CAP_PER_SEC,
        )
        return _DEFAULT_RATE_CAP_PER_SEC
    if parsed < 1:
        log.warning(
            "PHOENIX_STREAM_RATE_CAP=%d is < 1; using default %d events/sec",
            parsed,
            _DEFAULT_RATE_CAP_PER_SEC,
        )
        return _DEFAULT_RATE_CAP_PER_SEC
    return parsed


# ---------------------------------------------------------------------------
# Payload shapes (TypedDicts for IDE + mypy support)


class TokenDeltaPayload(TypedDict):
    """Payload for ``token.delta`` events."""

    task_id: str
    axis_id: str
    provider: str
    model: str
    delta_text: str
    cumulative_tokens: int
    cost_so_far_usd: float


class ToolCallPayload(TypedDict):
    """Payload for ``cognition.tool_call`` events."""

    task_id: str
    axis_id: str
    provider: str
    model: str
    tool_name: str
    tool_args: dict[str, Any]


class ToolResultPayload(TypedDict):
    """Payload for ``cognition.tool_result`` events.

    Phoenix returns a result summary (not the full tool result body) so
    the WS subscriber sees the shape without ledger-level detail; the
    full result lives in the Omega Ledger entry per 13-D2's
    prompt_disposition handling.
    """

    task_id: str
    axis_id: str
    tool_name: str
    result_summary: str
    latency_ms: float


class TokenDroppedPayload(TypedDict):
    """Payload for the ``token.dropped`` summary event.

    Emitted at finalize when any deltas were suppressed (HASH_ONLY or
    rate-cap). Carries the total dropped count + a per-reason breakdown.
    """

    task_id: str
    total_dropped: int
    dropped_hash_only: int
    dropped_rate_cap: int


# ---------------------------------------------------------------------------
# StreamingTokenEmitter


class StreamingTokenEmitter:
    """Bridges adapter streaming to the event broker with P13-6
    suppression + rate-cap drop tracking.

    The emitter is constructed per-task by the cognition-axis dispatch
    path (or the verification gate's cognition branch when it lands in
    Step 9+). Each ``emit_*`` method gates on the configured
    ``prompt_disposition`` and the per-second rate cap; drops are
    counted and surfaced via :meth:`finalize`'s ``token.dropped`` summary.

    The class is NOT thread-safe; one instance per task. Phoenix's
    cognition dispatch is single-threaded per task.
    """

    def __init__(
        self,
        *,
        broker: BrokerProtocol,
        task_id: str,
        prompt_disposition: PromptDisposition = "HASH_ONLY",
        rate_cap_per_sec: int | None = None,
    ) -> None:
        """Construct.

        Args:
            broker: The :class:`BrokerProtocol`-conformant event broker
                (in-memory or NATS-backed).
            task_id: The Phoenix task identifier this stream belongs to.
            prompt_disposition: The 13-D2 prompt disposition. ``HASH_ONLY``
                (default) suppresses ``token.delta`` per P13-6 default.
                ``VERBATIM`` emits normally. ``ENCRYPTED_OPT_IN`` is
                handled like ``VERBATIM`` for WS purposes (the ledger
                handles encryption; the live stream still emits).
            rate_cap_per_sec: Override the configured rate cap. ``None``
                reads from ``PHOENIX_STREAM_RATE_CAP`` env var.
        """
        self._broker = broker
        self._task_id = task_id
        self._prompt_disposition = prompt_disposition
        self._rate_cap_per_sec = (
            rate_cap_per_sec if rate_cap_per_sec is not None else _read_rate_cap()
        )
        # Drop counters split by reason for the token.dropped summary.
        self._dropped_hash_only: int = 0
        self._dropped_rate_cap: int = 0
        # Rate-limiter state: count + start of the current second window.
        self._current_second_start: float = 0.0
        self._current_second_count: int = 0
        # Already-finalized check so finalize() is idempotent.
        self._finalized = False

    @property
    def prompt_disposition(self) -> PromptDisposition:
        return self._prompt_disposition

    @property
    def dropped_hash_only(self) -> int:
        return self._dropped_hash_only

    @property
    def dropped_rate_cap(self) -> int:
        return self._dropped_rate_cap

    @property
    def total_dropped(self) -> int:
        return self._dropped_hash_only + self._dropped_rate_cap

    def emit_token_delta(
        self,
        *,
        axis_id: str,
        provider: str,
        model: str,
        delta_text: str,
        cumulative_tokens: int,
        cost_so_far_usd: float,
    ) -> bool:
        """Emit a ``token.delta`` event, gated by disposition + rate cap.

        Returns ``True`` when the event was emitted, ``False`` when
        suppressed (HASH_ONLY) or dropped (rate cap exceeded).
        """
        # P13-6 default: HASH_ONLY → suppress token.delta entirely.
        # Per ledger replay semantics, the consumer reconstructs from
        # task.complete; live stream sees only that final event.
        if self._prompt_disposition == "HASH_ONLY":
            self._dropped_hash_only += 1
            return False

        # Per-second rate cap. Verification-gate events use a separate
        # subject and never hit this counter.
        if self._is_rate_capped():
            self._dropped_rate_cap += 1
            return False

        payload: TokenDeltaPayload = {
            "task_id": self._task_id,
            "axis_id": axis_id,
            "provider": provider,
            "model": model,
            "delta_text": delta_text,
            "cumulative_tokens": cumulative_tokens,
            "cost_so_far_usd": cost_so_far_usd,
        }
        self._broker.emit(self._task_id, EVENT_TOKEN_DELTA, payload=dict(payload))
        return True

    def emit_tool_call(
        self,
        *,
        axis_id: str,
        provider: str,
        model: str,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> None:
        """Emit a ``cognition.tool_call`` event.

        NOT subject to HASH_ONLY suppression — tool-call metadata is
        considered safe to surface even under hash-only ledger
        disposition (it carries no prompt content; only the structural
        decision to invoke a tool). Subject to the rate cap.
        """
        if self._is_rate_capped():
            self._dropped_rate_cap += 1
            return
        payload: ToolCallPayload = {
            "task_id": self._task_id,
            "axis_id": axis_id,
            "provider": provider,
            "model": model,
            "tool_name": tool_name,
            "tool_args": dict(tool_args),
        }
        self._broker.emit(self._task_id, EVENT_COGNITION_TOOL_CALL, payload=dict(payload))

    def emit_tool_result(
        self,
        *,
        axis_id: str,
        tool_name: str,
        result_summary: str,
        latency_ms: float,
    ) -> None:
        """Emit a ``cognition.tool_result`` event.

        Carries a *summary* of the tool result, not the full body
        (the full body lives in the ledger; the WS surface is for
        live progress). NOT subject to HASH_ONLY suppression for
        the same reason as tool_call.
        """
        if self._is_rate_capped():
            self._dropped_rate_cap += 1
            return
        payload: ToolResultPayload = {
            "task_id": self._task_id,
            "axis_id": axis_id,
            "tool_name": tool_name,
            "result_summary": result_summary,
            "latency_ms": latency_ms,
        }
        self._broker.emit(self._task_id, EVENT_COGNITION_TOOL_RESULT, payload=dict(payload))

    def finalize(self) -> None:
        """Emit the ``token.dropped`` summary if any drops occurred.

        Called by the dispatch site after the underlying provider
        stream completes (success or failure). Idempotent — repeated
        calls are no-ops.
        """
        if self._finalized:
            return
        self._finalized = True
        if self.total_dropped == 0:
            return
        payload: TokenDroppedPayload = {
            "task_id": self._task_id,
            "total_dropped": self.total_dropped,
            "dropped_hash_only": self._dropped_hash_only,
            "dropped_rate_cap": self._dropped_rate_cap,
        }
        self._broker.emit(self._task_id, EVENT_TOKEN_DROPPED, payload=dict(payload))

    # ------------------------------------------------------------------
    # Rate-limiter internals

    def _is_rate_capped(self) -> bool:
        """Check + update the per-second rate-cap counter.

        Returns ``True`` if the current second's emit count has
        reached the cap. Otherwise increments the counter and returns
        ``False``.
        """
        now = time.monotonic()
        # Window-start: the floor of the current second.
        window_start = int(now)
        if window_start != int(self._current_second_start):
            # New second; reset the counter.
            self._current_second_start = float(window_start)
            self._current_second_count = 0
        if self._current_second_count >= self._rate_cap_per_sec:
            return True
        self._current_second_count += 1
        return False


# ---------------------------------------------------------------------------
# Event-type filter (used by the WS subscription)


def event_type_matches_filter(
    event_type: str,
    filter_patterns: tuple[str, ...],
) -> bool:
    """Check whether ``event_type`` matches any pattern in
    ``filter_patterns``.

    Supports exact match (``"token.delta"``) and trailing-wildcard
    match (``"cognition.*"``). Empty tuple = no filter, matches all
    events.

    Used by the WS endpoint's ``?events=`` query parameter handler.
    """
    if not filter_patterns:
        return True
    for pattern in filter_patterns:
        if pattern == event_type:
            return True
        if pattern.endswith(".*") and event_type.startswith(pattern[:-2] + "."):
            return True
    return False


def parse_event_filter(raw: str | None) -> tuple[str, ...]:
    """Parse the WS ``?events=`` query parameter.

    Accepts a comma-separated list of event-type names with optional
    trailing wildcard (``"token.delta,cognition.*"``). Empty / None
    returns the empty tuple = match-all.
    """
    if not raw:
        return ()
    parts = [p.strip() for p in raw.split(",")]
    return tuple(p for p in parts if p)


__all__ = [
    "EVENT_TOKEN_DELTA",
    "EVENT_COGNITION_TOOL_CALL",
    "EVENT_COGNITION_TOOL_RESULT",
    "EVENT_TOKEN_DROPPED",
    "TokenDeltaPayload",
    "ToolCallPayload",
    "ToolResultPayload",
    "TokenDroppedPayload",
    "StreamingTokenEmitter",
    "PromptDisposition",
    "event_type_matches_filter",
    "parse_event_filter",
]
