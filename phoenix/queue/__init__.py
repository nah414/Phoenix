"""Phoenix queue package -- NATS JetStream connection + embedded runner.

Phase 6b Step 5 exports the connection wrapper and the embedded
runner's lifecycle helpers at the package level. Step 6 adds the
task-queue and (optional NATS-backed) event broker.
"""

from phoenix.queue.embedded_runner import (
    EmbeddedNATSHandle,
    EmbeddedNATSNotFound,
    EmbeddedNATSNotReady,
    start_embedded_nats,
    stop_embedded_nats,
)
from phoenix.queue.nats_client import connect_nats

__all__ = [
    "EmbeddedNATSHandle",
    "EmbeddedNATSNotFound",
    "EmbeddedNATSNotReady",
    "connect_nats",
    "start_embedded_nats",
    "stop_embedded_nats",
]
