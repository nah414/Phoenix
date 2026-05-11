"""Admin verification + pending-review override endpoints (Phase 8 Step 5).

Per architecture v1 Section 7.7 + 8.2: three endpoints exposing
verification-gate state to ops.

- ``GET /v1/admin/tasks-pending-review`` -- list queued DEGRADED
  solves waiting on operator review. Per Phase 8 Step 5's
  OPEN-3 lock (2026-05-11), the verification gate now enqueues
  DEGRADED + DEGRADED_BUDGET_BOUND solves into this queue
  automatically.
- ``POST /v1/admin/tasks-pending-review/{task_id}/override`` --
  apply an operator disposition. Requires ``can_override_human_review``
  on top of ``is_admin``. Writes the resolution row + emits the
  audit event + appends an :class:`OverrideByOperatorEntry` to the
  Omega Ledger.
- ``GET /v1/admin/verification/rung-distribution`` -- aggregates
  ``verification.gate.started`` audit events to produce a histogram
  of rung selection (R1..R5) over the recent window.

**Override disposition values** (BUILDGUIDE §3.5):
- ``ship-as-degraded`` -- accept the DEGRADED result as-is; the
  audit + ledger record the operator's acceptance.
- ``reject`` -- mark the result as unacceptable; downstream callers
  can use this signal to discard cached values.
- ``re-run-with-tighter-bounds`` -- signal the original submitter
  to resubmit with a tighter ``max_error_bar``. Phoenix doesn't
  auto-resubmit in v1; the disposition is a recommendation
  recorded in the audit + ledger.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import Header, HTTPException, Request
from pydantic import BaseModel, Field

from phoenix.admin.audit_decorator import emit_admin_audit
from phoenix.admin.auth import require_admin
from phoenix.admin.errors import AdminPrivilegeRequired, TaskNotPendingReview
from phoenix.admin.router import admin_router
from phoenix.identity.bootstrap import IdentityError, extract_or_bootstrap
from phoenix.ledger import (
    OverrideByOperatorEntry,
    get_ledger,
    override_to_ledger_entry,
)
from phoenix.safety.errors import AuthError, PermissionDenied
from phoenix.safety.gate import verify_request
from phoenix.safety.kill_switch import KillSwitchEngaged
from phoenix.safety.rate_limiter import RateLimitExceeded

logger = logging.getLogger(__name__)


_VALID_DISPOSITIONS = {"ship-as-degraded", "reject", "re-run-with-tighter-bounds"}


def _admin_authn(
    request: Request,
    authorization: str | None,
    event_prefix: str,
    action_key: str = "admin.read",
    require_capability: str | None = None,
) -> Any:
    """Shared auth chain. The optional ``require_capability`` kwarg adds
    a Phase 6a permissions-registry capability check on top of
    ``is_admin``. The override endpoint uses ``can_override_human_review``.
    """
    request_id: str = request.state.request_id

    try:
        actor, _ = extract_or_bootstrap(authorization)
    except IdentityError as exc:
        emit_admin_audit(
            actor=None,
            event_type=f"{event_prefix}.error.identity",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(status_code=401, detail=f"identity error: {exc}") from exc

    try:
        verify_request(
            actor,
            action_key=action_key,
            requires_capability=require_capability,
            request_id=request_id,
            skip_kill_switch_check=True,
        )
    except KillSwitchEngaged as exc:
        emit_admin_audit(
            actor=actor,
            event_type=f"{event_prefix}.error.kill_switch",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(status_code=503, detail=f"kill switch engaged: {exc}") from exc
    except AuthError as exc:
        emit_admin_audit(
            actor=actor,
            event_type=f"{event_prefix}.error.auth",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(status_code=401, detail=f"auth error: {exc}") from exc
    except PermissionDenied as exc:
        emit_admin_audit(
            actor=actor,
            event_type=f"{event_prefix}.error.permission",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RateLimitExceeded as exc:
        emit_admin_audit(
            actor=actor,
            event_type=f"{event_prefix}.error.rate_limit",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(int(exc.retry_after_seconds + 1))},
        ) from exc

    try:
        require_admin(actor)
    except AdminPrivilegeRequired as exc:
        emit_admin_audit(
            actor=actor,
            event_type=f"{event_prefix}.error.privilege",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    return actor


# ---------------------------------------------------------------------------
# GET /v1/admin/tasks-pending-review


@admin_router.get("/tasks-pending-review")
def list_tasks_pending_review(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """List queued DEGRADED solves waiting on operator review.

    Each row carries: ``review_id``, ``task_id``, ``actor_id`` (the
    original submitter), ``result`` (compact snapshot with value /
    error_bar / sigma / phoenix_agreement_type /
    omega_ledger_entry_id cross-reference), ``agreement_type``,
    ``queued_at_unix``.

    Cost: ``admin.read=1``.
    """
    request_id: str = request.state.request_id
    actor = _admin_authn(request, authorization, event_prefix="admin.tasks_pending_review.list")

    try:
        from phoenix.state import get_state_backend

        rows = get_state_backend().list_pending_reviews()
    except Exception as exc:
        logger.exception("Failed to list pending reviews")
        rows = []
        emit_admin_audit(
            actor=actor,
            event_type="admin.tasks_pending_review.list.error",
            parameters={"error": str(exc)},
            request_id=request_id,
        )

    emit_admin_audit(
        actor=actor,
        event_type="admin.tasks_pending_review.list.read",
        parameters={"count": len(rows)},
        request_id=request_id,
    )
    return {"reviews": rows, "count": len(rows)}


# ---------------------------------------------------------------------------
# POST /v1/admin/tasks-pending-review/{task_id}/override


class OverrideBody(BaseModel):
    """Body for the override endpoint.

    Fields:
      - ``disposition``: one of ``ship-as-degraded`` / ``reject`` /
        ``re-run-with-tighter-bounds``. Free-form string in v1 so
        v1.x can add more dispositions without a schema break;
        validation against the canonical set happens in the handler.
      - ``reason``: free-text operator rationale, audit-only.
    """

    disposition: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="", max_length=4096)


@admin_router.post("/tasks-pending-review/{task_id}/override")
def override_pending_review(
    task_id: str,
    body: OverrideBody,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Apply an operator override to a queued DEGRADED solve.

    Requires both ``is_admin`` AND ``can_override_human_review`` on the
    actor's permissions record.

    Effects:
      1. Looks up the pending review row by ``task_id``.
      2. Validates the disposition is one of the canonical three.
      3. Writes the resolution via
         :meth:`StateBackend.resolve_pending_review`.
      4. Emits ``admin.override.applied`` audit event.
      5. Appends an :class:`OverrideByOperatorEntry` ledger entry.

    Status codes:
      - 200: override applied.
      - 400: invalid disposition string.
      - 401/403/429/503: standard admin auth chain.
      - 409 ``TaskNotPendingReview``: no queued row for this task_id
        (already resolved or never queued).
    """
    request_id: str = request.state.request_id
    actor = _admin_authn(
        request,
        authorization,
        event_prefix="admin.tasks_pending_review.override",
        action_key="admin.mutate",
        require_capability="can_override_human_review",
    )

    # Validate disposition.
    if body.disposition not in _VALID_DISPOSITIONS:
        emit_admin_audit(
            actor=actor,
            event_type="admin.tasks_pending_review.override.error.bad_disposition",
            parameters={"disposition": body.disposition},
            request_id=request_id,
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid disposition {body.disposition!r}. "
                f"Valid values: {sorted(_VALID_DISPOSITIONS)}."
            ),
        )

    # Look up the queued row.
    from phoenix.state import get_state_backend

    backend = get_state_backend()
    matching = [row for row in backend.list_pending_reviews() if row.get("task_id") == task_id]
    if not matching:
        emit_admin_audit(
            actor=actor,
            event_type="admin.tasks_pending_review.override.error.not_found",
            parameters={"task_id": task_id},
            request_id=request_id,
        )
        raise HTTPException(
            status_code=409,
            detail=str(TaskNotPendingReview(task_id)),
        )

    # Resolve the most recent queued entry (matching may have N rows
    # if a task was re-submitted; the most recent unresolved is the
    # canonical target).
    target = matching[-1]
    review_id = target["review_id"]

    backend.resolve_pending_review(
        review_id,
        {
            "resolved_by": actor.name,
            "resolved_at_unix": time.time(),
            "verdict": body.disposition,
            "rationale": body.reason,
        },
    )

    # Audit event.
    emit_admin_audit(
        actor=actor,
        event_type="admin.tasks_pending_review.override.applied",
        parameters={
            "task_id": task_id,
            "review_id": review_id,
            "disposition": body.disposition,
            "reason": body.reason,
        },
        request_id=request_id,
    )

    # Ledger entry (best-effort -- failure doesn't undo the resolution).
    try:
        ledger_entry = override_to_ledger_entry(
            OverrideByOperatorEntry(
                operator_id=actor.name,
                affected_task_id=task_id,
                override_disposition=body.disposition,
                reason=body.reason,
            )
        )
        get_ledger().append_entry(ledger_entry)
    except Exception:
        logger.exception("Failed to append override ledger entry for task_id=%s", task_id)
        emit_admin_audit(
            actor=actor,
            event_type="admin.tasks_pending_review.override.ledger_failed",
            parameters={"task_id": task_id, "review_id": review_id},
            request_id=request_id,
        )

    return {
        "task_id": task_id,
        "review_id": review_id,
        "disposition": body.disposition,
        "resolved_by": actor.name,
        "resolved_at_unix": time.time(),
    }


# ---------------------------------------------------------------------------
# GET /v1/admin/verification/rung-distribution


@admin_router.get("/verification/rung-distribution")
def rung_distribution(
    request: Request,
    since_unix: float = 0.0,
    limit: int = 5000,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Histogram of verification rungs (R1..R5) selected over a window.

    Reads ``verification.gate.started`` events from the audit log
    and tallies their ``initial_rung`` parameter. Per architecture
    §8.2: "Most tasks landing at R5 means error budgets are too
    tight or drift is suspected" -- this endpoint produces the
    histogram ops uses to spot that pattern.

    Query params:
      - ``since_unix`` (float, default 0): events at or after.
      - ``limit`` (int, default 5000, capped at 10000): how many
        audit events to scan. Larger limits = longer windows but
        higher SQL load.
    """
    request_id: str = request.state.request_id
    actor = _admin_authn(
        request, authorization, event_prefix="admin.verification.rung_distribution"
    )

    capped_limit = max(1, min(int(limit), 10_000))
    try:
        from phoenix.state import get_state_backend

        rows = get_state_backend().list_audit_events(
            since_unix=float(since_unix),
            limit=capped_limit,
        )
    except Exception:
        rows = []

    counts: dict[str, int] = {}
    for row in rows:
        if row.get("event_type") != "verification.gate.started":
            continue
        params = row.get("parameters") or {}
        # parameters comes back as a JSON string from the SQLite
        # backend; parse if needed.
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except Exception:
                params = {}
        rung = params.get("initial_rung")
        if not rung:
            continue
        counts[str(rung)] = counts.get(str(rung), 0) + 1

    total = sum(counts.values())
    payload: dict[str, Any] = {
        "since_unix": float(since_unix),
        "events_scanned": len(rows),
        "rung_counts": counts,
        "total_solves_in_window": total,
        "sampled_at_unix": time.time(),
    }
    emit_admin_audit(
        actor=actor,
        event_type="admin.verification.rung_distribution.read",
        parameters={"total_solves": total, "rung_count_keys": sorted(counts.keys())},
        request_id=request_id,
    )
    return payload


__all__ = [
    "list_tasks_pending_review",
    "override_pending_review",
    "rung_distribution",
]
