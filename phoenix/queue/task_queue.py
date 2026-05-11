"""NATS JetStream task queue (Phase 6b Step 6).

Per architecture v1 Section 1 Decision 32 + the locked Phase 6b
open-item 4 (2026-05-10) JetStream consumer modes:

- ``phoenix.tasks.submit.<latency_tier>`` -- durable, no TTL. Task
  submissions must not be lost; durable consumers survive daemon
  restart and resume from last-acked message.
- ``phoenix.tasks.events.<task_id>`` -- ephemeral. Real-time
  WebSocket updates; reconnect replay is uninteresting.
- ``phoenix.drift.alerts`` -- durable, MAX_AGE=10m. Briefly
  disconnected ops dashboards catch up; longer history lives in the
  audit log at Phase 7. The constant lives here so Step 8's drift
  WebSocket endpoint and Step 7's drift detector share the canonical
  subject name; the stream is declared by the drift detector module.

The Step 6 deliverable is :class:`TaskQueue` plus the subject
constants. Step 7 (drift detector) and Step 8 (drift WS endpoint)
import these constants. Routes.py refactoring to publish through the
queue is a Phase 7 concern -- Phase 6b ships the queue infrastructure
and the optional NATS broker; the in-memory broker remains the
default and the synchronous solve path inside routes.py is unchanged.

:class:`TaskQueue` is async-only -- it wraps the nats-py asyncio
client directly. Synchronous callers (the verification gate's event
emission path) go through
:class:`phoenix.api.event_broker.NATSEventBroker` which provides a
sync facade on top of an internal asyncio loop.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nats.aio.client import Client as NATSClient
    from nats.aio.subscription import Subscription
    from nats.js import JetStreamContext


# ---------------------------------------------------------------------------
# Subject constants -- the canonical names every Phoenix module should use.

SUBMIT_SUBJECT_PREFIX = "phoenix.tasks.submit"
"""Durable ingress subject prefix. Per-tier suffix appended at publish."""

EVENTS_SUBJECT_PREFIX = "phoenix.tasks.events"
"""Ephemeral per-task event subject prefix. Per-task suffix appended."""

DRIFT_ALERTS_SUBJECT = "phoenix.drift.alerts"
"""Single subject for drift-alert events (Step 7 detector, Step 8 WS)."""


# Stream config per BUILDGUIDE Step 6 + locked OPEN-4 (2026-05-10).

STREAM_NAME_TASKS = "phoenix-tasks"
"""JetStream stream name for the task ingress queue."""

STREAM_NAME_DRIFT = "phoenix-drift"
"""JetStream stream name for drift alerts (declared by Step 7's detector)."""

STREAM_MAX_AGE_TASKS_SECONDS = 24 * 60 * 60
"""24-hour retention on the tasks stream."""

STREAM_MAX_AGE_DRIFT_SECONDS = 10 * 60
"""10-minute retention on drift alerts per OPEN-4."""

DEFAULT_WORKER_POOL_SIZE = 4
"""Default per-daemon worker pool size for the submit queue consumers."""


def submit_subject(latency_tier: str) -> str:
    """Compose the durable submit subject for a given latency tier.

    ``latency_tier`` is the string value of :class:`LatencyTier` (e.g.
    ``"batch_realtime"``). The returned subject is published to and
    consumed from by Phoenix workers.
    """
    return f"{SUBMIT_SUBJECT_PREFIX}.{latency_tier}"


def event_subject(task_id: str) -> str:
    """Compose the ephemeral per-task event subject."""
    return f"{EVENTS_SUBJECT_PREFIX}.{task_id}"


# ---------------------------------------------------------------------------
# TaskQueue -- async wrapper around the nats-py client + JetStream context.


class TaskQueue:
    """Async JetStream-backed task queue.

    Construction is decoupled from connection: the caller passes an
    already-connected :class:`nats.aio.client.Client` (typically from
    :func:`phoenix.queue.nats_client.connect_nats`). Call
    :meth:`setup_streams` once at daemon startup to declare streams
    idempotently; subsequent publish/subscribe calls assume streams
    exist.

    The class itself does not import ``nats`` at module load time --
    the type hints use ``TYPE_CHECKING`` imports. Concrete nats-py
    types are imported lazily inside the methods that need them, so
    SQLite-only Phoenix installs without the ``[nats]`` extra can
    import this module without errors and only fail at method-call
    time with a clear ``ImportError``.
    """

    def __init__(self, nats_client: NATSClient) -> None:
        self._nc = nats_client
        self._js: JetStreamContext | None = None

    async def setup_streams(self) -> None:
        """Idempotently declare the ``phoenix-tasks`` JetStream stream.

        Stream config (per locked OPEN-4, 2026-05-10):

        - Subjects: ``phoenix.tasks.submit.>`` (durable ingress).
        - Retention: ``WORK_QUEUE`` -- consumers ack once, message
          deleted from stream.
        - Storage: file-backed (survives NATS restart).
        - MAX_AGE: 24 hours.

        Idempotent: if the stream already exists with a compatible
        config, the underlying ``add_stream`` call returns the
        existing config without modification.
        """
        import nats.js.api
        import nats.js.errors

        self._js = self._nc.jetstream()
        try:
            await self._js.add_stream(
                name=STREAM_NAME_TASKS,
                subjects=[f"{SUBMIT_SUBJECT_PREFIX}.>"],
                retention=nats.js.api.RetentionPolicy.WORK_QUEUE,
                storage=nats.js.api.StorageType.FILE,
                max_age=STREAM_MAX_AGE_TASKS_SECONDS * 1_000_000_000,
            )
        except nats.js.errors.BadRequestError:
            # Stream exists with a different-but-compatible config; the
            # idempotency contract holds without forcing an update.
            pass

    async def publish_submit(self, latency_tier: str, payload: bytes) -> None:
        """Publish a task submission to the durable ingress queue.

        Raises:
            RuntimeError: :meth:`setup_streams` was not called first.
        """
        if self._js is None:
            raise RuntimeError("TaskQueue.setup_streams() must be called before publish_submit")
        await self._js.publish(submit_subject(latency_tier), payload)

    async def publish_event(self, task_id: str, payload: bytes) -> None:
        """Publish a per-task event (ephemeral subject; no stream).

        Per OPEN-4: per-task events do not flow through JetStream --
        they're pub/sub-only for real-time WS streaming. Subscribers
        that aren't connected when an event fires miss it; that is
        the desired semantics for live progress streams.
        """
        await self._nc.publish(event_subject(task_id), payload)

    async def subscribe_submit(
        self,
        latency_tier: str,
        handler: Any,
        *,
        durable_name: str | None = None,
    ) -> Any:
        """Subscribe a durable consumer to the submit queue.

        Per OPEN-4 the submit-subject consumers are durable so a worker
        restart resumes from the last acked message rather than
        dropping in-flight submissions.

        ``handler`` is an async callable invoked with each
        :class:`nats.aio.msg.Msg`; the handler is responsible for
        calling ``await msg.ack()`` (or ``msg.nak()`` to retry).

        ``durable_name`` defaults to ``"phoenix_worker_<latency_tier>"``;
        override only when running multiple isolated worker pools.
        """
        if self._js is None:
            raise RuntimeError("TaskQueue.setup_streams() must be called before subscribe_submit")
        durable = durable_name or f"phoenix_worker_{latency_tier}"
        return await self._js.subscribe(
            submit_subject(latency_tier),
            durable=durable,
            cb=handler,
        )

    async def subscribe_events(self, task_id: str, handler: Any) -> Subscription:
        """Subscribe to per-task events on the ephemeral subject.

        Per OPEN-4: ephemeral pub/sub via the core NATS client (not
        JetStream). The returned :class:`nats.aio.subscription.Subscription`
        must be ``unsubscribe()``-ed by the caller when done (typically
        on WebSocket disconnect).
        """
        return await self._nc.subscribe(event_subject(task_id), cb=handler)

    async def subscribe_events_wildcard(self, handler: Any) -> Subscription:
        """Subscribe to ALL per-task events (``phoenix.tasks.events.>``).

        Used by :class:`phoenix.api.event_broker.NATSEventBroker` to
        mirror all event traffic into its local buffer for the
        sync :meth:`get_events` API.
        """
        return await self._nc.subscribe(f"{EVENTS_SUBJECT_PREFIX}.>", cb=handler)

    async def close(self) -> None:
        """Close the underlying NATS connection.

        Idempotent on already-closed connections (the nats-py client
        tolerates the call).
        """
        await self._nc.close()


__all__ = [
    "DEFAULT_WORKER_POOL_SIZE",
    "DRIFT_ALERTS_SUBJECT",
    "EVENTS_SUBJECT_PREFIX",
    "STREAM_MAX_AGE_DRIFT_SECONDS",
    "STREAM_MAX_AGE_TASKS_SECONDS",
    "STREAM_NAME_DRIFT",
    "STREAM_NAME_TASKS",
    "SUBMIT_SUBJECT_PREFIX",
    "TaskQueue",
    "event_subject",
    "submit_subject",
]
