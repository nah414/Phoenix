"""Migration runner -- applies pending schema migrations idempotently.

Per the Phase 6b open-item 1 decision (2026-05-10): migrations are
Python-callable modules. Each migration file declares at module level:

- ``VERSION: int`` -- the integer version number, unique and monotonic.
- ``DESCRIPTION: str`` -- one-line human-readable label.
- ``apply(conn, phoenix_release) -> None`` -- forward migration body.
- ``revert(conn) -> None`` -- inverse for tests and disaster recovery.

The migration body inspects the connection type to dispatch SQLite
vs Postgres dialect; the runner does not need to know which dialect
is in use, only that ``conn`` is the right driver-native connection
object.

These four attributes structurally satisfy :class:`MigrationModule`.

Discovery is explicit (no filesystem scan, no decorator magic):
:data:`ALL_MIGRATIONS` enumerates each migration module in version
order. Adding a Phase 7 migration adds one import + one list entry,
both visible to mypy and to anyone reading this file. This matches
Phoenix's audit-grade ethos -- nothing dynamic, nothing hidden.

The runner records applied versions in ``schema_version``. On a fresh
DB the table doesn't exist yet; SQLite signals this via
``sqlite3.OperationalError`` and Postgres via a ``NULL`` from
``to_regclass``. Both paths normalize to "no migrations applied".
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any, Protocol, cast

from phoenix.state.migrations import phase6b_initial, phase7_ledger, phase10_cost_ledger


class MigrationModule(Protocol):
    """Structural Protocol that a migration module satisfies.

    Each migration declares its body at module level; Python module
    top-level functions present as Callable attributes (not bound
    methods), which is why ``apply`` and ``revert`` here are typed as
    :class:`Callable`. The first ``Any`` covers both
    :class:`sqlite3.Connection` and ``psycopg.Connection`` -- the
    migration body dispatches on ``isinstance(conn, sqlite3.Connection)``.
    """

    VERSION: int
    DESCRIPTION: str
    apply: Callable[[Any, str], None]
    revert: Callable[[Any], None]


# Explicit migration enumeration in apply order. mypy sees the module
# satisfies MigrationModule structurally; the cast spells that out.
ALL_MIGRATIONS: list[MigrationModule] = [
    cast(MigrationModule, phase6b_initial),
    cast(MigrationModule, phase7_ledger),
    cast(MigrationModule, phase10_cost_ledger),
]


def applied_versions(conn: Any) -> set[int]:
    """Return the set of migration versions already applied.

    Returns an empty set when ``schema_version`` does not yet exist
    (fresh database). Dialect dispatch:

    - SQLite (``isinstance(conn, sqlite3.Connection)``): catch
      :class:`sqlite3.OperationalError` raised on missing table. The
      Phoenix daemon opens SQLite at ``isolation_level=None``
      (autocommit) so a failed read does not leave the connection
      in a bad transaction state.
    - Postgres: probe with ``to_regclass('schema_version')`` which
      returns ``NULL`` when the table does not exist. This avoids
      the transaction-aborted state that an ``UndefinedTable``
      exception would leave on the psycopg connection.
    """
    if isinstance(conn, sqlite3.Connection):
        try:
            cur = conn.execute("SELECT version FROM schema_version")
        except sqlite3.OperationalError:
            return set()
        return {int(row[0]) for row in cur.fetchall()}

    # Postgres path -- duck-typed via cursor() context manager.
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('schema_version')")
        exists_row = cur.fetchone()
        if exists_row is None or exists_row[0] is None:
            return set()
        cur.execute("SELECT version FROM schema_version")
        return {int(row[0]) for row in cur.fetchall()}


def apply_pending(conn: Any, *, phoenix_release: str) -> list[int]:
    """Apply any migrations whose ``VERSION`` is not yet in
    ``schema_version``. Idempotent.

    Returns the list of versions that were applied during this call
    (empty when nothing was pending). Migrations apply in
    :data:`ALL_MIGRATIONS` order; each migration's own ``apply``
    function dispatches on connection type and commits its
    transaction.
    """
    applied = applied_versions(conn)
    applied_now: list[int] = []
    for migration in ALL_MIGRATIONS:
        if migration.VERSION in applied:
            continue
        migration.apply(conn, phoenix_release)
        applied_now.append(migration.VERSION)
    return applied_now
