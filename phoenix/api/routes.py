"""Phoenix v1 -- front-door REST surface.

Phase 0 shipped ``/v1/health`` only. Phase 2 adds ``POST /v1/tasks`` --
the Solver-only path through Trinity Core's pipeline. Per architecture v1
Section 5.2 the full surface lands across later phases:

- Tasks endpoints -- Phase 2 ships ``POST /v1/tasks`` (Solver-only). The
  list / get / replay / approve_promotion / cancel endpoints land in
  Phase 3+ once Control + Orchestrate are wired and the ledger backs them.
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
from phoenix.trinity.data_model import PhysicsTask, ToleranceSpec
from phoenix.trinity.pipeline import solve
from phoenix.trinity.solver.engine import (
    FrontierPhysicsRefused,
    NoEligibleSolverError,
)

app = FastAPI(
    title="Phoenix",
    description="Production-grade quantum-accuracy middleware (Phase 2 -- Solver-only path)",
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
# POST /v1/tasks (Phase 2 -- Solver-only)


class PhysicsContextRequest(BaseModel):
    """JSON shape of the vendored ``PhysicsContext`` over the front door.

    Phase 2 exposes only the fields needed for the QHO / TISE / TDSE
    benchmarks. Phase 3+ extends with the full PhysicsContext surface
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


@app.post("/v1/tasks")
def submit_task(req: SolveRequest) -> dict[str, Any]:
    """Submit a physics task. Phase 2 returns the Solver-only output.

    Phase 3 promotes the response shape to the full ``Result`` envelope
    (``value`` + ``error_bar`` + ``sigma`` + ``agreement_type`` + full
    :class:`ProvenanceTrace`). Phase 2 is honest about its incomplete
    state via the ``phase: phase_2_solver_only`` markers everywhere and
    the ``reproducibility_asterisk`` in the response body.

    Status code mapping (Phase 2):

    - 200: completed Solver-only solve (CandidateAnswer payload).
    - 400: invalid latency_tier string OR no eligible solver (registry
      mismatch / invalid regime_hint).
    - 403: frontier-physics regime requested without
      ``frontier_physics=True`` opt-in (Decision 7).
    - 501: latency tier defined-but-not-routable in this Phoenix release
      (STREAMING_REALTIME -> v2; PERCEPTION_REALTIME -> Phase 12+).
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
        candidate = solve(task)
    except LatencyTierNotImplemented as exc:
        # Defined-but-not-routable tier: tell the client which release
        # ships support, not just "not implemented".
        raise HTTPException(
            status_code=501,
            detail=f"latency tier {exc.tier.value!r} not yet supported: {exc}",
        ) from exc
    except FrontierPhysicsRefused as exc:
        # Capability-gated regime requested without permission. Phase 6
        # adds the safety-gate Actor capability check above this layer;
        # Phase 2 returns 403 directly from the engine boundary.
        raise HTTPException(
            status_code=403,
            detail=(f"frontier-physics regime {exc.regime_name!r} refused: {exc}"),
        ) from exc
    except NoEligibleSolverError as exc:
        # Bad regime_hint or broken/partial registry. 400 on caller-input
        # cause; 500 would otherwise propagate via FastAPI default handler.
        raise HTTPException(
            status_code=400,
            detail=f"no eligible solver: {exc}",
        ) from exc

    return {
        "task_id": request_id,
        "status": "completed_solver_only",
        "phase": "phase_2_solver_only",
        "candidate_answer": {
            "solver_id": candidate.solver_id,
            "value": float(candidate.value),
            "error_bar_solver": candidate.error_bar_solver,
            "sigma_solver": candidate.sigma_solver,
            "solver_kpi_bundle": candidate.solver_kpi_bundle,
        },
        "provenance": {
            "request_id": (
                candidate.provenance_solver.request_id
                if candidate.provenance_solver is not None
                else request_id
            ),
            "dispatched_solver": (
                candidate.provenance_solver.dispatched_solver
                if candidate.provenance_solver is not None
                else candidate.solver_id
            ),
            "n_grid_low": (
                candidate.provenance_solver.n_grid_low
                if candidate.provenance_solver is not None
                else None
            ),
            "n_grid_high": (
                candidate.provenance_solver.n_grid_high
                if candidate.provenance_solver is not None
                else None
            ),
            "wall_clock_ms_total": (
                candidate.provenance_solver.wall_clock_ms_total
                if candidate.provenance_solver is not None
                else None
            ),
            "phase": (
                candidate.provenance_solver.phase
                if candidate.provenance_solver is not None
                else "phase_2_solver_only"
            ),
        },
        "reproducibility_asterisk": (
            "Phase 2 ships Solver-only output. Control (DPD) + Orchestrate "
            "(verification gate) land in Phase 3, at which point this "
            "endpoint returns a full Result envelope with combined "
            "error_bar + sigma + agreement_type."
        ),
    }
