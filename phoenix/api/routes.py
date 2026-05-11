"""Phoenix v1 -- front-door REST surface.

Phase 0 shipped ``/v1/health``. Phase 2 added ``POST /v1/tasks`` returning
a Solver-only ``CandidateAnswer``. Phase 3 promotes the response to the
full :class:`Result` envelope: top-level ``value``, ``error_bar``,
``sigma``, ``agreement_type``, ``kpi_bundle_orchestrate`` (typed
:class:`KPIBundle`), and a flattened :class:`ProvenanceTrace` with
solver + control + orchestrate sub-blocks plus the
``cloud_shots_recorded`` flag (Section 1 Decision 20).

Per architecture v1 Section 5.2 the rest of the surface lands across later
phases:

- Tasks endpoints -- Phase 2 + 3 ship ``POST /v1/tasks``. The list / get /
  replay / approve_promotion / cancel endpoints land in Phase 3+ once the
  ledger backs them.
- Audit/ledger -- Phase 7+.
- Admin -- Phase 8.
- Adapters -- Phase 9.
- Identity -- Phase 6 (state backend dependency).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import (
    FastAPI,
    Header,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, Field

from phoenix._internal.latency import LatencyTier, LatencyTierNotImplemented
from phoenix._internal.version import __version__, read_vendor_version
from phoenix.api.drift_alerts import DRIFT_ALERTS_CHANNEL, install_drift_alert_emitter
from phoenix.api.event_broker import get_broker, to_dict
from phoenix.api.ws_auth import WSTokenError, get_store as get_ws_token_store
from phoenix.audit import AuditEvent, get_emitter
from phoenix.identity.bootstrap import IdentityError, extract_or_bootstrap
from phoenix.safety.errors import AuthError, PermissionDenied
from phoenix.safety.gate import verify_request
from phoenix.safety.kill_switch import KillSwitchEngaged, set_store_backend
from phoenix.safety.rate_limiter import RateLimitExceeded
from phoenix.state import get_state_backend, reset_state_backend
from phoenix.trinity.control.engine import ControlVerificationError
from phoenix.trinity.data_model import PhysicsTask, ToleranceSpec
from phoenix.trinity.orchestrate.kpi_bundle import KPIStatus
from phoenix.trinity.orchestrate.provider_client import OrchestrateProviderError
from phoenix.trinity.pipeline import solve
from phoenix.trinity.solver.engine import (
    FrontierPhysicsRefused,
    NoEligibleSolverError,
)
from phoenix.verification.drift_detector import get_detector, reset_detector
from phoenix.verification.rung_table import select_initial_rung


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Phoenix daemon lifecycle (Phase 6b Steps 4 + 8 + Phase 7 Step 3 startup wiring).

    On startup:

    - **Phase 6b Step 4**: construct the configured :class:`StateBackend`
      from :func:`phoenix.state.get_state_backend` and wire it into the
      kill-switch write-through path.
    - **Phase 6b Step 8**: get the :class:`DriftDetector` singleton and
      register the drift-alert emitter callback that bridges drift
      cycles into the event broker -- the
      ``/v1/ws/calibration/drift`` endpoint reads from that broker
      channel.
    - **Phase 7 Step 3**: when ``$PHOENIX_OTEL_ENABLED=1`` is set,
      construct an :class:`~phoenix.audit.otel_adapter.OTelExporter`
      and register it as a second sink on the singleton audit
      emitter. The JSONL sink (Step 1 default) remains active in
      parallel. When the env flag is unset, the OTel SDK is never
      imported -- safe on installs without the ``otel`` extra.

    On shutdown: close the audit emitter (which flushes both sinks),
    clear the kill-switch wiring and close the backend, then reset
    the detector singleton (which stops its scheduler if started).

    Per Decision 31, the backend choice is made once at startup and
    not switchable at runtime. Per Decision 17 the drift detector's
    scheduler stays **idle** by default -- the launcher script or an
    explicit ops command starts it; the bridge wired here just makes
    sure that when cycles DO happen, alerts reach the WS endpoint.
    """
    backend = get_state_backend()
    set_store_backend(backend)
    # Step 8: drift detector -> event broker bridge.
    detector = get_detector()
    broker = get_broker()
    install_drift_alert_emitter(detector, broker)
    # Phase 7 Step 3: register the OTel sink if $PHOENIX_OTEL_ENABLED=1.
    # from_env() returns None when the flag is unset -- no opentelemetry
    # import attempted. The sink stays attached for the daemon lifetime;
    # reset_emitter() on shutdown drains + closes both JSONL and OTel.
    from phoenix.audit.otel_adapter import OTelExporter

    otel_sink = OTelExporter.from_env()
    if otel_sink is not None:
        get_emitter().add_sink(otel_sink)

    # Phase 7 Step 9: register the router intelligence's drift-feedback
    # callback as the second register_drift_callback caller (the first
    # was the verification gate in Phase 6b). Per-provider drift
    # multipliers shape estimated_fidelity per §4.6 Source C.
    from phoenix.router.intelligence import register_for_drift_updates

    register_for_drift_updates()
    try:
        yield
    finally:
        # reset_emitter flushes both the JSONL writer and the OTel
        # processor (and shuts down the OTel LoggerProvider) per
        # the AuditSink.close() contract.
        from phoenix.audit import reset_emitter

        reset_emitter()
        set_store_backend(None)
        reset_state_backend()
        reset_detector()


def _safe_audit_emit(
    *,
    layer: str,
    event_type: str,
    actor_id: str = "unknown",
    parameters: dict[str, Any] | None = None,
    result_hash: str = "",
    request_id: str | None = None,
) -> None:
    """Fire-and-forget audit emit that never raises.

    Step 2 wires audit emits across the API surface (middleware for
    REST, inline for WS connect/close). Per the Phase 7 Step 1
    contract, audit failures must never propagate into the request
    handling path -- this helper enforces that invariant.
    """
    try:
        get_emitter().emit(
            AuditEvent(
                timestamp_unix=time.time(),
                actor_id=actor_id,
                layer=layer,
                event_type=event_type,
                parameters=dict(parameters or {}),
                result_hash=result_hash,
                request_id=request_id,
            )
        )
    except Exception:
        # Last-resort: never let the audit emit take down the request.
        pass


app = FastAPI(
    title="Phoenix",
    description=(
        "Production-grade quantum-accuracy middleware "
        "(Phase 3 -- Solver + Control + Orchestrate path)"
    ),
    version=__version__,
    openapi_url="/v1/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Phase 7 Step 2: HTTP audit middleware -- emit api.request.* events for
# every REST request (entry + completion + error). The middleware also
# generates the canonical per-request UUID and stashes it on
# ``request.state.request_id`` so handlers can pass it through to
# ``verify_request`` for the safety-gate audit emit AND to the
# ``PhysicsTask.request_id`` field for downstream verification-gate /
# ledger correlation.
#
# WebSocket endpoints bypass HTTP middleware -- their inline emits are
# in each ``@app.websocket(...)`` handler.


@app.middleware("http")
async def _audit_http_middleware(request: Request, call_next: Any) -> Any:
    """Emit api.request.* audit events around every HTTP request."""
    request_id = f"req_{uuid.uuid4().hex}"
    request.state.request_id = request_id
    method = request.method
    path = str(request.url.path)
    start_time = time.time()

    _safe_audit_emit(
        layer="api",
        event_type="api.request.start",
        parameters={"method": method, "path": path},
        request_id=request_id,
    )

    try:
        response = await call_next(request)
    except Exception as exc:
        _safe_audit_emit(
            layer="api",
            event_type="api.request.error",
            parameters={
                "method": method,
                "path": path,
                "error_type": type(exc).__name__,
                "error_detail": str(exc),
                "duration_ms": (time.time() - start_time) * 1000.0,
            },
            request_id=request_id,
        )
        raise

    _safe_audit_emit(
        layer="api",
        event_type="api.request.complete",
        parameters={
            "method": method,
            "path": path,
            "status_code": response.status_code,
            "duration_ms": (time.time() - start_time) * 1000.0,
        },
        request_id=request_id,
    )
    return response


# ---------------------------------------------------------------------------
# Health probe (Phase 0)


@app.get("/v1/health")
def health() -> dict[str, Any]:
    """Liveness/readiness probe per architecture v1 Section 5.2.

    Phase 0 returns: phoenix version, vendor-manifest read result, a static
    ``calibration_status`` of ``"not_loaded"`` (drift monitoring lands in
    Phase 7), and the current UTC timestamp.
    """
    vendor = read_vendor_version()
    return {
        "status": "ok",
        "phoenix_version": __version__,
        "vendor_manifest": vendor,
        # Phase 0 placeholder; Phase 7 wires in drift monitoring.
        "calibration_status": "not_loaded",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# POST /v1/tasks (Phase 3 -- full Result envelope)


class PhysicsContextRequest(BaseModel):
    """JSON shape of the vendored ``PhysicsContext`` over the front door.

    Phase 2 + 3 expose only the fields needed for the QHO / TISE / TDSE
    benchmarks. Phase 4+ extends with the full PhysicsContext surface
    (spin, magnetic field, gravitational regime, custom Hamiltonian, ...)
    once the broader benchmark suite needs them.
    """

    mass_kg: float
    length_scale_m: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToleranceRequest(BaseModel):
    """JSON shape of :class:`ToleranceSpec` over the front door."""

    max_error_bar: float = 1e-3
    reproducibility_mode: str = "default"
    latency_tier: str = "batch_realtime"  # maps to LatencyTier enum value
    frontier_physics: bool = False


class SolveRequest(BaseModel):
    """JSON shape of a :class:`PhysicsTask` over the front door."""

    physics_context: PhysicsContextRequest
    tolerance: ToleranceRequest = Field(default_factory=ToleranceRequest)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _provenance_to_dict(provenance: Any) -> dict[str, Any]:
    """Flatten a :class:`ProvenanceTrace` into a JSON-serializable dict.

    Phase 3 ships solver + control + orchestrate sub-blocks plus the
    ``cloud_shots_recorded`` mirror flag. Internal axis-result objects
    (which carry ndarray data) are summarized via their stable
    ``axis_name`` + ``error_bar_contribution`` + ``metric`` (when present)
    rather than serialized fully -- the full audit-grade trace lives in
    Phase 7's ledger, not the front-door response.
    """
    if provenance is None:
        return {}

    out: dict[str, Any] = {
        "request_id": provenance.request_id,
        "cloud_shots_recorded": provenance.cloud_shots_recorded,
        # Phase 7 Step 6: ledger entry correlation key.
        # None when the verification gate's ledger composition failed
        # (StateBackend unavailable, etc.).
        "omega_ledger_entry_id": provenance.omega_ledger_entry_id,
    }

    if provenance.solver is not None:
        out["solver"] = {
            "request_id": provenance.solver.request_id,
            "dispatched_solver": provenance.solver.dispatched_solver,
            "n_grid_low": provenance.solver.n_grid_low,
            "n_grid_high": provenance.solver.n_grid_high,
            "wall_clock_ms_total": provenance.solver.wall_clock_ms_total,
            "phase": provenance.solver.phase,
            "axis_1_error_bar_contribution": (
                provenance.solver.cross_precision_axis_result.error_bar_contribution
                if provenance.solver.cross_precision_axis_result is not None
                else None
            ),
        }

    if provenance.control is not None:
        out["control"] = {
            "request_id": provenance.control.request_id,
            "dpd_n_blocks": provenance.control.dpd_n_blocks,
            "probe_strengths_used": provenance.control.probe_strengths_used,
            "total_backaction": provenance.control.total_backaction,
            "trace_preservation": provenance.control.trace_preservation,
            "positivity_check": provenance.control.positivity_check,
            "wall_clock_ms": provenance.control.wall_clock_ms,
            "phase": provenance.control.phase,
            "axis_2_error_bar_contribution": (
                provenance.control.cross_control_axis_result.error_bar_contribution
                if provenance.control.cross_control_axis_result is not None
                else None
            ),
            "axis_2_metric": (
                provenance.control.cross_control_axis_result.metadata.get("metric")
                if provenance.control.cross_control_axis_result is not None
                else None
            ),
        }

    if provenance.orchestrate is not None:
        out["orchestrate"] = {
            "request_id": provenance.orchestrate.request_id,
            "provider_id": provenance.orchestrate.provider_id,
            "backend_name": provenance.orchestrate.backend_name,
            "quantum_technology": provenance.orchestrate.quantum_technology,
            "shots_used": provenance.orchestrate.shots_used,
            "latency_us": provenance.orchestrate.latency_us,
            "bundle_hash": provenance.orchestrate.bundle_hash,
            "cloud_shots_recorded": provenance.orchestrate.cloud_shots_recorded,
            "wall_clock_ms": provenance.orchestrate.wall_clock_ms,
            "phase": provenance.orchestrate.phase,
        }

    return out


def _kpi_bundle_to_dict(bundle: Any) -> dict[str, Any]:
    """JSON shape for a :class:`KPIBundle` -- enum value as string."""
    return {
        "fidelity": bundle.fidelity,
        "latency_us": bundle.latency_us,
        "backaction": bundle.backaction,
        "shots_used": bundle.shots_used,
        "shot_budget": bundle.shot_budget,
        "status": bundle.status.value
        if isinstance(bundle.status, KPIStatus)
        else str(bundle.status),
    }


@app.post("/v1/tasks")
def submit_task(
    req: SolveRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Submit a physics task. Phase 3 returns the full :class:`Result` envelope.

    Phase 2 returned a Solver-only ``CandidateAnswer`` with a
    ``phase: phase_2_solver_only`` honesty marker. Phase 3 promotes to the
    architecturally-correct ``Result`` envelope: top-level ``value``,
    ``error_bar`` (quadrature-combined per Section 11.1.1 placeholder),
    ``sigma``, ``agreement_type``, ``kpi_bundle_orchestrate``, and a
    ``provenance`` block carrying solver + control + orchestrate
    sub-traces. The ``phase`` marker now reads
    ``phase_3_solver_control_orchestrate`` everywhere.

    Status code mapping:

    - 200: completed three-layer solve (full Result envelope).
    - 400: invalid latency_tier string OR no eligible solver (registry
      mismatch / invalid regime_hint).
    - 403: frontier-physics regime requested without
      ``frontier_physics=True`` opt-in (Decision 7).
    - 422: Control's DPD propagator raised
      :class:`ControlVerificationError` (trace drift > 1e-3 OR positivity
      violation). The detail names the offending values.
    - 501: latency tier defined-but-not-routable in this Phoenix release
      (STREAMING_REALTIME -> v2; PERCEPTION_REALTIME -> Phase 12+).
    - 502: Orchestrate raised :class:`OrchestrateProviderError` (provider
      submission failed). The detail names the provider_id + bundle_hash.
    """
    # Lazy import: the vendored synthesis package is only available after
    # phoenix.__init__ runs sys.path injection. Importing at module load
    # would break fresh clones before Phase 1 vendor sync.
    from synthesis.equations.base import PhysicsContext

    # Phase 7 Step 2: use the request_id set by the HTTP middleware so
    # downstream audit events (safety gate, verification gate, ledger
    # entry) all share the same correlation key.
    request_id: str = request.state.request_id

    # Phase 6a: Actor verification at the front door + safety gate.
    # Per locked scope (2026-05-08): Actor required with bootstrap-actor
    # fallback when the Authorization header is absent and the keystore
    # is present. Tests + dev-mode "just call /v1/tasks" preserved via
    # the bootstrap path; production callers send signed Actor headers.
    try:
        actor, _was_bootstrapped = extract_or_bootstrap(authorization)
    except IdentityError as exc:
        raise HTTPException(status_code=401, detail=f"identity error: {exc}") from exc

    # Section 7.4 9-stage safety gate. Determine the rung-cost label
    # from the user's max_error_bar (initial rung selection same as
    # the verification gate). Frontier-physics regime read from
    # task.metadata when present so the gate can do the authority
    # check at Section 7.4 step 6.
    initial_rung = select_initial_rung(req.tolerance.max_error_bar)
    requested_regime = req.metadata.get("regime_hint") if req.metadata else None
    try:
        verify_request(
            actor,
            action_key="tasks_submit",
            requires_capability="can_submit_tasks",
            rung_for_cost=initial_rung.name,
            requested_regime=str(requested_regime).upper() if requested_regime else None,
            task_frontier_physics_flag=req.tolerance.frontier_physics,
            request_id=request_id,
        )
    except KillSwitchEngaged as exc:
        raise HTTPException(
            status_code=503,
            detail=f"kill switch engaged: {exc} (engaged_by={exc.engaged_by})",
        ) from exc
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=f"auth error: {exc}") from exc
    except PermissionDenied as exc:
        raise HTTPException(
            status_code=403,
            detail=(
                f"permission denied: actor={exc.actor_name!r} lacks {exc.missing_capability!r}"
            ),
        ) from exc
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail=(
                f"rate limit exceeded: cost={exc.cost}, tokens_remaining="
                f"{exc.tokens_remaining:.2f}, retry_after_s={exc.retry_after_seconds:.1f}"
            ),
            headers={"Retry-After": str(int(exc.retry_after_seconds + 1))},
        ) from exc
    except FrontierPhysicsRefused as exc:
        # Section 7.4 step 6: frontier-physics authority check at the
        # safety gate (earlier than the Phase 2 engine-boundary check).
        # Same HTTP 403 status code; the gate-layer rejection has a
        # more specific actor-level message.
        raise HTTPException(
            status_code=403,
            detail=f"frontier-physics regime {exc.regime_name!r} refused: {exc}",
        ) from exc

    # Map latency_tier string to enum (400 on bad value).
    try:
        tier = LatencyTier(req.tolerance.latency_tier)
    except ValueError as exc:
        valid = [t.value for t in LatencyTier]
        raise HTTPException(
            status_code=400,
            detail=(f"unknown latency_tier {req.tolerance.latency_tier!r}; valid values: {valid}"),
        ) from exc

    ctx = PhysicsContext(
        mass_kg=req.physics_context.mass_kg,
        length_scale_m=req.physics_context.length_scale_m,
        metadata=req.physics_context.metadata,
    )
    tolerance = ToleranceSpec(
        max_error_bar=req.tolerance.max_error_bar,
        reproducibility_mode=req.tolerance.reproducibility_mode,
        latency_tier=tier,
        frontier_physics=req.tolerance.frontier_physics,
    )
    # request_id already pulled from request.state.request_id above (Phase 7 Step 2).
    task = PhysicsTask(
        physics_context=ctx,
        tolerance=tolerance,
        actor=actor,  # Phase 6a wires verified Actor (bootstrap or signed).
        request_id=request_id,
        metadata=dict(req.metadata),
    )

    try:
        result = solve(task)
    except LatencyTierNotImplemented as exc:
        raise HTTPException(
            status_code=501,
            detail=f"latency tier {exc.tier.value!r} not yet supported: {exc}",
        ) from exc
    except FrontierPhysicsRefused as exc:
        raise HTTPException(
            status_code=403,
            detail=(f"frontier-physics regime {exc.regime_name!r} refused: {exc}"),
        ) from exc
    except NoEligibleSolverError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"no eligible solver: {exc}",
        ) from exc
    except ControlVerificationError as exc:
        # DPD trace/positivity violation. Section 7 fail-closed posture.
        raise HTTPException(
            status_code=422,
            detail=(
                f"control verification failed: {exc} "
                f"(trace_preservation={exc.trace_preservation:.6f}, "
                f"positivity_check={exc.positivity_check})"
            ),
        ) from exc
    except OrchestrateProviderError as exc:
        # Provider submission failed; Phase 4's Router failover protocol
        # (Section 4.5) will eventually catch this for retry on alternates.
        raise HTTPException(
            status_code=502,
            detail=(
                f"provider submission failed: {exc} "
                f"(provider_id={exc.provider_id}, bundle_hash={exc.bundle_hash})"
            ),
        ) from exc

    # The agreement_type is a vendored DisagreementType enum; serialize the
    # value (a stable string).
    agreement_type_value = (
        result.agreement_type.value
        if hasattr(result.agreement_type, "value")
        else str(result.agreement_type)
    )

    return {
        "task_id": request_id,
        "status": "completed",
        "phase": "phase_5_verification_gate",
        "value": float(result.value),
        "error_bar": float(result.error_bar),
        "sigma": float(result.sigma),
        "agreement_type": agreement_type_value,
        "kpi_bundle_orchestrate": _kpi_bundle_to_dict(result.kpi_bundle_orchestrate),
        "provenance": _provenance_to_dict(result.provenance),
        "reproducibility_asterisk": (
            "Phase 3 ships the local-simulator path through all three Trinity "
            "Core subsystems (Solver + Control + Orchestrate). Cross-provider "
            "verification (Axis 3, the third wobble axis comparing cloud and "
            "simulator) lands in Phase 5 alongside the rung table. Cloud "
            "quantum providers (IBM, Braket, IonQ) land in Phase 4. The "
            "reproducibility asterisk per Section 1 Decision 20 reads "
            "`cloud_shots_recorded` on the provenance trace; Phase 3's "
            "local-simulator path has `cloud_shots_recorded=False`."
        ),
    }


# ---------------------------------------------------------------------------
# Phase 7 Step 10: GET /v1/audit/events + GET /v1/audit/ledger/verify
#
# Per architecture v1 Section 5.2: read-only audit endpoints. Both
# require any authenticated actor (no special capability); rate-limited
# at the standard "tasks_get" cost. Phase 8's /v1/admin/ledger/integrity-
# report layers an admin-only fuller report on top.


@app.get("/v1/audit/events")
def get_audit_events(
    request: Request,
    since_unix: float = 0.0,
    limit: int = 100,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """List audit events with ``timestamp_unix >= since_unix``,
    ordered ascending, up to ``limit`` rows.

    Per architecture v1 Section 5.2: reads from the StateBackend's
    ``audit_events`` table (Phase 6b shape). Authentication via the
    safety gate; no special capability required beyond a recognized
    Actor. Cost = 1 token per request (action_key=``tasks_get``).

    Query params:
      - ``since_unix`` (float, default 0): only events at or after
        this unix timestamp.
      - ``limit`` (int, default 100, max 1000): cap on rows returned.

    Status codes:
      - 200: success; body is ``{"events": [...], "count": N}``.
      - 401: missing / bad Actor signature.
      - 429: rate-limited.
      - 503: kill switch engaged.
    """
    request_id: str = request.state.request_id
    try:
        actor, _ = extract_or_bootstrap(authorization)
    except IdentityError as exc:
        raise HTTPException(status_code=401, detail=f"identity error: {exc}") from exc
    try:
        verify_request(
            actor,
            action_key="tasks_get",
            request_id=request_id,
        )
    except KillSwitchEngaged as exc:
        raise HTTPException(status_code=503, detail=f"kill switch engaged: {exc}") from exc
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=f"auth error: {exc}") from exc
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(int(exc.retry_after_seconds + 1))},
        ) from exc

    capped_limit = max(1, min(int(limit), 1000))
    rows = get_state_backend().list_audit_events(
        since_unix=float(since_unix),
        limit=capped_limit,
    )
    return {"events": rows, "count": len(rows)}


@app.get("/v1/audit/ledger/verify")
def verify_ledger(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Verify the Omega Ledger hashchain integrity.

    Returns BOTH integrity reports:

    - ``sql_structural`` -- the SQL window-function walk from
      :meth:`StateBackend.verify_ledger_integrity`. Catches structural
      breaks (deleted rows, parent_hash rewritten in the chain) using
      a single ``LAG``-CTE query.
    - ``python_crypto`` -- the Python crypto walk from
      :meth:`OmegaLedger.verify_chain`. Recomputes SHA-256 per row;
      catches cryptographic tampering of ``payload_json`` that the
      SQL check can't see.

    A clean chain has ``valid=True`` on both checks. Mismatches name
    the first broken entry_id + a human-readable reason for ops triage.

    Status codes:
      - 200: success (whether or not the chain is valid -- the body
        carries the report).
      - 401 / 429 / 503: same safety-gate handling as
        :func:`get_audit_events`.
    """
    request_id: str = request.state.request_id
    try:
        actor, _ = extract_or_bootstrap(authorization)
    except IdentityError as exc:
        raise HTTPException(status_code=401, detail=f"identity error: {exc}") from exc
    try:
        verify_request(
            actor,
            action_key="tasks_get",
            request_id=request_id,
        )
    except KillSwitchEngaged as exc:
        raise HTTPException(status_code=503, detail=f"kill switch engaged: {exc}") from exc
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=f"auth error: {exc}") from exc
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(int(exc.retry_after_seconds + 1))},
        ) from exc

    # Lazy import: phoenix.ledger imports vendor.omega which resolves
    # via the sys.path injection in phoenix/__init__.py at first call.
    from phoenix.ledger import get_ledger

    sql_report = get_state_backend().verify_ledger_integrity()
    python_report = get_ledger().verify_chain()
    return {
        "sql_structural": sql_report,
        "python_crypto": {
            "valid": python_report.valid,
            "entries_checked": python_report.entries_checked,
            "first_broken_entry_id": python_report.first_broken_entry_id,
            "reason": python_report.reason,
        },
    }


# ---------------------------------------------------------------------------
# Phase 7 Step 8: POST /v1/tasks/{task_id}/replay


@app.post("/v1/tasks/{task_id}/replay")
def replay_task(
    task_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Re-run a previously-sealed task and verify the result hash.

    Per architecture v1 Section 5.2 line 956 + Section 1 Decisions
    19-21: the replay path reads the ledger entry for ``task_id``,
    restores the captured deterministic environment, re-runs the
    pipeline, and compares the recomputed ``result_hash`` against
    the recorded value.

    Authorization: requires ``can_replay_tasks`` permission. Rate-
    limited at ``replay`` cost.

    Status code mapping:

    - 200: replay succeeded; response carries the
      :class:`ReplayReport` shape.
    - 401: bad/missing actor signature.
    - 403: actor lacks ``can_replay_tasks``.
    - 404: no ledger entry has ``payload.task_id == task_id``.
    - 409: entry is incomplete (no ``task_spec`` /
      ``environment_snapshot``, or ``reproducibility_mode="default"``),
      OR the original used cloud shots Phoenix v1 cannot re-fetch.
    - 429: rate-limit exceeded.
    - 500: replay completed but the recomputed result_hash diverged
      from the recorded value (:class:`ReplayDivergence`).
    - 503: kill switch engaged.
    """
    request_id: str = request.state.request_id

    try:
        actor, _ = extract_or_bootstrap(authorization)
    except IdentityError as exc:
        raise HTTPException(status_code=401, detail=f"identity error: {exc}") from exc

    try:
        verify_request(
            actor,
            action_key="tasks_replay",
            requires_capability="can_replay_tasks",
            request_id=request_id,
        )
    except KillSwitchEngaged as exc:
        raise HTTPException(status_code=503, detail=f"kill switch engaged: {exc}") from exc
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=f"auth error: {exc}") from exc
    except PermissionDenied as exc:
        raise HTTPException(
            status_code=403,
            detail=(
                f"permission denied: actor={exc.actor_name!r} lacks {exc.missing_capability!r}"
            ),
        ) from exc
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(int(exc.retry_after_seconds + 1))},
        ) from exc

    # Lazy import: phoenix.ledger imports vendor.omega via sys.path
    # injection that phoenix/__init__.py runs at module load. The
    # FastAPI route is registered at import time, so we keep the
    # heavy imports inside the handler.
    from phoenix.ledger import (
        LedgerEntryNotFound,
        ReplayDivergence,
        ReplayEntryIncomplete,
        ReplayProviderUnavailable,
        replay,
    )

    try:
        report = replay(task_id)
    except LedgerEntryNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ReplayEntryIncomplete, ReplayProviderUnavailable) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ReplayDivergence as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "replay_divergence",
                "message": str(exc),
                "task_id": exc.task_id,
                "original_entry_id": exc.original_entry_id,
                "original_result_hash": exc.original_result_hash,
                "replayed_result_hash": exc.replayed_result_hash,
                "divergent_layer": exc.divergent_layer,
            },
        ) from exc

    return {
        "task_id": report.task_id,
        "original_entry_id": report.original_entry_id,
        "original_result_hash": report.original_result_hash,
        "replayed_result_hash": report.replayed_result_hash,
        "hashes_match": report.hashes_match,
        "divergent_layer": report.divergent_layer,
        "wall_clock_ms": report.wall_clock_ms,
    }


# ---------------------------------------------------------------------------
# Phase 6a: identity + WebSocket endpoints


class WSTokenRequest(BaseModel):
    """JSON shape of POST /v1/identity/ws-token request body."""

    # Phase 6a accepts an empty body and uses the Authorization header
    # for actor verification (matches the same /v1/tasks pattern).
    pass


@app.post("/v1/identity/ws-token")
def ws_token(
    request: Request,
    _req: WSTokenRequest | None = None,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Mint a single-use bearer token for opening a WebSocket connection.

    Per architecture v1 Section 5.3: the WS handshake uses a short-
    lived bearer token (60-second window, single-use) rather than the
    full Actor signature. Phase 6a's safety gate enforces the
    ``can_submit_tasks`` capability for this endpoint (it's a free
    endpoint by Section 7.5 cost catalogue but ``ws_token`` is keyed
    to 1 token).
    """
    request_id: str = request.state.request_id  # Phase 7 Step 2
    try:
        actor, _ = extract_or_bootstrap(authorization)
    except IdentityError as exc:
        raise HTTPException(status_code=401, detail=f"identity error: {exc}") from exc
    try:
        verify_request(
            actor,
            action_key="ws_token",
            requires_capability="can_submit_tasks",
            request_id=request_id,
        )
    except KillSwitchEngaged as exc:
        raise HTTPException(status_code=503, detail=f"kill switch engaged: {exc}") from exc
    except (AuthError, IdentityError) as exc:
        raise HTTPException(status_code=401, detail=f"auth error: {exc}") from exc
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(int(exc.retry_after_seconds + 1))},
        ) from exc
    token = get_ws_token_store().mint(actor.name)
    return {
        "token": token,
        "actor": actor.name,
        "expires_in_seconds": 60,
        "single_use": True,
    }


@app.websocket("/v1/ws/tasks/{task_id}/stream")
async def task_stream(websocket: WebSocket, task_id: str, token: str | None = None) -> None:
    """Stream verification-gate events for a single task.

    Per architecture v1 Section 5.3: the client opens this WS after
    minting a bearer token via POST /v1/identity/ws-token. Token is
    passed as the ``token`` query parameter (Phase 6a; the
    Authorization header path is harder to test with FastAPI's
    TestClient, so query param is the v1 contract for the simple
    case). Token is consumed on connect (single-use).

    Phase 6a streams events from the in-memory broker buffer in a
    polling loop (100ms cadence). Phase 6b's NATS JetStream consumer
    replaces this with a push-based subscription.

    Close codes:
    - 1000 (normal): task completed (task.complete event sent).
    - 1008 (policy violation): token invalid / expired / used.
    """
    # Phase 7 Step 2: WS handlers bypass HTTP middleware, so mint a
    # fresh per-connection request_id here for audit correlation.
    request_id = f"req_{uuid.uuid4().hex}"
    if not token:
        _safe_audit_emit(
            layer="api",
            event_type="api.ws.connect_rejected",
            parameters={
                "path": "/v1/ws/tasks/{task_id}/stream",
                "task_id": task_id,
                "reason": "missing_token",
            },
            request_id=request_id,
        )
        await websocket.close(code=1008, reason="missing token query parameter")
        return
    try:
        actor_name = get_ws_token_store().consume(token)
    except WSTokenError as exc:
        _safe_audit_emit(
            layer="api",
            event_type="api.ws.connect_rejected",
            parameters={
                "path": "/v1/ws/tasks/{task_id}/stream",
                "task_id": task_id,
                "reason": "invalid_token",
                "error_detail": str(exc),
            },
            request_id=request_id,
        )
        await websocket.close(code=1008, reason=str(exc))
        return

    await websocket.accept()
    _safe_audit_emit(
        layer="api",
        event_type="api.ws.connect_accepted",
        actor_id=actor_name,
        parameters={
            "path": "/v1/ws/tasks/{task_id}/stream",
            "task_id": task_id,
        },
        request_id=request_id,
    )
    broker = get_broker()
    cursor = 0
    # Phase 6a poll-based stream: bounded loop so a long-disconnected
    # task doesn't keep the WS open forever. v1.x adjusts to push.
    max_iterations = 6000  # 6000 * 0.1s = 600s = 10 min timeout
    close_code = 1000
    close_reason = "stream poll timeout"
    try:
        for _ in range(max_iterations):
            new_events = broker.get_events(task_id, since_index=cursor)
            for event in new_events:
                await websocket.send_json(to_dict(event))
                cursor += 1
                if event.type in ("task.complete", "task.failed"):
                    close_reason = "task finished"
                    await websocket.close(code=1000, reason=close_reason)
                    return
            await asyncio.sleep(0.1)
        # Polling timeout reached without task.complete; close gracefully.
        await websocket.close(code=close_code, reason=close_reason)
    except WebSocketDisconnect:
        # Client disconnected; nothing to clean up (broker buffer
        # remains for any future reconnect).
        close_code = 1006
        close_reason = "client disconnect"
        return
    finally:
        _safe_audit_emit(
            layer="api",
            event_type="api.ws.closed",
            actor_id=actor_name,
            parameters={
                "path": "/v1/ws/tasks/{task_id}/stream",
                "task_id": task_id,
                "close_code": close_code,
                "close_reason": close_reason,
                "events_streamed": cursor,
            },
            request_id=request_id,
        )


# ---------------------------------------------------------------------------
# Phase 6b Step 8: /v1/ws/calibration/drift -- ops-dashboard drift alerts


@app.websocket("/v1/ws/calibration/drift")
async def calibration_drift_stream(
    websocket: WebSocket,
    token: str | None = None,
) -> None:
    """Stream drift-alert events to ops dashboards.

    Per architecture v1 Section 5.3 line 995: emits a ``drift.alert``
    event each time the :class:`DriftDetector` reports a state
    transition (healthy <-> warning <-> high_confidence_warning).
    The detector's cadence is 6h by default (Decision 17), so events
    are sparse; the WS poll cadence (250ms) is set for ops-dashboard
    responsiveness when alerts DO fire, not throughput.

    **Authentication** matches ``/v1/ws/tasks/.../stream``: client
    mints a bearer token via ``POST /v1/identity/ws-token`` (which
    accepts the bootstrap-actor fallback per Phase 6a Decision 4 +
    Phase 6b open-item 7 locked 2026-05-10), passes it as the
    ``token`` query parameter, token is consumed on connect
    (single-use, 60s window).

    **Event format**: serialized :class:`TaskEvent` JSON where
    ``task_id`` is the constant ``"phoenix.drift.alerts"`` channel
    name, ``type`` is ``"drift.alert"``, and ``payload`` carries
    ``{"from_state", "to_state", "firing_detectors",
    "detector_summaries"}``.

    **Backlog replay** on connect: the WS handler starts at cursor 0
    so a client reconnecting mid-day sees all alerts the in-memory
    broker still has buffered (capped at 1000 events). The NATS
    broker mode honors the per-locked-OPEN-4 10-minute MAX_AGE on
    the durable drift-alerts stream.

    Close codes:

    - 1000 (normal): poll-loop timeout reached (~8 hours).
    - 1008 (policy violation): token missing / invalid / expired /
      already consumed.
    """
    # Phase 7 Step 2: WS handlers bypass HTTP middleware, so mint a
    # fresh per-connection request_id here for audit correlation.
    request_id = f"req_{uuid.uuid4().hex}"
    if not token:
        _safe_audit_emit(
            layer="api",
            event_type="api.ws.connect_rejected",
            parameters={
                "path": "/v1/ws/calibration/drift",
                "reason": "missing_token",
            },
            request_id=request_id,
        )
        await websocket.close(code=1008, reason="missing token query parameter")
        return
    try:
        actor_name = get_ws_token_store().consume(token)
    except WSTokenError as exc:
        _safe_audit_emit(
            layer="api",
            event_type="api.ws.connect_rejected",
            parameters={
                "path": "/v1/ws/calibration/drift",
                "reason": "invalid_token",
                "error_detail": str(exc),
            },
            request_id=request_id,
        )
        await websocket.close(code=1008, reason=str(exc))
        return

    await websocket.accept()
    _safe_audit_emit(
        layer="api",
        event_type="api.ws.connect_accepted",
        actor_id=actor_name,
        parameters={"path": "/v1/ws/calibration/drift"},
        request_id=request_id,
    )
    broker = get_broker()
    cursor = 0
    poll_seconds = 0.25
    # ~8h loop bound; clients should auto-reconnect for longer sessions.
    # Per Decision 17 drift cycles are minutes-scale work but state
    # transitions are sparse, so this just bounds runaway connections.
    max_iterations = int(8 * 60 * 60 / poll_seconds)
    close_code = 1000
    close_reason = "stream poll timeout"
    try:
        for _ in range(max_iterations):
            new_events = broker.get_events(DRIFT_ALERTS_CHANNEL, since_index=cursor)
            for event in new_events:
                await websocket.send_json(to_dict(event))
                cursor += 1
            await asyncio.sleep(poll_seconds)
        await websocket.close(code=close_code, reason=close_reason)
    except WebSocketDisconnect:
        # Client disconnected; broker buffer remains for any future
        # reconnect (subject to the buffer cap / NATS retention).
        close_code = 1006
        close_reason = "client disconnect"
        return
    finally:
        _safe_audit_emit(
            layer="api",
            event_type="api.ws.closed",
            actor_id=actor_name,
            parameters={
                "path": "/v1/ws/calibration/drift",
                "close_code": close_code,
                "close_reason": close_reason,
                "events_streamed": cursor,
            },
            request_id=request_id,
        )
