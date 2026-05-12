"""Postgres concrete implementation of :class:`StateBackend` (Phase 6b).

Per architecture v1 Section 1 Decision 31: Postgres is opt-in for org
deployments needing concurrency, replication, or audit-grade
durability. Selected at startup via ``$PHOENIX_STATE_BACKEND=postgres``
+ ``$PHOENIX_STATE_BACKEND_DSN``.

Per the locked open-item 2 decision (2026-05-10): sync psycopg3 wrapped
in ``asyncio.to_thread`` at the call sites -- symmetric with the SQLite
path's sync stdlib ``sqlite3`` wrap. Connection pooling via psycopg's
:class:`~psycopg_pool.ConnectionPool` keeps a small pool of
connections so concurrent asyncio worker threads each get their own.

Install via ``pip install phoenix-middleware[postgres]`` to pull in
``psycopg[binary]`` + ``psycopg_pool``. Without the postgres extra,
:class:`PostgresStateBackend` raises a clear :class:`ImportError` on
construction, naming the missing extra.

Schema is identical in shape to the SQLite backend, with type and
identity-column variants per :mod:`phoenix.state.migrations.phase6b_initial`.
Bool columns map naturally to Python ``bool`` round-trip; timestamp
columns use ``DOUBLE PRECISION`` for unix-epoch float precision; the
``audit_events.event_id`` identity column uses ``BIGINT GENERATED ALWAYS
AS IDENTITY`` (Postgres 10+ syntax) in place of SQLite's
``INTEGER PRIMARY KEY AUTOINCREMENT``.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import TYPE_CHECKING, Any

from phoenix.state.migrations import runner

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool


_DEFAULT_PHOENIX_RELEASE = "1.0.0.dev9"


def _default_dsn() -> str | None:
    """Resolve the Postgres DSN from env vars.

    Prefers ``$PHOENIX_STATE_BACKEND_DSN`` (the canonical Phase 6b
    Step 4 factory variable); falls back to ``$PHOENIX_POSTGRES_DSN``
    as an alias for ops who prefer the explicit name.
    """
    return os.environ.get("PHOENIX_STATE_BACKEND_DSN") or os.environ.get("PHOENIX_POSTGRES_DSN")


class PostgresStateBackend:
    """Postgres implementation of the :class:`StateBackend` Protocol.

    Opt-in via ``$PHOENIX_STATE_BACKEND=postgres``. Requires the
    ``postgres`` optional extra; the runtime import inside ``__init__``
    raises a clear :class:`ImportError` naming the missing extra when
    psycopg / psycopg_pool are unavailable.

    Uses a small connection pool so concurrent asyncio worker threads
    each check out a connection. Pool sizes default to 1-10; tune via
    ``min_size`` / ``max_size`` constructor args.

    Each method opens a connection from the pool, runs its query inside
    a transaction (psycopg3's default), and returns the connection to
    the pool at block exit. ``commit`` happens at the ``with`` block
    boundary unless an exception aborts the transaction.
    """

    def __init__(
        self,
        dsn: str | None = None,
        *,
        min_size: int = 1,
        max_size: int = 10,
        phoenix_release: str = _DEFAULT_PHOENIX_RELEASE,
    ) -> None:
        try:
            from psycopg_pool import ConnectionPool
        except ImportError as e:
            raise ImportError(
                "PostgresStateBackend requires the `postgres` extra. "
                "Install with: pip install phoenix-middleware[postgres]"
            ) from e

        resolved_dsn = dsn if dsn is not None else _default_dsn()
        if not resolved_dsn:
            raise ValueError("Postgres DSN required: pass dsn= or set $PHOENIX_STATE_BACKEND_DSN")

        self._dsn = resolved_dsn
        self._pool: ConnectionPool | None = ConnectionPool(
            conninfo=resolved_dsn,
            min_size=min_size,
            max_size=max_size,
            open=True,
        )
        # Block until the pool has at least min_size live connections.
        self._pool.wait()
        self._lock = threading.RLock()

        # Run pending migrations on a checked-out connection.
        with self._pool.connection() as conn:
            runner.apply_pending(conn, phoenix_release=phoenix_release)

    def close(self) -> None:
        """Close the connection pool. Idempotent."""
        with self._lock:
            if self._pool is not None:
                self._pool.close()
                self._pool = None

    def _require_pool(self) -> ConnectionPool:
        """Return the active pool or raise if the backend was closed."""
        if self._pool is None:
            raise RuntimeError("PostgresStateBackend has been closed; construct a new instance")
        return self._pool

    # --- Phase 6a: kill switch ---

    def get_kill_switch_state(self) -> dict[str, Any]:
        with self._lock, self._require_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
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
        with self._lock, self._require_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO kill_switch_state
                       (id, engaged, engaged_at_utc, engaged_by,
                        reason, updated_unix)
                   VALUES (1, %s, %s, %s, %s, %s)
                   ON CONFLICT (id) DO UPDATE SET
                       engaged = EXCLUDED.engaged,
                       engaged_at_utc = EXCLUDED.engaged_at_utc,
                       engaged_by = EXCLUDED.engaged_by,
                       reason = EXCLUDED.reason,
                       updated_unix = EXCLUDED.updated_unix""",
                (
                    bool(state.get("engaged", False)),
                    state.get("engaged_at_utc"),
                    state.get("engaged_by"),
                    state.get("reason"),
                    time.time(),
                ),
            )

    # --- Phase 6b: solve cost ledger ---

    def get_solve_cost_record(self, solve_id: str) -> dict[str, Any] | None:
        with self._lock, self._require_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT solve_id, actor_id, cost_usd_estimate,
                          cost_usd_actual, provider, submitted_at_unix,
                          completed_at_unix
                   FROM solve_cost_ledger WHERE solve_id = %s""",
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
        with self._lock, self._require_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO solve_cost_ledger
                       (solve_id, actor_id, cost_usd_estimate,
                        cost_usd_actual, provider, submitted_at_unix,
                        completed_at_unix)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (solve_id) DO UPDATE SET
                       actor_id = EXCLUDED.actor_id,
                       cost_usd_estimate = EXCLUDED.cost_usd_estimate,
                       cost_usd_actual = EXCLUDED.cost_usd_actual,
                       provider = EXCLUDED.provider,
                       submitted_at_unix = EXCLUDED.submitted_at_unix,
                       completed_at_unix = EXCLUDED.completed_at_unix""",
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
        with self._lock, self._require_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO audit_events
                       (timestamp_unix, actor_id, layer, event_type,
                        parameters_json, result_hash)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
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
        with self._lock, self._require_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT timestamp_unix, actor_id, layer, event_type,
                          parameters_json, result_hash
                   FROM audit_events
                   WHERE timestamp_unix >= %s
                   ORDER BY timestamp_unix ASC
                   LIMIT %s""",
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
        with self._lock, self._require_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO pending_review_queue
                       (review_id, task_id, actor_id, result_json,
                        agreement_type, queued_at_unix)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
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
        with self._lock, self._require_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
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
        with self._lock, self._require_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE pending_review_queue
                   SET resolved_by = %s,
                       resolved_at_unix = %s,
                       verdict = %s,
                       rationale = %s
                   WHERE review_id = %s""",
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
        with self._lock, self._require_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
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
        with self._lock, self._require_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO drift_state_snapshot
                       (id, cycle_unix, state, firing_detectors_json,
                        detector_summaries_json, updated_unix)
                   VALUES (1, %s, %s, %s, %s, %s)
                   ON CONFLICT (id) DO UPDATE SET
                       cycle_unix = EXCLUDED.cycle_unix,
                       state = EXCLUDED.state,
                       firing_detectors_json = EXCLUDED.firing_detectors_json,
                       detector_summaries_json = EXCLUDED.detector_summaries_json,
                       updated_unix = EXCLUDED.updated_unix""",
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
        with self._lock, self._require_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
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
        with self._lock, self._require_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO actor_permissions
                       (actor_name, can_submit_tasks, can_replay_tasks,
                        can_load_adapter, can_unload_adapter,
                        frontier_physics, can_override_human_review,
                        is_admin, rate_limit_tier, updated_unix)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (actor_name) DO UPDATE SET
                       can_submit_tasks = EXCLUDED.can_submit_tasks,
                       can_replay_tasks = EXCLUDED.can_replay_tasks,
                       can_load_adapter = EXCLUDED.can_load_adapter,
                       can_unload_adapter = EXCLUDED.can_unload_adapter,
                       frontier_physics = EXCLUDED.frontier_physics,
                       can_override_human_review = EXCLUDED.can_override_human_review,
                       is_admin = EXCLUDED.is_admin,
                       rate_limit_tier = EXCLUDED.rate_limit_tier,
                       updated_unix = EXCLUDED.updated_unix""",
                (
                    actor_name,
                    bool(permission["can_submit_tasks"]),
                    bool(permission["can_replay_tasks"]),
                    bool(permission["can_load_adapter"]),
                    bool(permission["can_unload_adapter"]),
                    bool(permission["frontier_physics"]),
                    bool(permission["can_override_human_review"]),
                    bool(permission["is_admin"]),
                    permission["rate_limit_tier"],
                    time.time(),
                ),
            )

    # --- Phase 7: Omega Ledger durable store ---

    def append_ledger_entry(self, entry_record: dict[str, Any]) -> None:
        with self._lock, self._require_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ledger_entries
                       (entry_id, entry_kind, timestamp_unix, actor_id,
                        parent_hash, entry_hash, payload_json)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
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
        with self._lock, self._require_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT entry_id, entry_kind, timestamp_unix, actor_id,
                          parent_hash, entry_hash, payload_json
                   FROM ledger_entries
                   WHERE timestamp_unix >= %s
                   ORDER BY timestamp_unix ASC, entry_id ASC
                   LIMIT %s""",
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

        Postgres dialect mirrors the SQLite path: a CTE with
        ``LAG(entry_hash, 1, 'GENESIS')`` over timestamp order detects
        the first row whose ``parent_hash`` doesn't link to the
        previous row's ``entry_hash``. Postgres has had window
        functions since 8.4 so this works across all supported targets.
        """
        with self._lock, self._require_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
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
            cur.execute("SELECT COUNT(*) FROM ledger_entries")
            count_row = cur.fetchone()
        total = int(count_row[0]) if count_row else 0
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
