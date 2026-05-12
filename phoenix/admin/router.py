"""Admin :class:`APIRouter` aggregator (Phase 8 Step 1).

Per locked OPEN-1 (2026-05-11): admin endpoints live on a dedicated
:class:`fastapi.APIRouter` that the main FastAPI app includes with
the ``/v1/admin`` prefix and the ``Admin`` OpenAPI tag. Each admin
endpoint group (kill switch, health, calibration, verification,
router, audit replay, adapters) registers its own routes onto the
shared :data:`admin_router` so the wiring stays modular.

Phase 8 Step 1 ships the aggregator + one minimal sanity route
(:func:`_admin_ping`) so the auth + audit composition can be
exercised end-to-end before Steps 2-9 fill in the real handlers.
Step 10 verifies the OpenAPI schema tags every admin route correctly.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from phoenix.admin.audit_decorator import emit_admin_audit
from phoenix.admin.auth import require_admin
from phoenix.admin.errors import AdminPrivilegeRequired
from phoenix.identity.bootstrap import IdentityError, extract_or_bootstrap
from phoenix.safety.errors import AuthError, PermissionDenied
from phoenix.safety.gate import verify_request
from phoenix.safety.kill_switch import KillSwitchEngaged
from phoenix.safety.rate_limiter import RateLimitExceeded

admin_router = APIRouter(prefix="/v1/admin", tags=["Admin"])


@admin_router.get("/_ping")
def _admin_ping(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Sanity-check endpoint that exercises the full admin auth chain.

    Phase 8 Step 1 registers this so the composition of
    :func:`extract_or_bootstrap` + :func:`verify_request` +
    :func:`require_admin` + :func:`emit_admin_audit` can be tested
    end-to-end before Steps 2-9 fill in the real handlers.

    Returns ``{"status": "ok", "actor": "<name>"}`` on success.

    Status codes:
      - 200: actor is admin.
      - 401: missing / bad actor signature.
      - 403: actor lacks ``is_admin`` (or specific capability).
      - 429: rate-limited.
      - 503: kill switch engaged.
    """
    request_id: str = request.state.request_id

    try:
        actor, _ = extract_or_bootstrap(authorization)
    except IdentityError as exc:
        emit_admin_audit(
            actor=None,
            event_type="admin.ping.error.identity",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(status_code=401, detail=f"identity error: {exc}") from exc

    # Admin endpoints stay callable while the kill switch is engaged
    # so ops can diagnose + manage the system mid-incident (matches the
    # §8.4 "Phoenix is operable without paging the developer" principle).
    try:
        verify_request(
            actor,
            action_key="admin.ping",
            request_id=request_id,
            skip_kill_switch_check=True,
        )
    except KillSwitchEngaged as exc:
        emit_admin_audit(
            actor=actor,
            event_type="admin.ping.error.kill_switch",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(status_code=503, detail=f"kill switch engaged: {exc}") from exc
    except AuthError as exc:
        emit_admin_audit(
            actor=actor,
            event_type="admin.ping.error.auth",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(status_code=401, detail=f"auth error: {exc}") from exc
    except PermissionDenied as exc:
        emit_admin_audit(
            actor=actor,
            event_type="admin.ping.error.permission",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(
            status_code=403,
            detail=str(exc),
        ) from exc
    except RateLimitExceeded as exc:
        emit_admin_audit(
            actor=actor,
            event_type="admin.ping.error.rate_limit",
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
            event_type="admin.ping.error.privilege",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    emit_admin_audit(
        actor=actor,
        event_type="admin.ping.success",
        parameters={},
        request_id=request_id,
    )
    return {"status": "ok", "actor": actor.name}


__all__ = ["admin_router"]
