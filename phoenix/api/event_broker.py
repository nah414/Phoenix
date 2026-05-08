"""In-memory event broker for verification-gate WebSocket streaming.

Per architecture v1 Section 5.3 + Section 6.6: the verification gate
emits structured events (task.started, task.solver.complete,
task.control.complete, task.orchestrate.progress,
task.verification.promoted, task.verification.demoted, task.complete,
task.failed) which downstream consumers (WebSocket clients, audit log,
ops dashboards) consume.

Per Phase 6a scope (2026-05-08): in-memory broker; Phase 6b's NATS
JetStream ships durable persistence + cross-process pub/sub. Phase 6a
buffers events per task_id; WebSocket handlers stream from the buffer.

The broker is a module-level singleton; the verification gate (Step 7)
calls :meth:`emit` during the solve flow; the WebSocket handler (Step
8) calls :meth:`subscribe` to read.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class TaskEvent:
    """One verification-gate event.

    Stable shape across releases. The ``type`` discriminator names the
    event family per Section 5.3:

    - ``"task.started"`` -- gate entered solve.
    - ``"task.solver.complete"`` -- Axis 1 + Solver done.
    - ``"task.control.complete"`` -- Axis 2 + Control done.
    - ``"task.orchestrate.progress"`` -- Orchestrate dispatching.
    - ``"task.verification.promoted"`` -- gate promoted rung.
    - ``"task.verification.demoted"`` -- gate demoted rung.
    - ``"task.complete"`` -- final Result available.
    - ``"task.failed"`` -- error envelope.
    """

    task_id: str
    type: str
    timestamp_unix: float
    payload: dict[str, Any] = field(default_factory=dict)


class EventBroker:
    """Per-task in-memory event buffer (Phase 6a)."""

    def __init__(self, *, max_events_per_task: int = 1000) -> None:
        self._buffers: dict[str, list[TaskEvent]] = {}
        self._lock = threading.Lock()
        self._max = max_events_per_task

    def emit(self, task_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
        """Append an event to the task's buffer.

        Phase 6a evicts oldest events when buffer exceeds
        ``max_events_per_task`` (default 1000) to bound memory growth
        on long-running daemons. Phase 6b's NATS replaces the buffer
        with durable JetStream consumers.
        """
        event = TaskEvent(
            task_id=task_id,
            type=event_type,
            timestamp_unix=time.time(),
            payload=dict(payload) if payload else {},
        )
        with self._lock:
            buf = self._buffers.setdefault(task_id, [])
            buf.append(event)
            if len(buf) > self._max:
                # Evict oldest events but keep recent.
                self._buffers[task_id] = buf[-self._max :]

    def get_events(self, task_id: str, *, since_index: int = 0) -> list[TaskEvent]:
        """Snapshot of events for ``task_id`` from ``since_index`` onward.

        Returns an empty list when the task has no buffered events.
        Caller is expected to track its own cursor; the WebSocket
        handler in Step 8 polls with the cursor it last saw.
        """
        with self._lock:
            buf = self._buffers.get(task_id, [])
            if since_index >= len(buf):
                return []
            return list(buf[since_index:])

    def event_count(self, task_id: str) -> int:
        """Current buffered-event count for ``task_id`` (Phase 6a ops helper)."""
        with self._lock:
            return len(self._buffers.get(task_id, []))

    def clear(self, task_id: str) -> None:
        """Drop all buffered events for ``task_id``. Phase 6a tests
        use this for isolation."""
        with self._lock:
            self._buffers.pop(task_id, None)

    def clear_all(self) -> None:
        """Drop all buffered events for all tasks. Phase 6a tests use
        this for isolation."""
        with self._lock:
            self._buffers.clear()


def to_dict(event: TaskEvent) -> dict[str, Any]:
    """Serialize a :class:`TaskEvent` to JSON-friendly dict for
    WebSocket emission."""
    return asdict(event)


# Module-level singleton.
_BROKER: EventBroker | None = None


def get_broker() -> EventBroker:
    """Lazy module-level :class:`EventBroker` singleton."""
    global _BROKER
    if _BROKER is None:
        _BROKER = EventBroker()
    return _BROKER
