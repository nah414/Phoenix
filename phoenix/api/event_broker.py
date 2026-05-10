"""Event broker for verification-gate WebSocket streaming.

Per architecture v1 Section 5.3 + Section 6.6: the verification gate
emits structured events (task.started, task.solver.complete,
task.control.complete, task.orchestrate.progress,
task.verification.promoted, task.verification.demoted, task.complete,
task.failed) which downstream consumers (WebSocket clients, audit log,
ops dashboards) consume.

Phase 6a shipped :class:`EventBroker` -- in-memory per-task buffer
with a sync :meth:`emit` / :meth:`get_events` / :meth:`clear` API.
Phase 6b Step 6 adds :class:`NATSEventBroker` -- the same sync API
on top of NATS pub/sub. Selection happens at :func:`get_broker` time
via the ``$PHOENIX_EVENT_BROKER`` env var:

- ``memory`` (default): in-memory only, single-process, zero
  external deps. Test runs use this. Existing Phase 6a callers
  work unchanged.
- ``nats``: NATS-backed cross-process pub/sub. Locally still buffers
  events for the sync :meth:`get_events` poll API used by the
  Phase 6a WebSocket handler. Requires the ``[nats]`` extra
  (``pip install phoenix-middleware[nats]``) and a reachable NATS
  server. Other tools (ops dashboards, observers) can subscribe to
  ``phoenix.tasks.events.>`` for fan-out.

Both implementations satisfy :class:`BrokerProtocol`; the verification
gate's emit calls do not change regardless of which is active.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from nats.aio.client import Client as NATSClient
    from nats.aio.msg import Msg
    from nats.aio.subscription import Subscription

logger = logging.getLogger(__name__)


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


class BrokerProtocol(Protocol):
    """The sync event-broker contract.

    Both :class:`EventBroker` (in-memory) and :class:`NATSEventBroker`
    structurally satisfy this. :func:`get_broker` returns
    ``BrokerProtocol``-typed values so callers see one shape
    regardless of which impl is active.
    """

    def emit(
        self,
        task_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None: ...

    def get_events(self, task_id: str, *, since_index: int = 0) -> list[TaskEvent]: ...

    def event_count(self, task_id: str) -> int: ...

    def clear(self, task_id: str) -> None: ...

    def clear_all(self) -> None: ...


class EventBroker:
    """Per-task in-memory event buffer (Phase 6a).

    Synchronous, single-process, zero external deps. Tests and
    default ``$PHOENIX_EVENT_BROKER=memory`` selections use this.
    Phase 6b Step 6's :class:`NATSEventBroker` swaps in for
    cross-process fan-out via the same API.
    """

    def __init__(self, *, max_events_per_task: int = 1000) -> None:
        self._buffers: dict[str, list[TaskEvent]] = {}
        self._lock = threading.Lock()
        self._max = max_events_per_task

    def emit(
        self,
        task_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Append an event to the task's buffer.

        Evicts oldest events when buffer exceeds
        ``max_events_per_task`` (default 1000) to bound memory growth
        on long-running daemons.
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
                self._buffers[task_id] = buf[-self._max :]

    def get_events(self, task_id: str, *, since_index: int = 0) -> list[TaskEvent]:
        """Snapshot of events for ``task_id`` from ``since_index`` onward.

        Returns an empty list when the task has no buffered events.
        Caller is expected to track its own cursor; the WebSocket
        handler polls with the cursor it last saw.
        """
        with self._lock:
            buf = self._buffers.get(task_id, [])
            if since_index >= len(buf):
                return []
            return list(buf[since_index:])

    def event_count(self, task_id: str) -> int:
        """Current buffered-event count for ``task_id`` (ops helper)."""
        with self._lock:
            return len(self._buffers.get(task_id, []))

    def clear(self, task_id: str) -> None:
        """Drop all buffered events for ``task_id``."""
        with self._lock:
            self._buffers.pop(task_id, None)

    def clear_all(self) -> None:
        """Drop all buffered events for all tasks."""
        with self._lock:
            self._buffers.clear()


def to_dict(event: TaskEvent) -> dict[str, Any]:
    """Serialize a :class:`TaskEvent` to JSON-friendly dict for
    WebSocket emission."""
    return asdict(event)


class NATSEventBroker:
    """NATS-backed event broker (Phase 6b Step 6).

    Provides the same synchronous :meth:`emit` / :meth:`get_events` /
    :meth:`clear` API as :class:`EventBroker`. Internally runs a
    daemon thread with its own asyncio loop:

    - :meth:`emit` appends to a local buffer (same shape as
      :class:`EventBroker`) AND fires a NATS publish via
      ``asyncio.run_coroutine_threadsafe`` on the broker's loop.
    - A subscriber on ``phoenix.tasks.events.>`` mirrors events
      published by other Phoenix daemons into the same local buffer,
      so a single daemon's :meth:`get_events` poll sees events from
      the whole cluster.
    - :meth:`get_events` reads the local buffer (sync, no NATS roundtrip).

    The buffer is the sync poll surface that Phoenix's Phase 6a
    WebSocket handler needs. NATS is the durable cross-process
    transport that makes the broker more than single-process.

    Per locked Phase 6b open-item 4 (2026-05-10): per-task event
    subjects are ephemeral pub/sub (not JetStream), so reconnect
    replay is uninteresting -- the local buffer is the source of
    truth for any individual daemon's WS streams.

    **Activation:** set ``$PHOENIX_EVENT_BROKER=nats`` and ensure the
    ``[nats]`` extra is installed. Default in Phase 6b stays
    ``memory`` for test isolation.

    Raises:
        ImportError: ``nats-py`` is not installed. The message names
            the ``[nats]`` extra so the caller has a clear recovery
            step.
        TimeoutError: NATS connection does not establish within
            ``connect_timeout_seconds``.
    """

    def __init__(
        self,
        url: str | None = None,
        *,
        max_events_per_task: int = 1000,
        connect_timeout_seconds: float = 10.0,
    ) -> None:
        try:
            import nats  # noqa: F401  -- presence check
        except ImportError as e:
            raise ImportError(
                "NATSEventBroker requires the `nats` extra. "
                "Install with: pip install phoenix-middleware[nats]"
            ) from e

        # Lazy import keeps phoenix.queue.nats_client off this module's
        # static import graph -- mypy resolves the type through the
        # TYPE_CHECKING block above.
        from phoenix.queue.nats_client import _default_url

        self._url = url or _default_url()
        self._max = max_events_per_task
        self._buffers: dict[str, list[TaskEvent]] = {}
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._nc: NATSClient | None = None
        self._sub: Subscription | None = None
        self._ready_event = threading.Event()
        self._error: BaseException | None = None

        self._thread = threading.Thread(
            target=self._run_loop,
            name="phoenix-nats-broker",
            daemon=True,
        )
        self._thread.start()

        if not self._ready_event.wait(timeout=connect_timeout_seconds):
            raise TimeoutError(
                f"NATSEventBroker did not connect to {self._url!r} "
                f"within {connect_timeout_seconds}s"
            )
        if self._error is not None:
            raise self._error

    # ----- loop / connect lifecycle -----

    def _run_loop(self) -> None:
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._async_connect())
        except BaseException as e:
            self._error = e
            self._ready_event.set()
            return
        self._ready_event.set()
        try:
            self._loop.run_forever()
        finally:
            if self._loop is not None and not self._loop.is_closed():
                self._loop.close()

    async def _async_connect(self) -> None:
        import nats

        self._nc = await nats.connect(self._url)
        # Mirror all per-task events into the local buffer so the sync
        # get_events poll surface sees them. Per OPEN-4 this subject
        # is ephemeral (no JetStream); we lose any events published
        # before this subscriber attached, which matches the live-stream
        # semantics by design.
        self._sub = await self._nc.subscribe("phoenix.tasks.events.>", cb=self._on_event)

    async def _on_event(self, msg: Msg) -> None:
        try:
            data = json.loads(msg.data)
        except (ValueError, TypeError):
            logger.exception("NATSEventBroker received unparseable event on %s", msg.subject)
            return
        task_id = data.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            return
        event = TaskEvent(
            task_id=task_id,
            type=str(data.get("type", "")),
            timestamp_unix=float(data.get("timestamp_unix", 0.0)),
            payload=dict(data.get("payload") or {}),
        )
        with self._lock:
            buf = self._buffers.setdefault(task_id, [])
            buf.append(event)
            if len(buf) > self._max:
                self._buffers[task_id] = buf[-self._max :]

    # ----- BrokerProtocol surface -----

    def emit(
        self,
        task_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        event = TaskEvent(
            task_id=task_id,
            type=event_type,
            timestamp_unix=time.time(),
            payload=dict(payload) if payload else {},
        )
        # Local buffer fill (same shape as in-memory broker).
        with self._lock:
            buf = self._buffers.setdefault(task_id, [])
            buf.append(event)
            if len(buf) > self._max:
                self._buffers[task_id] = buf[-self._max :]
        # Fire-and-forget NATS publish for cross-process visibility.
        if self._loop is not None and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_publish(task_id, event), self._loop)

    async def _async_publish(self, task_id: str, event: TaskEvent) -> None:
        if self._nc is None:
            return
        subject = f"phoenix.tasks.events.{task_id}"
        payload_bytes = json.dumps(to_dict(event)).encode("utf-8")
        try:
            await self._nc.publish(subject, payload_bytes)
        except Exception:
            logger.exception("NATS publish failed for task %s", task_id)

    def get_events(self, task_id: str, *, since_index: int = 0) -> list[TaskEvent]:
        with self._lock:
            buf = self._buffers.get(task_id, [])
            if since_index >= len(buf):
                return []
            return list(buf[since_index:])

    def event_count(self, task_id: str) -> int:
        with self._lock:
            return len(self._buffers.get(task_id, []))

    def clear(self, task_id: str) -> None:
        with self._lock:
            self._buffers.pop(task_id, None)

    def clear_all(self) -> None:
        with self._lock:
            self._buffers.clear()

    # ----- shutdown -----

    def close(self) -> None:
        """Stop the background loop and close the NATS connection.

        Idempotent on already-closed instances. Used by
        :func:`reset_brokers` and by tests; the FastAPI lifespan does
        not call this in Phase 6b (the in-memory default never holds
        a connection).
        """
        if self._loop is not None and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self._async_close(), self._loop)
            try:
                future.result(timeout=5.0)
            except Exception:
                logger.exception("Error during NATSEventBroker async close")
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    async def _async_close(self) -> None:
        if self._sub is not None:
            try:
                await self._sub.unsubscribe()
            except Exception:
                logger.exception("NATS subscription unsubscribe failed")
        if self._nc is not None:
            try:
                await self._nc.close()
            except Exception:
                logger.exception("NATS client close failed")


# ---------------------------------------------------------------------------
# Module-level dispatch: env-var-driven broker selection.

_MEMORY_BROKER: EventBroker | None = None
_NATS_BROKER: NATSEventBroker | None = None
_BROKER_LOCK = threading.Lock()


def get_broker() -> BrokerProtocol:
    """Return the configured broker, constructed lazily on first call.

    Selection precedence (per locked Phase 6b open-item 4, 2026-05-10):

    1. ``$PHOENIX_EVENT_BROKER`` env var. Accepted values:
       ``"memory"`` (default) or ``"nats"``.
    2. Default: ``memory``.

    Raises:
        ValueError: env var is set to an unrecognized value.
        ImportError: ``nats`` selected but ``[nats]`` extra absent.
        TimeoutError: ``nats`` selected but NATS connection times out.
    """
    kind = os.environ.get("PHOENIX_EVENT_BROKER", "memory").strip().lower()
    if kind == "memory":
        return _get_memory_broker()
    if kind == "nats":
        return _get_nats_broker()
    raise ValueError(
        f"PHOENIX_EVENT_BROKER={kind!r} is not recognized; "
        f"valid values are 'memory' (default) or 'nats'"
    )


def _get_memory_broker() -> EventBroker:
    """Lazy in-memory broker singleton."""
    global _MEMORY_BROKER
    with _BROKER_LOCK:
        if _MEMORY_BROKER is None:
            _MEMORY_BROKER = EventBroker()
        return _MEMORY_BROKER


def _get_nats_broker() -> NATSEventBroker:
    """Lazy NATS broker singleton. Construction may raise per
    :class:`NATSEventBroker`."""
    global _NATS_BROKER
    with _BROKER_LOCK:
        if _NATS_BROKER is None:
            _NATS_BROKER = NATSEventBroker()
        return _NATS_BROKER


def reset_brokers() -> None:
    """Reset both broker singletons.

    Closes the NATS broker if one was constructed. Tests use this for
    isolation between scenarios; the FastAPI lifespan shutdown calls
    it for clean teardown of NATS connections.
    """
    global _MEMORY_BROKER, _NATS_BROKER
    with _BROKER_LOCK:
        if _NATS_BROKER is not None:
            try:
                _NATS_BROKER.close()
            except Exception:
                logger.exception("Error closing NATSEventBroker on reset")
        _MEMORY_BROKER = None
        _NATS_BROKER = None
