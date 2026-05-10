"""Tests for the broker dispatch + NATSEventBroker (Phase 6b Step 6).

Coverage focus:

- ``get_broker()`` default returns the in-memory ``EventBroker``
  (Phase 6a contract preserved).
- ``$PHOENIX_EVENT_BROKER=memory`` matches the default.
- ``$PHOENIX_EVENT_BROKER=invalid`` raises a clear ``ValueError``.
- ``$PHOENIX_EVENT_BROKER=nats`` with ``[nats]`` extra absent raises
  a clear ``ImportError`` naming the extra.
- ``reset_brokers()`` clears both singletons.
- :class:`BrokerProtocol` structurally accepts the in-memory broker
  (and would accept the NATS broker -- exercised in the real-NATS
  test in ``test_nats_runner.py``).

**Skipped by default:** any test that would actually launch a NATS
connection. The default Phase 6b test run never opens port 4222.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _reset_brokers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure each test starts with a clean broker singleton and a
    known env-var baseline. ``reset_brokers`` is called explicitly
    in setup AND teardown so a test that constructs a NATS broker
    cleans up its background thread even on assertion failure."""
    from phoenix.api.event_broker import reset_brokers

    reset_brokers()
    monkeypatch.delenv("PHOENIX_EVENT_BROKER", raising=False)
    yield
    reset_brokers()


def test_default_returns_in_memory_broker() -> None:
    """No env var set -> in-memory ``EventBroker``."""
    from phoenix.api.event_broker import EventBroker, get_broker

    broker = get_broker()
    assert isinstance(broker, EventBroker)


def test_explicit_memory_returns_in_memory_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOENIX_EVENT_BROKER", "memory")
    from phoenix.api.event_broker import EventBroker, get_broker

    broker = get_broker()
    assert isinstance(broker, EventBroker)


def test_case_insensitive_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIX_EVENT_BROKER", "Memory")
    from phoenix.api.event_broker import EventBroker, get_broker

    broker = get_broker()
    assert isinstance(broker, EventBroker)


def test_invalid_kind_raises_valueerror(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIX_EVENT_BROKER", "redis")
    from phoenix.api.event_broker import get_broker

    with pytest.raises(ValueError, match="redis"):
        get_broker()


def test_memory_broker_is_singleton() -> None:
    from phoenix.api.event_broker import get_broker

    first = get_broker()
    second = get_broker()
    assert first is second


def test_reset_brokers_clears_memory_singleton() -> None:
    from phoenix.api.event_broker import get_broker, reset_brokers

    first = get_broker()
    reset_brokers()
    second = get_broker()
    assert first is not second


def test_nats_kind_without_extra_raises_importerror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When PHOENIX_EVENT_BROKER=nats but the `[nats]` extra is not
    installed, get_broker raises ImportError naming the extra."""
    monkeypatch.setenv("PHOENIX_EVENT_BROKER", "nats")

    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> Any:
        if name == "nats" or name.startswith("nats."):
            raise ImportError("simulated missing nats-py")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    from phoenix.api.event_broker import get_broker

    with pytest.raises(ImportError, match=r"\[nats\]"):
        get_broker()


def test_in_memory_broker_satisfies_broker_protocol() -> None:
    """EventBroker structurally satisfies BrokerProtocol.

    runtime_checkable Protocols verify by ``isinstance`` only when the
    Protocol is decorated; ours is plain ``Protocol`` so we duck-type
    by attribute presence + callable shape. This guards against an
    accidental rename of one of the BrokerProtocol methods on
    EventBroker."""
    from phoenix.api.event_broker import EventBroker

    broker = EventBroker()
    for method_name in ("emit", "get_events", "event_count", "clear", "clear_all"):
        assert callable(getattr(broker, method_name))


def test_existing_phase6a_test_pattern_still_works() -> None:
    """The Phase 6a usage pattern in test_ws_endpoint.py must keep
    working unchanged: get_broker(), clear_all(), emit, get_events."""
    from phoenix.api.event_broker import get_broker

    broker = get_broker()
    broker.clear_all()

    broker.emit("task_xyz", "task.started", {"foo": "bar"})
    broker.emit("task_xyz", "task.complete", {"value": 42})

    events = broker.get_events("task_xyz")
    assert len(events) == 2
    assert events[0].type == "task.started"
    assert events[1].type == "task.complete"
    assert events[1].payload == {"value": 42}

    assert broker.event_count("task_xyz") == 2
    broker.clear("task_xyz")
    assert broker.event_count("task_xyz") == 0
