"""Typed errors for the state backend subsystem (Phase 11 Step 1).

Per architecture v1 Section 10.7 ("Compositional fail-closed test")
+ Section 6.8 (fail-closed on subsystem unavailability): Phoenix
v1 ships typed errors for each subsystem so the panic-mode tests
can assert deterministically on the failure mode rather than
pattern-matching against driver-level exceptions
(``sqlite3.OperationalError`` / ``psycopg.Error``) that leak
implementation details.

The state backend's ``_require_conn`` path raises
:class:`StateBackendUnavailable` when the connection is closed
or the underlying driver reports the database is unreachable.
The audit log, the kill switch, the cost ledger, the pending-
review queue, and the Omega Ledger all read from the state
backend; when it's down, Phoenix refuses operations rather than
silently degrading per the Section 10.7 fail-closed contract.
"""

from __future__ import annotations


class StateBackendUnavailable(Exception):
    """The state backend cannot be reached.

    Raised by :class:`SQLiteStateBackend` and
    :class:`PostgresStateBackend` (and any future backend impl)
    when:

    - The connection has been closed (``backend.close()``).
    - The underlying driver reports the database is unreachable
      (file missing, network partition, server down).
    - A write operation fails with a non-recoverable driver-level
      error that callers should not silently retry.

    Phoenix-level callers (admin endpoints, ledger writer, audit
    sink) catch :class:`StateBackendUnavailable` and fail-closed
    rather than fall back to in-memory state. Per Section 10.7:
    "Phoenix must fail fast and loud with typed errors -- never
    silently degrade or return a result without verification."

    The ``backend_kind`` attribute names which backend impl was
    in use (``"sqlite"`` | ``"postgres"`` | ``"unknown"``) so
    operators reading logs know which dependency to investigate.
    """

    def __init__(
        self,
        message: str,
        *,
        backend_kind: str = "unknown",
    ) -> None:
        super().__init__(message)
        self.backend_kind = backend_kind


__all__ = ["StateBackendUnavailable"]
