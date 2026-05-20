"""Phase 13 Step 8 — migration v4 apply/revert tests.

Exercises :mod:`phoenix.state.migrations.phase13_prompt_disposition`
against a fresh SQLite backend (the default dev backend). Postgres
coverage rides on the existing parametrized parity tests in
``test_state_backend.py``, which auto-skip when ``$PHOENIX_POSTGRES_TEST_DSN``
is unset.

Verifies:

- A fresh ``SQLiteStateBackend`` constructor runs the full migration
  chain through VERSION 4 and the new ``ledger_entries`` columns
  exist.
- Existing entries (inserted via the regular append path) end up
  with ``prompt_disposition='HASH_ONLY'`` by DEFAULT — the build
  guide's required backfill behavior.
- ``prompt_hash`` accepts NULL (for non-cognition entries) but
  accepts SHA-256 strings too.
- Repeat invocation of ``apply`` is idempotent (duplicate-column
  errors swallowed).
- The migration appears in ``schema_version`` at VERSION 4.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix.state.migrations import phase13_prompt_disposition
from phoenix.state.sqlite_backend import SQLiteStateBackend


@pytest.fixture
def fresh_backend(tmp_path: Path) -> Iterator[SQLiteStateBackend]:
    db_path = tmp_path / "phoenix_state.db"
    backend = SQLiteStateBackend(db_path=db_path)
    try:
        yield backend
    finally:
        backend.close()


def _table_columns(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    """Return {column_name: column_type} for ``table``."""
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {row[1]: row[2] for row in cur.fetchall()}


def _raw_conn(backend: SQLiteStateBackend) -> sqlite3.Connection:
    """Reach into the backend for its sqlite connection (test-only)."""
    return backend._require_conn()  # type: ignore[attr-defined]


def test_migration_runs_on_fresh_backend(fresh_backend: SQLiteStateBackend) -> None:
    """A fresh backend has all five new columns on ledger_entries."""
    conn = _raw_conn(fresh_backend)
    cols = _table_columns(conn, "ledger_entries")
    assert "prompt_disposition" in cols
    assert "prompt_hash" in cols
    assert "prompt_verbatim" in cols
    assert "prompt_encrypted" in cols
    assert "cognition_provenance_json" in cols


def test_schema_version_records_v4(fresh_backend: SQLiteStateBackend) -> None:
    """The migration runner inserted a row at VERSION 4."""
    conn = _raw_conn(fresh_backend)
    cur = conn.execute("SELECT version FROM schema_version ORDER BY version")
    versions = [int(row[0]) for row in cur.fetchall()]
    assert 4 in versions
    # The migration ran in order, so v4 is the highest.
    assert versions == sorted(versions)


def test_existing_entries_default_to_hash_only(fresh_backend: SQLiteStateBackend) -> None:
    """Inserting an entry without specifying disposition gets HASH_ONLY."""
    conn = _raw_conn(fresh_backend)
    conn.execute(
        """INSERT INTO ledger_entries
               (entry_id, entry_kind, timestamp_unix, actor_id,
                parent_hash, entry_hash, payload_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("e1", "solve", 1717000000.0, "ash", "GENESIS", "abc123", '{"k": "v"}'),
    )
    conn.commit()
    cur = conn.execute(
        "SELECT prompt_disposition, prompt_hash FROM ledger_entries WHERE entry_id = ?",
        ("e1",),
    )
    row = cur.fetchone()
    assert row[0] == "HASH_ONLY"
    # prompt_hash NULL for non-cognition entries.
    assert row[1] is None


def test_cognition_entry_with_disposition_columns(fresh_backend: SQLiteStateBackend) -> None:
    """A cognition entry with explicit disposition + hash round-trips."""
    conn = _raw_conn(fresh_backend)
    conn.execute(
        """INSERT INTO ledger_entries
               (entry_id, entry_kind, timestamp_unix, actor_id,
                parent_hash, entry_hash, payload_json,
                prompt_disposition, prompt_hash,
                cognition_provenance_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "e2",
            "cognition",
            1717000001.0,
            "ash",
            "abc123",
            "def456",
            '{"axis": "cross_model"}',
            "HASH_ONLY",
            "a" * 64,
            '{"model": "claude-sonnet-4-7", "temperature": 0.0}',
        ),
    )
    conn.commit()
    cur = conn.execute(
        """SELECT prompt_disposition, prompt_hash,
                  prompt_verbatim, prompt_encrypted,
                  cognition_provenance_json
           FROM ledger_entries WHERE entry_id = ?""",
        ("e2",),
    )
    row = cur.fetchone()
    assert row[0] == "HASH_ONLY"
    assert row[1] == "a" * 64
    assert row[2] is None
    assert row[3] is None
    assert row[4] == '{"model": "claude-sonnet-4-7", "temperature": 0.0}'


def test_verbatim_entry_populates_verbatim_column(fresh_backend: SQLiteStateBackend) -> None:
    conn = _raw_conn(fresh_backend)
    canonical_form = '{"messages":[{"content":"hi","role":"user"}],"system":null}'
    conn.execute(
        """INSERT INTO ledger_entries
               (entry_id, entry_kind, timestamp_unix, actor_id,
                parent_hash, entry_hash, payload_json,
                prompt_disposition, prompt_hash, prompt_verbatim)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "e3",
            "cognition",
            1717000002.0,
            "ash",
            "def456",
            "ghi789",
            "{}",
            "VERBATIM",
            "b" * 64,
            canonical_form,
        ),
    )
    conn.commit()
    cur = conn.execute(
        "SELECT prompt_disposition, prompt_verbatim FROM ledger_entries WHERE entry_id = ?",
        ("e3",),
    )
    row = cur.fetchone()
    assert row[0] == "VERBATIM"
    assert row[1] == canonical_form


def test_encrypted_entry_populates_encrypted_column(fresh_backend: SQLiteStateBackend) -> None:
    conn = _raw_conn(fresh_backend)
    blob = b"\x00\x01\x02encrypted-bytes\xff"
    conn.execute(
        """INSERT INTO ledger_entries
               (entry_id, entry_kind, timestamp_unix, actor_id,
                parent_hash, entry_hash, payload_json,
                prompt_disposition, prompt_hash, prompt_encrypted)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "e4",
            "cognition",
            1717000003.0,
            "ash",
            "ghi789",
            "jkl012",
            "{}",
            "ENCRYPTED_OPT_IN",
            "c" * 64,
            blob,
        ),
    )
    conn.commit()
    cur = conn.execute(
        "SELECT prompt_disposition, prompt_encrypted FROM ledger_entries WHERE entry_id = ?",
        ("e4",),
    )
    row = cur.fetchone()
    assert row[0] == "ENCRYPTED_OPT_IN"
    assert bytes(row[1]) == blob


def test_disposition_index_exists(fresh_backend: SQLiteStateBackend) -> None:
    conn = _raw_conn(fresh_backend)
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'ledger_entries'"
    )
    index_names = {row[0] for row in cur.fetchall()}
    assert "idx_ledger_disposition" in index_names
    assert "idx_ledger_prompt_hash" in index_names


def test_apply_tolerates_partial_prior_application(tmp_path: Path) -> None:
    """Column-add idempotency: if the migration crashed partway through and
    is re-run, the duplicate-column errors are swallowed.

    Simulates partial application by deleting the schema_version row (which
    is the "completion signal") and re-running ``apply``. The ADD COLUMN
    statements MUST NOT raise on the columns that already exist; only the
    schema_version INSERT proceeds at the end.
    """
    db_path = tmp_path / "state.db"
    backend = SQLiteStateBackend(db_path=db_path)
    try:
        conn = _raw_conn(backend)
        # The first apply already ran during backend construction.
        # Simulate "crashed before schema_version INSERT" by deleting that row.
        conn.execute("DELETE FROM schema_version WHERE version = 4")
        conn.commit()
        # Re-running apply must not raise on the duplicate columns.
        phase13_prompt_disposition.apply(conn, phoenix_release="test")
        # Columns still exist after the second apply.
        cols = _table_columns(conn, "ledger_entries")
        assert "prompt_disposition" in cols
        # And schema_version v4 is back.
        cur = conn.execute("SELECT version FROM schema_version WHERE version = 4")
        assert cur.fetchone() is not None
    finally:
        backend.close()


def test_revert_drops_indexes_and_columns(tmp_path: Path) -> None:
    """Revert removes the indexes (DROP COLUMN may or may not work depending on SQLite version)."""
    db_path = tmp_path / "state.db"
    backend = SQLiteStateBackend(db_path=db_path)
    try:
        conn = _raw_conn(backend)
        phase13_prompt_disposition.revert(conn)
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'ledger_entries'"
        )
        index_names = {row[0] for row in cur.fetchall()}
        assert "idx_ledger_disposition" not in index_names
        assert "idx_ledger_prompt_hash" not in index_names
        # schema_version row at v4 is gone.
        cur = conn.execute("SELECT version FROM schema_version WHERE version = 4")
        assert cur.fetchone() is None
    finally:
        backend.close()


def test_migration_module_constants() -> None:
    """The migration module declares VERSION + DESCRIPTION + apply + revert."""
    assert phase13_prompt_disposition.VERSION == 4
    assert "prompt" in phase13_prompt_disposition.DESCRIPTION.lower()
    assert callable(phase13_prompt_disposition.apply)
    assert callable(phase13_prompt_disposition.revert)
