"""Admin endpoint: grant cognition prompt-storage permissions (Phase 13 Step 9).

Per Phase 13 build guide §4.9 + 13-D2 (locked 2026-05-18): granting
the privacy-bearing flags (``can_store_prompt_verbatim``,
``can_store_prompt_encrypted``, ``can_store_raw_provider_body``) is
a high-trust admin operation. The grant lands two places:

1. The :class:`~phoenix.safety.permissions.PermissionsRegistry`
   (per-actor mutable snapshot read by the safety gate's Stage 6b).
2. The Omega Ledger as a :class:`PermissionGrantEntry` (immutable,
   hash-chained — the audit story for "who granted what to whom").

The endpoint is named ``grant-prompt-verbatim`` per the build guide
but accepts any of the three Phase 13 privacy flags via the
``capability`` body field. A single endpoint reduces admin-side
surface area; the per-capability policy is encoded in the allowed
set below.

**SAFETY:** This endpoint MUST NOT be called by non-admin actors.
The admin auth chain is enforced via :func:`_admin_authn`
(mirrors :mod:`phoenix.admin.budget_override`). Revocation flows
through the same endpoint with ``new_value=False``; explicit revoke
is the Step 10 acceptance-battery contract.
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import Any

from fastapi import Header, HTTPException, Request
from pydantic import BaseModel, Field

from phoenix.admin.audit_decorator import emit_admin_audit
from phoenix.admin.auth import require_admin
from phoenix.admin.errors import AdminPrivilegeRequired
from phoenix.identity.bootstrap import IdentityError, extract_or_bootstrap
from phoenix.ledger import (
    PermissionGrantEntry,
    get_ledger,
    permission_grant_to_ledger_entry,
)
from phoenix.safety.errors import AuthError, PermissionDenied
from phoenix.safety.gate import verify_request
from phoenix.safety.kill_switch import KillSwitchEngaged
from phoenix.safety.permissions import get_registry as get_permissions_registry
from phoenix.safety.rate_limiter import RateLimitExceeded

logger = logging.getLogger(__name__)


# Only the Phase 13 privacy-bearing flags are grantable via this endpoint.
# can_call_cognition / can_call_mcp_server / can_register_mcp_server /
# can_receive_token_stream are functional capabilities managed via the
# generic enrollment path; they don't carry the on-chain audit story
# that the privacy flags require.
_GRANTABLE_CAPABILITIES = frozenset(
    {
        "can_store_prompt_verbatim",
        "can_store_prompt_encrypted",
        "can_store_raw_provider_body",
    }
)


class GrantPromptVerbatimPayload(BaseModel):
    """JSON body for POST /v1/identity/permissions/grant-prompt-verbatim.

    Despite the endpoint name (``grant-prompt-verbatim``), the
    ``capability`` field lets an admin grant any of the three Phase 13
    privacy-bearing flags. The endpoint name reflects the most common
    case — verbatim grants — without forcing parallel endpoints for
    every privacy flag.
    """

    target_actor: str = Field(..., min_length=1, max_length=64)
    capability: str = Field(...)
    new_value: bool = Field(...)
    rationale: str = Field(..., min_length=1)
    expires_at_unix: float | None = Field(default=None, gt=0)


def _admin_authn(
    request: Request,
    authorization: str | None,
    event_prefix: str,
) -> Any:
    """Standard admin auth chain (mirrors :mod:`phoenix.admin.budget_override`)."""
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


# Mounted at /v1/identity/... (not /v1/admin/...) per the build guide's
# explicit naming. The endpoint is still admin-only — the path lives under
# /v1/identity because the operation mutates an actor's identity record.
# We register directly on the main FastAPI app (not via admin_router)
# because admin_router has prefix "/v1/admin"; matching the
# Phase 13 Step 6 MCP-admin endpoints which also register on app directly.
def post_grant_prompt_verbatim(
    payload: GrantPromptVerbatimPayload,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Grant or revoke a Phase 13 privacy-bearing capability.

    Validation (in order):
      1. Standard admin auth chain.
      2. ``capability`` in :data:`_GRANTABLE_CAPABILITIES`.
      3. ``expires_at_unix`` (if set) > now.

    On success:
      - Updates the actor's :class:`ActorPermissions` snapshot in the
        registry (via :func:`dataclasses.replace`).
      - Appends a :class:`PermissionGrantEntry` to the Omega Ledger.
      - Emits ``admin.permission.grant.success`` audit event.

    Status codes:
      - 200: grant recorded.
      - 400: invalid capability OR expires_at_unix in the past.
      - 401/403/429/503: standard admin auth chain.
      - 422: payload validation (Pydantic; missing rationale).
    """
    request_id: str = request.state.request_id
    actor = _admin_authn(request, authorization, event_prefix="admin.permission.grant")

    if payload.capability not in _GRANTABLE_CAPABILITIES:
        emit_admin_audit(
            actor=actor,
            event_type="admin.permission.grant.error.bad_capability",
            parameters={"capability": payload.capability},
            request_id=request_id,
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"capability {payload.capability!r} is not grantable via this "
                f"endpoint; allowed: {sorted(_GRANTABLE_CAPABILITIES)}"
            ),
        )

    now = time.time()
    if payload.expires_at_unix is not None and payload.expires_at_unix <= now:
        emit_admin_audit(
            actor=actor,
            event_type="admin.permission.grant.error.bad_expiry",
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

    # Update the registry snapshot (per-actor mutable state). The
    # explicit if-tree (rather than ``replace(current, **{cap: val})``)
    # keeps mypy --strict happy: ActorPermissions has mixed-type fields,
    # and ``**dict[str, bool]`` isn't typeable as kwargs for replace().
    registry = get_permissions_registry()
    current = registry.get(payload.target_actor)
    if payload.capability == "can_store_prompt_verbatim":
        updated = replace(current, can_store_prompt_verbatim=payload.new_value)
    elif payload.capability == "can_store_prompt_encrypted":
        updated = replace(current, can_store_prompt_encrypted=payload.new_value)
    elif payload.capability == "can_store_raw_provider_body":
        updated = replace(current, can_store_raw_provider_body=payload.new_value)
    else:  # pragma: no cover -- _GRANTABLE_CAPABILITIES check above guards this.
        raise HTTPException(
            status_code=500,
            detail=f"capability {payload.capability!r} passed validation but has no handler",
        )
    registry.set(payload.target_actor, updated)

    # Append the on-chain audit record.
    ledger_entry = permission_grant_to_ledger_entry(
        PermissionGrantEntry(
            granted_actor_name=payload.target_actor,
            granted_by=actor.name,
            capability=payload.capability,
            new_value=payload.new_value,
            rationale=payload.rationale,
            expires_at_unix=payload.expires_at_unix,
        )
    )
    link = get_ledger().append_entry(ledger_entry)

    emit_admin_audit(
        actor=actor,
        event_type="admin.permission.grant.success",
        parameters={
            "target_actor": payload.target_actor,
            "capability": payload.capability,
            "new_value": payload.new_value,
            "expires_at_unix": payload.expires_at_unix,
            "ledger_entry_hash": link.entry_hash,
        },
        request_id=request_id,
    )

    return {
        "target_actor": payload.target_actor,
        "capability": payload.capability,
        "new_value": payload.new_value,
        "expires_at_unix": payload.expires_at_unix,
        "ledger_entry_hash": link.entry_hash,
    }


__all__ = ["GrantPromptVerbatimPayload", "post_grant_prompt_verbatim"]
