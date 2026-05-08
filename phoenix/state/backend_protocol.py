"""Abstract :class:`StateBackend` Protocol for durable persistence.

Per architecture v1 Section 1 Decision 31: Phoenix's state backend is
SQLite by default (zero-config), Postgres opt-in for org deployments.
Per the locked Phase 6a scope (2026-05-08): Phase 6a ships the
Protocol + a JSON-file impl for the few state types that need
persistence (kill switch, ActorPermissions). Phase 6b lands the SQLite
+ Postgres concrete impls.

The Protocol is intentionally narrow in Phase 6a -- just the methods
the safety gate needs. Phase 6b expands with cost ledger queries,
audit-event writes, drift-state reads, etc. New methods append; the
Phase 6a contract is stable.
"""

from __future__ import annotations

from typing import Any, Protocol


class StateBackend(Protocol):
    """The minimal persistence surface the safety gate consumes.

    Phase 6a JSON-file impl maps each method to a small JSON file
    under ``~/.phoenix/runtime/``. Phase 6b SQLite impl maps to rows
    in ``state.db``.

    All methods are synchronous in Phase 6a (the daemon is sync).
    Phase 6b's NATS + WebSocket async refactor extends the Protocol
    with async siblings (e.g. ``async_get_kill_switch_state``).
    """

    def get_kill_switch_state(self) -> dict[str, Any]:
        """Return the current kill-switch state record.

        Phase 6a shape: ``{"engaged": bool, "engaged_at_utc": str | None,
        "engaged_by": str | None, "reason": str | None}``. Section 8.3
        names additional fields the dev-ops admin endpoint uses (e.g.
        whose key triggered, why); Phase 6a ships the minimum shape.
        """
        ...

    def set_kill_switch_state(self, state: dict[str, Any]) -> None:
        """Persist the kill-switch state record.

        Phase 6a writes atomically (tmp file + rename); Phase 6b uses
        a transaction. The state survives daemon restart per the
        locked Section 11.5.1 disposition (persist + refuse-to-start
        posture).
        """
        ...
