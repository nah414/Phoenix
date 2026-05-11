"""Parametrized state-backend parity tests (Phase 6b Step 9).

Per the BUILDGUIDE Step 9 spec: the same test bodies run against both
:class:`SQLiteStateBackend` and :class:`PostgresStateBackend`. The
parametrization is the *audit-grade parity check* -- if a method's
contract drifts between backends, exactly one of the two
parametrizations fails the same assertion, naming which dialect
disagreed.

**Backend selection per parametrization:**

- ``sqlite`` -- always runs. Constructs a fresh
  :class:`SQLiteStateBackend` against a per-test tmp_path so writes
  never touch ``~/.phoenix/runtime/state.db``.
- ``postgres`` -- skips when ``$PHOENIX_POSTGRES_TEST_DSN`` is unset
  (CI without a Postgres test instance) OR when the ``[postgres]``
  extra is not installed. Drops the Phase 6b schema in teardown via
  :func:`phoenix.state.migrations.phase6b_initial.revert` so the
  next parametrization sees a clean slate.

This file deliberately overlaps with the Step 2 single-backend
``test_sqlite_backend.py`` and the Step 3 ``test_postgres_backend.py``
-- those preserve their per-backend git history and Phase 2/3
acceptance evidence; this Step 9 file adds explicit parity-of-
contract checks the others can't express.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from phoenix.state.migrations import phase6b_initial, phase7_ledger
from phoenix.state.sqlite_backend import SQLiteStateBackend


def _make_sqlite_backend(tmp_path: Path) -> SQLiteStateBackend:
    return SQLiteStateBackend(db_path=tmp_path / "state.db")


def _make_postgres_backend(_tmp_path: Path) -> Any:
    """Construct a :class:`PostgresStateBackend` or skip the test.

    Skip conditions (any of):
    - ``[postgres]`` extra not installed (psycopg / psycopg_pool absent)
    - ``$PHOENIX_POSTGRES_TEST_DSN`` not set
    """
    pytest.importorskip("psycopg")
    pytest.importorskip("psycopg_pool")
    dsn = os.environ.get("PHOENIX_POSTGRES_TEST_DSN")
    if not dsn:
        pytest.skip(
            "PHOENIX_POSTGRES_TEST_DSN not set; set to a reachable test Postgres DSN to run parity"
        )
    from phoenix.state.postgres_backend import PostgresStateBackend

    return PostgresStateBackend(dsn=dsn, min_size=1, max_size=2)


@pytest.fixture(params=["sqlite", "postgres"])
def backend(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Any]:
    """Parametrized :class:`StateBackend` fixture.

    Each test runs twice -- once per parametrization. Postgres
    teardown reverts the Phase 6b schema so the next test fixture
    construction reruns the migration cleanly.
    """
    kind = request.param
    if kind == "sqlite":
        b = _make_sqlite_backend(tmp_path)
    else:
        b = _make_postgres_backend(tmp_path)
    try:
        yield b
    finally:
        if kind == "postgres":
            try:
                with b._require_pool().connection() as conn:  # type: ignore[attr-defined]
                    # Revert in reverse migration order (newest first)
                    # so foreign-key + schema_version cleanup stays tidy.
                    phase7_ledger.revert(conn)
                    phase6b_initial.revert(conn)
            except Exception:
                pass
        b.close()


# ---------------------------------------------------------------------------
# Phase 6a contract: kill switch (unchanged)


def test_kill_switch_default_is_disengaged(backend: Any) -> None:
    state = backend.get_kill_switch_state()
    assert state == {
        "engaged": False,
        "engaged_at_utc": None,
        "engaged_by": None,
        "reason": None,
    }


def test_kill_switch_round_trip(backend: Any) -> None:
    backend.set_kill_switch_state(
        {
            "engaged": True,
            "engaged_at_utc": "2026-05-10T12:00:00+00:00",
            "engaged_by": "ash",
            "reason": "parity test",
        }
    )
    state = backend.get_kill_switch_state()
    assert state["engaged"] is True
    assert state["engaged_by"] == "ash"
    assert state["reason"] == "parity test"


def test_kill_switch_persists_across_simulated_restart(
    backend: Any, tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """The kill-switch state survives backend close + reopen.

    For SQLite this exercises the on-disk persistence. For Postgres
    this exercises connection-pool teardown + reconstruction against
    the same DSN.
    """
    backend.set_kill_switch_state(
        {
            "engaged": True,
            "engaged_at_utc": "2026-05-10T13:00:00+00:00",
            "engaged_by": "adam",
            "reason": "restart durability",
        }
    )
    backend.close()

    kind = request.node.callspec.params["backend"]
    if kind == "sqlite":
        b2 = SQLiteStateBackend(db_path=tmp_path / "state.db")
    else:
        from phoenix.state.postgres_backend import PostgresStateBackend

        dsn = os.environ["PHOENIX_POSTGRES_TEST_DSN"]
        b2 = PostgresStateBackend(dsn=dsn, min_size=1, max_size=2)
    try:
        state = b2.get_kill_switch_state()
        assert state["engaged"] is True
        assert state["engaged_by"] == "adam"
    finally:
        if kind == "postgres":
            try:
                with b2._require_pool().connection() as conn:  # type: ignore[attr-defined]
                    phase7_ledger.revert(conn)
                    phase6b_initial.revert(conn)
            except Exception:
                pass
        b2.close()


# ---------------------------------------------------------------------------
# Phase 6b: solve cost ledger


def test_solve_cost_round_trip(backend: Any) -> None:
    record: dict[str, Any] = {
        "actor_id": "ash",
        "cost_usd_estimate": 0.42,
        "cost_usd_actual": None,
        "provider": "ibm_quantum",
        "submitted_at_unix": 1717000000.0,
        "completed_at_unix": None,
    }
    backend.put_solve_cost_record("solve_p9_1", record)
    fetched = backend.get_solve_cost_record("solve_p9_1")
    assert fetched is not None
    assert fetched["solve_id"] == "solve_p9_1"
    assert fetched["cost_usd_estimate"] == pytest.approx(0.42)
    assert fetched["cost_usd_actual"] is None
    assert fetched["completed_at_unix"] is None


def test_solve_cost_upsert_on_completion(backend: Any) -> None:
    """Completing a solve overwrites the prior estimate-only record."""
    base = {
        "actor_id": "ash",
        "cost_usd_estimate": 0.5,
        "cost_usd_actual": None,
        "provider": "braket",
        "submitted_at_unix": 1717000000.0,
        "completed_at_unix": None,
    }
    backend.put_solve_cost_record("solve_p9_2", base)
    completed = {**base, "cost_usd_actual": 0.48, "completed_at_unix": 1717000111.0}
    backend.put_solve_cost_record("solve_p9_2", completed)
    fetched = backend.get_solve_cost_record("solve_p9_2")
    assert fetched is not None
    assert fetched["cost_usd_actual"] == pytest.approx(0.48)
    assert fetched["completed_at_unix"] == pytest.approx(1717000111.0)


def test_solve_cost_missing_returns_none(backend: Any) -> None:
    assert backend.get_solve_cost_record("never-seen") is None


# ---------------------------------------------------------------------------
# Phase 6b: audit events (append-only)


def test_audit_events_append_and_list(backend: Any) -> None:
    base_ts = 1717100000.0
    for i in range(4):
        backend.append_audit_event(
            {
                "timestamp_unix": base_ts + i,
                "actor_id": "ash",
                "layer": "safety_gate",
                "event_type": f"event.{i}",
                "parameters": {"i": i, "cost": 5},
                "result_hash": f"sha256:{i:064d}",
            }
        )
    listed = backend.list_audit_events(since_unix=base_ts, limit=10)
    assert len(listed) == 4
    assert listed[0]["event_type"] == "event.0"
    assert listed[-1]["event_type"] == "event.3"
    assert listed[2]["parameters"] == {"i": 2, "cost": 5}


def test_audit_events_since_filter(backend: Any) -> None:
    base_ts = 1717200000.0
    for i in range(5):
        backend.append_audit_event(
            {
                "timestamp_unix": base_ts + i,
                "actor_id": "ash",
                "layer": "safety_gate",
                "event_type": "tick",
                "parameters": {"i": i},
                "result_hash": f"sha256:{i:064d}",
            }
        )
    since_filtered = backend.list_audit_events(since_unix=base_ts + 2.5, limit=10)
    indices = [ev["parameters"]["i"] for ev in since_filtered]
    assert indices == [3, 4]


def test_audit_events_respects_limit(backend: Any) -> None:
    base_ts = 1717300000.0
    for i in range(8):
        backend.append_audit_event(
            {
                "timestamp_unix": base_ts + i,
                "actor_id": "ash",
                "layer": "safety_gate",
                "event_type": "tick",
                "parameters": {"i": i},
                "result_hash": "h",
            }
        )
    limited = backend.list_audit_events(since_unix=base_ts, limit=3)
    assert len(limited) == 3


# ---------------------------------------------------------------------------
# Phase 6b: pending review queue


def test_pending_review_lifecycle(backend: Any) -> None:
    backend.enqueue_pending_review(
        {
            "review_id": "p9_rev_1",
            "task_id": "task_p9_xyz",
            "actor_id": "ash",
            "result": {"agreement_type": "DEGRADED", "value": 4.2},
            "agreement_type": "DEGRADED",
            "queued_at_unix": 1717200000.0,
        }
    )
    backend.enqueue_pending_review(
        {
            "review_id": "p9_rev_2",
            "task_id": "task_p9_abc",
            "actor_id": "adam",
            "result": {"agreement_type": "WOBBLE", "value": 1.1},
            "agreement_type": "WOBBLE",
            "queued_at_unix": 1717200005.0,
        }
    )
    pending = backend.list_pending_reviews()
    assert [r["review_id"] for r in pending] == ["p9_rev_1", "p9_rev_2"]

    backend.resolve_pending_review(
        "p9_rev_1",
        {
            "resolved_by": "adam",
            "resolved_at_unix": 1717200010.0,
            "verdict": "approved",
            "rationale": "within budget",
        },
    )
    remaining = backend.list_pending_reviews()
    assert [r["review_id"] for r in remaining] == ["p9_rev_2"]


# ---------------------------------------------------------------------------
# Phase 6b: drift state snapshot (singleton row, upsert semantics)


def test_drift_state_snapshot_round_trip(backend: Any) -> None:
    assert backend.get_drift_state_snapshot() is None
    backend.put_drift_state_snapshot(
        {
            "cycle_unix": 1717300000.0,
            "state": "warning",
            "firing_detectors": ["tier1_analytical"],
            "detector_summaries": {"tier1_analytical": "HO-1 outside tolerance"},
        }
    )
    fetched = backend.get_drift_state_snapshot()
    assert fetched is not None
    assert fetched["state"] == "warning"
    assert fetched["firing_detectors"] == ["tier1_analytical"]


def test_drift_state_snapshot_upsert_overwrites(backend: Any) -> None:
    backend.put_drift_state_snapshot(
        {
            "cycle_unix": 1717300000.0,
            "state": "warning",
            "firing_detectors": ["tier1_analytical"],
            "detector_summaries": {"tier1_analytical": "HO-1 outside tolerance"},
        }
    )
    backend.put_drift_state_snapshot(
        {
            "cycle_unix": 1717300600.0,
            "state": "healthy",
            "firing_detectors": [],
            "detector_summaries": {},
        }
    )
    fetched = backend.get_drift_state_snapshot()
    assert fetched is not None
    assert fetched["state"] == "healthy"
    assert fetched["firing_detectors"] == []
    assert fetched["cycle_unix"] == pytest.approx(1717300600.0)


# ---------------------------------------------------------------------------
# Phase 6b: actor permissions shadow


def test_actor_permissions_round_trip(backend: Any) -> None:
    backend.put_actor_permission(
        "ash",
        {
            "can_submit_tasks": True,
            "can_replay_tasks": True,
            "can_load_adapter": True,
            "can_unload_adapter": True,
            "frontier_physics": True,
            "can_override_human_review": True,
            "is_admin": True,
            "rate_limit_tier": "admin",
        },
    )
    backend.put_actor_permission(
        "guest",
        {
            "can_submit_tasks": True,
            "can_replay_tasks": False,
            "can_load_adapter": False,
            "can_unload_adapter": False,
            "frontier_physics": False,
            "can_override_human_review": False,
            "is_admin": False,
            "rate_limit_tier": "default",
        },
    )
    perms = backend.list_actor_permissions()
    by_name = {p["actor_name"]: p for p in perms}
    assert by_name["ash"]["is_admin"] is True
    assert by_name["ash"]["rate_limit_tier"] == "admin"
    assert by_name["guest"]["is_admin"] is False
    assert by_name["guest"]["frontier_physics"] is False


def test_actor_permissions_upsert(backend: Any) -> None:
    """Re-putting the same actor_name overwrites prior flags."""
    backend.put_actor_permission(
        "ash",
        {
            "can_submit_tasks": True,
            "can_replay_tasks": True,
            "can_load_adapter": True,
            "can_unload_adapter": True,
            "frontier_physics": True,
            "can_override_human_review": True,
            "is_admin": True,
            "rate_limit_tier": "admin",
        },
    )
    backend.put_actor_permission(
        "ash",
        {
            "can_submit_tasks": True,
            "can_replay_tasks": False,
            "can_load_adapter": False,
            "can_unload_adapter": False,
            "frontier_physics": False,
            "can_override_human_review": False,
            "is_admin": False,
            "rate_limit_tier": "default",
        },
    )
    perms = backend.list_actor_permissions()
    assert len(perms) == 1
    assert perms[0]["is_admin"] is False
    assert perms[0]["rate_limit_tier"] == "default"


# ---------------------------------------------------------------------------
# Migration idempotency


def test_migrations_idempotent_under_restart(
    backend: Any, tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """Re-initializing the same backend re-applies migrations cleanly
    (the schema_version PRIMARY KEY guard prevents double-apply)."""
    # First instantiation seeded by the fixture; mutate state.
    backend.set_kill_switch_state(
        {
            "engaged": True,
            "engaged_at_utc": "2026-05-10T14:00:00+00:00",
            "engaged_by": "test",
            "reason": "migration idempotency",
        }
    )

    kind = request.node.callspec.params["backend"]
    backend.close()

    if kind == "sqlite":
        b2 = SQLiteStateBackend(db_path=tmp_path / "state.db")
    else:
        from phoenix.state.postgres_backend import PostgresStateBackend

        dsn = os.environ["PHOENIX_POSTGRES_TEST_DSN"]
        b2 = PostgresStateBackend(dsn=dsn, min_size=1, max_size=2)
    try:
        # If migrations re-ran, schema_version would UNIQUE-violate
        # AND the kill_switch_state row would be untouched (CREATE
        # TABLE IF NOT EXISTS is a no-op, ON CONFLICT in the row
        # insert wouldn't fire because the migration only inserts
        # when starting fresh).
        state = b2.get_kill_switch_state()
        assert state["engaged"] is True
        assert state["engaged_by"] == "test"
    finally:
        if kind == "postgres":
            try:
                with b2._require_pool().connection() as conn:  # type: ignore[attr-defined]
                    phase7_ledger.revert(conn)
                    phase6b_initial.revert(conn)
            except Exception:
                pass
        b2.close()


# ---------------------------------------------------------------------------
# Phase 7 Step 5: ledger durable store parity


def _make_ledger_entry(
    *,
    entry_id: str,
    timestamp_unix: float,
    parent_hash: str,
    entry_hash: str,
    actor_id: str = "adam",
    entry_kind: str = "solve",
    payload_json: str = "{}",
) -> dict[str, Any]:
    """Build a minimal ledger entry dict matching the StateBackend shape."""
    return {
        "entry_id": entry_id,
        "entry_kind": entry_kind,
        "timestamp_unix": timestamp_unix,
        "actor_id": actor_id,
        "parent_hash": parent_hash,
        "entry_hash": entry_hash,
        "payload_json": payload_json,
    }


def test_ledger_append_then_list_roundtrip(backend: Any) -> None:
    """append_ledger_entry persists; list_ledger_entries reads back same shape."""
    entry = _make_ledger_entry(
        entry_id="e1",
        timestamp_unix=1000.0,
        parent_hash="GENESIS",
        entry_hash="a" * 64,
        actor_id="adam",
        entry_kind="solve",
        payload_json='{"task_id":"req_1"}',
    )
    backend.append_ledger_entry(entry)
    rows = backend.list_ledger_entries(since_unix=0, limit=10)
    assert len(rows) == 1
    assert rows[0] == entry


def test_ledger_list_orders_by_timestamp(backend: Any) -> None:
    """list_ledger_entries returns rows in timestamp order, oldest first."""
    backend.append_ledger_entry(
        _make_ledger_entry(
            entry_id="late",
            timestamp_unix=2000.0,
            parent_hash="b" * 64,
            entry_hash="c" * 64,
        )
    )
    backend.append_ledger_entry(
        _make_ledger_entry(
            entry_id="early",
            timestamp_unix=1000.0,
            parent_hash="GENESIS",
            entry_hash="b" * 64,
        )
    )
    rows = backend.list_ledger_entries(since_unix=0, limit=10)
    assert len(rows) == 2
    assert rows[0]["entry_id"] == "early"
    assert rows[1]["entry_id"] == "late"


def test_ledger_list_respects_since_unix(backend: Any) -> None:
    """list_ledger_entries filters rows older than since_unix."""
    backend.append_ledger_entry(
        _make_ledger_entry(
            entry_id="old",
            timestamp_unix=100.0,
            parent_hash="GENESIS",
            entry_hash="d" * 64,
        )
    )
    backend.append_ledger_entry(
        _make_ledger_entry(
            entry_id="new",
            timestamp_unix=2000.0,
            parent_hash="d" * 64,
            entry_hash="e" * 64,
        )
    )
    rows = backend.list_ledger_entries(since_unix=1000.0, limit=10)
    assert len(rows) == 1
    assert rows[0]["entry_id"] == "new"


def test_ledger_list_respects_limit(backend: Any) -> None:
    """list_ledger_entries caps at limit rows."""
    prev_hash = "GENESIS"
    for i in range(5):
        entry_hash = f"{i:064x}"
        backend.append_ledger_entry(
            _make_ledger_entry(
                entry_id=f"e_{i}",
                timestamp_unix=1000.0 + i,
                parent_hash=prev_hash,
                entry_hash=entry_hash,
            )
        )
        prev_hash = entry_hash
    rows = backend.list_ledger_entries(since_unix=0, limit=3)
    assert len(rows) == 3
    assert [r["entry_id"] for r in rows] == ["e_0", "e_1", "e_2"]


def test_ledger_verify_empty_chain_is_valid(backend: Any) -> None:
    """A ledger with no entries verifies clean."""
    report = backend.verify_ledger_integrity()
    assert report["valid"] is True
    assert report["entries_checked"] == 0
    assert report["first_broken_entry_id"] is None


def test_ledger_verify_clean_chain(backend: Any) -> None:
    """A linear chain verifies clean (parent_hash chains correctly)."""
    prev_hash = "GENESIS"
    for i in range(3):
        entry_hash = f"{i:064x}"
        backend.append_ledger_entry(
            _make_ledger_entry(
                entry_id=f"e_{i}",
                timestamp_unix=1000.0 + i,
                parent_hash=prev_hash,
                entry_hash=entry_hash,
            )
        )
        prev_hash = entry_hash
    report = backend.verify_ledger_integrity()
    assert report["valid"] is True
    assert report["entries_checked"] == 3
    assert report["first_broken_entry_id"] is None


def test_ledger_verify_catches_broken_link(backend: Any) -> None:
    """A chain with a parent_hash mismatch fails verification."""
    backend.append_ledger_entry(
        _make_ledger_entry(
            entry_id="e_0",
            timestamp_unix=1000.0,
            parent_hash="GENESIS",
            entry_hash="0" * 64,
        )
    )
    # entry_1's parent_hash should be entry_0's entry_hash ("0"*64),
    # but we set it to a forged value -- structural break the SQL
    # window function catches.
    backend.append_ledger_entry(
        _make_ledger_entry(
            entry_id="e_1",
            timestamp_unix=2000.0,
            parent_hash="F" * 64,  # WRONG -- should be e_0's entry_hash
            entry_hash="1" * 64,
        )
    )
    report = backend.verify_ledger_integrity()
    assert report["valid"] is False
    assert report["entries_checked"] == 2
    assert report["first_broken_entry_id"] == "e_1"
    assert report["reason"] is not None
    assert "parent_hash mismatch" in report["reason"]
