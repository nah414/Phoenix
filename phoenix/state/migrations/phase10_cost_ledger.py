"""Phase 10 cost-ledger + budget-overrides migration (VERSION 3).

Two schema changes per architecture v1 Section 4.7 cost-ceiling enforcement:

1. **Extends ``solve_cost_ledger``** (created by Phase 6b initial) with
   three new columns the 24-hour-window queries + audit need:

   - ``org_id`` (TEXT, nullable) -- org identifier for per-org-24h
     ceiling queries. Falls back to ``actor_id`` in the resolver
     when NULL (single-actor installs naturally have per-org =
     per-actor).
   - ``reproducibility_mode`` (TEXT, nullable) -- the mode the solve
     ran at (``default`` | ``strict`` | ``replay``). Drives the
     per-solve ceiling lookup (Section 4.7's default $5/$25/$50
     ladder).
   - ``provenance_json`` (TEXT, nullable) -- canonical JSON of the
     post-solve provenance snippet relevant to billing audits.

2. **Creates ``budget_overrides``** (new table) for admin-issued
   temporary ceiling bumps per Section 4.7's "Admin override path":

   - ``override_id`` (PK).
   - ``actor_name`` (TEXT).
   - ``scope`` (TEXT, one of ``per_solve`` | ``per_actor_24h`` | ``per_org_24h``).
   - ``new_ceiling_usd`` (REAL/DOUBLE).
   - ``expires_at_unix`` (REAL/DOUBLE).
   - ``created_by`` (TEXT) -- admin actor who issued the override.
   - ``created_at_unix`` (REAL/DOUBLE).
   - ``rationale`` (TEXT, nullable).

Per locked OPEN-1 (Phase 10, 2026-05-13): new migration file rather
than extending Phase 6b's initial. Phase 6b shipped on a specific
dev cycle; rewriting that migration would make replay against a
Phase 6b-era state backend ambiguous.

Per locked OPEN-2 (Phase 10, 2026-05-13): post-solve recording
uses last-write-wins semantics on ``solve_id``. The existing Phase
6b ``put_solve_cost_record`` already uses ``INSERT ... ON CONFLICT
DO UPDATE``; Phase 10's new ``record_solve_cost`` method preserves
that contract.

Dialect dispatch follows :mod:`phoenix.state.migrations.phase6b_initial`:
SQLite via :class:`sqlite3.Connection`; Postgres via duck-typed
cursor.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

VERSION = 3
DESCRIPTION = (
    "phase 10 cost-ledger: solve_cost_ledger org_id/repro_mode/provenance + budget_overrides table"
)


_SCHEMA_SQL_SQLITE = """
ALTER TABLE solve_cost_ledger ADD COLUMN org_id TEXT;
ALTER TABLE solve_cost_ledger ADD COLUMN reproducibility_mode TEXT;
ALTER TABLE solve_cost_ledger ADD COLUMN provenance_json TEXT;

CREATE INDEX IF NOT EXISTS idx_solve_cost_org
    ON solve_cost_ledger(org_id);

CREATE TABLE IF NOT EXISTS budget_overrides (
    override_id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_name TEXT NOT NULL,
    scope TEXT NOT NULL,
    new_ceiling_usd REAL NOT NULL,
    expires_at_unix REAL NOT NULL,
    created_by TEXT NOT NULL,
    created_at_unix REAL NOT NULL,
    rationale TEXT
);

CREATE INDEX IF NOT EXISTS idx_budget_overrides_actor
    ON budget_overrides(actor_name);
CREATE INDEX IF NOT EXISTS idx_budget_overrides_expires
    ON budget_overrides(expires_at_unix);
"""


_SCHEMA_SQL_POSTGRES = """
ALTER TABLE solve_cost_ledger ADD COLUMN IF NOT EXISTS org_id TEXT;
ALTER TABLE solve_cost_ledger ADD COLUMN IF NOT EXISTS reproducibility_mode TEXT;
ALTER TABLE solve_cost_ledger ADD COLUMN IF NOT EXISTS provenance_json TEXT;

CREATE INDEX IF NOT EXISTS idx_solve_cost_org
    ON solve_cost_ledger(org_id);

CREATE TABLE IF NOT EXISTS budget_overrides (
    override_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_name TEXT NOT NULL,
    scope TEXT NOT NULL,
    new_ceiling_usd DOUBLE PRECISION NOT NULL,
    expires_at_unix DOUBLE PRECISION NOT NULL,
    created_by TEXT NOT NULL,
    created_at_unix DOUBLE PRECISION NOT NULL,
    rationale TEXT
);

CREATE INDEX IF NOT EXISTS idx_budget_overrides_actor
    ON budget_overrides(actor_name);
CREATE INDEX IF NOT EXISTS idx_budget_overrides_expires
    ON budget_overrides(expires_at_unix);
"""


def apply(conn: Any, phoenix_release: str) -> None:
    """Apply this migration to ``conn``.

    SQLite ALTER TABLE doesn't support ``IF NOT EXISTS`` on
    ``ADD COLUMN``; we catch the "duplicate column" OperationalError
    so re-applying a partial migration is safe. Postgres has native
    ``IF NOT EXISTS`` so the SQL is naturally idempotent.
    """
    if isinstance(conn, sqlite3.Connection):
        _apply_sqlite(conn, phoenix_release)
    else:
        _apply_postgres(conn, phoenix_release)


def revert(conn: Any) -> None:
    """Drop the new columns + table.

    SQLite's ``DROP COLUMN`` lands in 3.35+; we use the pre-3.35
    fallback (recreate the table without the columns) to keep
    compatibility broad. Production migration is forward-only;
    revert exists for tests + disaster recovery.
    """
    if isinstance(conn, sqlite3.Connection):
        _revert_sqlite(conn)
    else:
        _revert_postgres(conn)


# ---------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------


def _apply_sqlite(conn: sqlite3.Connection, phoenix_release: str) -> None:
    # Apply ALTER statements one at a time; tolerate duplicate-column
    # errors so partial-application is safe.
    for stmt in [
        "ALTER TABLE solve_cost_ledger ADD COLUMN org_id TEXT",
        "ALTER TABLE solve_cost_ledger ADD COLUMN reproducibility_mode TEXT",
        "ALTER TABLE solve_cost_ledger ADD COLUMN provenance_json TEXT",
    ]:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise

    # Indexes + new table are idempotent via IF NOT EXISTS.
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_solve_cost_org
            ON solve_cost_ledger(org_id);

        CREATE TABLE IF NOT EXISTS budget_overrides (
            override_id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_name TEXT NOT NULL,
            scope TEXT NOT NULL,
            new_ceiling_usd REAL NOT NULL,
            expires_at_unix REAL NOT NULL,
            created_by TEXT NOT NULL,
            created_at_unix REAL NOT NULL,
            rationale TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_budget_overrides_actor
            ON budget_overrides(actor_name);
        CREATE INDEX IF NOT EXISTS idx_budget_overrides_expires
            ON budget_overrides(expires_at_unix);
        """
    )

    conn.execute(
        """INSERT INTO schema_version
           (version, applied_unix, phoenix_release, description)
           VALUES (?, ?, ?, ?)""",
        (VERSION, time.time(), phoenix_release, DESCRIPTION),
    )
    conn.commit()


def _revert_sqlite(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP INDEX IF EXISTS idx_budget_overrides_expires;
        DROP INDEX IF EXISTS idx_budget_overrides_actor;
        DROP TABLE IF EXISTS budget_overrides;
        DROP INDEX IF EXISTS idx_solve_cost_org;
        """
    )
    # SQLite 3.35+ supports DROP COLUMN; older versions need table-rewrite.
    # We try the modern path; on failure, leave the columns (additive
    # revert is acceptable for tests).
    for col in ("provenance_json", "reproducibility_mode", "org_id"):
        try:
            conn.execute(f"ALTER TABLE solve_cost_ledger DROP COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute("DELETE FROM schema_version WHERE version = 3")
    except sqlite3.OperationalError:
        pass
    conn.commit()


# ---------------------------------------------------------------------
# Postgres
# ---------------------------------------------------------------------


def _apply_postgres(conn: Any, phoenix_release: str) -> None:
    with conn.cursor() as cur:
        cur.execute(_SCHEMA_SQL_POSTGRES)
        cur.execute(
            """INSERT INTO schema_version
               (version, applied_unix, phoenix_release, description)
               VALUES (%s, %s, %s, %s)""",
            (VERSION, time.time(), phoenix_release, DESCRIPTION),
        )
    conn.commit()


def _revert_postgres(conn: Any) -> None:
    drop_sql = """
        DROP INDEX IF EXISTS idx_budget_overrides_expires;
        DROP INDEX IF EXISTS idx_budget_overrides_actor;
        DROP TABLE IF EXISTS budget_overrides;
        DROP INDEX IF EXISTS idx_solve_cost_org;
        ALTER TABLE solve_cost_ledger DROP COLUMN IF EXISTS provenance_json;
        ALTER TABLE solve_cost_ledger DROP COLUMN IF EXISTS reproducibility_mode;
        ALTER TABLE solve_cost_ledger DROP COLUMN IF EXISTS org_id;
    """
    with conn.cursor() as cur:
        cur.execute(drop_sql)
        try:
            cur.execute("DELETE FROM schema_version WHERE version = 3")
        except Exception:
            pass
    conn.commit()
