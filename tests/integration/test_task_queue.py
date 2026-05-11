"""Tests for phoenix.queue.task_queue (Phase 6b Step 6).

Coverage:

- Subject helpers and constants match the locked OPEN-4 spec.
- ``TaskQueue.setup_streams`` calls JetStream with the expected
  config (verified via a recording mock).
- ``publish_submit`` requires ``setup_streams`` first.
- ``publish_event`` goes through the core NATS client (no
  JetStream).
- The real-NATS lifecycle exercises remain in
  ``test_nats_runner.py`` -- this file is mock-only so it runs
  without nats-py / nats-server installed AND never disturbs a
  running NATS instance.
"""

from __future__ import annotations

from typing import Any

import pytest


class TestSubjectHelpers:
    """Subject naming + constants are stable contracts (other modules
    import these). Pin them so a rename surfaces in CI."""

    def test_submit_subject_prefix(self) -> None:
        from phoenix.queue.task_queue import SUBMIT_SUBJECT_PREFIX

        assert SUBMIT_SUBJECT_PREFIX == "phoenix.tasks.submit"

    def test_events_subject_prefix(self) -> None:
        from phoenix.queue.task_queue import EVENTS_SUBJECT_PREFIX

        assert EVENTS_SUBJECT_PREFIX == "phoenix.tasks.events"

    def test_drift_alerts_subject(self) -> None:
        from phoenix.queue.task_queue import DRIFT_ALERTS_SUBJECT

        assert DRIFT_ALERTS_SUBJECT == "phoenix.drift.alerts"

    def test_stream_names(self) -> None:
        from phoenix.queue.task_queue import STREAM_NAME_DRIFT, STREAM_NAME_TASKS

        assert STREAM_NAME_TASKS == "phoenix-tasks"
        assert STREAM_NAME_DRIFT == "phoenix-drift"

    def test_max_age_constants(self) -> None:
        from phoenix.queue.task_queue import (
            STREAM_MAX_AGE_DRIFT_SECONDS,
            STREAM_MAX_AGE_TASKS_SECONDS,
        )

        assert STREAM_MAX_AGE_TASKS_SECONDS == 24 * 60 * 60
        assert STREAM_MAX_AGE_DRIFT_SECONDS == 10 * 60

    def test_submit_subject_helper(self) -> None:
        from phoenix.queue.task_queue import submit_subject

        assert submit_subject("batch_realtime") == "phoenix.tasks.submit.batch_realtime"
        assert submit_subject("streaming_realtime") == "phoenix.tasks.submit.streaming_realtime"

    def test_event_subject_helper(self) -> None:
        from phoenix.queue.task_queue import event_subject

        assert event_subject("req_abc123") == "phoenix.tasks.events.req_abc123"


# ---------------------------------------------------------------------------
# TaskQueue behavior with mocked nats client + jetstream context.


class _FakeJetStream:
    def __init__(self) -> None:
        self.add_stream_calls: list[dict[str, Any]] = []
        self.publish_calls: list[tuple[str, bytes]] = []
        self.subscribe_calls: list[dict[str, Any]] = []
        self._raise_on_add_stream: BaseException | None = None

    async def add_stream(self, **kwargs: Any) -> Any:
        self.add_stream_calls.append(kwargs)
        if self._raise_on_add_stream is not None:
            raise self._raise_on_add_stream
        return object()

    async def publish(self, subject: str, payload: bytes) -> Any:
        self.publish_calls.append((subject, payload))
        return object()

    async def subscribe(self, subject: str, **kwargs: Any) -> Any:
        record = {"subject": subject, **kwargs}
        self.subscribe_calls.append(record)
        return object()


class _FakeNATSClient:
    def __init__(self) -> None:
        self._js = _FakeJetStream()
        self.publish_calls: list[tuple[str, bytes]] = []
        self.subscribe_calls: list[dict[str, Any]] = []
        self.closed = False

    def jetstream(self) -> _FakeJetStream:
        return self._js

    async def publish(self, subject: str, payload: bytes) -> Any:
        self.publish_calls.append((subject, payload))
        return object()

    async def subscribe(self, subject: str, **kwargs: Any) -> Any:
        record = {"subject": subject, **kwargs}
        self.subscribe_calls.append(record)
        return object()

    async def close(self) -> None:
        self.closed = True


@pytest.fixture()
def fake_nc() -> _FakeNATSClient:
    return _FakeNATSClient()


@pytest.mark.asyncio
async def test_setup_streams_declares_tasks_stream(fake_nc: _FakeNATSClient) -> None:
    """setup_streams must declare phoenix-tasks with WORK_QUEUE/FILE/24h."""
    nats_api = pytest.importorskip("nats.js.api")
    from phoenix.queue.task_queue import (
        STREAM_MAX_AGE_TASKS_SECONDS,
        STREAM_NAME_TASKS,
        SUBMIT_SUBJECT_PREFIX,
        TaskQueue,
    )

    tq = TaskQueue(fake_nc)  # type: ignore[arg-type]
    await tq.setup_streams()

    assert len(fake_nc._js.add_stream_calls) == 1
    call = fake_nc._js.add_stream_calls[0]
    assert call["name"] == STREAM_NAME_TASKS
    assert call["subjects"] == [f"{SUBMIT_SUBJECT_PREFIX}.>"]
    assert call["retention"] == nats_api.RetentionPolicy.WORK_QUEUE
    assert call["storage"] == nats_api.StorageType.FILE
    # max_age stored as nanoseconds.
    assert call["max_age"] == STREAM_MAX_AGE_TASKS_SECONDS * 1_000_000_000


@pytest.mark.asyncio
async def test_setup_streams_tolerates_existing_stream(
    fake_nc: _FakeNATSClient,
) -> None:
    """add_stream raising BadRequestError (stream already exists) is
    treated as idempotent success."""
    pytest.importorskip("nats.js.errors")
    import nats.js.errors

    fake_nc._js._raise_on_add_stream = nats.js.errors.BadRequestError(
        description="stream already in use"
    )

    from phoenix.queue.task_queue import TaskQueue

    tq = TaskQueue(fake_nc)  # type: ignore[arg-type]
    # Must not raise.
    await tq.setup_streams()


@pytest.mark.asyncio
async def test_publish_submit_requires_setup_streams(
    fake_nc: _FakeNATSClient,
) -> None:
    """publish_submit before setup_streams raises a clear RuntimeError."""
    from phoenix.queue.task_queue import TaskQueue

    tq = TaskQueue(fake_nc)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="setup_streams"):
        await tq.publish_submit("batch_realtime", b"payload")


@pytest.mark.asyncio
async def test_publish_submit_goes_through_jetstream(
    fake_nc: _FakeNATSClient,
) -> None:
    """After setup_streams, publish_submit routes through jetstream.publish
    to the per-tier subject."""
    pytest.importorskip("nats.js.api")
    from phoenix.queue.task_queue import TaskQueue

    tq = TaskQueue(fake_nc)  # type: ignore[arg-type]
    await tq.setup_streams()
    await tq.publish_submit("batch_realtime", b"task-bytes")

    assert fake_nc._js.publish_calls == [("phoenix.tasks.submit.batch_realtime", b"task-bytes")]


@pytest.mark.asyncio
async def test_publish_event_goes_through_core_nats(
    fake_nc: _FakeNATSClient,
) -> None:
    """publish_event uses the core NATS client (no JetStream) per
    OPEN-4 ephemeral subject."""
    from phoenix.queue.task_queue import TaskQueue

    tq = TaskQueue(fake_nc)  # type: ignore[arg-type]
    await tq.publish_event("req_xyz", b"event-bytes")

    assert fake_nc.publish_calls == [("phoenix.tasks.events.req_xyz", b"event-bytes")]
    # JetStream was not touched.
    assert fake_nc._js.publish_calls == []


@pytest.mark.asyncio
async def test_subscribe_submit_uses_durable_consumer(
    fake_nc: _FakeNATSClient,
) -> None:
    """Durable consumer name defaults to phoenix_worker_<tier>; can
    override."""
    pytest.importorskip("nats.js.api")
    from phoenix.queue.task_queue import TaskQueue

    async def handler(msg: Any) -> None:  # pragma: no cover - never called here
        pass

    tq = TaskQueue(fake_nc)  # type: ignore[arg-type]
    await tq.setup_streams()
    await tq.subscribe_submit("batch_realtime", handler)

    assert len(fake_nc._js.subscribe_calls) == 1
    call = fake_nc._js.subscribe_calls[0]
    assert call["subject"] == "phoenix.tasks.submit.batch_realtime"
    assert call["durable"] == "phoenix_worker_batch_realtime"
    assert call["cb"] is handler

    # Custom durable name.
    await tq.subscribe_submit("batch_realtime", handler, durable_name="custom_worker")
    assert fake_nc._js.subscribe_calls[1]["durable"] == "custom_worker"


@pytest.mark.asyncio
async def test_subscribe_events_wildcard_uses_core_nats(
    fake_nc: _FakeNATSClient,
) -> None:
    """The NATSEventBroker wildcard subscriber goes through core NATS."""
    from phoenix.queue.task_queue import TaskQueue

    async def handler(msg: Any) -> None:  # pragma: no cover
        pass

    tq = TaskQueue(fake_nc)  # type: ignore[arg-type]
    await tq.subscribe_events_wildcard(handler)

    assert len(fake_nc.subscribe_calls) == 1
    call = fake_nc.subscribe_calls[0]
    assert call["subject"] == "phoenix.tasks.events.>"
    assert call["cb"] is handler


@pytest.mark.asyncio
async def test_close_delegates_to_client(fake_nc: _FakeNATSClient) -> None:
    from phoenix.queue.task_queue import TaskQueue

    tq = TaskQueue(fake_nc)  # type: ignore[arg-type]
    await tq.close()
    assert fake_nc.closed is True
