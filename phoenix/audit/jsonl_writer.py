"""JSONL audit sink — date-stamped daily files (Phase 7 Step 1).

Per locked Phase 7 open-item 1 (2026-05-11): the JSONL writer rotates
files daily at midnight UTC. Filenames: ``events-YYYY-MM-DD.jsonl``
under ``~/.phoenix/runtime/audit/``. No size cap; long-term archival
is Phoenix Cloud's commercial bundle (Decision 35).

**Concurrency model:**

The writer owns a daemon thread that drains a bounded
:class:`queue.Queue` of :class:`AuditEvent` instances. The public
:meth:`emit` enqueues and returns in microseconds; the actual file
write happens off the caller's hot path. This matches the
"fire-and-forget to a buffered async writer; <50 µs overhead" target
from Section 7 line 1524.

**Overflow policy:** if the queue is full (default capacity 10,000),
the writer drops the event and logs a warning. PERF over correctness
on the audit emit path -- a backed-up sink must never block solve
latency. Operators detect the drop via the warning log + the audit
event for "audit queue full" emitted on the same writer (best-effort).

**Shutdown:** :meth:`close` signals the background thread to stop
after draining the remaining queue. Tests call this in teardown; the
FastAPI lifespan calls it on Phoenix shutdown.

**Date rotation:** the background thread checks each event's
``timestamp_unix`` against the current open file's date; if the date
changed (midnight UTC tick), the old file closes and a new one opens.
No clock-driven rotation -- purely event-driven, which means a quiet
period followed by a single event in the new day still rolls cleanly.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from phoenix.audit.event_format import AuditEvent

logger = logging.getLogger(__name__)


DEFAULT_QUEUE_CAPACITY = 10000
DEFAULT_DRAIN_TIMEOUT_SECONDS = 5.0


def _default_audit_dir() -> Path:
    """Resolve the audit-log directory from env var or default.

    ``$PHOENIX_AUDIT_DIR`` overrides; default is
    ``~/.phoenix/runtime/audit/``.
    """
    override = os.environ.get("PHOENIX_AUDIT_DIR")
    if override:
        return Path(override)
    return Path.home() / ".phoenix" / "runtime" / "audit"


def _event_date_utc(timestamp_unix: float) -> str:
    """Format an event's date as ``YYYY-MM-DD`` in UTC."""
    return datetime.fromtimestamp(timestamp_unix, tz=timezone.utc).strftime("%Y-%m-%d")


class JSONLWriter:
    """Fire-and-forget JSONL audit sink with date-stamped daily files.

    Construction starts the background thread. Calls to :meth:`emit`
    enqueue the event and return; the daemon thread writes to disk.
    Call :meth:`close` to drain + stop on shutdown.
    """

    def __init__(
        self,
        audit_dir: Path | None = None,
        *,
        queue_capacity: int = DEFAULT_QUEUE_CAPACITY,
    ) -> None:
        self._dir = audit_dir if audit_dir is not None else _default_audit_dir()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._queue: queue.Queue[AuditEvent] = queue.Queue(maxsize=queue_capacity)
        self._stop_event = threading.Event()
        self._drops = 0
        self._thread = threading.Thread(
            target=self._run,
            name="phoenix-audit-jsonl-writer",
            daemon=True,
        )
        self._thread.start()

    @property
    def audit_dir(self) -> Path:
        """The directory daily JSONL files are written to."""
        return self._dir

    @property
    def drop_count(self) -> int:
        """Number of events dropped due to queue overflow. Ops surface."""
        return self._drops

    def emit(self, event: AuditEvent) -> None:
        """Enqueue an :class:`AuditEvent` for write. Fire-and-forget.

        On queue overflow the event is dropped + counted (visible via
        :attr:`drop_count`). The drop is also logged at WARNING level
        for ops visibility.
        """
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            self._drops += 1
            logger.warning(
                "JSONLWriter queue full; dropping audit event %r (total drops: %d)",
                event.event_type,
                self._drops,
            )

    def close(self, drain_timeout_seconds: float = DEFAULT_DRAIN_TIMEOUT_SECONDS) -> None:
        """Signal the background thread to stop after draining the queue.

        Idempotent. Joins up to ``drain_timeout_seconds`` waiting for
        the thread to finish; if the queue is huge and the timeout is
        hit, residual events are lost (the file still closes cleanly).
        """
        self._stop_event.set()
        self._thread.join(timeout=drain_timeout_seconds)

    def _run(self) -> None:
        """Background worker: pop events, route to date-stamped files."""
        current_date: str | None = None
        current_file: TextIO | None = None
        try:
            while not (self._stop_event.is_set() and self._queue.empty()):
                try:
                    event = self._queue.get(timeout=0.25)
                except queue.Empty:
                    continue
                event_date = _event_date_utc(event.timestamp_unix)
                if event_date != current_date:
                    if current_file is not None:
                        current_file.close()
                    target = self._dir / f"events-{event_date}.jsonl"
                    current_file = target.open("a", encoding="utf-8")
                    current_date = event_date
                # current_file is non-None after the date-change block.
                assert current_file is not None
                current_file.write(event.to_json_line())
                current_file.flush()
        finally:
            if current_file is not None:
                try:
                    current_file.close()
                except Exception:
                    logger.exception("JSONLWriter: error closing audit file")


__all__ = ["DEFAULT_QUEUE_CAPACITY", "DEFAULT_DRAIN_TIMEOUT_SECONDS", "JSONLWriter"]
