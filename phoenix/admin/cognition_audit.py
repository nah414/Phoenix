"""Admin endpoint: cognition-spend audit query (Phase 13 Step 9).

Per Phase 13 build guide §4.9: ``GET /v1/admin/audit/cognition-spend``
gives admins a view into cognition-specific spending separate from
the existing solve-cost audit.

The query model is a rolling-24h aggregate per actor: how much has
each actor spent on cognition in the last 24h (or N hours if
``window_hours`` query param is set). The data comes from the
``ledger_entries`` table's :attr:`cognition_provenance_json` column
(added by Phase 13 Step 8's migration) — each cognition entry
carries the cost in its provenance JSON, so the query walks the
ledger entries for the window and aggregates.

**Note:** Phase 13 ships this endpoint with a SQL-side aggregation
that reads cost from the cognition_provenance_json JSON blob. The
Phase 13.x acceptance battery may add a dedicated
``cognition_cost_ledger`` table for faster queries, parallel to
``solve_cost_ledger``; for v1.1 the JSON-blob aggregation is
sufficient and avoids another migration.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import Header, Query, Request

from phoenix.admin.audit_decorator import emit_admin_audit
from phoenix.admin.grant_prompt_verbatim import _admin_authn
from phoenix.admin.router import admin_router
from phoenix.state import get_state_backend

logger = logging.getLogger(__name__)


_MAX_WINDOW_HOURS = 24 * 30  # 30 days; safety bound on long lookbacks.


@admin_router.get("/audit/cognition-spend")
def get_cognition_spend(
    request: Request,
    authorization: str | None = Header(default=None),
    window_hours: int = Query(default=24, gt=0, le=_MAX_WINDOW_HOURS),
    actor_filter: str | None = Query(default=None, min_length=1, max_length=64),
) -> dict[str, Any]:
    """Aggregate cognition spend per actor over a rolling window.

    Query params:
      - ``window_hours`` (default 24, max 720): lookback window.
      - ``actor_filter`` (optional): limit results to this actor name.

    Response shape::

        {
            "window_hours": 24,
            "window_start_unix": 1717000000.0,
            "window_end_unix": 1717086400.0,
            "per_actor_spend_usd": {
                "ash": 0.42,
                "adam": 1.18
            },
            "total_entries": 47,
            "total_spend_usd": 1.60
        }

    Status codes:
      - 200: aggregation returned.
      - 401/403/429/503: standard admin auth chain.
      - 422: invalid query params (Pydantic validation).
    """
    request_id: str = request.state.request_id
    actor = _admin_authn(request, authorization, event_prefix="admin.audit.cognition_spend")

    now = time.time()
    window_start = now - (window_hours * 3600.0)

    backend = get_state_backend()

    # Walk the ledger entries in the window. The backend method
    # list_ledger_entries returns dicts with payload_json + entry_kind;
    # we filter to cognition entries client-side because the
    # cognition-provenance JSON parsing is cheaper than a SQL
    # JSON_EXTRACT across all dialects.
    entries = backend.list_ledger_entries(
        since_unix=window_start,
        limit=10_000,
    )

    per_actor: dict[str, float] = {}
    total = 0.0
    counted = 0
    for entry in entries:
        if entry.get("entry_kind") != "cognition":
            continue
        actor_id = entry.get("actor_id", "")
        if actor_filter is not None and actor_id != actor_filter:
            continue
        # Parse the payload to reach the cost field. Per Phase 13 Step 8
        # the payload contains the cognition_provenance dict; the
        # axis's ledger writer (Step 4/10 wiring) puts cost in
        # provenance.cost_usd.
        payload_raw = entry.get("payload_json", "{}")
        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            continue
        cost_usd = _extract_cost(payload)
        per_actor[actor_id] = per_actor.get(actor_id, 0.0) + cost_usd
        total += cost_usd
        counted += 1

    emit_admin_audit(
        actor=actor,
        event_type="admin.audit.cognition_spend.success",
        parameters={
            "window_hours": window_hours,
            "actor_filter": actor_filter,
            "total_entries": counted,
            "total_spend_usd": total,
        },
        request_id=request_id,
    )

    return {
        "window_hours": window_hours,
        "window_start_unix": window_start,
        "window_end_unix": now,
        "per_actor_spend_usd": per_actor,
        "total_entries": counted,
        "total_spend_usd": total,
    }


def _extract_cost(payload: dict[str, Any]) -> float:
    """Pull the cost_usd value out of a cognition entry payload.

    The cognition wobble axis writes its cost into the provenance
    JSON under ``provenance.cost_usd``. Older entries (pre-Step 10
    wiring) may not have the field; treat missing as 0.0 so the
    aggregation doesn't crash on partial data.

    Falls back through several JSON paths the axis MAY use:

    1. ``payload["cost_usd"]`` — direct (Phase 13.x wiring).
    2. ``payload["provenance"]["cost_usd"]`` — nested.
    3. Anything else → 0.0.
    """
    direct = payload.get("cost_usd")
    if isinstance(direct, (int, float)):
        return float(direct)
    provenance = payload.get("provenance")
    if isinstance(provenance, dict):
        nested = provenance.get("cost_usd")
        if isinstance(nested, (int, float)):
            return float(nested)
    return 0.0


__all__ = ["get_cognition_spend"]
