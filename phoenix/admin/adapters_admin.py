"""Admin adapter management endpoints (Phase 8 Step 9).

Per locked OPEN-6 (2026-05-11): Phase 8 registers
``POST /v1/admin/adapters/{adapter_id}/force-revalidate`` as a 501
stub so v1 client integrators can see the full admin surface and
handle the not-implemented response gracefully. The real handler
lands in Phase 9 alongside the LoRA adapter sandbox.

Advertising the endpoint surface early matches the architecture
spec's Section 8.2 framing ("Section 8.2 names the endpoint"). The
stub:

- Goes through the full standard admin auth chain so an integrator's
  401/403/429/503 paths are exercised today.
- Returns HTTP 501 with a clear "lands in Phase 9" message body
  on the success path.
- Emits an ``admin.adapters.force_revalidate.not_implemented`` audit
  event so the audit log records the call attempt.

``GET /v1/admin/adapters/{id}/round-trip-history`` is deferred
entirely to Phase 9 per OPEN-6 -- not registered here.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Header, HTTPException, Request

from phoenix.admin.audit_decorator import emit_admin_audit
from phoenix.admin.auth import require_admin
from phoenix.admin.errors import AdminPrivilegeRequired
from phoenix.admin.router import admin_router
from phoenix.identity.bootstrap import IdentityError, extract_or_bootstrap
from phoenix.safety.errors import AuthError, PermissionDenied
from phoenix.safety.gate import verify_request
from phoenix.safety.kill_switch import KillSwitchEngaged
from phoenix.safety.rate_limiter import RateLimitExceeded

logger = logging.getLogger(__name__)


def _admin_authn(
    request: Request,
    authorization: str | None,
    event_prefix: str,
    action_key: str = "admin.mutate",
) -> Any:
    """Standard admin auth chain. Mutation endpoints default to admin.mutate."""
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


@admin_router.post("/adapters/{adapter_id}/force-revalidate")
def force_revalidate_adapter(
    adapter_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Force inference-time revalidation of a loaded LoRA adapter.

    Per architecture v1 Section 2.7: when ops suspects an adapter has
    degraded since load (drifted output, suspicious inference
    behavior), this endpoint re-runs the canonical validation suite
    against the adapter and returns the result.

    **Phase 8 v1 (locked OPEN-6, 2026-05-11):** registered as a 501
    stub. Phase 9 lands the LoRA adapter sandbox + the real handler.
    Advertising the endpoint surface today lets v1 client
    integrators see the full admin shape and handle the
    not-implemented response gracefully.

    Status codes:
      - 401/403/429: standard admin auth chain.
      - 501: not implemented; Phase 9 fills in the handler.
    """
    request_id: str = request.state.request_id
    actor = _admin_authn(
        request,
        authorization,
        event_prefix="admin.adapters.force_revalidate",
        action_key="admin.mutate",
    )

    emit_admin_audit(
        actor=actor,
        event_type="admin.adapters.force_revalidate.not_implemented",
        parameters={"adapter_id": adapter_id, "phase": "phase_8_stub"},
        request_id=request_id,
    )
    raise HTTPException(
        status_code=501,
        detail=(
            f"Adapter force-revalidate for {adapter_id!r} is not implemented "
            f"in Phase 8. The LoRA adapter sandbox + management plane lands "
            f"in Phase 9. The endpoint is registered today so v1 clients "
            f"can integrate against the full admin surface."
        ),
    )


__all__ = ["force_revalidate_adapter"]
