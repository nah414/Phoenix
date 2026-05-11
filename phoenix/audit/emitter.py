"""Audit emitter — singleton + multi-sink dispatch (Phase 7 Step 1).

The emitter is the single point of audit-event truth in Phoenix.
Callers (safety gate, verification gate, API routes, drift detector)
call :func:`get_emitter` once and emit events; the emitter fans the
event out to all registered sinks.

**Sinks (Phase 7 Step 1):** the JSONL writer is registered
unconditionally. Step 3 adds the OTel adapter as a second sink when
``$PHOENIX_OTEL_ENABLED=1`` is set at FastAPI lifespan startup.

**Singleton + reset:** matches the Phase 6b pattern for
``get_state_backend`` / ``get_broker`` / ``get_detector`` -- one
process-lifetime singleton constructed lazily on first call, with a
:func:`reset_emitter` helper for test isolation and FastAPI lifespan
shutdown.

**Sink exception isolation:** if a sink's :meth:`emit` raises, the
exception is caught + logged; the emitter continues to the next sink.
A single broken sink does NOT take down the emit path -- preserving
the "fire-and-forget audit" guarantee that no audit emit blocks the
solve hot path.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from phoenix.audit.event_format import AuditEvent

logger = logging.getLogger(__name__)


class AuditSink(Protocol):
    """Sinks consume :class:`AuditEvent` instances.

    Step 1 ships :class:`~phoenix.audit.jsonl_writer.JSONLWriter`.
    Step 3 adds :class:`OTelExporter`. Both satisfy this protocol.
    """

    def emit(self, event: AuditEvent) -> None: ...

    def close(self, drain_timeout_seconds: float = ...) -> None: ...


class AuditEmitter:
    """Multi-sink audit-event dispatcher.

    Thread-safe: the sink list is guarded by an :class:`RLock` so
    concurrent emits from multiple solver threads don't race.
    """

    def __init__(self) -> None:
        self._sinks: list[AuditSink] = []
        self._lock = threading.RLock()

    def add_sink(self, sink: AuditSink) -> None:
        """Register a new sink. Idempotent on identity (sinks are not
        deduped by class -- multiple JSONL writers to different
        directories would each register separately)."""
        with self._lock:
            self._sinks.append(sink)

    def remove_sink(self, sink: AuditSink) -> bool:
        """Remove a previously-registered sink. Returns True if removed."""
        with self._lock:
            try:
                self._sinks.remove(sink)
                return True
            except ValueError:
                return False

    def emit(self, event: AuditEvent) -> None:
        """Dispatch an event to all registered sinks.

        Exceptions from individual sinks are caught + logged so a single
        broken sink doesn't take down the emit path. The order is
        registration-order so deterministic across test runs.
        """
        with self._lock:
            sinks = list(self._sinks)
        for sink in sinks:
            try:
                sink.emit(event)
            except Exception:
                logger.exception(
                    "Audit sink %r emit failed for event_type=%r",
                    type(sink).__name__,
                    event.event_type,
                )

    def close(self, drain_timeout_seconds: float = 5.0) -> None:
        """Close all sinks. Idempotent on already-closed sinks (each
        sink owns its own idempotency contract).
        """
        with self._lock:
            sinks = list(self._sinks)
            self._sinks.clear()
        for sink in sinks:
            try:
                sink.close(drain_timeout_seconds=drain_timeout_seconds)
            except Exception:
                logger.exception("Audit sink %r close failed", type(sink).__name__)


# ---------------------------------------------------------------------------
# Module-level singleton (matches Phase 6b factory pattern).

_EMITTER: AuditEmitter | None = None
_EMITTER_LOCK = threading.Lock()


def get_emitter() -> AuditEmitter:
    """Return the singleton :class:`AuditEmitter`.

    Constructed lazily on first call. The JSONL writer is registered
    immediately (Phase 7 Step 1 default). Phase 7 Step 3 wires the
    OTel sink at FastAPI lifespan startup when ``$PHOENIX_OTEL_ENABLED=1``.
    """
    global _EMITTER
    with _EMITTER_LOCK:
        if _EMITTER is None:
            _EMITTER = AuditEmitter()
            # Default sink: the JSONL writer. Lazy import keeps
            # event_format clean of audit-dir side effects.
            from phoenix.audit.jsonl_writer import JSONLWriter

            _EMITTER.add_sink(JSONLWriter())
        return _EMITTER


def reset_emitter() -> None:
    """Close and clear the singleton. Tests + FastAPI lifespan shutdown
    use this. Idempotent.
    """
    global _EMITTER
    with _EMITTER_LOCK:
        if _EMITTER is not None:
            try:
                _EMITTER.close()
            except Exception:
                logger.exception("Failed to close audit emitter on reset")
            _EMITTER = None


__all__ = ["AuditEmitter", "AuditSink", "get_emitter", "reset_emitter"]
