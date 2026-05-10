"""Phase 6b initial schema migration (VERSION 1).

Creates the six tables specified by architecture v1 Section 10.3 (line
2279 enumerates five; Phase 6b adds ``drift_state_snapshot`` per Section
6.5's drift-state replay rule):

- ``kill_switch_state`` (Section 8.3 -- singleton row)
- ``solve_cost_ledger`` (Section 4.7)
- ``audit_events`` (Section 1 Decision 16 -- append-only)
- ``pending_review_queue`` (Section 7.7)
- ``actor_permissions`` (Section 7.3 -- shadow of the JSON registry)
- ``drift_state_snapshot`` (Section 6.5 -- singleton row)

Plus a ``schema_version`` metadata table to make the migration runner
idempotent.

The migration is Python-callable per the locked open-item 1 decision
(2026-05-10): bodies are typically a single ``conn.executescript`` call
on a module-level SQL string, so SQL stays in plaintext, but Python
escapes are available where needed -- this migration uses one for the
kill-switch JSON import.

Phase 6b Step 3 added Postgres dialect support. The two dialects share
table shapes but differ in types and identity-column syntax:

- ``REAL`` (SQLite 8-byte float) -> ``DOUBLE PRECISION`` (Postgres).
  SQLite tolerates both; Postgres's ``REAL`` is 4-byte and would lose
  unix-timestamp precision.
- ``INTEGER NOT NULL`` for boolean columns (SQLite stores as 0/1)
  -> ``BOOLEAN NOT NULL`` (Postgres native bool). Python ``bool``
  serializes correctly into both via the respective drivers.
- ``INTEGER PRIMARY KEY AUTOINCREMENT`` -> ``BIGINT GENERATED ALWAYS
  AS IDENTITY PRIMARY KEY`` (modern Postgres 10+ syntax).
- ``INSERT OR REPLACE INTO`` (SQLite) -> ``INSERT INTO ... ON CONFLICT
  (id) DO UPDATE SET ...`` (Postgres, also valid SQLite).
- Placeholder ``?`` (SQLite) -> ``%s`` (psycopg).

:func:`apply` dispatches on ``isinstance(conn, sqlite3.Connection)``;
Postgres is the else branch (duck-typed via ``conn.cursor()``).
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

VERSION = 1
DESCRIPTION = "phase 6b initial schema: 6 state tables + schema_version"


_SCHEMA_SQL_SQLITE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_unix REAL NOT NULL,
    phoenix_release TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kill_switch_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    engaged INTEGER NOT NULL,
    engaged_at_utc TEXT,
    engaged_by TEXT,
    reason TEXT,
    updated_unix REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS solve_cost_ledger (
    solve_id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL,
    cost_usd_estimate REAL NOT NULL,
    cost_usd_actual REAL,
    provider TEXT NOT NULL,
    submitted_at_unix REAL NOT NULL,
    completed_at_unix REAL
);

CREATE INDEX IF NOT EXISTS idx_solve_cost_actor
    ON solve_cost_ledger(actor_id);
CREATE INDEX IF NOT EXISTS idx_solve_cost_submitted
    ON solve_cost_ledger(submitted_at_unix);

CREATE TABLE IF NOT EXISTS audit_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_unix REAL NOT NULL,
    actor_id TEXT NOT NULL,
    layer TEXT NOT NULL,
    event_type TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    result_hash TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp
    ON audit_events(timestamp_unix);

CREATE TABLE IF NOT EXISTS pending_review_queue (
    review_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    result_json TEXT NOT NULL,
    agreement_type TEXT NOT NULL,
    queued_at_unix REAL NOT NULL,
    resolved_by TEXT,
    resolved_at_unix REAL,
    verdict TEXT,
    rationale TEXT
);

CREATE INDEX IF NOT EXISTS idx_pending_unresolved
    ON pending_review_queue(queued_at_unix)
    WHERE resolved_at_unix IS NULL;

CREATE TABLE IF NOT EXISTS actor_permissions (
    actor_name TEXT PRIMARY KEY,
    can_submit_tasks INTEGER NOT NULL,
    can_replay_tasks INTEGER NOT NULL,
    can_load_adapter INTEGER NOT NULL,
    can_unload_adapter INTEGER NOT NULL,
    frontier_physics INTEGER NOT NULL,
    can_override_human_review INTEGER NOT NULL,
    is_admin INTEGER NOT NULL,
    rate_limit_tier TEXT NOT NULL,
    updated_unix REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS drift_state_snapshot (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cycle_unix REAL NOT NULL,
    state TEXT NOT NULL,
    firing_detectors_json TEXT NOT NULL,
    detector_summaries_json TEXT NOT NULL,
    updated_unix REAL NOT NULL
);
"""


_SCHEMA_SQL_POSTGRES = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_unix DOUBLE PRECISION NOT NULL,
    phoenix_release TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kill_switch_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    engaged BOOLEAN NOT NULL,
    engaged_at_utc TEXT,
    engaged_by TEXT,
    reason TEXT,
    updated_unix DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS solve_cost_ledger (
    solve_id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL,
    cost_usd_estimate DOUBLE PRECISION NOT NULL,
    cost_usd_actual DOUBLE PRECISION,
    provider TEXT NOT NULL,
    submitted_at_unix DOUBLE PRECISION NOT NULL,
    completed_at_unix DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_solve_cost_actor
    ON solve_cost_ledger(actor_id);
CREATE INDEX IF NOT EXISTS idx_solve_cost_submitted
    ON solve_cost_ledger(submitted_at_unix);

CREATE TABLE IF NOT EXISTS audit_events (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    timestamp_unix DOUBLE PRECISION NOT NULL,
    actor_id TEXT NOT NULL,
    layer TEXT NOT NULL,
    event_type TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    result_hash TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp
    ON audit_events(timestamp_unix);

CREATE TABLE IF NOT EXISTS pending_review_queue (
    review_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    result_json TEXT NOT NULL,
    agreement_type TEXT NOT NULL,
    queued_at_unix DOUBLE PRECISION NOT NULL,
    resolved_by TEXT,
    resolved_at_unix DOUBLE PRECISION,
    verdict TEXT,
    rationale TEXT
);

CREATE INDEX IF NOT EXISTS idx_pending_unresolved
    ON pending_review_queue(queued_at_unix)
    WHERE resolved_at_unix IS NULL;

CREATE TABLE IF NOT EXISTS actor_permissions (
    actor_name TEXT PRIMARY KEY,
    can_submit_tasks BOOLEAN NOT NULL,
    can_replay_tasks BOOLEAN NOT NULL,
    can_load_adapter BOOLEAN NOT NULL,
    can_unload_adapter BOOLEAN NOT NULL,
    frontier_physics BOOLEAN NOT NULL,
    can_override_human_review BOOLEAN NOT NULL,
    is_admin BOOLEAN NOT NULL,
    rate_limit_tier TEXT NOT NULL,
    updated_unix DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS drift_state_snapshot (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cycle_unix DOUBLE PRECISION NOT NULL,
    state TEXT NOT NULL,
    firing_detectors_json TEXT NOT NULL,
    detector_summaries_json TEXT NOT NULL,
    updated_unix DOUBLE PRECISION NOT NULL
);
"""


def apply(conn: Any, phoenix_release: str) -> None:
    """Apply this migration to ``conn``. Called by the runner only when
    ``VERSION`` is not yet recorded in ``schema_version``.

    Dispatches on connection type: SQLite via :class:`sqlite3.Connection`,
    Postgres via duck-typed cursor (psycopg).
    """
    if isinstance(conn, sqlite3.Connection):
        _apply_sqlite(conn, phoenix_release)
    else:
        _apply_postgres(conn, phoenix_release)


def revert(conn: Any) -> None:
    """Revert this migration. Drops all Phase 6b tables.

    Provided for completeness; Phoenix's production migration path is
    forward-only. Used by tests that need to verify revert correctness
    and by ops in disaster-recovery scenarios.
    """
    drop_sql = """
        DROP TABLE IF EXISTS drift_state_snapshot;
        DROP TABLE IF EXISTS actor_permissions;
        DROP TABLE IF EXISTS pending_review_queue;
        DROP TABLE IF EXISTS audit_events;
        DROP TABLE IF EXISTS solve_cost_ledger;
        DROP TABLE IF EXISTS kill_switch_state;
        DROP TABLE IF EXISTS schema_version;
    """
    if isinstance(conn, sqlite3.Connection):
        conn.executescript(drop_sql)
        conn.commit()
    else:
        with conn.cursor() as cur:
            cur.execute(drop_sql)
        conn.commit()


def _apply_sqlite(conn: sqlite3.Connection, phoenix_release: str) -> None:
    conn.executescript(_SCHEMA_SQL_SQLITE)
    conn.execute(
        """INSERT INTO schema_version
           (version, applied_unix, phoenix_release, description)
           VALUES (?, ?, ?, ?)""",
        (VERSION, time.time(), phoenix_release, DESCRIPTION),
    )
    _import_phase6a_kill_switch_json_sqlite(conn)
    conn.commit()


def _apply_postgres(conn: Any, phoenix_release: str) -> None:
    with conn.cursor() as cur:
        cur.execute(_SCHEMA_SQL_POSTGRES)
        cur.execute(
            """INSERT INTO schema_version
               (version, applied_unix, phoenix_release, description)
               VALUES (%s, %s, %s, %s)""",
            (VERSION, time.time(), phoenix_release, DESCRIPTION),
        )
        _import_phase6a_kill_switch_json_postgres(cur)
    conn.commit()


def _import_phase6a_kill_switch_json_sqlite(conn: sqlite3.Connection) -> None:
    """Best-effort SQLite import of ``~/.phoenix/runtime/kill_switch.json``.

    Phase 6a's JSON file (if present) is the prior source of truth for
    kill-switch state. We seed the SQLite row with its content so the
    write-through ramp in :mod:`phoenix.safety.kill_switch` sees a
    consistent starting point. On any read error (missing file,
    corrupted JSON, IO failure) we leave the row absent --
    :meth:`SQLiteStateBackend.get_kill_switch_state` returns the
    disengaged default when no row exists, which matches Phase 6a's
    behavior when the JSON file is missing.
    """
    state = _read_phase6a_kill_switch_json()
    if state is None:
        return
    conn.execute(
        """INSERT OR REPLACE INTO kill_switch_state
           (id, engaged, engaged_at_utc, engaged_by, reason, updated_unix)
           VALUES (1, ?, ?, ?, ?, ?)""",
        (
            1 if state.get("engaged", False) else 0,
            state.get("engaged_at_utc"),
            state.get("engaged_by"),
            state.get("reason"),
            time.time(),
        ),
    )


def _import_phase6a_kill_switch_json_postgres(cur: Any) -> None:
    """Best-effort Postgres import of ``~/.phoenix/runtime/kill_switch.json``.

    Mirrors :func:`_import_phase6a_kill_switch_json_sqlite` -- the only
    differences are placeholder syntax (``%s`` vs ``?``) and upsert
    syntax (``ON CONFLICT DO UPDATE`` vs ``INSERT OR REPLACE``).
    """
    state = _read_phase6a_kill_switch_json()
    if state is None:
        return
    cur.execute(
        """INSERT INTO kill_switch_state
           (id, engaged, engaged_at_utc, engaged_by, reason, updated_unix)
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


def _read_phase6a_kill_switch_json() -> dict[str, Any] | None:
    """Read ``~/.phoenix/runtime/kill_switch.json`` if present and valid.

    Returns ``None`` on missing file or any read/parse error. Shared
    between the SQLite and Postgres import paths.
    """
    json_path = Path.home() / ".phoenix" / "runtime" / "kill_switch.json"
    if not json_path.exists():
        return None
    try:
        with json_path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return dict(data)
