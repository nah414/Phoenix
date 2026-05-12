"""Admin adapter management endpoints.

Phase 8 Step 9 shipped a 501 stub for
``POST /v1/admin/adapters/{adapter_id}/force-revalidate`` (locked
OPEN-6 deferral); Phase 9 Step 4 replaces the stub with the real
handler that drives the loaded adapter through the inference-time
round-trip validator and exposes
``GET /v1/admin/adapters/{adapter_id}/round-trip-history`` for the
per-adapter validation ring buffer.

**force-revalidate** is a mutation endpoint (it appends a fresh
entry to the history ring) -- cost = ``admin.mutate``. The handler
goes through the standard admin auth chain, looks up the adapter
in the registry, re-runs the validator, appends the result, and
returns the per-test breakdown. 404 when the adapter isn't loaded.

**round-trip-history** is a read endpoint -- cost = ``admin.read``.
Returns the per-adapter history ring snapshot (most recent 50
entries by default). 404 when the adapter isn't loaded.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Header, HTTPException, Request

from phoenix.adapters.errors import AdapterNotLoaded
from phoenix.adapters.registry import get_registry as get_adapter_registry
from phoenix.adapters.validator import run_round_trip_validation
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
    against the adapter and returns the result. The fresh entry is
    appended to the per-adapter history ring so subsequent calls to
    ``GET /v1/admin/adapters/{id}/round-trip-history`` see it.

    Status codes:
      - 200: revalidation completed (whether it passed or failed
        is in the body's ``validation_passed`` field).
      - 401/403/429/503: standard admin auth chain.
      - 404: adapter isn't loaded.
    """
    request_id: str = request.state.request_id
    actor = _admin_authn(
        request,
        authorization,
        event_prefix="admin.adapters.force_revalidate",
        action_key="admin.mutate",
    )

    registry = get_adapter_registry()
    try:
        record = registry.get(adapter_id)
    except AdapterNotLoaded as exc:
        emit_admin_audit(
            actor=actor,
            event_type="admin.adapters.force_revalidate.not_loaded",
            parameters={"adapter_id": adapter_id},
            request_id=request_id,
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    entry = run_round_trip_validation(record.adapter)
    registry.append_history(adapter_id, entry)

    outcome = "success" if entry.passed else "failed"
    emit_admin_audit(
        actor=actor,
        event_type=f"admin.adapters.force_revalidate.{outcome}",
        parameters={
            "adapter_id": adapter_id,
            "passed": entry.passed,
            "failed_cases": list(entry.failed_cases),
            "wall_clock_ms": entry.wall_clock_ms,
        },
        request_id=request_id,
    )
    return {
        "adapter_id": adapter_id,
        "validation_passed": entry.passed,
        "failed_cases": list(entry.failed_cases),
        "wall_clock_ms": entry.wall_clock_ms,
        "timestamp_unix": entry.timestamp_unix,
    }


@admin_router.get("/adapters/{adapter_id}/round-trip-history")
def round_trip_history(
    adapter_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Return the per-adapter validation history ring snapshot.

    Each entry carries:
      - ``timestamp_unix`` (float): when the validation ran.
      - ``passed`` (bool): overall pass/fail.
      - ``failed_cases`` (list[str]): canonical inputs that didn't
        round-trip. Empty list when passed.
      - ``wall_clock_ms`` (float): how long the suite took.

    Ordering is oldest -> newest. Ring buffer cap is 50 entries
    per adapter (registry default); older entries roll off.

    Status codes:
      - 200: history (possibly empty -- an adapter loaded via the
        sandbox/CLI that bypassed validation, or one that was just
        registered without revalidating).
      - 401/403/429/503: standard admin auth chain.
      - 404: adapter isn't loaded.
    """
    request_id: str = request.state.request_id
    actor = _admin_authn(
        request,
        authorization,
        event_prefix="admin.adapters.round_trip_history",
        action_key="admin.read",
    )

    registry = get_adapter_registry()
    try:
        history = registry.history_snapshot(adapter_id)
    except AdapterNotLoaded as exc:
        emit_admin_audit(
            actor=actor,
            event_type="admin.adapters.round_trip_history.not_loaded",
            parameters={"adapter_id": adapter_id},
            request_id=request_id,
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    emit_admin_audit(
        actor=actor,
        event_type="admin.adapters.round_trip_history.read",
        parameters={
            "adapter_id": adapter_id,
            "entry_count": len(history),
        },
        request_id=request_id,
    )
    return {
        "adapter_id": adapter_id,
        "count": len(history),
        "history": [entry.to_dict() for entry in history],
    }


__all__ = ["force_revalidate_adapter", "round_trip_history"]
