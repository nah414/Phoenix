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

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from phoenix._internal.latency import LatencyTier, LatencyTierNotImplemented
from phoenix._internal.version import __version__, read_vendor_version
from phoenix.trinity.control.engine import ControlVerificationError
from phoenix.trinity.data_model import PhysicsTask, ToleranceSpec
from phoenix.trinity.orchestrate.kpi_bundle import KPIStatus
from phoenix.trinity.orchestrate.provider_client import OrchestrateProviderError
from phoenix.trinity.pipeline import solve
from phoenix.trinity.solver.engine import (
    FrontierPhysicsRefused,
    NoEligibleSolverError,
)

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
)


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
def submit_task(req: SolveRequest) -> dict[str, Any]:
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

    # Map latency_tier string to enum (400 on bad value).
    try:
        tier = LatencyTier(req.tolerance.latency_tier)
    except ValueError as exc:
        valid = [t.value for t in LatencyTier]
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown latency_tier {req.tolerance.latency_tier!r}; " f"valid values: {valid}"
            ),
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
    request_id = f"req_{uuid.uuid4().hex}"
    task = PhysicsTask(
        physics_context=ctx,
        tolerance=tolerance,
        actor=None,  # Phase 6 wires Actor verification at the front door.
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
