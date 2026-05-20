"""Phase 13 prompt-disposition migration (VERSION 4).

Per Phase 13 build guide Step 8 + 13-D2 (locked 2026-05-18): the
Omega Ledger gains the columns needed to enforce hash-only-default
prompt storage with opt-in verbatim / encrypted dispositions.

**Naming note:** the build guide says ``solve_entries``; the
canonical table holding hashchained ledger entries in this codebase
is ``ledger_entries`` (created by :mod:`phase7_ledger`). This
migration extends ``ledger_entries``. The naming drift in the build
guide is documented here so the next reader doesn't go searching for
a non-existent table.

**Columns added to ``ledger_entries``:**

- ``prompt_disposition TEXT NOT NULL DEFAULT 'HASH_ONLY'`` — the
  13-D2 taxonomy value. Existing rows backfill to ``'HASH_ONLY'``
  via the DEFAULT. NOT NULL prevents accidental writes that skip
  the disposition.
- ``prompt_hash TEXT NULL`` — SHA-256 hex of the canonicalized
  prompt. NULL for non-cognition entries (existing solve / override
  / kill_switch / enrollment rows don't have a prompt). Populated
  by cognition wobble axes on every cognition ledger append.
- ``prompt_verbatim TEXT NULL`` — the canonical prompt JSON;
  filled only when ``prompt_disposition='VERBATIM'``. Stored in
  clear; access guarded by the actor's
  ``can_store_prompt_verbatim`` permission (Step 9).
- ``prompt_encrypted BLOB NULL`` — encrypted canonical prompt;
  filled only when ``prompt_disposition='ENCRYPTED_OPT_IN'``.
  Phase 13 ships the column; the customer-key-management ceremony
  that populates it lands later. See
  :mod:`phoenix.ledger.encryption`.
- ``cognition_provenance_json TEXT NULL`` — JSON blob with the
  cognition provider's fingerprint, sampling parameters
  (temperature, top_p, response_format), tool-list hash, and the
  classifier's output (class + confidence + classifier_version).
  Filled for cognition entries; NULL otherwise.

**Why NULL on ``prompt_hash`` (and not NOT NULL):**

The build guide draft says "prompt_hash TEXT NOT NULL". Strictly
applying that breaks the migration against any non-empty Phase 7
ledger — existing solve/override/kill_switch/enrollment rows don't
have a prompt and can't be backfilled with a meaningful hash. The
resolution: allow ``prompt_hash`` NULL at the column level; enforce
NOT NULL at the application level for cognition entries (the
cognition axis ledger-append helper checks).

This keeps the migration additive + zero-downtime, the security
contract preserved (cognition entries MUST have prompt_hash; the
axis enforces), and the existing non-cognition rows untouched.

**Indexes added:**

- ``idx_ledger_disposition`` on ``(prompt_disposition)`` — for the
  Step 8 audit query "count entries by disposition" and for
  filter-by-disposition queries in the admin audit endpoint
  (Step 9's ``GET /v1/admin/audit/cognition-spend`` filters here).
- ``idx_ledger_prompt_hash`` on ``(prompt_hash)`` — for replay's
  hash-match lookup ("does this reissued prompt match any stored
  entry?"). Sparse index; SQLite + Postgres both handle the NULL
  case correctly (NULLs not indexed).

Dialect dispatch follows :mod:`phoenix.state.migrations.phase10_cost_ledger`:
SQLite via :class:`sqlite3.Connection`, Postgres via duck-typed
cursor. SQLite ``ALTER TABLE ADD COLUMN`` lacks ``IF NOT EXISTS``;
duplicate-column-name errors are caught so partial re-application is
safe.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

VERSION = 4
DESCRIPTION = (
    "phase 13 prompt-disposition: ledger_entries gains prompt_disposition/"
    "prompt_hash/prompt_verbatim/prompt_encrypted/cognition_provenance_json"
)


# ---------------------------------------------------------------------------
# SQL


_SQLITE_ALTER_STATEMENTS: list[str] = [
    "ALTER TABLE ledger_entries ADD COLUMN prompt_disposition TEXT NOT NULL DEFAULT 'HASH_ONLY'",
    "ALTER TABLE ledger_entries ADD COLUMN prompt_hash TEXT",
    "ALTER TABLE ledger_entries ADD COLUMN prompt_verbatim TEXT",
    "ALTER TABLE ledger_entries ADD COLUMN prompt_encrypted BLOB",
    "ALTER TABLE ledger_entries ADD COLUMN cognition_provenance_json TEXT",
]


_SQLITE_INDEX_SCRIPT = """
CREATE INDEX IF NOT EXISTS idx_ledger_disposition
    ON ledger_entries(prompt_disposition);
CREATE INDEX IF NOT EXISTS idx_ledger_prompt_hash
    ON ledger_entries(prompt_hash);
"""


_POSTGRES_SCHEMA_SQL = """
ALTER TABLE ledger_entries
    ADD COLUMN IF NOT EXISTS prompt_disposition TEXT
        NOT NULL DEFAULT 'HASH_ONLY';
ALTER TABLE ledger_entries
    ADD COLUMN IF NOT EXISTS prompt_hash TEXT;
ALTER TABLE ledger_entries
    ADD COLUMN IF NOT EXISTS prompt_verbatim TEXT;
ALTER TABLE ledger_entries
    ADD COLUMN IF NOT EXISTS prompt_encrypted BYTEA;
ALTER TABLE ledger_entries
    ADD COLUMN IF NOT EXISTS cognition_provenance_json TEXT;

CREATE INDEX IF NOT EXISTS idx_ledger_disposition
    ON ledger_entries(prompt_disposition);
CREATE INDEX IF NOT EXISTS idx_ledger_prompt_hash
    ON ledger_entries(prompt_hash);
"""


# ---------------------------------------------------------------------------
# Apply / revert


def apply(conn: Any, phoenix_release: str) -> None:
    """Apply this migration to ``conn``.

    Dialect dispatch: SQLite via :class:`sqlite3.Connection`,
    Postgres via duck-typed cursor.
    """
    if isinstance(conn, sqlite3.Connection):
        _apply_sqlite(conn, phoenix_release)
    else:
        _apply_postgres(conn, phoenix_release)


def revert(conn: Any) -> None:
    """Revert this migration.

    Drops the five new columns + their indexes. Production migration
    is forward-only; revert exists for tests + disaster recovery.
    Reverting destroys any opt-in VERBATIM / ENCRYPTED_OPT_IN data
    that was written; ops should never call this against a
    production ledger that has accepted opt-in entries.
    """
    if isinstance(conn, sqlite3.Connection):
        _revert_sqlite(conn)
    else:
        _revert_postgres(conn)


# ---------------------------------------------------------------------------
# SQLite


def _apply_sqlite(conn: sqlite3.Connection, phoenix_release: str) -> None:
    # ADD COLUMN: tolerate "duplicate column name" so partial-application
    # is safe (matches phase10_cost_ledger's pattern).
    for stmt in _SQLITE_ALTER_STATEMENTS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise

    # Indexes via IF NOT EXISTS — naturally idempotent.
    conn.executescript(_SQLITE_INDEX_SCRIPT)

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
        DROP INDEX IF EXISTS idx_ledger_prompt_hash;
        DROP INDEX IF EXISTS idx_ledger_disposition;
        """
    )
    # SQLite 3.35+ supports DROP COLUMN; older versions need table-rewrite.
    # We try the modern path; on failure, leave the columns (additive
    # revert is acceptable for tests).
    for col in (
        "cognition_provenance_json",
        "prompt_encrypted",
        "prompt_verbatim",
        "prompt_hash",
        "prompt_disposition",
    ):
        try:
            conn.execute(f"ALTER TABLE ledger_entries DROP COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute("DELETE FROM schema_version WHERE version = 4")
    except sqlite3.OperationalError:
        pass
    conn.commit()


# ---------------------------------------------------------------------------
# Postgres


def _apply_postgres(conn: Any, phoenix_release: str) -> None:
    with conn.cursor() as cur:
        cur.execute(_POSTGRES_SCHEMA_SQL)
        cur.execute(
            """INSERT INTO schema_version
               (version, applied_unix, phoenix_release, description)
               VALUES (%s, %s, %s, %s)""",
            (VERSION, time.time(), phoenix_release, DESCRIPTION),
        )
    conn.commit()


def _revert_postgres(conn: Any) -> None:
    drop_sql = """
        DROP INDEX IF EXISTS idx_ledger_prompt_hash;
        DROP INDEX IF EXISTS idx_ledger_disposition;
        ALTER TABLE ledger_entries DROP COLUMN IF EXISTS cognition_provenance_json;
        ALTER TABLE ledger_entries DROP COLUMN IF EXISTS prompt_encrypted;
        ALTER TABLE ledger_entries DROP COLUMN IF EXISTS prompt_verbatim;
        ALTER TABLE ledger_entries DROP COLUMN IF EXISTS prompt_hash;
        ALTER TABLE ledger_entries DROP COLUMN IF EXISTS prompt_disposition;
    """
    with conn.cursor() as cur:
        cur.execute(drop_sql)
        try:
            cur.execute("DELETE FROM schema_version WHERE version = 4")
        except Exception:
            pass
    conn.commit()
