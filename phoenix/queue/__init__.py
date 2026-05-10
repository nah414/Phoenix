"""Phoenix queue package -- NATS JetStream connection + embedded runner
+ task queue.

Phase 6b Step 5 shipped the connection wrapper and the embedded
runner's lifecycle helpers. Step 6 adds :class:`TaskQueue` plus the
canonical subject / stream constants every Phoenix module shares.
Step 7's drift detector and Step 8's drift WebSocket endpoint reuse
these constants directly.
"""

from phoenix.queue.embedded_runner import (
    EmbeddedNATSHandle,
    EmbeddedNATSNotFound,
    EmbeddedNATSNotReady,
    start_embedded_nats,
    stop_embedded_nats,
)
from phoenix.queue.nats_client import connect_nats
from phoenix.queue.task_queue import (
    DEFAULT_WORKER_POOL_SIZE,
    DRIFT_ALERTS_SUBJECT,
    EVENTS_SUBJECT_PREFIX,
    STREAM_MAX_AGE_DRIFT_SECONDS,
    STREAM_MAX_AGE_TASKS_SECONDS,
    STREAM_NAME_DRIFT,
    STREAM_NAME_TASKS,
    SUBMIT_SUBJECT_PREFIX,
    TaskQueue,
    event_subject,
    submit_subject,
)

__all__ = [
    "DEFAULT_WORKER_POOL_SIZE",
    "DRIFT_ALERTS_SUBJECT",
    "EVENTS_SUBJECT_PREFIX",
    "EmbeddedNATSHandle",
    "EmbeddedNATSNotFound",
    "EmbeddedNATSNotReady",
    "STREAM_MAX_AGE_DRIFT_SECONDS",
    "STREAM_MAX_AGE_TASKS_SECONDS",
    "STREAM_NAME_DRIFT",
    "STREAM_NAME_TASKS",
    "SUBMIT_SUBJECT_PREFIX",
    "TaskQueue",
    "connect_nats",
    "event_subject",
    "start_embedded_nats",
    "stop_embedded_nats",
    "submit_subject",
]
