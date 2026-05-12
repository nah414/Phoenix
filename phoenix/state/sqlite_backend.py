"""SQLite concrete implementation of :class:`StateBackend` (Phase 6b).

Per architecture v1 Section 1 Decision 31: SQLite is the default state
backend for zero-config solo installs. Postgres (Step 3) is opt-in for
org deployments via ``$PHOENIX_STATE_BACKEND=postgres``.

Connection lifecycle: one connection per process, opened at ``__init__``
(which also runs pending migrations) and closed at :meth:`close`. The
factory in Step 4 keeps a module-level singleton; tests construct their
own throwaway instances against ``:memory:`` or a tmp-path DB.

WAL journal mode is enabled at startup for read concurrency. Foreign
keys are enabled (off by default in SQLite).

All methods are synchronous. The Phoenix daemon (async FastAPI) wraps
these calls in ``asyncio.to_thread`` at the call sites per the locked
open-item 2 decision (2026-05-10): symmetry across SQLite + Postgres
backends -- both use the sync-driver-via-threadpool pattern.

The store keeps an internal ``RLock``; same-thread re-entry is safe
(matches the Phase 6a permissions registry pattern after the RLock
fix landed in Phase 6a step 9).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from phoenix.state.migrations import runner

_DEFAULT_PHOENIX_RELEASE = "1.0.0.dev10"


def _default_db_path() -> Path:
    """Resolve the SQLite database path from env var or default."""
    override = os.environ.get("PHOENIX_SQLITE_DB_PATH")
    if override:
        return Path(override)
    return Path.home() / ".phoenix" / "runtime" / "state.db"


class SQLiteStateBackend:
    """SQLite implementation of the :class:`StateBackend` Protocol.

    Phase 6b ships this as the default backend. Construct with no args
    for the standard ``~/.phoenix/runtime/state.db`` path; pass
    ``db_path=Path(":memory:")`` for tests.
    """

    def __init__(
        self,
        db_path: Path | None = None,
        *,
        phoenix_release: str = _DEFAULT_PHOENIX_RELEASE,
    ) -> None:
        self._db_path = db_path if db_path is not None else _default_db_path()
        if str(self._db_path) != ":memory:":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = sqlite3.connect(
            str(self._db_path),
            isolation_level=None,
            check_same_thread=False,
        )
        # WAL is incompatible with :memory: but harmless to attempt.
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.RLock()
        runner.apply_pending(self._conn, phoenix_release=phoenix_release)

    def close(self) -> None:
        """Close the SQLite connection. Idempotent."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _require_conn(self) -> sqlite3.Connection:
        """Return the active connection or raise if the backend was closed."""
        if self._conn is None:
            raise RuntimeError("SQLiteStateBackend has been closed; construct a new instance")
        return self._conn

    # --- Phase 6a: kill switch ---

    def get_kill_switch_state(self) -> dict[str, Any]:
        with self._lock:
            cur = self._require_conn().execute(
                """SELECT engaged, engaged_at_utc, engaged_by, reason
                   FROM kill_switch_state WHERE id = 1"""
            )
            row = cur.fetchone()
        if row is None:
            return {
                "engaged": False,
                "engaged_at_utc": None,
                "engaged_by": None,
                "reason": None,
            }
        return {
            "engaged": bool(row[0]),
            "engaged_at_utc": row[1],
            "engaged_by": row[2],
            "reason": row[3],
        }

    def set_kill_switch_state(self, state: dict[str, Any]) -> None:
        with self._lock:
            self._require_conn().execute(
                """INSERT INTO kill_switch_state
                       (id, engaged, engaged_at_utc, engaged_by,
                        reason, updated_unix)
                   VALUES (1, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       engaged = excluded.engaged,
                       engaged_at_utc = excluded.engaged_at_utc,
                       engaged_by = excluded.engaged_by,
                       reason = excluded.reason,
                       updated_unix = excluded.updated_unix""",
                (
                    1 if state.get("engaged", False) else 0,
                    state.get("engaged_at_utc"),
                    state.get("engaged_by"),
                    state.get("reason"),
                    time.time(),
                ),
            )

    # --- Phase 6b: solve cost ledger ---

    def get_solve_cost_record(self, solve_id: str) -> dict[str, Any] | None:
        with self._lock:
            cur = self._require_conn().execute(
                """SELECT solve_id, actor_id, cost_usd_estimate,
                          cost_usd_actual, provider, submitted_at_unix,
                          completed_at_unix
                   FROM solve_cost_ledger WHERE solve_id = ?""",
                (solve_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "solve_id": row[0],
            "actor_id": row[1],
            "cost_usd_estimate": row[2],
            "cost_usd_actual": row[3],
            "provider": row[4],
            "submitted_at_unix": row[5],
            "completed_at_unix": row[6],
        }

    def put_solve_cost_record(self, solve_id: str, record: dict[str, Any]) -> None:
        with self._lock:
            self._require_conn().execute(
                """INSERT INTO solve_cost_ledger
                       (solve_id, actor_id, cost_usd_estimate,
                        cost_usd_actual, provider, submitted_at_unix,
                        completed_at_unix)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(solve_id) DO UPDATE SET
                       actor_id = excluded.actor_id,
                       cost_usd_estimate = excluded.cost_usd_estimate,
                       cost_usd_actual = excluded.cost_usd_actual,
                       provider = excluded.provider,
                       submitted_at_unix = excluded.submitted_at_unix,
                       completed_at_unix = excluded.completed_at_unix""",
                (
                    solve_id,
                    record["actor_id"],
                    record["cost_usd_estimate"],
                    record.get("cost_usd_actual"),
                    record["provider"],
                    record["submitted_at_unix"],
                    record.get("completed_at_unix"),
                ),
            )

    # --- Phase 6b: audit events ---

    def append_audit_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._require_conn().execute(
                """INSERT INTO audit_events
                       (timestamp_unix, actor_id, layer, event_type,
                        parameters_json, result_hash)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    event["timestamp_unix"],
                    event["actor_id"],
                    event["layer"],
                    event["event_type"],
                    json.dumps(event["parameters"], sort_keys=True),
                    event["result_hash"],
                ),
            )

    def list_audit_events(self, since_unix: float, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._require_conn().execute(
                """SELECT timestamp_unix, actor_id, layer, event_type,
                          parameters_json, result_hash
                   FROM audit_events
                   WHERE timestamp_unix >= ?
                   ORDER BY timestamp_unix ASC
                   LIMIT ?""",
                (since_unix, limit),
            )
            rows = cur.fetchall()
        return [
            {
                "timestamp_unix": row[0],
                "actor_id": row[1],
                "layer": row[2],
                "event_type": row[3],
                "parameters": json.loads(row[4]),
                "result_hash": row[5],
            }
            for row in rows
        ]

    # --- Phase 6b: pending review queue ---

    def enqueue_pending_review(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._require_conn().execute(
                """INSERT INTO pending_review_queue
                       (review_id, task_id, actor_id, result_json,
                        agreement_type, queued_at_unix)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    record["review_id"],
                    record["task_id"],
                    record["actor_id"],
                    json.dumps(record["result"], sort_keys=True),
                    record["agreement_type"],
                    record["queued_at_unix"],
                ),
            )

    def list_pending_reviews(self) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._require_conn().execute(
                """SELECT review_id, task_id, actor_id, result_json,
                          agreement_type, queued_at_unix
                   FROM pending_review_queue
                   WHERE resolved_at_unix IS NULL
                   ORDER BY queued_at_unix ASC"""
            )
            rows = cur.fetchall()
        return [
            {
                "review_id": row[0],
                "task_id": row[1],
                "actor_id": row[2],
                "result": json.loads(row[3]),
                "agreement_type": row[4],
                "queued_at_unix": row[5],
            }
            for row in rows
        ]

    def resolve_pending_review(self, review_id: str, resolution: dict[str, Any]) -> None:
        with self._lock:
            self._require_conn().execute(
                """UPDATE pending_review_queue
                   SET resolved_by = ?,
                       resolved_at_unix = ?,
                       verdict = ?,
                       rationale = ?
                   WHERE review_id = ?""",
                (
                    resolution["resolved_by"],
                    resolution["resolved_at_unix"],
                    resolution["verdict"],
                    resolution["rationale"],
                    review_id,
                ),
            )

    # --- Phase 6b: drift state snapshot ---

    def get_drift_state_snapshot(self) -> dict[str, Any] | None:
        with self._lock:
            cur = self._require_conn().execute(
                """SELECT cycle_unix, state, firing_detectors_json,
                          detector_summaries_json
                   FROM drift_state_snapshot WHERE id = 1"""
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "cycle_unix": row[0],
            "state": row[1],
            "firing_detectors": json.loads(row[2]),
            "detector_summaries": json.loads(row[3]),
        }

    def put_drift_state_snapshot(self, snapshot: dict[str, Any]) -> None:
        with self._lock:
            self._require_conn().execute(
                """INSERT INTO drift_state_snapshot
                       (id, cycle_unix, state, firing_detectors_json,
                        detector_summaries_json, updated_unix)
                   VALUES (1, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       cycle_unix = excluded.cycle_unix,
                       state = excluded.state,
                       firing_detectors_json = excluded.firing_detectors_json,
                       detector_summaries_json = excluded.detector_summaries_json,
                       updated_unix = excluded.updated_unix""",
                (
                    snapshot["cycle_unix"],
                    snapshot["state"],
                    json.dumps(snapshot["firing_detectors"]),
                    json.dumps(snapshot["detector_summaries"]),
                    time.time(),
                ),
            )

    # --- Phase 6b: ActorPermissions shadow ---

    def list_actor_permissions(self) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._require_conn().execute(
                """SELECT actor_name, can_submit_tasks, can_replay_tasks,
                          can_load_adapter, can_unload_adapter,
                          frontier_physics, can_override_human_review,
                          is_admin, rate_limit_tier
                   FROM actor_permissions"""
            )
            rows = cur.fetchall()
        return [
            {
                "actor_name": row[0],
                "can_submit_tasks": bool(row[1]),
                "can_replay_tasks": bool(row[2]),
                "can_load_adapter": bool(row[3]),
                "can_unload_adapter": bool(row[4]),
                "frontier_physics": bool(row[5]),
                "can_override_human_review": bool(row[6]),
                "is_admin": bool(row[7]),
                "rate_limit_tier": row[8],
            }
            for row in rows
        ]

    def put_actor_permission(self, actor_name: str, permission: dict[str, Any]) -> None:
        with self._lock:
            self._require_conn().execute(
                """INSERT INTO actor_permissions
                       (actor_name, can_submit_tasks, can_replay_tasks,
                        can_load_adapter, can_unload_adapter,
                        frontier_physics, can_override_human_review,
                        is_admin, rate_limit_tier, updated_unix)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(actor_name) DO UPDATE SET
                       can_submit_tasks = excluded.can_submit_tasks,
                       can_replay_tasks = excluded.can_replay_tasks,
                       can_load_adapter = excluded.can_load_adapter,
                       can_unload_adapter = excluded.can_unload_adapter,
                       frontier_physics = excluded.frontier_physics,
                       can_override_human_review = excluded.can_override_human_review,
                       is_admin = excluded.is_admin,
                       rate_limit_tier = excluded.rate_limit_tier,
                       updated_unix = excluded.updated_unix""",
                (
                    actor_name,
                    1 if permission["can_submit_tasks"] else 0,
                    1 if permission["can_replay_tasks"] else 0,
                    1 if permission["can_load_adapter"] else 0,
                    1 if permission["can_unload_adapter"] else 0,
                    1 if permission["frontier_physics"] else 0,
                    1 if permission["can_override_human_review"] else 0,
                    1 if permission["is_admin"] else 0,
                    permission["rate_limit_tier"],
                    time.time(),
                ),
            )

    # --- Phase 7: Omega Ledger durable store ---

    def append_ledger_entry(self, entry_record: dict[str, Any]) -> None:
        with self._lock:
            self._require_conn().execute(
                """INSERT INTO ledger_entries
                       (entry_id, entry_kind, timestamp_unix, actor_id,
                        parent_hash, entry_hash, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry_record["entry_id"],
                    entry_record["entry_kind"],
                    entry_record["timestamp_unix"],
                    entry_record["actor_id"],
                    entry_record["parent_hash"],
                    entry_record["entry_hash"],
                    entry_record["payload_json"],
                ),
            )

    def list_ledger_entries(
        self,
        *,
        since_unix: float,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._require_conn().execute(
                """SELECT entry_id, entry_kind, timestamp_unix, actor_id,
                          parent_hash, entry_hash, payload_json
                   FROM ledger_entries
                   WHERE timestamp_unix >= ?
                   ORDER BY timestamp_unix ASC, entry_id ASC
                   LIMIT ?""",
                (since_unix, limit),
            )
            rows = cur.fetchall()
        return [
            {
                "entry_id": row[0],
                "entry_kind": row[1],
                "timestamp_unix": row[2],
                "actor_id": row[3],
                "parent_hash": row[4],
                "entry_hash": row[5],
                "payload_json": row[6],
            }
            for row in rows
        ]

    def verify_ledger_integrity(self) -> dict[str, Any]:
        """SQL window-function structural check over ``ledger_entries``.

        Uses :func:`LAG` (SQLite 3.25+, included in all supported
        Phoenix builds) to compare each row's ``parent_hash`` to the
        previous row's ``entry_hash`` in timestamp order. Returns the
        first row whose linkage breaks, or ``{valid: True}`` on a
        clean chain.

        Catches structural breaks (deleted middle rows, parent_hash
        rewritten) but NOT cryptographic tampering of ``payload_json``.
        Full crypto verification lives in
        :meth:`OmegaLedger.verify_chain`.
        """
        with self._lock:
            cur = self._require_conn().execute(
                """WITH ordered AS (
                       SELECT entry_id, parent_hash, entry_hash,
                              LAG(entry_hash, 1, 'GENESIS')
                                  OVER (ORDER BY timestamp_unix ASC,
                                                entry_id ASC) AS expected_prev
                       FROM ledger_entries
                   )
                   SELECT entry_id, parent_hash, expected_prev
                   FROM ordered
                   WHERE parent_hash != expected_prev
                   ORDER BY entry_id ASC
                   LIMIT 1"""
            )
            broken = cur.fetchone()
            count_cur = self._require_conn().execute("SELECT COUNT(*) FROM ledger_entries")
            total = int(count_cur.fetchone()[0])
        if broken is None:
            return {
                "valid": True,
                "entries_checked": total,
                "first_broken_entry_id": None,
                "reason": None,
            }
        return {
            "valid": False,
            "entries_checked": total,
            "first_broken_entry_id": broken[0],
            "reason": (
                f"parent_hash mismatch: expected {str(broken[2])[:16]}... "
                f"got {str(broken[1])[:16]}..."
            ),
        }
