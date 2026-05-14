"""Typed errors for the queue subsystem (Phase 11 Step 1).

Per architecture v1 Section 10.7 ("Compositional fail-closed test"):
when the NATS JetStream connection is unavailable, Phoenix must
fail with a typed error rather than leaking driver-level
exceptions (``nats.errors.NoServersError``,
``nats.errors.ConnectionClosedError``) or generic
:class:`RuntimeError` from setup-not-called paths.

The NATS-backed task queue + event broker raise
:class:`QueueUnavailable` when:

- :meth:`TaskQueue.setup_streams` was not called before
  publish/subscribe (programmer error caught at the boundary).
- The JetStream context cannot reach the configured NATS server.
- A publish or subscribe operation fails with a non-recoverable
  driver-level error.

The in-memory broker (``InMemoryEventBroker``) does not raise
this -- it's the local fallback used when NATS isn't configured,
not when NATS is configured but down. The §10.7 acceptance
specifically targets the NATS-configured-but-unreachable case.
"""

from __future__ import annotations


class QueueUnavailable(Exception):
    """The task queue cannot be reached.

    Raised by :class:`TaskQueue` and the NATS-backed
    :class:`EventBroker` impl when the JetStream connection is
    unreachable or the queue subsystem was not properly
    initialized.

    Phoenix-level callers (task submission path, WS event broker)
    catch :class:`QueueUnavailable` and fail-closed rather than
    fall back to an in-memory queue silently. Per Section 10.7:
    fail fast + loud + typed.

    The ``subject`` attribute, when populated, names the NATS
    subject the failed operation was targeting (e.g.,
    ``"phoenix.tasks.submit.batch_realtime"``) so operators can
    correlate the typed error with NATS-side logs.
    """

    def __init__(
        self,
        message: str,
        *,
        subject: str | None = None,
    ) -> None:
        super().__init__(message)
        self.subject = subject


__all__ = ["QueueUnavailable"]
