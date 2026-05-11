"""Admin calibration drill-down + force-cycle endpoints (Phase 8 Step 4).

Per architecture v1 Section 8.2:

- ``GET /v1/admin/calibration/detail`` -- full drift detector state.
  Per-checker last-run timestamp + state, cadence configuration, the
  last :class:`DriftSnapshot` if a cycle has run.
- ``GET /v1/admin/calibration/history?since_unix&limit`` -- drift
  cycle history from the audit log, filtered to ``drift.*`` event
  types over a configurable time window.
- ``POST /v1/admin/calibration/run`` -- force a drift cycle to run
  now, regardless of cadence. Body: ``{wait: bool = false}``.
  ``wait=true`` blocks until the cycle completes (typical 5-7 minutes
  on the full statistical sweep per Decision 17 PERF note);
  ``wait=false`` returns immediately with a cycle_id and the cycle
  runs in a daemon thread. Raises :class:`CalibrationRunInProgress`
  (HTTP 409) if a cycle is already running.

**Concurrency model:** a module-level non-blocking
:class:`threading.Lock` tracks "is a force-cycle running right now".
Acquired before invoking :meth:`DriftDetector.run_cycle`; released
in the ``finally`` block. The drift detector itself synchronizes
its own internal state with an independent lock; this admin-side
lock is purely so we can return 409 instead of blocking when ops
double-clicks the button. The detector's background scheduler
(when started) is independent of this lock and continues to fire
on its own cadence.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any

from fastapi import Header, HTTPException, Request
from pydantic import BaseModel

from phoenix.admin.audit_decorator import emit_admin_audit
from phoenix.admin.auth import require_admin
from phoenix.admin.errors import AdminPrivilegeRequired, CalibrationRunInProgress
from phoenix.admin.router import admin_router
from phoenix.identity.bootstrap import IdentityError, extract_or_bootstrap
from phoenix.safety.errors import AuthError, PermissionDenied
from phoenix.safety.gate import verify_request
from phoenix.safety.kill_switch import KillSwitchEngaged
from phoenix.safety.rate_limiter import RateLimitExceeded

logger = logging.getLogger(__name__)


# Module-level non-blocking lock guarding the force-cycle endpoint.
# Acquired with ``blocking=False``; failure to acquire means a cycle
# is in progress and the endpoint raises CalibrationRunInProgress.
_RUN_LOCK = threading.Lock()


def _admin_authn(
    request: Request,
    authorization: str | None,
    event_prefix: str,
    action_key: str = "admin.read",
) -> Any:
    """Shared auth chain. Returns the verified Actor; raises HTTPException
    on failure (audit emit already recorded)."""
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
# /v1/admin/calibration/detail


def _checker_state(checker: Any) -> dict[str, Any]:
    """Best-effort per-checker state introspection.

    Each Phase 6b checker (Tier1Analytical, MLStatistical,
    CrossVersion) exposes a ``name`` attribute. Beyond that the
    public surface varies; :class:`Tier1AnalyticalChecker` has
    ``last_results()``; the others don't yet. We probe defensively.
    """
    state: dict[str, Any] = {"name": getattr(checker, "name", type(checker).__name__)}
    # Phase 6b Tier1 exposes last_results() -> {benchmark_name: bool}.
    last_results = getattr(checker, "last_results", None)
    if callable(last_results):
        try:
            state["last_results"] = last_results()
        except Exception as exc:
            state["last_results"] = {"status": "error", "error": str(exc)}
    return state


def _calibration_detail_payload() -> dict[str, Any]:
    """Compose the calibration-detail rollup."""
    out: dict[str, Any] = {"sampled_at_unix": time.time()}

    try:
        from phoenix.verification.drift_detector import get_detector

        detector = get_detector()
        # Cadence config (read-only).
        out["cadence_seconds"] = getattr(detector, "_cadence", None)
        # Scheduler running state.
        thread = getattr(detector, "_thread", None)
        out["scheduler_running"] = bool(thread is not None and thread.is_alive())

        # Per-checker state.
        checkers = getattr(detector, "_checkers", [])
        out["checkers"] = [_checker_state(c) for c in checkers]

        # Last snapshot.
        snap = detector.last_snapshot()
        if snap is None:
            out["last_snapshot"] = None
        else:
            out["last_snapshot"] = {
                "cycle_unix": snap.cycle_unix,
                "state": snap.state,
                "firing_detectors": list(snap.firing_detectors),
                "detector_summaries": dict(snap.detector_summaries),
            }
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"

    return out


@admin_router.get("/calibration/detail")
def calibration_detail(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Full drift detector state for ops triage.

    Returns: cadence configuration, scheduler running flag,
    per-checker state (with ``last_results`` for the Tier-1 battery
    when available), and the most recent :class:`DriftSnapshot`
    (or ``None`` if no cycle has run yet).

    Read-only; cost ``admin.read=1``.
    """
    request_id: str = request.state.request_id
    actor = _admin_authn(request, authorization, event_prefix="admin.calibration.detail")
    payload = _calibration_detail_payload()
    emit_admin_audit(
        actor=actor,
        event_type="admin.calibration.detail.read",
        parameters={
            "scheduler_running": payload.get("scheduler_running", False),
            "has_snapshot": payload.get("last_snapshot") is not None,
        },
        request_id=request_id,
    )
    return payload


# ---------------------------------------------------------------------------
# /v1/admin/calibration/history


@admin_router.get("/calibration/history")
def calibration_history(
    request: Request,
    since_unix: float = 0.0,
    limit: int = 200,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Drift cycle history from the audit log.

    Returns ``audit_events`` rows whose ``event_type`` matches
    ``drift.*`` (i.e. the drift-detector + drift-alert events
    Phase 6b/7 emit), ordered ascending by timestamp, capped at
    ``limit``. The audit log is the canonical history; the drift
    detector's own state is "what's happening RIGHT NOW", whereas
    the audit log is "what happened over the last window".

    Query params:
      - ``since_unix`` (float, default 0) -- only events at or after.
      - ``limit`` (int, default 200, capped at 1000).

    Read-only; cost ``admin.read=1``.
    """
    request_id: str = request.state.request_id
    actor = _admin_authn(request, authorization, event_prefix="admin.calibration.history")

    capped_limit = max(1, min(int(limit), 1000))

    # Pull from both sources: the structured audit log (Phase 6b's
    # state backend audit_events table) AND the JSONL writer. The
    # state backend's table is the canonical store; we read from
    # there. Phase 7 Step 1's JSONL writer mirrors but isn't a
    # query source.
    from phoenix.state import get_state_backend

    try:
        all_events = get_state_backend().list_audit_events(
            since_unix=float(since_unix),
            limit=10_000,  # pull a wide window, filter client-side
        )
    except Exception:
        all_events = []
    drift_events = [e for e in all_events if str(e.get("event_type", "")).startswith("drift.")][
        :capped_limit
    ]

    payload: dict[str, Any] = {
        "events": drift_events,
        "count": len(drift_events),
        "since_unix": float(since_unix),
        "limit": capped_limit,
    }
    emit_admin_audit(
        actor=actor,
        event_type="admin.calibration.history.read",
        parameters={"count": len(drift_events), "since_unix": float(since_unix)},
        request_id=request_id,
    )
    return payload


# ---------------------------------------------------------------------------
# POST /v1/admin/calibration/run


class CalibrationRunBody(BaseModel):
    """Body for ``POST /v1/admin/calibration/run``.

    ``wait`` semantics:
    - ``False`` (default): the cycle runs in a daemon background
      thread; the response returns immediately with the cycle_id.
      Caller can poll ``/calibration/detail`` to see when
      ``last_snapshot.cycle_unix`` updates.
    - ``True``: the request blocks until the cycle completes
      (typically 5-7 minutes on the full statistical sweep per
      Decision 17). The response carries the resulting
      :class:`DriftSnapshot`.
    """

    wait: bool = False


def _run_cycle_under_lock(cycle_id: str, actor_name: str, request_id: str) -> Any:
    """Helper that the foreground (wait=True) + background (wait=False)
    paths both call. Holds :data:`_RUN_LOCK` for the cycle duration.

    Returns the resulting :class:`DriftSnapshot`. Releases the lock
    in ``finally`` so a checker exception doesn't pin the lock
    forever.
    """
    from phoenix.verification.drift_detector import get_detector

    detector = get_detector()
    try:
        snapshot = detector.run_cycle()
        emit_admin_audit(
            actor=None,
            event_type="admin.calibration.run.completed",
            parameters={
                "cycle_id": cycle_id,
                "actor_name": actor_name,
                "state": snapshot.state,
                "firing_detectors": list(snapshot.firing_detectors),
            },
            request_id=request_id,
        )
        return snapshot
    except Exception as exc:
        logger.exception("Force-cycle %s failed", cycle_id)
        emit_admin_audit(
            actor=None,
            event_type="admin.calibration.run.error",
            parameters={
                "cycle_id": cycle_id,
                "actor_name": actor_name,
                "error_type": type(exc).__name__,
                "error_detail": str(exc),
            },
            request_id=request_id,
        )
        raise
    finally:
        _RUN_LOCK.release()


@admin_router.post("/calibration/run")
def calibration_run(
    body: CalibrationRunBody,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Force a drift cycle to run now, regardless of scheduler cadence.

    Status codes:
      - 200: ``wait=False`` -- cycle queued in background; response
        carries ``cycle_id`` + ``status="running"``. ``wait=True`` --
        cycle completed; response carries the
        :class:`DriftSnapshot` shape under ``snapshot``.
      - 401/403/429/503: auth / permission / rate-limit / kill
        switch errors (kill-switch path defensive only -- admin
        endpoints bypass Stage 0).
      - 409 ``CalibrationRunInProgress``: another cycle is already
        in flight; retry once the prior cycle completes (poll
        ``/calibration/detail.last_snapshot.cycle_unix`` to know
        when).
    """
    request_id: str = request.state.request_id
    actor = _admin_authn(
        request,
        authorization,
        event_prefix="admin.calibration.run",
        action_key="admin.mutate",
    )

    cycle_id = f"cycle_{uuid.uuid4().hex}"

    # Non-blocking acquire: if a cycle is in progress, 409.
    if not _RUN_LOCK.acquire(blocking=False):
        exc = CalibrationRunInProgress(
            "A drift cycle is already running; retry after it completes."
        )
        emit_admin_audit(
            actor=actor,
            event_type="admin.calibration.run.error.in_progress",
            parameters={"cycle_id": cycle_id},
            request_id=request_id,
        )
        raise HTTPException(status_code=409, detail=str(exc))

    emit_admin_audit(
        actor=actor,
        event_type="admin.calibration.run.started",
        parameters={"cycle_id": cycle_id, "wait": body.wait},
        request_id=request_id,
    )

    if body.wait:
        # Foreground: run synchronously. _run_cycle_under_lock releases
        # the lock in its finally block.
        snapshot = _run_cycle_under_lock(cycle_id, actor.name, request_id)
        return {
            "cycle_id": cycle_id,
            "status": "completed",
            "snapshot": {
                "cycle_unix": snapshot.cycle_unix,
                "state": snapshot.state,
                "firing_detectors": list(snapshot.firing_detectors),
                "detector_summaries": dict(snapshot.detector_summaries),
            },
        }

    # Background: spawn a daemon thread; release happens inside.
    thread = threading.Thread(
        target=_run_cycle_under_lock,
        args=(cycle_id, actor.name, request_id),
        daemon=True,
        name=f"admin-cal-cycle-{cycle_id[:12]}",
    )
    thread.start()
    return {
        "cycle_id": cycle_id,
        "status": "running",
        "wait": False,
    }


__all__ = [
    "calibration_detail",
    "calibration_history",
    "calibration_run",
]
