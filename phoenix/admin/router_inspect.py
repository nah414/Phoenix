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


__all__ = [
    "provider_health_history",
    "router_decisions",
]
