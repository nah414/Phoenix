"""Migration runner -- applies pending schema migrations idempotently.

Per the Phase 6b open-item 1 decision (2026-05-10): migrations are
Python-callable modules. Each migration file declares at module level:

- ``VERSION: int`` -- the integer version number, unique and monotonic.
- ``DESCRIPTION: str`` -- one-line human-readable label.
- ``apply(conn, phoenix_release) -> None`` -- forward migration body.
- ``revert(conn) -> None`` -- inverse for tests and disaster recovery.

These four attributes structurally satisfy :class:`MigrationModule`.

Discovery is explicit (no filesystem scan, no decorator magic):
:data:`ALL_MIGRATIONS` enumerates each migration module in version
order. Adding a Phase 7 migration adds one import + one list entry,
both visible to mypy and to anyone reading this file. This matches
Phoenix's audit-grade ethos -- nothing dynamic, nothing hidden.

The runner records applied versions in ``schema_version``. On a fresh
DB the table doesn't exist yet; we catch the OperationalError on the
first read and treat "table missing" as "no migrations applied" --
exactly what we want for the first call into a new DB.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Protocol, cast

from phoenix.state.migrations import phase6b_initial


class MigrationModule(Protocol):
    """Structural Protocol that a migration module satisfies.

    Each migration declares its body at module level; Python module
    top-level functions present as Callable attributes (not bound
    methods), which is why ``apply`` and ``revert`` here are typed as
    :class:`Callable`.
    """

    VERSION: int
    DESCRIPTION: str
    apply: Callable[[sqlite3.Connection, str], None]
    revert: Callable[[sqlite3.Connection], None]


# Explicit migration enumeration in apply order. mypy sees the module
# satisfies MigrationModule structurally; the cast spells that out.
ALL_MIGRATIONS: list[MigrationModule] = [cast(MigrationModule, phase6b_initial)]


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    """Return the set of migration versions already applied.

    Returns an empty set when ``schema_version`` does not yet exist
    (fresh database). Catching the specific OperationalError is the
    idiomatic SQLite check for "table missing" without an extra
    ``sqlite_master`` probe.
    """
    try:
        cur = conn.execute("SELECT version FROM schema_version")
    except sqlite3.OperationalError:
        return set()
    return {int(row[0]) for row in cur.fetchall()}


def apply_pending(conn: sqlite3.Connection, *, phoenix_release: str) -> list[int]:
    """Apply any migrations whose ``VERSION`` is not yet in
    ``schema_version``. Idempotent.

    Returns the list of versions that were applied during this call
    (empty when nothing was pending). Migrations apply in
    :data:`ALL_MIGRATIONS` order; each migration's own ``apply``
    function commits its transaction.
    """
    applied = applied_versions(conn)
    applied_now: list[int] = []
    for migration in ALL_MIGRATIONS:
        if migration.VERSION in applied:
            continue
        migration.apply(conn, phoenix_release)
        applied_now.append(migration.VERSION)
    return applied_now
