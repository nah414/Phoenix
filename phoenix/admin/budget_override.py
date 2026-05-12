"""Admin budget override endpoint (Phase 10 Step 8, Section 4.7).

Per architecture v1 Section 4.7 "Admin override path":

  An admin actor with ``is_admin=True`` can issue
  ``POST /v1/admin/budget/override`` with
  ``{actor: "<name>", new_ceiling_usd: <amount>, expires_at: <ts>}``
  to grant a temporary budget bump. Override events are top-priority
  audit-log entries and Omega Ledger links.

  **SAFETY:** override never *removes* a ceiling -- ``new_ceiling_usd
  = null`` is rejected; the user must specify a finite value. The
  lowest possible override is the ceiling already in effect;
  override only grants more budget, never less.

Phase 10 extends the spec body shape with an explicit ``scope`` field
(per locked OPEN-4): one of ``per_solve`` | ``per_actor_24h`` |
``per_org_24h``. The three scopes match Section 4.7's three default
ceilings; making them explicit means an admin override at the
per-org level is auditably distinct from a per-actor override (one
operator vs one team gets headroom).

The override row lands in the ``budget_overrides`` state-backend
table created at Phase 10 Step 2. The ledger entry uses the new
``BudgetOverrideEntry`` kind (locked OPEN-6: distinct from Phase 8's
``OverrideByOperatorEntry`` which is for HUMAN_REVIEW solve
overrides).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import Header, HTTPException, Request
from pydantic import BaseModel, Field

from phoenix.admin.audit_decorator import emit_admin_audit
from phoenix.admin.auth import require_admin
from phoenix.admin.errors import AdminPrivilegeRequired
from phoenix.admin.router import admin_router
from phoenix.identity.bootstrap import IdentityError, extract_or_bootstrap
from phoenix.ledger import (
    BudgetOverrideEntry,
    budget_override_to_ledger_entry,
    get_ledger,
)
from phoenix.safety.errors import AuthError, PermissionDenied
from phoenix.safety.gate import verify_request
from phoenix.safety.kill_switch import KillSwitchEngaged
from phoenix.safety.rate_limiter import RateLimitExceeded
from phoenix.state import get_state_backend

logger = logging.getLogger(__name__)


_VALID_SCOPES = frozenset({"per_solve", "per_actor_24h", "per_org_24h"})


class BudgetOverridePayload(BaseModel):
    """JSON body for POST /v1/admin/budget/override.

    Per Section 4.7: ``new_ceiling_usd`` must be > 0 (overrides only
    raise); ``expires_at_unix`` must be in the future at submit time.
    """

    actor_name: str = Field(..., min_length=1, max_length=64)
    new_ceiling_usd: float = Field(..., gt=0)
    expires_at_unix: float = Field(..., gt=0)
    scope: str = Field(...)
    rationale: str | None = None


def _admin_authn(
    request: Request,
    authorization: str | None,
    event_prefix: str,
) -> Any:
    """Standard admin auth chain (mirrors phoenix.admin.adapters_admin)."""
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
            action_key="admin.mutate",
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


@admin_router.post("/budget/override")
def post_budget_override(
    payload: BudgetOverridePayload,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Grant a temporary cost-ceiling bump.

    Validation (in order):
      1. Standard admin auth chain.
      2. ``scope`` in canonical set.
      3. ``expires_at_unix`` > now (no retro-active or already-expired
         overrides).

    On success:
      - Writes a row to ``budget_overrides`` via
        :meth:`StateBackend.insert_budget_override`.
      - Appends a ``BudgetOverrideEntry`` to the Omega Ledger.
      - Emits ``admin.budget.override.success`` audit event.

    Status codes:
      - 200: override recorded.
      - 400: invalid scope OR expires_at_unix in the past.
      - 401/403/429/503: standard admin auth chain.
      - 422: payload validation (Pydantic; e.g. new_ceiling_usd <= 0).
    """
    request_id: str = request.state.request_id
    actor = _admin_authn(request, authorization, event_prefix="admin.budget.override")

    if payload.scope not in _VALID_SCOPES:
        emit_admin_audit(
            actor=actor,
            event_type="admin.budget.override.error.bad_scope",
            parameters={"scope": payload.scope},
            request_id=request_id,
        )
        raise HTTPException(
            status_code=400,
            detail=(f"invalid scope {payload.scope!r}; must be one of {sorted(_VALID_SCOPES)}"),
        )

    now = time.time()
    if payload.expires_at_unix <= now:
        emit_admin_audit(
            actor=actor,
            event_type="admin.budget.override.error.bad_expiry",
            parameters={
                "expires_at_unix": payload.expires_at_unix,
                "now_unix": now,
            },
            request_id=request_id,
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"expires_at_unix must be in the future "
                f"(got {payload.expires_at_unix}, now {now:.0f})"
            ),
        )

    backend = get_state_backend()
    override_id = backend.insert_budget_override(
        actor_name=payload.actor_name,
        scope=payload.scope,
        new_ceiling_usd=payload.new_ceiling_usd,
        expires_at_unix=payload.expires_at_unix,
        created_by=actor.name,
        rationale=payload.rationale,
    )

    # Append a ledger entry so the audit chain is on-chain.
    ledger_entry = budget_override_to_ledger_entry(
        BudgetOverrideEntry(
            granted_actor_name=payload.actor_name,
            granted_by=actor.name,
            scope=payload.scope,
            new_ceiling_usd=payload.new_ceiling_usd,
            expires_at_unix=payload.expires_at_unix,
            rationale=payload.rationale,
        )
    )
    link = get_ledger().append_entry(ledger_entry)

    emit_admin_audit(
        actor=actor,
        event_type="admin.budget.override.success",
        parameters={
            "actor_name": payload.actor_name,
            "scope": payload.scope,
            "new_ceiling_usd": payload.new_ceiling_usd,
            "expires_at_unix": payload.expires_at_unix,
            "override_id": override_id,
            "ledger_entry_hash": link.entry_hash,
        },
        request_id=request_id,
    )

    return {
        "override_id": override_id,
        "actor_name": payload.actor_name,
        "scope": payload.scope,
        "new_ceiling_usd": payload.new_ceiling_usd,
        "expires_at_unix": payload.expires_at_unix,
        "ledger_entry_hash": link.entry_hash,
    }


__all__ = ["post_budget_override"]
