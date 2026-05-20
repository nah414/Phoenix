"""Admin endpoint: cognition-specific budget override (Phase 13 Step 9).

Per Phase 13 build guide §4.9: cognition cost patterns differ from
solve costs — a chat-style cognition workload can issue thousands of
small token-priced calls per hour, whereas a physics solve issues
one expensive provider call per request. The two should be
separately tracked so an admin can grant cognition headroom without
inadvertently raising the physics ceiling (and vice versa).

This endpoint mirrors :mod:`phoenix.admin.budget_override` but uses
the three cognition-specific scope tokens:

- ``per_cognition_call`` — per-request cognition cost ceiling.
- ``per_actor_24h_cognition`` — rolling 24h per-actor cognition spend.
- ``per_org_24h_cognition`` — rolling 24h per-org cognition spend.

The underlying ``budget_overrides`` table is shared (no new
migration). The Phase 10 budget-override resolver consults its own
:data:`_VALID_SCOPES`; the cognition resolver (lands at Step 10
acceptance battery) consults this module's
:data:`_VALID_COGNITION_SCOPES`. Storing both in the same table means
``GET /v1/admin/budget/list`` shows both kinds; storing distinct
scope strings means the resolvers never accidentally pick up the
wrong rows.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import Header, HTTPException, Request
from pydantic import BaseModel, Field

from phoenix.admin.audit_decorator import emit_admin_audit
from phoenix.admin.grant_prompt_verbatim import _admin_authn
from phoenix.admin.router import admin_router
from phoenix.ledger import (
    BudgetOverrideEntry,
    budget_override_to_ledger_entry,
    get_ledger,
)
from phoenix.state import get_state_backend

logger = logging.getLogger(__name__)


_VALID_COGNITION_SCOPES = frozenset(
    {
        "per_cognition_call",
        "per_actor_24h_cognition",
        "per_org_24h_cognition",
    }
)


class CognitionBudgetOverridePayload(BaseModel):
    """JSON body for POST /v1/admin/budget/cognition-override.

    Same shape as :class:`BudgetOverridePayload` but the ``scope``
    must be a cognition-specific value; non-cognition scopes are 400.
    """

    actor_name: str = Field(..., min_length=1, max_length=64)
    new_ceiling_usd: float = Field(..., gt=0)
    expires_at_unix: float = Field(..., gt=0)
    scope: str = Field(...)
    rationale: str | None = None


@admin_router.post("/budget/cognition-override")
def post_cognition_budget_override(
    payload: CognitionBudgetOverridePayload,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Grant a temporary cognition-specific cost-ceiling bump.

    Validation (in order):
      1. Standard admin auth chain.
      2. ``scope`` in :data:`_VALID_COGNITION_SCOPES`.
      3. ``expires_at_unix`` > now.

    On success:
      - Writes a row to ``budget_overrides`` via
        :meth:`StateBackend.insert_budget_override` (shared table;
        the scope string distinguishes cognition from physics).
      - Appends a :class:`BudgetOverrideEntry` to the Omega Ledger
        (same entry kind — the cognition vs physics distinction is
        carried by the scope string in the payload).
      - Emits ``admin.budget.cognition_override.success`` audit event.

    Status codes:
      - 200: override recorded.
      - 400: invalid scope OR expires_at_unix in the past.
      - 401/403/429/503: standard admin auth chain.
      - 422: payload validation (Pydantic; e.g. new_ceiling_usd <= 0).
    """
    request_id: str = request.state.request_id
    actor = _admin_authn(request, authorization, event_prefix="admin.budget.cognition_override")

    if payload.scope not in _VALID_COGNITION_SCOPES:
        emit_admin_audit(
            actor=actor,
            event_type="admin.budget.cognition_override.error.bad_scope",
            parameters={"scope": payload.scope},
            request_id=request_id,
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"invalid cognition scope {payload.scope!r}; must be one of "
                f"{sorted(_VALID_COGNITION_SCOPES)}"
            ),
        )

    now = time.time()
    if payload.expires_at_unix <= now:
        emit_admin_audit(
            actor=actor,
            event_type="admin.budget.cognition_override.error.bad_expiry",
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
        event_type="admin.budget.cognition_override.success",
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


__all__ = ["post_cognition_budget_override"]
