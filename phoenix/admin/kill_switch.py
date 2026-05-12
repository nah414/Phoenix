"""Admin kill-switch endpoints (Phase 8 Step 2).

Per architecture v1 Section 8.3: Phoenix's emergency-stop. Three
endpoints land on the admin :class:`APIRouter`:

- ``POST /v1/admin/kill-switch/engage`` -- write engaged state +
  emit audit + append :class:`KillSwitchEntry` ledger row with
  ``transition="engaged"``.
- ``POST /v1/admin/kill-switch/release`` -- mirror image with
  ``transition="released"`` + cross-reference to the original
  ``engaged_at_unix``.
- ``GET /v1/admin/kill-switch/status`` -- return persisted state.

**Stage-0 bypass:** all three endpoints invoke ``verify_request``
with ``skip_kill_switch_check=True``. The architecture rationale
(Section 8.3): "The ``POST /v1/admin/kill-switch/release`` endpoint
remains available -- only an admin can lift the switch they (or
another admin) engaged." We extend this to engage + status too:
re-engaging with a fresher rationale must work while already
engaged; status must be readable for ops triage.

**Audit + ledger emit ordering:** the audit event fires AFTER the
state-backend write but BEFORE the ledger append. Rationale: the
state-backend write is the source-of-truth event (the kill switch
IS engaged once that row commits); the audit event records that
the operator-action happened; the ledger entry is the hashchained
attestation. If the ledger append fails (StateBackend unavailable
mid-engage), the audit emit captures the fact that the engage
happened even without the ledger row — operators can reconstruct
the state from audit + the kill-switch state row.

**Idempotency:** engaging an already-engaged switch succeeds; the
new rationale overwrites the old. Releasing an already-released
switch succeeds; no-op on the state-backend row but the audit +
ledger record the operator's release action. This matches REST
idempotency expectations.
"""

from __future__ import annotations

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
    KillSwitchEntry,
    get_ledger,
    kill_switch_to_ledger_entry,
)
from phoenix.safety.errors import AuthError
from phoenix.safety.gate import verify_request
from phoenix.safety.kill_switch import get_store as get_kill_switch_store
from phoenix.safety.rate_limiter import RateLimitExceeded


class KillSwitchEngageBody(BaseModel):
    """Body for ``POST /v1/admin/kill-switch/engage``.

    ``rationale`` is optional but strongly recommended -- the audit
    trail and ledger entry both record it verbatim for post-incident
    review. Empty string is allowed; ``None`` becomes ``""``.
    """

    rationale: str = Field(default="", max_length=4096)


class KillSwitchReleaseBody(BaseModel):
    """Body for ``POST /v1/admin/kill-switch/release``.

    ``rationale`` records why ops decided to lift the switch ("issue
    resolved, hotfix deployed, false alarm"). Same shape as
    :class:`KillSwitchEngageBody`.
    """

    rationale: str = Field(default="", max_length=4096)


def _admin_authn_skipping_kill_switch(
    request: Request,
    authorization: str | None,
    action_key: str,
    event_prefix: str,
) -> Any:
    """Shared auth chain for kill-switch admin endpoints.

    Returns the verified :class:`Actor` on success. Raises the
    appropriate :class:`HTTPException` on failure (route-translated
    audit emits already recorded).

    Identical to ``/v1/admin/_ping``'s composition except for
    ``skip_kill_switch_check=True``.
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
            request_id=request_id,
            skip_kill_switch_check=True,
        )
    except AuthError as exc:
        emit_admin_audit(
            actor=actor,
            event_type=f"{event_prefix}.error.auth",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(status_code=401, detail=f"auth error: {exc}") from exc
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


def _state_to_dict(state: Any) -> dict[str, Any]:
    """Serialize :class:`KillSwitchState` for the JSON response."""
    return {
        "engaged": bool(state.engaged),
        "engaged_at_utc": state.engaged_at_utc,
        "engaged_by": state.engaged_by,
        "reason": state.reason,
    }


@admin_router.post("/kill-switch/engage")
def engage_kill_switch(
    body: KillSwitchEngageBody,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Engage the kill switch.

    Effects:

    1. Writes ``KillSwitchState(engaged=True, ...)`` to JSON + state
       backend via :meth:`KillSwitchStore.engage`.
    2. Emits a top-priority audit event ``admin.kill_switch.engaged``.
    3. Appends a :class:`KillSwitchEntry` ledger entry with
       ``transition="engaged"``.

    Status codes:
      - 200: engaged; body returns the new state.
      - 401: missing / bad actor.
      - 403: actor lacks ``is_admin``.
      - 429: rate-limited.

    Note: this endpoint can be invoked while the switch is already
    engaged (e.g. to update the rationale); the audit + ledger
    record the new engage event regardless. The Stage-0 kill-switch
    check is bypassed for this endpoint family per architecture
    §8.3 (an admin must always be able to reach the kill-switch
    surface).
    """
    request_id: str = request.state.request_id
    actor = _admin_authn_skipping_kill_switch(
        request, authorization, action_key="admin.mutate", event_prefix="admin.kill_switch.engage"
    )

    rationale = body.rationale or ""
    store = get_kill_switch_store()
    new_state = store.engage(by=actor.name, reason=rationale)

    emit_admin_audit(
        actor=actor,
        event_type="admin.kill_switch.engaged",
        parameters={
            "rationale": rationale,
            "engaged_at_utc": new_state.engaged_at_utc,
        },
        request_id=request_id,
    )

    # Append the hashchained ledger entry.
    try:
        ledger_entry = kill_switch_to_ledger_entry(
            KillSwitchEntry(
                transition="engaged",
                by=actor.name,
                reason=rationale,
                engaged_at_unix=time.time(),
            )
        )
        get_ledger().append_entry(ledger_entry)
    except Exception:
        # Ledger failures must not undo the engage -- the state-backend
        # row + audit event are already durable. Per Phase 7 Step 6
        # discipline: audit-path failures degrade observability, not
        # safety. A separate admin audit event records the ledger miss
        # so ops sees it.
        emit_admin_audit(
            actor=actor,
            event_type="admin.kill_switch.engage.ledger_failed",
            parameters={"rationale": rationale},
            request_id=request_id,
        )

    return _state_to_dict(new_state)


@admin_router.post("/kill-switch/release")
def release_kill_switch(
    body: KillSwitchReleaseBody,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Release the kill switch.

    Effects mirror :func:`engage_kill_switch`:

    1. Reads current state for the ``engaged_at_unix`` cross-reference.
    2. Writes ``KillSwitchState()`` (disengaged) via
       :meth:`KillSwitchStore.release`.
    3. Emits ``admin.kill_switch.released`` audit event.
    4. Appends a :class:`KillSwitchEntry` ledger entry with
       ``transition="released"``.

    Releasing an already-released switch succeeds; the audit + ledger
    still record the operator's release intent.
    """
    request_id: str = request.state.request_id
    actor = _admin_authn_skipping_kill_switch(
        request, authorization, action_key="admin.mutate", event_prefix="admin.kill_switch.release"
    )

    rationale = body.rationale or ""
    store = get_kill_switch_store()
    prior_state = store.read()
    # Compute engaged_at_unix from the prior state for ledger cross-ref.
    engaged_at_unix: float | None = None
    if prior_state.engaged_at_utc:
        try:
            from datetime import datetime

            # parse ISO 8601 string back to unix seconds
            engaged_at_unix = datetime.fromisoformat(
                prior_state.engaged_at_utc.replace("Z", "+00:00")
            ).timestamp()
        except (ValueError, TypeError):
            engaged_at_unix = None

    new_state = store.release()

    emit_admin_audit(
        actor=actor,
        event_type="admin.kill_switch.released",
        parameters={
            "rationale": rationale,
            "was_engaged": prior_state.engaged,
            "engaged_at_utc": prior_state.engaged_at_utc,
            "engaged_by": prior_state.engaged_by,
        },
        request_id=request_id,
    )

    try:
        ledger_entry = kill_switch_to_ledger_entry(
            KillSwitchEntry(
                transition="released",
                by=actor.name,
                reason=rationale,
                engaged_at_unix=engaged_at_unix,
            )
        )
        get_ledger().append_entry(ledger_entry)
    except Exception:
        emit_admin_audit(
            actor=actor,
            event_type="admin.kill_switch.release.ledger_failed",
            parameters={"rationale": rationale},
            request_id=request_id,
        )

    return _state_to_dict(new_state)


@admin_router.get("/kill-switch/status")
def kill_switch_status(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Return the current persisted kill-switch state.

    Useful for ops triage and post-incident review. No state
    mutation; rate-limited at ``admin.read=1`` token.
    """
    request_id: str = request.state.request_id
    actor = _admin_authn_skipping_kill_switch(
        request, authorization, action_key="admin.read", event_prefix="admin.kill_switch.status"
    )

    state = get_kill_switch_store().read()
    emit_admin_audit(
        actor=actor,
        event_type="admin.kill_switch.status.read",
        parameters={"engaged": bool(state.engaged)},
        request_id=request_id,
    )
    return _state_to_dict(state)


__all__ = [
    "engage_kill_switch",
    "kill_switch_status",
    "release_kill_switch",
]
