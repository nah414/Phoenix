"""Phase 7 ledger migration (VERSION 2).

Adds the ``ledger_entries`` table backing :class:`OmegaLedger`'s
durable persistence per architecture v1 Section 1 Decision 15 +
Section 6.7 (verification provenance composition).

Per locked OPEN-4 (2026-05-11): ``ledger_entries`` is a **separate**
table from ``audit_events``. The two have different audiences and
different retention contracts:

- ``audit_events`` (Phase 6b) is the structured-event firehose --
  one row per gate decision / request / WS connect / drift cycle.
  High write rate, retention measured in days. Powered by the
  audit emitter's JSONL writer.
- ``ledger_entries`` (Phase 7) is the hashchained provenance store
  -- one row per Phoenix solve plus override/kill-switch/enrollment
  state transitions. Lower write rate, retention forever (append-only,
  no truncation). Powered by :class:`phoenix.ledger.OmegaLedger`.

The schema mirrors the slim vendored
:mod:`vendor.omega.ledger`'s ``omega_entries`` table plus a few
Phoenix-side fields the typed payload dataclasses
(:mod:`phoenix.ledger.entry_types`) need:

- ``entry_id PRIMARY KEY`` -- UUID4 string the OmegaLedger mints.
- ``entry_kind`` -- ``"solve"`` | ``"override_by_operator"`` |
  ``"kill_switch"`` | ``"enrollment"`` (the typed-payload
  discriminator).
- ``timestamp_unix`` -- server-clock float.
- ``actor_id`` -- lowercase Actor name (Phoenix Section 7.3).
- ``parent_hash`` -- previous entry's ``entry_hash``, or
  ``"GENESIS"`` for the first row.
- ``entry_hash`` -- ``SHA-256(parent_hash | entry_kind | payload_json)``
  computed by the vendored module.
- ``payload_json`` -- canonical JSON of the typed-payload dataclass.

Indexes on ``timestamp_unix`` (for time-range listing) and
``entry_kind`` (for kind-filtered queries).

Dialect differences mirror :mod:`phoenix.state.migrations.phase6b_initial`:
SQLite uses ``REAL`` for floats; Postgres uses ``DOUBLE PRECISION``.
Identity columns + types are otherwise identical.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

VERSION = 2
DESCRIPTION = "phase 7 ledger: ledger_entries table for hashchained provenance"


_SCHEMA_SQL_SQLITE = """
CREATE TABLE IF NOT EXISTS ledger_entries (
    entry_id TEXT PRIMARY KEY,
    entry_kind TEXT NOT NULL,
    timestamp_unix REAL NOT NULL,
    actor_id TEXT NOT NULL,
    parent_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ledger_timestamp
    ON ledger_entries(timestamp_unix);
CREATE INDEX IF NOT EXISTS idx_ledger_kind
    ON ledger_entries(entry_kind);
"""


_SCHEMA_SQL_POSTGRES = """
CREATE TABLE IF NOT EXISTS ledger_entries (
    entry_id TEXT PRIMARY KEY,
    entry_kind TEXT NOT NULL,
    timestamp_unix DOUBLE PRECISION NOT NULL,
    actor_id TEXT NOT NULL,
    parent_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ledger_timestamp
    ON ledger_entries(timestamp_unix);
CREATE INDEX IF NOT EXISTS idx_ledger_kind
    ON ledger_entries(entry_kind);
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
    """Revert this migration. Drops the ``ledger_entries`` table.

    Production migration path is forward-only; revert exists for tests
    and disaster recovery. Note: dropping ``ledger_entries`` destroys
    the hashchain. Real ops should never call this against a
    production ledger.
    """
    drop_sql = "DROP TABLE IF EXISTS ledger_entries;"
    delete_version_sql = "DELETE FROM schema_version WHERE version = 2"

    if isinstance(conn, sqlite3.Connection):
        conn.executescript(drop_sql)
        try:
            conn.execute(delete_version_sql)
        except sqlite3.OperationalError:
            # schema_version may not exist if phase 6b was also reverted.
            pass
        conn.commit()
    else:
        with conn.cursor() as cur:
            cur.execute(drop_sql)
            try:
                cur.execute(delete_version_sql)
            except Exception:
                pass
        conn.commit()


def _apply_sqlite(conn: sqlite3.Connection, phoenix_release: str) -> None:
    conn.executescript(_SCHEMA_SQL_SQLITE)
    conn.execute(
        """INSERT INTO schema_version
           (version, applied_unix, phoenix_release, description)
           VALUES (?, ?, ?, ?)""",
        (VERSION, time.time(), phoenix_release, DESCRIPTION),
    )
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
    conn.commit()
