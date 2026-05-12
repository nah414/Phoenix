"""Admin audit replay + ledger integrity report (Phase 8 Step 8).

Two read-only endpoints per architecture v1 Section 8.2:

- ``GET /v1/admin/audit/replay?since_unix&until_unix&event_type&actor_id&limit``
  -- admin-scoped audit query. Beyond ``/v1/audit/events`` (which is
  open to any authenticated actor), this surface accepts arbitrary
  ``event_type`` prefix + ``actor_id`` filters so ops can drill into
  events that the user-facing endpoint doesn't expose (rate-limit
  denials of other actors, signature-verification failures,
  kill-switch transitions, admin actions themselves).

- ``GET /v1/admin/ledger/integrity-report?window_hours`` -- full
  hashchain walk plus per-entry-kind distribution histogram over
  the configured window. Calls both the SQL structural check
  (:meth:`StateBackend.verify_ledger_integrity`) and the Python
  crypto walk (:meth:`OmegaLedger.verify_chain`). The Phase 8
  surface goes beyond the existing ``/v1/audit/ledger/verify``
  endpoint by adding the tag distribution + chain-head pointer so
  ops can detect a "chain stopped growing 2 hours ago" problem.

Both endpoints are tagged ``Admin`` in OpenAPI and run with
``admin.read=1`` cost.
"""

from __future__ import annotations

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
    """Standard admin auth chain (parse → safety gate → require_admin)."""
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
# GET /v1/admin/audit/replay


@admin_router.get("/audit/replay")
def audit_replay(
    request: Request,
    since_unix: float = 0.0,
    until_unix: float | None = None,
    event_type_prefix: str | None = None,
    actor_id: str | None = None,
    layer: str | None = None,
    limit: int = 500,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Admin-scoped audit query with rich filters.

    Reads from the state_backend audit_events table. Filters compose
    (all applied as AND). The user-facing ``/v1/audit/events``
    endpoint only exposes the unfiltered surface; admin replay adds
    event_type / actor_id / layer / time-window filters AND surfaces
    events the user-facing surface filters out (rate-limit denials
    of other actors, signature-verification failures, kill-switch
    transitions, admin actions themselves).

    Query params:
      - ``since_unix`` (float, default 0): events at or after.
      - ``until_unix`` (float, optional): events at or before. None
        means "no upper bound".
      - ``event_type_prefix`` (str, optional): event_type starts
        with this string (e.g. "admin." for all admin events,
        "safety.gate.denied" for denials).
      - ``actor_id`` (str, optional): exact-match actor.
      - ``layer`` (str, optional): exact-match layer
        ("api" | "safety_gate" | "verification_gate" | "drift_detector"
         | "admin" | "ledger" | etc.).
      - ``limit`` (int, default 500, capped at 10000).
    """
    request_id: str = request.state.request_id
    actor = _admin_authn(request, authorization, event_prefix="admin.audit.replay")

    capped_limit = max(1, min(int(limit), 10_000))
    # Pull a wider window from SQL than the limit so client-side
    # filters can prune. The SQL ORDER BY timestamp ASC + LIMIT
    # combo would otherwise skip newer matching events when the
    # filter is restrictive.
    fetch_limit = min(capped_limit * 10, 100_000)

    try:
        from phoenix.state import get_state_backend

        rows = get_state_backend().list_audit_events(
            since_unix=float(since_unix),
            limit=fetch_limit,
        )
    except Exception:
        rows = []

    filtered: list[dict[str, Any]] = []
    for row in rows:
        if until_unix is not None and float(row.get("timestamp_unix", 0)) > float(until_unix):
            continue
        if event_type_prefix is not None and not str(row.get("event_type", "")).startswith(
            event_type_prefix
        ):
            continue
        if actor_id is not None and row.get("actor_id") != actor_id:
            continue
        if layer is not None and row.get("layer") != layer:
            continue
        filtered.append(row)
        if len(filtered) >= capped_limit:
            break

    payload = {
        "events": filtered,
        "count": len(filtered),
        "since_unix": float(since_unix),
        "until_unix": until_unix,
        "filters": {
            "event_type_prefix": event_type_prefix,
            "actor_id": actor_id,
            "layer": layer,
        },
        "limit": capped_limit,
        "sampled_at_unix": time.time(),
    }
    emit_admin_audit(
        actor=actor,
        event_type="admin.audit.replay.read",
        parameters={
            "count": len(filtered),
            "event_type_prefix": event_type_prefix,
            "actor_id_filter": actor_id,
            "layer_filter": layer,
        },
        request_id=request_id,
    )
    return payload


# ---------------------------------------------------------------------------
# GET /v1/admin/ledger/integrity-report


def _entry_kind_distribution(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Tally entry_kind across the row list."""
    counts: dict[str, int] = {}
    for row in rows:
        kind = str(row.get("entry_kind", "unknown"))
        counts[kind] = counts.get(kind, 0) + 1
    return counts


@admin_router.get("/ledger/integrity-report")
def ledger_integrity_report(
    request: Request,
    window_hours: float | None = None,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Full Omega Ledger integrity report.

    Returns BOTH integrity checks plus the per-entry-kind distribution
    and chain-head pointer:

    - ``sql_structural``: StateBackend.verify_ledger_integrity()
      (SQL window-function structural check).
    - ``python_crypto``: OmegaLedger.verify_chain() (Python SHA-256
      recomputation).
    - ``entry_kind_distribution``: histogram of entry_kind values
      (solve, override_by_operator, kill_switch, enrollment) over
      the configured window. Per architecture §8.2: "how many
      OVERRIDE_BY_OPERATOR, how many PROPOSED_BY_AGENT, how many
      normal solves".
    - ``chain_head``: most recent entry_hash + its timestamp_unix.
      Lets ops detect "chain stopped growing N hours ago".

    Query params:
      - ``window_hours`` (float, optional): only count entries from
        the last N hours toward ``entry_kind_distribution``. None
        means "all entries".
    """
    request_id: str = request.state.request_id
    actor = _admin_authn(request, authorization, event_prefix="admin.ledger.integrity_report")

    sql_report: dict[str, Any] = {}
    py_report_dict: dict[str, Any] = {}
    distribution: dict[str, int] = {}
    chain_head: dict[str, Any] | None = None
    total_entries = 0

    try:
        from phoenix.ledger import get_ledger
        from phoenix.state import get_state_backend

        backend = get_state_backend()
        sql_report = backend.verify_ledger_integrity()
        py_report = get_ledger().verify_chain()
        py_report_dict = {
            "valid": py_report.valid,
            "entries_checked": py_report.entries_checked,
            "first_broken_entry_id": py_report.first_broken_entry_id,
            "reason": py_report.reason,
        }

        # Pull a wide window for the histogram + chain-head detection.
        # The window_hours filter is applied client-side.
        all_rows = backend.list_ledger_entries(since_unix=0.0, limit=1_000_000)
        total_entries = len(all_rows)
        if all_rows:
            last_row = all_rows[-1]
            chain_head = {
                "entry_hash": str(last_row.get("entry_hash", "")),
                "entry_kind": str(last_row.get("entry_kind", "")),
                "timestamp_unix": float(last_row.get("timestamp_unix", 0.0)),
                "age_seconds": time.time() - float(last_row.get("timestamp_unix", 0.0)),
            }

        if window_hours is not None and window_hours > 0:
            cutoff = time.time() - float(window_hours) * 3600.0
            windowed = [r for r in all_rows if float(r.get("timestamp_unix", 0)) >= cutoff]
        else:
            windowed = all_rows
        distribution = _entry_kind_distribution(windowed)
    except Exception as exc:
        logger.exception("Failed to build ledger integrity report")
        emit_admin_audit(
            actor=actor,
            event_type="admin.ledger.integrity_report.error",
            parameters={"error": str(exc)},
            request_id=request_id,
        )

    payload: dict[str, Any] = {
        "sql_structural": sql_report,
        "python_crypto": py_report_dict,
        "entry_kind_distribution": distribution,
        "total_entries": total_entries,
        "chain_head": chain_head,
        "window_hours": window_hours,
        "sampled_at_unix": time.time(),
    }
    emit_admin_audit(
        actor=actor,
        event_type="admin.ledger.integrity_report.read",
        parameters={
            "total_entries": total_entries,
            "sql_valid": sql_report.get("valid"),
            "python_valid": py_report_dict.get("valid"),
        },
        request_id=request_id,
    )
    return payload


__all__ = ["audit_replay", "ledger_integrity_report"]
