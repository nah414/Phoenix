"""Tests for phoenix.audit (Phase 7 Step 1).

Coverage:

- :class:`AuditEvent` round-trip via ``to_json_line`` / ``event_from_dict``.
- :class:`JSONLWriter` writes to a date-stamped file in the configured
  audit dir.
- Date rotation when an event arrives with a different UTC date.
- Queue overflow drops + increments :attr:`JSONLWriter.drop_count`
  rather than raising.
- :class:`AuditEmitter` fans events to all registered sinks.
- Sink exceptions are isolated -- a broken sink doesn't take down
  the emitter or the other sinks.
- :func:`get_emitter` singleton + :func:`reset_emitter` lifecycle.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from phoenix.audit import (
    AuditEmitter,
    AuditEvent,
    JSONLWriter,
    event_from_dict,
    get_emitter,
    reset_emitter,
)


@pytest.fixture(autouse=True)
def _reset_emitter_singleton(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Each test starts with a fresh emitter singleton and an audit dir
    routed to a per-test tmp_path so writes never touch the real
    ``~/.phoenix/runtime/audit/``."""
    reset_emitter()
    monkeypatch.setenv("PHOENIX_AUDIT_DIR", str(tmp_path / "audit"))
    yield
    reset_emitter()


# ---------------------------------------------------------------------------
# AuditEvent


class TestAuditEvent:
    def test_to_dict_field_order(self) -> None:
        event = AuditEvent(
            timestamp_unix=1717400000.5,
            actor_id="ash",
            layer="safety_gate",
            event_type="safety.stage5.rate_limit_deducted",
            parameters={"cost": 5, "tier": "default"},
            result_hash="sha256:abcdef",
            request_id="req_xyz",
        )
        d = event.to_dict()
        keys = list(d.keys())
        # Stable order: timestamp first, identity next, event meta, payload.
        assert keys == [
            "timestamp_unix",
            "actor_id",
            "layer",
            "event_type",
            "parameters",
            "result_hash",
            "request_id",
        ]
        assert d["parameters"] == {"cost": 5, "tier": "default"}

    def test_to_json_line_is_one_line_with_newline(self) -> None:
        event = AuditEvent(
            timestamp_unix=1717400000.5,
            actor_id="ash",
            layer="safety_gate",
            event_type="safety.stage5.rate_limit_deducted",
            parameters={"cost": 5},
            result_hash="sha256:abcdef",
        )
        line = event.to_json_line()
        # Exactly one trailing newline; no embedded newlines in the JSON.
        assert line.endswith("\n")
        assert line.count("\n") == 1
        # Round-trip via JSON parsing.
        record = json.loads(line.strip())
        assert record["event_type"] == "safety.stage5.rate_limit_deducted"

    def test_round_trip_via_event_from_dict(self) -> None:
        original = AuditEvent(
            timestamp_unix=1717400000.5,
            actor_id="adam",
            layer="verification_gate",
            event_type="verification.gate.promoted",
            parameters={"from_rung": "R3", "to_rung": "R4"},
            result_hash="sha256:deadbeef",
            request_id="req_42",
        )
        line = original.to_json_line()
        record = json.loads(line)
        rebuilt = event_from_dict(record)
        assert rebuilt == original

    def test_event_with_no_request_id_serializes_null(self) -> None:
        event = AuditEvent(
            timestamp_unix=1717400000.0,
            actor_id="ash",
            layer="api",
            event_type="api.health",
            parameters={},
            result_hash="",
            request_id=None,
        )
        record = json.loads(event.to_json_line())
        assert record["request_id"] is None

    def test_ascii_safe_payload(self) -> None:
        """Non-ASCII characters in payload values are escaped, not raw."""
        event = AuditEvent(
            timestamp_unix=1717400000.0,
            actor_id="ash",
            layer="safety_gate",
            event_type="safety.stage1.bad_actor_name",
            parameters={"reason": "ünïcödé"},
            result_hash="",
        )
        line = event.to_json_line()
        # ASCII-safe: escaped, not raw bytes.
        assert "\\u" in line
        # But it still round-trips correctly.
        record = json.loads(line)
        assert record["parameters"]["reason"] == "ünïcödé"


# ---------------------------------------------------------------------------
# JSONLWriter


class TestJSONLWriter:
    def test_writes_to_dated_file(self, tmp_path: Path) -> None:
        writer = JSONLWriter(audit_dir=tmp_path / "audit")
        try:
            event = AuditEvent(
                timestamp_unix=time.time(),
                actor_id="ash",
                layer="safety_gate",
                event_type="step1.smoke",
                parameters={"k": "v"},
                result_hash="sha256:0" * 8,
            )
            writer.emit(event)
            writer.close(drain_timeout_seconds=2.0)
        finally:
            # Ensure thread exit even if assertions fail.
            pass

        # Today's UTC date file should exist with our event.
        files = list((tmp_path / "audit").glob("events-*.jsonl"))
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "step1.smoke" in content
        assert content.endswith("\n")

    def test_date_rotation_across_two_dates(self, tmp_path: Path) -> None:
        """Two events on consecutive UTC dates land in different files."""
        from datetime import datetime, timedelta, timezone

        # Pick a concrete "day 1" at 00:00:01 UTC and "day 2" 24h later.
        day1 = datetime(2026, 5, 9, 0, 0, 1, tzinfo=timezone.utc)
        day2 = day1 + timedelta(days=1)
        ts_day1 = day1.timestamp()
        ts_day2 = day2.timestamp()

        writer = JSONLWriter(audit_dir=tmp_path / "audit")
        try:
            writer.emit(
                AuditEvent(
                    timestamp_unix=ts_day1,
                    actor_id="ash",
                    layer="t",
                    event_type="t.day1",
                    parameters={},
                    result_hash="",
                )
            )
            writer.emit(
                AuditEvent(
                    timestamp_unix=ts_day2,
                    actor_id="ash",
                    layer="t",
                    event_type="t.day2",
                    parameters={},
                    result_hash="",
                )
            )
            writer.close(drain_timeout_seconds=2.0)
        finally:
            pass

        files = sorted((tmp_path / "audit").glob("events-*.jsonl"))
        assert len(files) == 2
        # Files named events-YYYY-MM-DD.jsonl; the dates should differ
        # by exactly one day.
        date1 = datetime.strptime(files[0].stem.removeprefix("events-"), "%Y-%m-%d").date()
        date2 = datetime.strptime(files[1].stem.removeprefix("events-"), "%Y-%m-%d").date()
        assert (date2 - date1).days == 1
        assert "t.day1" in files[0].read_text(encoding="utf-8")
        assert "t.day2" in files[1].read_text(encoding="utf-8")

    def test_queue_overflow_drops_silently(self, tmp_path: Path) -> None:
        """When the queue is full, emits drop + count rather than raise."""
        writer = JSONLWriter(audit_dir=tmp_path / "audit", queue_capacity=2)
        # Don't let the background thread drain -- emit faster than it can.
        # Easiest: stop the thread immediately, then over-emit.
        writer._stop_event.set()  # type: ignore[attr-defined]
        writer._thread.join(timeout=2.0)  # type: ignore[attr-defined]
        try:
            for i in range(10):
                writer.emit(
                    AuditEvent(
                        timestamp_unix=time.time(),
                        actor_id="ash",
                        layer="t",
                        event_type=f"t.{i}",
                        parameters={},
                        result_hash="",
                    )
                )
            # Capacity 2 + the thread is stopped so nothing drains.
            # We emit 10; first 2 enqueue, remaining 8 drop.
            assert writer.drop_count == 8
        finally:
            writer.close(drain_timeout_seconds=0.5)


# ---------------------------------------------------------------------------
# AuditEmitter


class _RecordingSink:
    """Test double — records every emit."""

    def __init__(self) -> None:
        self.received: list[AuditEvent] = []
        self.closed = False

    def emit(self, event: AuditEvent) -> None:
        self.received.append(event)

    def close(self, drain_timeout_seconds: float = 5.0) -> None:
        self.closed = True


class _FailingSink:
    name = "failing"

    def __init__(self) -> None:
        self.closed = False

    def emit(self, _event: AuditEvent) -> None:
        raise RuntimeError("synthetic sink failure")

    def close(self, drain_timeout_seconds: float = 5.0) -> None:
        self.closed = True


class TestAuditEmitter:
    def test_fan_out_to_all_sinks(self) -> None:
        emitter = AuditEmitter()
        s1 = _RecordingSink()
        s2 = _RecordingSink()
        emitter.add_sink(s1)
        emitter.add_sink(s2)
        event = AuditEvent(
            timestamp_unix=1.0,
            actor_id="a",
            layer="t",
            event_type="t.x",
            parameters={},
            result_hash="",
        )
        emitter.emit(event)
        assert s1.received == [event]
        assert s2.received == [event]

    def test_sink_exception_isolated(self) -> None:
        """A broken sink doesn't take down the others (or the emitter)."""
        emitter = AuditEmitter()
        bad = _FailingSink()
        good = _RecordingSink()
        emitter.add_sink(bad)  # type: ignore[arg-type]
        emitter.add_sink(good)
        event = AuditEvent(
            timestamp_unix=1.0,
            actor_id="a",
            layer="t",
            event_type="t.x",
            parameters={},
            result_hash="",
        )
        emitter.emit(event)  # must not raise
        assert good.received == [event]

    def test_remove_sink(self) -> None:
        emitter = AuditEmitter()
        s = _RecordingSink()
        emitter.add_sink(s)
        assert emitter.remove_sink(s) is True
        assert emitter.remove_sink(s) is False  # already gone
        emitter.emit(
            AuditEvent(
                timestamp_unix=1.0,
                actor_id="a",
                layer="t",
                event_type="t.x",
                parameters={},
                result_hash="",
            )
        )
        assert s.received == []  # removed before emit

    def test_close_closes_all_sinks(self) -> None:
        emitter = AuditEmitter()
        s1 = _RecordingSink()
        s2 = _RecordingSink()
        emitter.add_sink(s1)
        emitter.add_sink(s2)
        emitter.close()
        assert s1.closed
        assert s2.closed


# ---------------------------------------------------------------------------
# Singleton lifecycle


def test_get_emitter_is_singleton() -> None:
    first = get_emitter()
    second = get_emitter()
    assert first is second


def test_reset_emitter_clears_singleton() -> None:
    first = get_emitter()
    reset_emitter()
    second = get_emitter()
    assert first is not second


def test_default_emitter_has_jsonl_sink(tmp_path: Path) -> None:
    """The default singleton ships with a JSONLWriter pre-registered, so
    the simplest possible Phoenix import-and-emit Just Works."""
    emitter = get_emitter()
    event = AuditEvent(
        timestamp_unix=time.time(),
        actor_id="ash",
        layer="t",
        event_type="default-sink-smoke",
        parameters={},
        result_hash="",
    )
    emitter.emit(event)
    # Drain via close so we know the JSONL write completed.
    reset_emitter()
    files = list((tmp_path / "audit").glob("events-*.jsonl"))
    assert len(files) == 1
    assert "default-sink-smoke" in files[0].read_text(encoding="utf-8")
