"""Admin router-decision + provider-health inspection (Phase 8 Step 6).

Two read-only endpoints per architecture v1 Section 8.2:

- ``GET /v1/admin/router/decisions?limit=N`` -- returns the last N
  :class:`RoutingDecision` records from the in-process ring buffer
  populated by :meth:`Router.decide`. Each row carries the full
  ``decision_provenance`` block: per-stage filter rationale, ranking
  weights, primary score, pricing-staleness flag.
- ``GET /v1/admin/providers/health-history?provider_id&since_unix``
  -- aggregates ``provider.health.*`` audit events to surface
  manual-quarantine / manual-restore / detector-fired-degradation
  transitions over the configured window.

Both are tagged ``admin.read`` (1-token cost) and tagged ``Admin``
in the OpenAPI schema. They share the standard 4-layer admin auth
chain (parse → safety gate → require_admin → audit emit).

The ring-buffer pattern (per locked OPEN-4) is deliberate: routing
decisions are also captured in the ledger entry's
``routing_provenance`` block (Phase 7 Step 6), which is the
authoritative durable record. The ring buffer is for "what just
happened" ops triage without going through SQL -- a recent-N view
that survives the daemon's lifetime but no longer.
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
from phoenix.admin.errors import AdminPrivilegeRequired, QuarantineDurationExceeded
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
    action_key: str = "admin.read",
) -> Any:
    """Standard 4-layer admin auth chain. Returns the verified Actor."""
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


# ---------------------------------------------------------------------------
# GET /v1/admin/router/decisions


def _decision_to_dict(decision: Any) -> dict[str, Any]:
    """Serialize a :class:`RoutingDecision` for the JSON response.

    Includes primary + alternates (provider_id + backend_name) plus
    the full decision_provenance block. The provenance is the
    interesting part for ops triage -- it shows which providers were
    filtered at each stage and the ranking math that picked the
    winner.
    """
    primary = decision.primary
    alternates = list(decision.alternates) if decision.alternates else []
    return {
        "primary": {
            "provider_id": primary.provider_id,
            "backend_name": primary.backend_name,
        },
        "alternates": [
            {"provider_id": a.provider_id, "backend_name": a.backend_name} for a in alternates
        ],
        "rationale": decision.rationale,
        "estimated_fidelity": decision.estimated_fidelity,
        "estimated_latency_ms": decision.estimated_latency_ms,
        "estimated_cost_usd": decision.estimated_cost_usd,
        "decision_provenance": decision.decision_provenance,
    }


@admin_router.get("/router/decisions")
def router_decisions(
    request: Request,
    limit: int = 50,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Return the last N :class:`RoutingDecision` records.

    Reads from the in-process ring buffer (default cap 1000, configurable
    via ``$PHOENIX_ROUTER_DECISION_LOG_SIZE``). Survives daemon lifetime
    only; the canonical durable record lives in each solve's ledger entry.

    Query params:
      - ``limit`` (int, default 50, capped at 1000): how many recent
        decisions to return, most-recent last.
    """
    request_id: str = request.state.request_id
    actor = _admin_authn(request, authorization, event_prefix="admin.router.decisions")
    from phoenix.router.decision import decision_log_snapshot

    capped_limit = max(1, min(int(limit), 1000))
    decisions = decision_log_snapshot(limit=capped_limit)
    payload = {
        "decisions": [_decision_to_dict(d) for d in decisions],
        "count": len(decisions),
        "limit": capped_limit,
        "sampled_at_unix": time.time(),
    }
    emit_admin_audit(
        actor=actor,
        event_type="admin.router.decisions.read",
        parameters={"returned_count": len(decisions), "limit": capped_limit},
        request_id=request_id,
    )
    return payload


# ---------------------------------------------------------------------------
# GET /v1/admin/providers/health-history


@admin_router.get("/providers/health-history")
def provider_health_history(
    request: Request,
    provider_id: str | None = None,
    since_unix: float = 0.0,
    limit: int = 500,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Provider health-state transitions from the audit log.

    Surfaces ``provider.health.*`` event_types: mark_degraded /
    mark_healthy / mark_offline transitions, manual-quarantine /
    manual-restore operations (when Step 7 lands those endpoints).

    Query params:
      - ``provider_id`` (str, optional): filter to events whose
        parameters reference this provider_id. None returns all
        provider events.
      - ``since_unix`` (float, default 0): events at or after.
      - ``limit`` (int, default 500, capped at 5000): how many
        audit events to scan.
    """
    request_id: str = request.state.request_id
    actor = _admin_authn(request, authorization, event_prefix="admin.providers.health_history")

    capped_limit = max(1, min(int(limit), 5000))
    try:
        from phoenix.state import get_state_backend

        rows = get_state_backend().list_audit_events(
            since_unix=float(since_unix),
            limit=capped_limit,
        )
    except Exception:
        rows = []

    matching: list[dict[str, Any]] = []
    for row in rows:
        event_type = str(row.get("event_type", ""))
        if not event_type.startswith("provider.health."):
            continue
        if provider_id is not None:
            params = row.get("parameters") or {}
            if isinstance(params, str):
                try:
                    params = json.loads(params)
                except Exception:
                    params = {}
            if params.get("provider_id") != provider_id:
                continue
        matching.append(row)

    payload = {
        "events": matching,
        "count": len(matching),
        "provider_id": provider_id,
        "since_unix": float(since_unix),
        "limit": capped_limit,
    }
    emit_admin_audit(
        actor=actor,
        event_type="admin.providers.health_history.read",
        parameters={
            "count": len(matching),
            "provider_id_filter": provider_id,
        },
        request_id=request_id,
    )
    return payload


# ---------------------------------------------------------------------------
# POST /v1/admin/providers/{provider_id}/manual-quarantine
# POST /v1/admin/providers/{provider_id}/manual-restore
#
# Per architecture v1 Section 8.2 + locked OPEN-5 (2026-05-11): manual
# provider state is operational, not audit-grade. Both endpoints emit
# top-priority audit events (so /v1/admin/providers/health-history
# surfaces them) but do NOT append to the Omega Ledger -- the ledger
# is reserved for the two architecture-listed mutations (kill switch +
# HUMAN_REVIEW override) per Section 8.4's explicit mutation surface.

# Per architecture spec: 24-hour policy cap on manual quarantine. ops
# wanting a longer window can re-quarantine when this expires; reducing
# the cap prevents "forgot a provider was quarantined for a week" mistakes.
_QUARANTINE_DURATION_CAP_SECONDS = 86400


class QuarantineBody(BaseModel):
    """Body for POST /v1/admin/providers/{provider_id}/manual-quarantine.

    Fields:
      - ``duration_seconds`` (int, default 3600=1h): how long the
        provider stays DEGRADED. Capped at 86400 (24h).
      - ``reason`` (str, audit-only): operator rationale.
    """

    duration_seconds: int = Field(default=3600, ge=1)
    reason: str = Field(default="", max_length=4096)


class RestoreBody(BaseModel):
    """Body for POST /v1/admin/providers/{provider_id}/manual-restore."""

    reason: str = Field(default="", max_length=4096)


def _provider_registry() -> Any:
    """Get the live module-level provider registry.

    Phoenix's pipeline keeps a singleton router (and therefore a singleton
    provider registry) lazily built on first solve. The admin endpoints
    must mutate THAT registry, not a fresh one, so changes are visible
    to subsequent /v1/tasks requests.
    """
    from phoenix.trinity.pipeline import _get_router

    return _get_router().registry


@admin_router.post("/providers/{provider_id}/manual-quarantine")
def manual_quarantine_provider(
    provider_id: str,
    body: QuarantineBody,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Mark a provider DEGRADED for a specified duration.

    Per architecture §8.2: "Useful when ops has out-of-band knowledge
    (IBM Quantum announced maintenance) that telemetry hasn't caught
    up to."

    Effects:
      1. Calls registry.mark_degraded(provider_id) with a
         degraded_until timestamp.
      2. Emits provider.health.manual_quarantine audit event so the
         /v1/admin/providers/health-history endpoint surfaces it.

    Status codes:
      - 200: quarantined; response carries new state + auto-restore time.
      - 400 QuarantineDurationExceeded: duration > 86400 seconds.
      - 401/403/429/503: standard admin chain.
      - 404: provider_id not in registry.
    """
    request_id: str = request.state.request_id
    actor = _admin_authn(
        request,
        authorization,
        event_prefix="admin.providers.manual_quarantine",
        action_key="admin.mutate",
    )

    if body.duration_seconds > _QUARANTINE_DURATION_CAP_SECONDS:
        exc = QuarantineDurationExceeded(
            requested_seconds=body.duration_seconds,
            cap_seconds=_QUARANTINE_DURATION_CAP_SECONDS,
        )
        emit_admin_audit(
            actor=actor,
            event_type="admin.providers.manual_quarantine.error.duration_exceeded",
            parameters={
                "provider_id": provider_id,
                "requested_seconds": body.duration_seconds,
                "cap_seconds": _QUARANTINE_DURATION_CAP_SECONDS,
            },
            request_id=request_id,
        )
        raise HTTPException(status_code=400, detail=str(exc))

    registry = _provider_registry()
    # Compute the degraded_until ISO-8601 timestamp.
    from datetime import datetime, timedelta, timezone

    degraded_until = datetime.now(timezone.utc) + timedelta(seconds=body.duration_seconds)
    degraded_until_utc = degraded_until.isoformat()

    try:
        registry.mark_degraded(provider_id, degraded_until_utc=degraded_until_utc)
    except KeyError as exc:
        emit_admin_audit(
            actor=actor,
            event_type="admin.providers.manual_quarantine.error.not_found",
            parameters={"provider_id": provider_id},
            request_id=request_id,
        )
        raise HTTPException(
            status_code=404,
            detail=f"Provider {provider_id!r} not registered.",
        ) from exc

    # Top-priority audit so /providers/health-history surfaces it.
    emit_admin_audit(
        actor=actor,
        event_type="provider.health.manual_quarantine",
        parameters={
            "provider_id": provider_id,
            "duration_seconds": body.duration_seconds,
            "degraded_until_utc": degraded_until_utc,
            "reason": body.reason,
        },
        request_id=request_id,
    )

    return {
        "provider_id": provider_id,
        "health": "degraded",
        "degraded_until_utc": degraded_until_utc,
        "duration_seconds": body.duration_seconds,
        "quarantined_by": actor.name,
        "reason": body.reason,
    }


@admin_router.post("/providers/{provider_id}/manual-restore")
def manual_restore_provider(
    provider_id: str,
    body: RestoreBody,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Lift a manual quarantine; restore the provider to HEALTHY.

    Mirror image of manual_quarantine_provider. Emits a
    provider.health.manual_restore audit event.

    Status codes:
      - 200: restored.
      - 401/403/429/503: standard admin chain.
      - 404: provider_id not in registry.
    """
    request_id: str = request.state.request_id
    actor = _admin_authn(
        request,
        authorization,
        event_prefix="admin.providers.manual_restore",
        action_key="admin.mutate",
    )

    registry = _provider_registry()
    try:
        registry.mark_healthy(provider_id)
    except KeyError as exc:
        emit_admin_audit(
            actor=actor,
            event_type="admin.providers.manual_restore.error.not_found",
            parameters={"provider_id": provider_id},
            request_id=request_id,
        )
        raise HTTPException(
            status_code=404,
            detail=f"Provider {provider_id!r} not registered.",
        ) from exc

    emit_admin_audit(
        actor=actor,
        event_type="provider.health.manual_restore",
        parameters={
            "provider_id": provider_id,
            "reason": body.reason,
        },
        request_id=request_id,
    )

    return {
        "provider_id": provider_id,
        "health": "healthy",
        "restored_by": actor.name,
        "reason": body.reason,
    }


__all__ = [
    "manual_quarantine_provider",
    "manual_restore_provider",
    "provider_health_history",
    "router_decisions",
]
