"""Admin system-health + budget + governor + inference-status endpoints
(Phase 8 Step 3).

Per architecture v1 Section 8.2: four read-only admin endpoints for
system inspection:

- ``GET /v1/admin/health/detailed`` -- extends ``/v1/health`` with
  per-subsystem rollup (Trinity Core readiness, drift detector,
  state backend, audit emitter, Omega Ledger, provider registry).
- ``GET /v1/admin/governor`` -- system resource snapshot via
  :mod:`psutil` per locked OPEN-2 (2026-05-11). v1 minimum:
  CPU%, RAM%, disk%, process RSS. GPU/VRAM/NPU/thermal fields
  return ``None`` until Phase 9's cloud-GPU adapter layer.
- ``GET /v1/admin/inference-status`` -- placeholder shape per
  architecture line 1694. v1 Phase 8 ships an empty-shape stub
  (``{adapters: [], queue_depth: 0, last_round_trip_at: null}``);
  Phase 9 fills it in alongside the LoRA adapter sandbox.
- ``GET /v1/admin/budget`` -- per-actor token-bucket state from
  :meth:`RateLimiter.snapshot`. Cumulative provider spend fields
  default to ``0.0`` (Phase 7's solve cost ledger has the data
  but no aggregation layer; v1.x aggregates).

All four endpoints share the standard admin auth chain
(:func:`extract_or_bootstrap` → :func:`verify_request` →
:func:`require_admin` → :func:`emit_admin_audit`), use the
``admin.read=1`` rate-limit cost, and emit
``admin.<endpoint>.read`` audit events on success.
"""

from __future__ import annotations

import time
from typing import Any

import psutil
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


def _admin_authn(
    request: Request,
    authorization: str | None,
    event_prefix: str,
) -> Any:
    """Shared auth chain for admin read endpoints.

    Returns the verified :class:`Actor` on success. Raises an
    :class:`HTTPException` on failure with the audit emit already
    recorded.
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

    # Admin endpoints stay callable while the kill switch is engaged so
    # ops can diagnose + manage the system mid-incident (matches the
    # §8.4 "Phoenix is operable without paging the developer" principle
    # and the kill_switch endpoint family's bypass).
    try:
        verify_request(
            actor,
            action_key="admin.read",
            request_id=request_id,
            skip_kill_switch_check=True,
        )
    except KillSwitchEngaged as exc:
        # Defensive -- with skip_kill_switch_check=True this path
        # shouldn't fire, but kept for completeness so future
        # refactors don't lose the audit emit.
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
# /v1/admin/health/detailed


def _detailed_health_payload() -> dict[str, Any]:
    """Compose the per-subsystem rollup.

    Each subsystem reports with best-effort defensive defaults: an
    exception in one sub-call doesn't take down the whole endpoint
    -- ops needs SOMETHING readable even when half the stack is
    degraded. Per-sub-call failure surfaces in the response as
    ``{status: "error", error: "..."}``.
    """
    out: dict[str, Any] = {}

    # Phoenix version.
    try:
        from phoenix._internal.version import __version__

        out["phoenix_version"] = __version__
    except Exception as exc:
        out["phoenix_version"] = {"status": "error", "error": str(exc)}

    # Kill switch state.
    try:
        from phoenix.safety.kill_switch import get_store as get_ks_store

        ks_state = get_ks_store().read()
        out["kill_switch"] = {
            "engaged": bool(ks_state.engaged),
            "engaged_at_utc": ks_state.engaged_at_utc,
            "engaged_by": ks_state.engaged_by,
            "reason": ks_state.reason,
        }
    except Exception as exc:
        out["kill_switch"] = {"status": "error", "error": str(exc)}

    # Drift detector state.
    try:
        from phoenix.verification.drift_detector import get_detector

        detector = get_detector()
        last_snapshot = detector.last_snapshot()
        if last_snapshot is None:
            out["drift_detector"] = {
                "state": "no_cycle_yet",
                "cycle_unix": None,
                "firing_detectors": [],
            }
        else:
            out["drift_detector"] = {
                "state": last_snapshot.state,
                "cycle_unix": last_snapshot.cycle_unix,
                "firing_detectors": list(last_snapshot.firing_detectors),
            }
    except Exception as exc:
        out["drift_detector"] = {"status": "error", "error": str(exc)}

    # State backend snapshot (write-latency probe: read a known
    # singleton row + measure the round-trip).
    try:
        from phoenix.state import get_state_backend

        backend = get_state_backend()
        start = time.perf_counter()
        _ = backend.get_kill_switch_state()
        latency_ms = (time.perf_counter() - start) * 1000.0
        # Count ledger entries (proxy for "is the durable ledger
        # responsive and how big has it grown").
        ledger_rows = backend.list_ledger_entries(since_unix=0.0, limit=1_000_000)
        out["state_backend"] = {
            "kind": type(backend).__name__,
            "read_latency_ms": round(latency_ms, 3),
            "ledger_entry_count": len(ledger_rows),
        }
    except Exception as exc:
        out["state_backend"] = {"status": "error", "error": str(exc)}

    # Audit emitter pending-queue depth (best-effort -- the JSONL
    # writer's internal Queue size + drop count).
    try:
        from phoenix.audit import get_emitter

        emitter = get_emitter()
        sinks = list(emitter._sinks)
        sink_summaries: list[dict[str, Any]] = []
        for sink in sinks:
            entry: dict[str, Any] = {"sink": type(sink).__name__}
            qsize = getattr(sink, "_queue", None)
            if qsize is not None and hasattr(qsize, "qsize"):
                entry["queue_depth"] = qsize.qsize()
            drops = getattr(sink, "drop_count", None)
            if drops is not None:
                entry["drop_count"] = int(drops)
            sink_summaries.append(entry)
        out["audit_emitter"] = {"sinks": sink_summaries}
    except Exception as exc:
        out["audit_emitter"] = {"status": "error", "error": str(exc)}

    # Event broker (in-memory or NATS).
    try:
        from phoenix.api.event_broker import get_broker

        broker = get_broker()
        out["event_broker"] = {
            "kind": type(broker).__name__,
        }
    except Exception as exc:
        out["event_broker"] = {"status": "error", "error": str(exc)}

    # Provider registry health summary. ``ProviderEntry.health`` is a
    # :class:`ProviderHealth` enum (HEALTHY / DEGRADED / OFFLINE), so
    # we compare the enum's ``.value`` (lowercase strings).
    try:
        from phoenix.router.provider_registry import (
            ProviderHealth,
            build_default_registry,
        )

        registry = build_default_registry()
        entries = list(registry.all_entries())
        out["provider_registry"] = {
            "total": len(entries),
            "healthy": sum(1 for e in entries if e.health is ProviderHealth.HEALTHY),
            "degraded": sum(1 for e in entries if e.health is ProviderHealth.DEGRADED),
            "offline": sum(1 for e in entries if e.health is ProviderHealth.OFFLINE),
        }
    except Exception as exc:
        out["provider_registry"] = {"status": "error", "error": str(exc)}

    out["checked_at_unix"] = time.time()
    return out


@admin_router.get("/health/detailed")
def detailed_health(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Extended health beyond ``/v1/health``.

    Returns per-subsystem state for ops triage: Trinity Core readiness
    is implicit (the daemon answers), drift detector + state backend +
    audit emitter + Omega Ledger entry count + provider registry
    health + event broker kind.

    Each sub-call is defensive: a sub-system failure surfaces as
    ``{status: "error", error: "..."}`` in the response, not as a
    failed endpoint. Per architecture §8.6: "Ops needs SOMETHING
    readable even when half the stack is degraded."
    """
    request_id: str = request.state.request_id
    actor = _admin_authn(request, authorization, event_prefix="admin.health.detailed")
    payload = _detailed_health_payload()
    emit_admin_audit(
        actor=actor,
        event_type="admin.health.detailed.read",
        parameters={
            "kill_switch_engaged": payload.get("kill_switch", {}).get("engaged", False),
            "ledger_entry_count": payload.get("state_backend", {}).get("ledger_entry_count", -1),
        },
        request_id=request_id,
    )
    return payload


# ---------------------------------------------------------------------------
# /v1/admin/governor


def _governor_payload() -> dict[str, Any]:
    """psutil-based system-resource snapshot per locked OPEN-2.

    Returns a dict with CPU%, RAM%, disk%, process RSS. GPU/VRAM/
    NPU/thermal fields return ``None`` -- Phase 9+ fills them when
    the cloud-GPU adapter lands.

    All psutil calls wrapped in try/except so a hostile environment
    (psutil restricted, exotic OS) still gets a partial response.
    """
    out: dict[str, Any] = {
        "cpu_percent": None,
        "ram_percent": None,
        "ram_used_bytes": None,
        "ram_total_bytes": None,
        "disk_percent": None,
        "disk_path": None,
        "process_rss_bytes": None,
        # Per locked OPEN-2: GPU/VRAM/NPU/thermal land in Phase 9+.
        "gpu_percent": None,
        "vram_percent": None,
        "npu_percent": None,
        "thermal_celsius": None,
        "sampled_at_unix": time.time(),
    }
    try:
        # Non-blocking sample; first call returns 0.0 immediately,
        # subsequent calls return actual % since the previous sample.
        out["cpu_percent"] = psutil.cpu_percent(interval=None)
    except Exception:
        pass
    try:
        vm = psutil.virtual_memory()
        out["ram_percent"] = float(vm.percent)
        out["ram_used_bytes"] = int(vm.used)
        out["ram_total_bytes"] = int(vm.total)
    except Exception:
        pass
    try:
        # Probe the current working directory's disk usage; ops can
        # cross-reference with the Phoenix runtime path on their own.
        import os as _os

        cwd = _os.getcwd()
        du = psutil.disk_usage(cwd)
        out["disk_percent"] = float(du.percent)
        out["disk_path"] = cwd
    except Exception:
        pass
    try:
        proc = psutil.Process()
        out["process_rss_bytes"] = int(proc.memory_info().rss)
    except Exception:
        pass
    return out


@admin_router.get("/governor")
def governor(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """System resource snapshot via :mod:`psutil`.

    Per locked OPEN-2 (2026-05-11): v1 minimum is psutil-only --
    CPU%, RAM%, disk%, process RSS. GPU/VRAM/NPU/thermal fields
    return ``None``; Phase 9+ populates them when the cloud-GPU
    adapter layer matures.
    """
    request_id: str = request.state.request_id
    actor = _admin_authn(request, authorization, event_prefix="admin.governor")
    payload = _governor_payload()
    emit_admin_audit(
        actor=actor,
        event_type="admin.governor.read",
        parameters={
            "cpu_percent": payload.get("cpu_percent"),
            "ram_percent": payload.get("ram_percent"),
        },
        request_id=request_id,
    )
    return payload


# ---------------------------------------------------------------------------
# /v1/admin/inference-status


@admin_router.get("/inference-status")
def inference_status(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Placeholder LoRA-adapter inference state.

    Per architecture line 1694: per-adapter active sessions, queue
    depth, last successful round-trip timestamp, validation suite
    status. **v1 Phase 8 ships an empty-shape stub** -- Phase 9's
    LoRA adapter sandbox fills it in. The empty-shape ships so
    integrators can build their dashboards against the contract
    today.
    """
    request_id: str = request.state.request_id
    actor = _admin_authn(request, authorization, event_prefix="admin.inference_status")
    payload: dict[str, Any] = {
        "adapters": [],
        "queue_depth": 0,
        "last_round_trip_at": None,
        "phase": "phase_8_placeholder",
        "note": (
            "LoRA adapter sandbox + management lands in Phase 9. "
            "Phase 8 ships the endpoint shape so dashboards can "
            "integrate against the v1 contract today."
        ),
    }
    emit_admin_audit(
        actor=actor,
        event_type="admin.inference_status.read",
        parameters={},
        request_id=request_id,
    )
    return payload


# ---------------------------------------------------------------------------
# /v1/admin/budget


@admin_router.get("/budget")
def budget(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Per-actor token-bucket state + cumulative spend (v1 stub).

    Token-bucket state comes from :meth:`RateLimiter.snapshot` which
    refills each bucket's projected ``tokens_remaining`` to the
    snapshot moment without mutating live state.

    Cumulative provider spend is a Phase 7 ``solve_cost_ledger``
    table column that's populated per-solve. v1 Phase 8 returns
    ``0.0`` placeholders -- the aggregation layer (sum across actors,
    rollup by month) lands in v1.x. The shape is fixed so clients
    can integrate against the v1 contract today.
    """
    request_id: str = request.state.request_id
    actor = _admin_authn(request, authorization, event_prefix="admin.budget")
    try:
        from phoenix.safety.rate_limiter import get_limiter

        buckets = get_limiter().snapshot()
    except Exception:
        buckets = []
    payload: dict[str, Any] = {
        "rate_limit_buckets": buckets,
        "cumulative_spend_usd_ytd": 0.0,
        "per_actor_spend_usd_ytd": [],
        "phase": "phase_8_minimum",
        "note": (
            "v1 Phase 8 ships token-bucket snapshot. Per-actor and "
            "org-level cumulative provider spend rollups land in "
            "v1.x; placeholder zeros for now."
        ),
        "sampled_at_unix": time.time(),
    }
    emit_admin_audit(
        actor=actor,
        event_type="admin.budget.read",
        parameters={"bucket_count": len(buckets)},
        request_id=request_id,
    )
    return payload


__all__ = [
    "budget",
    "detailed_health",
    "governor",
    "inference_status",
]
