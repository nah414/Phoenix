"""Phase 11 Step 2 -- NATS-down isolation test (§10.7 acceptance).

Per architecture v1 Section 10.7: "Phoenix must fail fast and loud
with typed errors (``QueueUnavailable``, ``StateBackendUnavailable``,
``DriftStateUnavailable``) -- never silently degrade or return a
result without verification."

This module pins the NATS-down portion of that contract:

1. ``TaskQueue.publish_submit`` / ``subscribe_submit`` raise
   :class:`QueueUnavailable` when ``setup_streams`` was never
   called (proxy for "NATS connection never established").
2. The ``kill_queue`` fixture composes -- when downstream code
   tries to publish through a TaskQueue whose publish path has
   been patched to fail-closed, the typed error surfaces all the
   way up.
3. ``NATSEventBroker`` (Phase 6b Step 6) raises
   :class:`QueueUnavailable` when configured to point at an
   unreachable NATS server. Skipped when ``nats-py`` is not
   installed (the ``[nats]`` optional extra), matching the
   existing test_nats_event_broker pattern.
4. **No silent fallback to in-memory broker.** When NATS is the
   configured event-broker AND it's down, Phoenix does NOT
   silently degrade to the InMemoryEventBroker -- it raises.
   The fail-closed contract composes the typed error all the way
   to the caller.

The Step 5 combined "simultaneous three" test composes this
NATS-down failure with state-backend-down + drift-detector-down.
"""

from __future__ import annotations

import pytest

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix.queue.errors import QueueUnavailable
from tests.integration.test_panic_mode.conftest import kill_queue


pytestmark = pytest.mark.acceptance


# ---------------------------------------------------------------------
# 1. TaskQueue surfaces QueueUnavailable on missing setup
# ---------------------------------------------------------------------


class TestTaskQueueFailClosed:
    @pytest.mark.asyncio
    async def test_publish_submit_fail_closed(self) -> None:
        """publish_submit without setup_streams() -> QueueUnavailable."""
        from phoenix.queue.task_queue import TaskQueue

        class _StubNatsClient:
            pass

        queue = TaskQueue(nats_client=_StubNatsClient())  # type: ignore[arg-type]
        # _js is None at construction; publish_submit fail-closes immediately
        with pytest.raises(QueueUnavailable) as excinfo:
            await queue.publish_submit("batch_realtime", b"payload")
        assert excinfo.value.subject == "phoenix.tasks.submit.batch_realtime"

    @pytest.mark.asyncio
    async def test_subscribe_submit_fail_closed(self) -> None:
        """subscribe_submit without setup_streams() -> QueueUnavailable."""
        from phoenix.queue.task_queue import TaskQueue

        class _StubNatsClient:
            pass

        async def _noop_handler(msg) -> None:  # noqa: ARG001
            pass

        queue = TaskQueue(nats_client=_StubNatsClient())  # type: ignore[arg-type]
        with pytest.raises(QueueUnavailable) as excinfo:
            await queue.subscribe_submit("batch_realtime", _noop_handler)
        assert excinfo.value.subject == "phoenix.tasks.submit.batch_realtime"


# ---------------------------------------------------------------------
# 2. kill_queue fixture: typed error composes through downstream callers
# ---------------------------------------------------------------------


class TestKillQueueComposition:
    @pytest.mark.asyncio
    async def test_downstream_caller_sees_typed_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A function that wraps publish_submit must surface QueueUnavailable
        intact (no swallow, no wrap-as-generic-Exception).
        """
        from phoenix.queue.task_queue import TaskQueue

        class _StubNatsClient:
            pass

        queue = TaskQueue(nats_client=_StubNatsClient())  # type: ignore[arg-type]

        async def _downstream_caller() -> None:
            # Simulates a higher-level path (e.g., a worker that publishes
            # task submissions). When NATS is down, this layer should NOT
            # catch + swallow.
            await queue.publish_submit("batch_realtime", b"task-payload")

        with kill_queue(monkeypatch):
            with pytest.raises(QueueUnavailable) as excinfo:
                await _downstream_caller()
            # The kill_queue fixture sets a synthetic subject so we can
            # confirm the right error surfaced from the right boundary.
            assert "panic-mode test" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_isinstance_check_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Callers that pattern-match with ``isinstance`` see the typed
        error -- not a parent ``RuntimeError`` or generic ``Exception``.
        """
        from phoenix.queue.task_queue import TaskQueue

        class _StubNatsClient:
            pass

        queue = TaskQueue(nats_client=_StubNatsClient())  # type: ignore[arg-type]

        with kill_queue(monkeypatch):
            try:
                await queue.publish_submit("batch_realtime", b"x")
            except Exception as exc:
                assert isinstance(exc, QueueUnavailable)
                # NOT a bare RuntimeError (which was the pre-Phase-11 behavior)
                assert type(exc) is not RuntimeError
            else:
                pytest.fail("expected QueueUnavailable, got no exception")


# ---------------------------------------------------------------------
# 3. NATSEventBroker connect-failure surfaces QueueUnavailable
# ---------------------------------------------------------------------


def _has_nats() -> bool:
    """Skip helper -- ``[nats]`` extra installed?"""
    try:
        import nats  # noqa: F401

        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _has_nats(), reason="nats-py not installed (no [nats] extra)")
class TestNATSEventBrokerFailClosed:
    """Run only when ``[nats]`` extra is installed.

    The broker spins up a daemon thread + asyncio loop on construction;
    pointing it at an unreachable address makes the connect coroutine
    raise inside the loop, which the constructor wraps as
    :class:`QueueUnavailable` per Phase 11 Step 2's update.
    """

    def test_unreachable_url_raises_queue_unavailable(self) -> None:
        from phoenix.api.event_broker import NATSEventBroker

        with pytest.raises(QueueUnavailable) as excinfo:
            NATSEventBroker(
                url="nats://127.0.0.1:1",  # unreachable port
                connect_timeout_seconds=0.5,
            )
        # The error names the unreachable URL so operators see the
        # configured target in logs.
        assert "127.0.0.1:1" in str(excinfo.value)


# ---------------------------------------------------------------------
# 4. No silent fallback to in-memory broker
# ---------------------------------------------------------------------


def test_no_silent_in_memory_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a caller asks for the NATS broker explicitly (via
    ``$PHOENIX_EVENT_BROKER=nats``) and NATS is down, Phoenix MUST NOT
    silently substitute the in-memory broker. The configured-but-
    unreachable case fail-closes; only the "no NATS configured at
    all" case falls back to in-memory.

    This is the §10.7 "never silently degrade" invariant in action.
    """
    # Set the env var to force NATS broker selection
    monkeypatch.setenv("PHOENIX_EVENT_BROKER", "nats")

    # Reset the broker singletons so the next get_broker() honors the env
    from phoenix.api import event_broker as event_broker_module
    from phoenix.api.event_broker import get_broker, reset_brokers

    reset_brokers()

    # Patch NATSEventBroker to raise QueueUnavailable on construction
    def _raising_nats_broker(*args, **kwargs):  # noqa: ANN002, ANN003
        raise QueueUnavailable(
            "synthetic NATS-down for panic-mode test",
            subject=None,
        )

    monkeypatch.setattr(event_broker_module, "NATSEventBroker", _raising_nats_broker)

    # get_broker() with PHOENIX_EVENT_BROKER=nats must propagate the
    # typed error -- no silent fallback to InMemoryEventBroker.
    with pytest.raises(QueueUnavailable):
        get_broker()

    # Restore singletons so subsequent tests start clean
    reset_brokers()
