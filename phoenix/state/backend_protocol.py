"""Abstract :class:`StateBackend` Protocol for durable persistence.

Per architecture v1 Section 1 Decision 31: Phoenix's state backend is
SQLite by default (zero-config), Postgres opt-in for org deployments.

Phase 6a shipped the Protocol with the two methods the safety gate
needed (kill-switch get/set) plus a JSON-file impl for runtime state.
Phase 6b (landing now, 1.0.0.dev7) lands SQLite + Postgres concrete
impls and expands the Protocol with the surfaces those impls need:

- ``solve_cost_ledger`` (Section 4.7 cost accounting)
- ``audit_events`` (Section 1 Decision 16 structured audit log)
- ``pending_review_queue`` (Section 7.7 operator-override flow)
- ``drift_state_snapshot`` (Section 6.5 drift-state replay rule)
- ``actor_permissions`` (Section 7.3; SQLite shadows the Phase 6a
  JSON registry until Phase 7 promotes SQLite to source of truth)

The Phase 6a contract (``get_kill_switch_state`` /
``set_kill_switch_state``) is unchanged: same signatures, same shapes.
New Phase 6b methods are appended; nothing renames or removes.
"""

from __future__ import annotations

from typing import Any, Protocol


class StateBackend(Protocol):
    """The persistence surface Phoenix's safety, verification, audit, and
    drift-detection layers consume.

    Phase 6a JSON-file impl maps each Phase 6a method to a small JSON
    file under ``~/.phoenix/runtime/``. Phase 6b SQLite impl (default
    for ``1.0.0.dev7``) maps to rows in ``~/.phoenix/runtime/state.db``;
    the Postgres impl (opt-in via ``$PHOENIX_STATE_BACKEND=postgres``)
    maps to the same schema in a configurable Postgres instance.

    All methods are synchronous. The Phoenix daemon is async (FastAPI),
    but the SQLite and Postgres backends call their drivers
    (stdlib ``sqlite3`` and sync ``psycopg`` respectively) inside
    ``asyncio.to_thread`` at the call sites. This keeps one pattern
    across both backends (Phase 6b open-item 2 locked 2026-05-10).
    """

    # --- Phase 6a: kill switch (unchanged contract) ---

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

    # --- Phase 6b: solve cost ledger (Section 4.7) ---

    def get_solve_cost_record(self, solve_id: str) -> dict[str, Any] | None:
        """Return the cost-accounting record for a solve, or ``None``
        when no record exists.

        Phase 6b shape: ``{"solve_id": str, "actor_id": str,
        "cost_usd_estimate": float, "cost_usd_actual": float | None,
        "provider": str, "submitted_at_unix": float,
        "completed_at_unix": float | None}``. See Section 4.7.
        """
        ...

    def put_solve_cost_record(self, solve_id: str, record: dict[str, Any]) -> None:
        """Persist or update a solve's cost-accounting record.

        Idempotent on ``solve_id``: re-issuing overwrites the prior
        entry. The cost record evolves over a solve's lifetime --
        estimate at submission, actual at completion.
        """
        ...

    # --- Phase 6b: structured audit log (Section 1 Decision 16) ---

    def append_audit_event(self, event: dict[str, Any]) -> None:
        """Append a structured audit event to the immutable event log.

        Required fields per Decision 16: ``timestamp_unix`` (float),
        ``actor_id`` (str), ``layer`` (str), ``event_type`` (str),
        ``parameters`` (dict), ``result_hash`` (str). Phase 6b stores
        locally; Phase 7's OTel adapter (Decision 22) exports a copy.

        The audit log is append-only -- no UPDATE or DELETE method
        appears on this Protocol. Tampering produces a hashchain
        mismatch (Section 1 Decision 15).
        """
        ...

    def list_audit_events(self, since_unix: float, limit: int) -> list[dict[str, Any]]:
        """List audit events with ``timestamp_unix >= since_unix``,
        ordered ascending by timestamp, up to ``limit`` rows.

        Consumed by ``GET /v1/audit/events`` (Section 5.2) and by the
        audit replay path at Phase 7 (Section 1 Decision 19).
        """
        ...

    # --- Phase 6b: pending-review queue (Section 7.7) ---

    def enqueue_pending_review(self, record: dict[str, Any]) -> None:
        """Queue a verification result that needs operator review.

        Phase 6b shape: ``{"review_id": str, "task_id": str,
        "actor_id": str, "result": dict, "agreement_type": str,
        "queued_at_unix": float}``. See Section 7.7.
        """
        ...

    def list_pending_reviews(self) -> list[dict[str, Any]]:
        """Return all unresolved pending-review records, oldest first.

        Empty list when the queue is empty. Phase 8's admin endpoint
        ``GET /v1/admin/tasks-pending-review`` consumes this directly.
        """
        ...

    def resolve_pending_review(self, review_id: str, resolution: dict[str, Any]) -> None:
        """Mark a pending-review record as resolved.

        Phase 6b resolution shape: ``{"resolved_by": str (actor_id),
        "resolved_at_unix": float, "verdict": "approved" | "rejected",
        "rationale": str}``. The record is preserved (no DELETE); the
        resolution links to it.
        """
        ...

    # --- Phase 6b: drift state snapshot (Section 6.5 + Decision 17) ---

    def get_drift_state_snapshot(self) -> dict[str, Any] | None:
        """Return the most recent drift-cycle snapshot, or ``None`` if
        no cycle has run yet.

        Phase 6b shape: ``{"cycle_unix": float, "state": "healthy" |
        "warning" | "high_confidence_warning", "firing_detectors":
        list[str], "detector_summaries": dict[str, str]}``. Persisted
        so detectors survive daemon restart and so replay (Section 6.5
        replay rule) sees the same drift state as the original run.
        """
        ...

    def put_drift_state_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Persist the most recent drift-cycle snapshot.

        Called by the ``DriftDetector`` orchestrator at the end of every
        cycle (Step 7). Overwrites the prior snapshot; full per-cycle
        history lives in ``audit_events`` (one event per cycle).
        """
        ...

    # --- Phase 6b: ActorPermissions shadow (Section 7.3) ---
    # Phase 6a's JSON-file registry in phoenix/safety/permissions.py
    # remains authoritative through Phase 6b. The SQLite/Postgres impls
    # shadow-write so the migration ramp at Phase 7 has a populated
    # table to promote.

    def list_actor_permissions(self) -> list[dict[str, Any]]:
        """Return all ActorPermissions records.

        Phase 6b shape per entry: the same fields as Phase 6a's JSON
        registry (see
        ``phoenix/safety/permissions.py::ActorPermissions``). Phase 7
        promotes the backend to source of truth.
        """
        ...

    def put_actor_permission(self, actor_name: str, permission: dict[str, Any]) -> None:
        """Persist or update one ActorPermissions record.

        Idempotent on ``actor_name``: re-issuing overwrites the prior
        entry.
        """
        ...

    # --- Phase 7: Omega Ledger durable store (Section 1 Decision 15) ---
    # The hashchain primitives live in vendor/omega/ledger.py + the
    # adapter at phoenix/ledger/omega_ledger.py. Phase 7 Step 5 adds
    # the state backend as the durable home for the chain so installs
    # using Postgres get replication/concurrency for free. Per locked
    # OPEN-4 (2026-05-11), ``ledger_entries`` is a separate table from
    # ``audit_events`` -- audit_events is the structured-event firehose;
    # ledger_entries is the long-lived provenance store.

    def append_ledger_entry(self, entry_record: dict[str, Any]) -> None:
        """Append a ledger entry to the durable store.

        Required fields per :class:`phoenix.ledger.LedgerEntry`:
        ``entry_id`` (str, primary key), ``entry_kind`` (str
        discriminator), ``timestamp_unix`` (float), ``actor_id`` (str),
        ``parent_hash`` (str; ``"GENESIS"`` for the first entry),
        ``entry_hash`` (str; SHA-256 hex), ``payload_json`` (str;
        canonical JSON of the typed payload).

        The store is append-only: there is no UPDATE or DELETE method.
        Tampering breaks the hashchain (Section 1 Decision 15).

        Raises if an entry with the same ``entry_id`` already exists --
        the OmegaLedger mints UUID4 IDs so collisions are
        cryptographically impossible; an error here would mean a
        client-side replay attack or a corrupted UUID generator.
        """
        ...

    def list_ledger_entries(
        self,
        *,
        since_unix: float,
        limit: int,
    ) -> list[dict[str, Any]]:
        """List ledger entries with ``timestamp_unix >= since_unix``,
        ordered ascending by timestamp, up to ``limit`` rows.

        Returned rows have the same shape as
        :meth:`append_ledger_entry` accepts. Consumed by the
        ``GET /v1/audit/ledger`` admin endpoint (architecture §5.2)
        and by the replay engine (Phase 7 Step 8) to walk the chain.
        """
        ...

    def verify_ledger_integrity(self) -> dict[str, Any]:
        """Walk the chain in SQL and return an integrity summary.

        Uses a window function (``LAG``) to verify each row's
        ``parent_hash`` matches the previous row's ``entry_hash`` in
        timestamp order. Catches structural breaks (a row whose
        parent_hash doesn't link to the prior row's entry_hash) but
        not cryptographic tampering of the payload -- the latter
        requires recomputing SHA-256 per row, which lives in the
        in-process :meth:`OmegaLedger.verify_chain` walk.

        Returns: ``{valid: bool, entries_checked: int,
        first_broken_entry_id: str | None, reason: str | None}``.

        Phase 8's ``GET /v1/admin/ledger/integrity-report`` invokes
        this method for the SQL-level structural check and the
        in-process :meth:`OmegaLedger.verify_chain` for the full
        cryptographic check; the report reconciles both.
        """
        ...
