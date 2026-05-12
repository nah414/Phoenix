"""Integration tests for :class:`SQLiteStateBackend` (Phase 6b Step 2).

Coverage focus for Step 2 (the full parametrized SQLite-vs-Postgres
parity suite lands at Step 9):

- Migration application is idempotent (re-init on the same DB is a
  no-op).
- Every Phase 6b Protocol method round-trips its dict shape.
- Kill-switch state survives backend close + reopen (durability).
- Kill-switch JSON import on first migration picks up an existing
  Phase 6a JSON file.
- ``KillSwitchStore`` write-through dual-writes when a backend is
  injected, and reads apply the fail-closed merge rule.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from phoenix.safety.kill_switch import KillSwitchStore
from phoenix.state.migrations import runner
from phoenix.state.sqlite_backend import SQLiteStateBackend


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "state.db"


@pytest.fixture()
def backend(db_path: Path) -> SQLiteStateBackend:
    b = SQLiteStateBackend(db_path=db_path)
    yield b
    b.close()


def test_migrations_apply_idempotently(db_path: Path) -> None:
    b1 = SQLiteStateBackend(db_path=db_path)
    versions_first = runner.applied_versions(b1._conn)  # type: ignore[attr-defined,arg-type]
    b1.close()

    b2 = SQLiteStateBackend(db_path=db_path)
    versions_second = runner.applied_versions(b2._conn)  # type: ignore[attr-defined,arg-type]
    b2.close()

    # Phase 6b shipped version 1; Phase 7 Step 5 adds version 2
    # (ledger_entries table); Phase 10 Step 2 adds version 3
    # (solve_cost_ledger Phase-10 columns + budget_overrides table).
    # The runner applies all three on a fresh DB.
    assert versions_first == {1, 2, 3}
    assert versions_first == versions_second


def test_kill_switch_round_trip(backend: SQLiteStateBackend) -> None:
    # Fresh DB has no row.
    initial = backend.get_kill_switch_state()
    assert initial == {
        "engaged": False,
        "engaged_at_utc": None,
        "engaged_by": None,
        "reason": None,
    }

    backend.set_kill_switch_state(
        {
            "engaged": True,
            "engaged_at_utc": "2026-05-10T12:00:00+00:00",
            "engaged_by": "ash",
            "reason": "soak test",
        }
    )
    after = backend.get_kill_switch_state()
    assert after["engaged"] is True
    assert after["engaged_by"] == "ash"
    assert after["reason"] == "soak test"


def test_kill_switch_survives_backend_restart(db_path: Path) -> None:
    b1 = SQLiteStateBackend(db_path=db_path)
    b1.set_kill_switch_state(
        {
            "engaged": True,
            "engaged_at_utc": "2026-05-10T13:00:00+00:00",
            "engaged_by": "adam",
            "reason": "restart-durability",
        }
    )
    b1.close()

    b2 = SQLiteStateBackend(db_path=db_path)
    state = b2.get_kill_switch_state()
    b2.close()

    assert state["engaged"] is True
    assert state["engaged_by"] == "adam"
    assert state["reason"] == "restart-durability"


def test_solve_cost_round_trip(backend: SQLiteStateBackend) -> None:
    record = {
        "actor_id": "ash",
        "cost_usd_estimate": 0.42,
        "cost_usd_actual": None,
        "provider": "ibm_quantum",
        "submitted_at_unix": 1717000000.0,
        "completed_at_unix": None,
    }
    backend.put_solve_cost_record("solve_abc", record)
    fetched = backend.get_solve_cost_record("solve_abc")
    assert fetched is not None
    assert fetched["solve_id"] == "solve_abc"
    assert fetched["actor_id"] == "ash"
    assert fetched["cost_usd_estimate"] == pytest.approx(0.42)
    assert fetched["completed_at_unix"] is None

    # Update on completion.
    record_completed = {**record, "cost_usd_actual": 0.39, "completed_at_unix": 1717000123.0}
    backend.put_solve_cost_record("solve_abc", record_completed)
    fetched2 = backend.get_solve_cost_record("solve_abc")
    assert fetched2 is not None
    assert fetched2["cost_usd_actual"] == pytest.approx(0.39)
    assert fetched2["completed_at_unix"] == pytest.approx(1717000123.0)


def test_audit_events_append_and_list(backend: SQLiteStateBackend) -> None:
    base_ts = 1717100000.0
    for i in range(5):
        backend.append_audit_event(
            {
                "timestamp_unix": base_ts + i,
                "actor_id": "ash",
                "layer": "safety_gate",
                "event_type": "stage5.rate_limit_deducted",
                "parameters": {"cost": 5, "tier": "default", "i": i},
                "result_hash": f"sha256:{i:064d}",
            }
        )

    listed = backend.list_audit_events(since_unix=base_ts + 2, limit=10)
    assert len(listed) == 3
    assert listed[0]["timestamp_unix"] == pytest.approx(base_ts + 2)
    assert listed[0]["parameters"]["i"] == 2
    assert listed[-1]["parameters"]["i"] == 4


def test_pending_review_enqueue_list_resolve(backend: SQLiteStateBackend) -> None:
    backend.enqueue_pending_review(
        {
            "review_id": "rev_1",
            "task_id": "task_xyz",
            "actor_id": "ash",
            "result": {"agreement_type": "DEGRADED", "value": 4.2},
            "agreement_type": "DEGRADED",
            "queued_at_unix": 1717200000.0,
        }
    )
    backend.enqueue_pending_review(
        {
            "review_id": "rev_2",
            "task_id": "task_abc",
            "actor_id": "adam",
            "result": {"agreement_type": "WOBBLE", "value": 1.1},
            "agreement_type": "WOBBLE",
            "queued_at_unix": 1717200005.0,
        }
    )

    pending = backend.list_pending_reviews()
    assert [r["review_id"] for r in pending] == ["rev_1", "rev_2"]
    assert pending[0]["result"]["value"] == pytest.approx(4.2)

    backend.resolve_pending_review(
        "rev_1",
        {
            "resolved_by": "adam",
            "resolved_at_unix": 1717200010.0,
            "verdict": "approved",
            "rationale": "result within budget after review",
        },
    )

    pending_after = backend.list_pending_reviews()
    assert [r["review_id"] for r in pending_after] == ["rev_2"]


def test_drift_state_snapshot_round_trip(backend: SQLiteStateBackend) -> None:
    assert backend.get_drift_state_snapshot() is None
    snapshot = {
        "cycle_unix": 1717300000.0,
        "state": "warning",
        "firing_detectors": ["tier1_analytical"],
        "detector_summaries": {"tier1_analytical": "HO-1 outside tolerance"},
    }
    backend.put_drift_state_snapshot(snapshot)
    fetched = backend.get_drift_state_snapshot()
    assert fetched is not None
    assert fetched["state"] == "warning"
    assert fetched["firing_detectors"] == ["tier1_analytical"]
    assert fetched["detector_summaries"] == {"tier1_analytical": "HO-1 outside tolerance"}

    # Overwrites prior snapshot.
    snapshot2 = {
        "cycle_unix": 1717300100.0,
        "state": "healthy",
        "firing_detectors": [],
        "detector_summaries": {},
    }
    backend.put_drift_state_snapshot(snapshot2)
    fetched2 = backend.get_drift_state_snapshot()
    assert fetched2 is not None
    assert fetched2["state"] == "healthy"
    assert fetched2["firing_detectors"] == []


def test_actor_permissions_shadow_round_trip(backend: SQLiteStateBackend) -> None:
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


def test_close_then_use_raises(backend: SQLiteStateBackend) -> None:
    backend.close()
    with pytest.raises(RuntimeError, match="closed"):
        backend.get_kill_switch_state()


# ---------- Migration: Phase 6a JSON kill-switch import ----------


def test_migration_imports_phase6a_kill_switch_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    runtime_dir = fake_home / ".phoenix" / "runtime"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "kill_switch.json").write_text(
        json.dumps(
            {
                "engaged": True,
                "engaged_at_utc": "2026-05-09T20:00:00+00:00",
                "engaged_by": "phoenix-cli",
                "reason": "phase 6a -> 6b migration test",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(Path, "home", lambda: fake_home)

    db_path = tmp_path / "state.db"
    b = SQLiteStateBackend(db_path=db_path)
    try:
        imported = b.get_kill_switch_state()
    finally:
        b.close()

    assert imported["engaged"] is True
    assert imported["engaged_by"] == "phoenix-cli"
    assert imported["reason"] == "phase 6a -> 6b migration test"


# ---------- KillSwitchStore write-through ----------


def test_kill_switch_store_write_through_dual_writes(tmp_path: Path, db_path: Path) -> None:
    json_path = tmp_path / "kill_switch.json"
    b = SQLiteStateBackend(db_path=db_path)
    try:
        store = KillSwitchStore(path=json_path, backend=b)
        state = store.engage(by="ash", reason="write-through test")

        # JSON file written.
        assert json_path.exists()
        json_data = json.loads(json_path.read_text(encoding="utf-8"))
        assert json_data["engaged"] is True
        assert json_data["engaged_by"] == "ash"

        # SQLite row written.
        backend_state = b.get_kill_switch_state()
        assert backend_state["engaged"] is True
        assert backend_state["engaged_by"] == "ash"
        assert backend_state["reason"] == state.reason
    finally:
        b.close()


def test_kill_switch_store_read_prefers_backend(tmp_path: Path, db_path: Path) -> None:
    json_path = tmp_path / "kill_switch.json"
    b = SQLiteStateBackend(db_path=db_path)
    try:
        # Pre-populate JSON disengaged, SQLite engaged (simulated drift).
        json_path.write_text(
            json.dumps(
                {
                    "engaged": False,
                    "engaged_at_utc": None,
                    "engaged_by": None,
                    "reason": None,
                }
            ),
            encoding="utf-8",
        )
        b.set_kill_switch_state(
            {
                "engaged": True,
                "engaged_at_utc": "2026-05-10T14:00:00+00:00",
                "engaged_by": "ash",
                "reason": "backend says engaged",
            }
        )

        store = KillSwitchStore(path=json_path, backend=b)
        state = store.read()

        # Fail-closed: backend engaged wins over JSON disengaged.
        assert state.engaged is True
        assert state.reason == "backend says engaged"
    finally:
        b.close()


def test_kill_switch_store_fail_closed_on_json_engaged_backend_disengaged(
    tmp_path: Path, db_path: Path
) -> None:
    json_path = tmp_path / "kill_switch.json"
    b = SQLiteStateBackend(db_path=db_path)
    try:
        # Pre-populate JSON engaged, SQLite disengaged (the reverse desync).
        json_path.write_text(
            json.dumps(
                {
                    "engaged": True,
                    "engaged_at_utc": "2026-05-10T14:30:00+00:00",
                    "engaged_by": "adam",
                    "reason": "json says engaged",
                }
            ),
            encoding="utf-8",
        )
        # Backend already disengaged by default (no row).

        store = KillSwitchStore(path=json_path, backend=b)
        state = store.read()

        # Fail-closed: JSON engaged wins over backend disengaged.
        assert state.engaged is True
        assert state.reason == "json says engaged"
    finally:
        b.close()


def test_kill_switch_store_no_backend_preserves_phase6a_behavior(
    tmp_path: Path,
) -> None:
    json_path = tmp_path / "kill_switch.json"
    store = KillSwitchStore(path=json_path)  # no backend
    initial = store.read()
    assert initial.engaged is False

    store.engage(by="ash", reason="phase 6a parity")
    after = store.read()
    assert after.engaged is True
    assert after.engaged_by == "ash"

    # Only JSON is written; no backend implies no SQLite interaction.
    assert json_path.exists()
