"""Parametrized event-broker parity tests (Phase 6b Step 9).

Per the BUILDGUIDE Step 9 spec: the same test bodies run against
both :class:`EventBroker` (in-memory) and :class:`NATSEventBroker`.
This asserts behavioral parity at the :class:`BrokerProtocol`
surface -- the verification gate's emit calls do not change shape
regardless of which broker is active.

**Broker selection per parametrization:**

- ``memory`` -- always runs. Fresh :class:`EventBroker` per test
  (constructor argument); the module-level singleton is *not* used
  here to avoid cross-test bleed.
- ``nats`` -- skips when either:
  - ``nats-py`` is not installed (the ``[nats]`` optional extra is
    not present), OR
  - ``$PHOENIX_NATS_TEST_ENABLED`` is not set to ``"1"`` (a running
    ``nats-server`` is required for ``NATSEventBroker`` to connect;
    the env-var gate matches the Step 5 real-NATS lifecycle pattern
    so we never disturb concurrent NATS usage by default).

Tests assert the **observable contract** (emit then get_events
returns the event with the same shape; clear empties the buffer;
event_count tracks). Tests do NOT assert NATS-specific behavior
like cross-process visibility -- that's the Step 9 BUILDGUIDE's
"NATSEventBroker against the same contract" wording.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

from phoenix.api.event_broker import EventBroker, TaskEvent


def _make_memory_broker() -> EventBroker:
    return EventBroker(max_events_per_task=1000)


def _make_nats_broker() -> Any:
    """Construct a :class:`NATSEventBroker` or skip the test."""
    pytest.importorskip("nats")
    if os.environ.get("PHOENIX_NATS_TEST_ENABLED") != "1":
        pytest.skip(
            "PHOENIX_NATS_TEST_ENABLED not set; "
            "set to 1 with a running nats-server to run real-NATS parity"
        )
    from phoenix.api.event_broker import NATSEventBroker

    return NATSEventBroker(connect_timeout_seconds=5.0)


@pytest.fixture(params=["memory", "nats"])
def broker(request: pytest.FixtureRequest) -> Iterator[Any]:
    kind = request.param
    b = _make_memory_broker() if kind == "memory" else _make_nats_broker()
    try:
        yield b
    finally:
        try:
            b.clear_all()
        except Exception:
            pass
        close = getattr(b, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# BrokerProtocol parity assertions.


def test_emit_then_get_events_returns_event(broker: Any) -> None:
    broker.emit("task_p9", "task.started", {"initial_rung": "R1"})
    events = broker.get_events("task_p9")
    # NATSEventBroker fans the same emit through both the local
    # buffer (immediate) AND a NATS publish that the subscriber
    # echoes back; the local buffer-append happens synchronously
    # inside emit, so the sync get_events call returns at least
    # one event right after emit.
    assert len(events) >= 1
    assert events[0].task_id == "task_p9"
    assert events[0].type == "task.started"
    assert events[0].payload == {"initial_rung": "R1"}


def test_get_events_empty_for_unknown_task(broker: Any) -> None:
    assert broker.get_events("never-emitted") == []


def test_get_events_since_index_skips_prior(broker: Any) -> None:
    for i in range(3):
        broker.emit("task_p9_2", "tick", {"i": i})
    all_events = broker.get_events("task_p9_2")
    assert len(all_events) >= 3
    # since_index=2 returns events at indices 2 onward.
    tail = broker.get_events("task_p9_2", since_index=2)
    assert len(tail) >= 1
    assert tail[0].payload["i"] == 2


def test_event_count_matches_emit_count(broker: Any) -> None:
    broker.emit("task_p9_3", "a", {})
    broker.emit("task_p9_3", "b", {})
    broker.emit("task_p9_3", "c", {})
    # NATSEventBroker may transiently see a higher count if the
    # NATS subscriber echo arrives before clear_all; we assert >=
    # to be tolerant of that ordering.
    assert broker.event_count("task_p9_3") >= 3


def test_clear_drops_per_task_buffer(broker: Any) -> None:
    broker.emit("task_p9_4", "a", {})
    broker.emit("task_p9_4", "b", {})
    broker.emit("other_task", "x", {})
    broker.clear("task_p9_4")
    assert broker.event_count("task_p9_4") == 0
    # Other task's buffer untouched.
    assert broker.event_count("other_task") >= 1


def test_clear_all_drops_every_buffer(broker: Any) -> None:
    broker.emit("task_p9_5a", "a", {})
    broker.emit("task_p9_5b", "b", {})
    broker.clear_all()
    assert broker.event_count("task_p9_5a") == 0
    assert broker.event_count("task_p9_5b") == 0


def test_emit_returns_taskevent_shape(broker: Any) -> None:
    """Every event returned by get_events is a :class:`TaskEvent`
    with the documented shape -- this is the BrokerProtocol contract
    consumers depend on."""
    broker.emit(
        "task_p9_6",
        "task.complete",
        {"value": 42, "agreement_type": "CONVERGED"},
    )
    events = broker.get_events("task_p9_6")
    assert len(events) >= 1
    ev = events[0]
    assert isinstance(ev, TaskEvent)
    assert ev.task_id == "task_p9_6"
    assert ev.type == "task.complete"
    assert isinstance(ev.timestamp_unix, float)
    assert ev.payload == {"value": 42, "agreement_type": "CONVERGED"}


def test_payload_default_empty_dict(broker: Any) -> None:
    """Calling emit without payload yields TaskEvent with empty
    payload dict (parity with both broker implementations)."""
    broker.emit("task_p9_7", "task.started")
    events = broker.get_events("task_p9_7")
    assert len(events) >= 1
    assert events[0].payload == {}
