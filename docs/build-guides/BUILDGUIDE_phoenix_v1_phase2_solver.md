# BUILDGUIDE — Phoenix v1 Phase 2: Solver wiring through Trinity Core pipeline

**Status:** DRAFT — under active design with Adam.
**Authoritative location:** `C:\Phoenix\BUILDGUIDE_phoenix_v1_phase2_solver.md`
**Architectural reference:** `C:\Phoenix\PHOENIX_ARCHITECTURE_v1.md` (v1.1 + 2026-05-08 follow-up)
**Phase scope:** Phase 2 only. Phase 3 (Control + Orchestrate) is a separate build guide.
**Date opened:** 2026-05-08.
**Author of record:** Adam (with Claude as design partner).

---

## 0 — What this build guide is

Phase 2's job is to wire Trinity Core's **Solver subsystem** through Phoenix's pipeline for the first time. Phase 1 vendored the substrate; Phase 2 makes it executable from the front door. End-to-end at the end of Phase 2: a user POSTs a `PhysicsTask` to `/v1/tasks`, Phoenix dispatches to the right vendored solver, runs cross-precision wobble (Axis 1), returns a typed `CandidateAnswer` with `error_bar_solver` and full provenance.

**Phase 2's definition of done:**
- `phoenix/trinity/data_model.py` ships the four typed dataclasses (`PhysicsTask`, `CandidateAnswer`, `VerifiedAnswer`, `Result`) from architecture §2.2.
- `phoenix/_internal/latency.py` ships the `LatencyTier` enum from the v1.1 follow-up Section 1 paragraph (BATCH_REALTIME routable; the other two raise `LatencyTierNotImplemented`).
- `phoenix/verification/wobble_axis.py` ships the `WobbleAxis` Protocol contract from the v1.1 follow-up §6.3 paragraph + the first concrete impl (`CrossPrecisionAxis`).
- `phoenix/trinity/solver/engine.py` adapts the vendored `EquationSolver` registry into Trinity's pipeline; uses `HamiltonianClassifier::can_handle()` for solver auto-selection.
- `phoenix/trinity/solver/cross_precision.py` houses the cross-precision wobble logic (called by `CrossPrecisionAxis`).
- `phoenix/trinity/pipeline.py` is the early three-subsystem orchestrator (Solver-only path; Control + Orchestrate are skipped with explicit `phase: phase_2_solver_only` provenance markers).
- `phoenix/api/routes.py` gains `POST /v1/tasks` that accepts a `PhysicsTask` JSON body and runs the pipeline.
- `tests/integration/test_solve_endpoint.py` exercises end-to-end: POST a QHO task, get back a CandidateAnswer with `error_bar_solver` and ground-state energy within `max_error_bar`.
- Pre-commit hooks (ruff, ruff-format, mypy strict, smoke test) all pass.
- Phoenix-the-package version bumps `1.0.0.dev1` → `1.0.0.dev2`.

**This guide does NOT cover:**
- Trinity Core's Control subsystem (Phase 3).
- Trinity Core's Orchestrate subsystem (Phase 3 — greenfield).
- The verification gate's full rung table + promotion logic (Phase 5).
- Provider routing (Phase 4).
- Safety gate, state backend, queue (Phase 6).
- Audit log + Omega Ledger + drift monitor (Phase 7).
- Admin endpoints (Phase 8).
- LoRA adapters, MCP server, CLI commands (Phase 9).
- OTel adapter, cloud seams concrete impls, standalone binary (Phase 10).
- Final §10.7 acceptance + release (Phase 11).

The Phase 2 endpoint deliberately returns a `CandidateAnswer` (Solver-only output) rather than a full `Result`. The endpoint's response carries an explicit `phase: phase_2_solver_only` provenance marker so consumers know they're getting Solver-only output. Phase 3 promotes the endpoint to return a full `Result` with Control + Orchestrate contributions.

## 1 — Prerequisites

Before starting Phase 2:

1. **Phase 1 acceptance.** All Phase 1 commits on `origin/main`. `python -c "from synthesis.equations.base import EquationSolver"` resolves from `vendor/`. `pytest tests/tier1/` passes 5/5.
2. **v1.1 architecture revision + 2026-05-08 follow-up landed.** `PHOENIX_ARCHITECTURE_v1.md` has §6.3's `WobbleAxis` paragraph, §10.3.1's generic `CloudSeams` registry, §1's `LatencyTier` enum after Decision 28. Phase 2 cites these.
3. **`PHOENIX_ADMIN_OVERRIDE` no longer needed for Phase 2.** That gate was specifically for `vendor_sync.py`. Phase 2 doesn't write to `vendor/`.
4. **No new runtime deps.** Phase 1 added `numpy`, `scipy`, `pyyaml`. Phase 2 uses those.
5. **Working tree clean.** `git status` reports clean before Step 1 begins.
6. **No OneDrive paths.** Adam's standing rule.

## 2 — Phase-gate review protocol

Phase 2 has **seven steps** (Section 3.1 through 3.7). Each step ends with the standard stop gate:

```
=== STEP N COMPLETE — AWAITING ADAM REVIEW ===
```

Same discipline as Phases 0 and 1. No advancement past a stop gate without explicit Adam approval.

**Standing rule from Phase 0/1 carried forward:** If a step reveals an architectural ambiguity not resolved by the v1.1 spec, mark it `[OPEN: ...]` and surface to Adam — do not silently invent a resolution.

## 3 — Phase 2 deliverables

### 3.1 — Step 1: Trinity Core data model + `LatencyTier` enum

**What lands:**
- `phoenix/_internal/latency.py` — the `LatencyTier` enum from the v1.1 follow-up (§1 post-Decision-28 paragraph). Three values: `BATCH_REALTIME` (routable), `STREAMING_REALTIME` (defined-but-not-routable), `PERCEPTION_REALTIME` (defined-but-not-routable). Plus `LatencyTierNotImplemented` typed exception.
- `phoenix/trinity/data_model.py` — the four Trinity Core typed dataclasses per architecture §2.2:
  - `PhysicsTask` — input. Fields per §2.2: `physics_context: PhysicsContext` (vendored from `synthesis.equations.base`), `tolerance: ToleranceSpec` (max_error_bar + reproducibility_mode + latency_tier + frontier_physics flag), `actor: Actor` (vendored from `actor.actor`; Phase 6 wires Actor verification, Phase 2 just types the field), `request_id: str` (UUID v7 string from front door), `metadata: dict[str, Any]`.
  - `CandidateAnswer` — what Solver produces. Fields per §2.2: `solver_id: str`, `value: Any`, `error_bar_solver: float` (from cross-precision wobble), `sigma_solver: float`, `solver_kpi_bundle: dict[str, Any]` (placeholder until Control's `KPIBundle` lands in Phase 3), `provenance_solver: SolverProvenance`.
  - `VerifiedAnswer` — what Control will produce. Fields per §2.2 (defined in Phase 2 for forward-compat; populated in Phase 3): `rho_verified: np.ndarray`, `dpd_result: Any` (Phase 3 imports the vendored DPDResult), `kpi_bundle_control: dict[str, Any]`, `error_bar_control: float`, `probe_strengths_used: list[float]`.
  - `Result` — final output. Fields per §2.2: `value: Any`, `error_bar: float`, `sigma: float`, `agreement_type: DisagreementType` (from vendored `wobble.disagreement_types`; note the post-2026-05-08 spec rename — was `AgreementType`), `kpi_bundle_orchestrate: dict[str, Any]`, `provenance: ProvenanceTrace`.
- `phoenix/trinity/data_model.py` also defines `ToleranceSpec`, `SolverProvenance`, `ProvenanceTrace` as supporting dataclasses.

**`LatencyTier` enum (verbatim from architecture §1 follow-up):**

```python
# phoenix/_internal/latency.py

from __future__ import annotations

from enum import Enum


class LatencyTier(Enum):
    """Latency tier each Phoenix solve commits to."""

    BATCH_REALTIME = "batch_realtime"
    """v1: 10-100 ms loops on local hardware; 100 ms-1 s for cloud-orchestrated."""

    STREAMING_REALTIME = "streaming_realtime"
    """v2: sub-millisecond loops, standing-computation API. Defined-but-not-routable in v1."""

    PERCEPTION_REALTIME = "perception_realtime"
    """v1.1 perception phase: sub-100 ms hard real-time per sensor frame.
    Defined-but-not-routable until Phase 12+."""


class LatencyTierNotImplemented(Exception):
    """Raised when a non-`BATCH_REALTIME` tier is requested in v1."""

    def __init__(self, tier: LatencyTier, reason: str):
        super().__init__(reason)
        self.tier = tier
```

**`PhysicsTask`, `CandidateAnswer`, `VerifiedAnswer`, `Result`** all decorated `@dataclass(frozen=True)` for immutability per Phoenix's defensive-typing discipline. Forward-compat fields (e.g. `VerifiedAnswer`'s `dpd_result: Any`) get tightened in Phase 3 when the actual types land.

**Verification:**

```powershell
python -c "from phoenix._internal.latency import LatencyTier, LatencyTierNotImplemented; print('OK')"
python -c "from phoenix.trinity.data_model import PhysicsTask, CandidateAnswer, VerifiedAnswer, Result; print('OK')"
pytest tests/ -v   # existing 19 tests should still pass
```

```
=== STEP 1 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.2 — Step 2: `WobbleAxis` Protocol + first concrete impl skeleton

**What lands:**
- `phoenix/verification/wobble_axis.py` — the `WobbleAxis` Protocol per architecture §6.3 (the v1.1 follow-up paragraph). Plus `AxisResult` dataclass (the row each axis adds to the distance matrix) and `RungDepth` enum (R1 through R5; full rung-table semantics land in Phase 5, but Phase 2 needs the enum so axis impls can accept a depth parameter).
- `phoenix/verification/__init__.py` already exists (Phase 0 skeleton); Phase 2 adds the imports.
- A *skeleton* `CrossPrecisionAxis` class implementing the Protocol — `applies_to()` and `name` work; `run()` is a stub that raises `NotImplementedError("Phase 2 Step 4 implements")`. Step 4 fills in the real logic; this step lands the Protocol contract first so the data model's relationships are wired.

**`WobbleAxis` Protocol (verbatim from architecture §6.3):**

```python
# phoenix/verification/wobble_axis.py

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from phoenix.trinity.data_model import PhysicsTask


class RungDepth(Enum):
    """Verification depth dial. Phase 2 ships R1 + R2 routes; Phase 5 fills R3-R5."""

    R1_FLOOR = 1
    R2_CROSS_PRECISION = 2
    R3_TWO_AXES = 3
    R4_THREE_AXES = 4
    R5_REPLICATED = 5


@dataclass(frozen=True)
class AxisResult:
    """One axis's contribution to the distance matrix + the combined error bar."""

    axis_name: str
    error_bar_contribution: float
    distance_matrix_row: list[float]
    metadata: dict[str, object]


class WobbleAxis(Protocol):
    """A single disagreement axis the verification gate can orchestrate.

    v1 ships three quantum axes (cross-precision, cross-control, cross-provider)
    in `phoenix/verification/wobble_axis.py`. v1.x extensions (perception phase 20)
    add three more axis impls without modifying the gate.
    """

    @property
    def name(self) -> str: ...

    def applies_to(self, task: PhysicsTask) -> bool: ...

    def run(self, task: PhysicsTask, depth: RungDepth) -> AxisResult: ...
```

**Skeleton `CrossPrecisionAxis`:**

```python
class CrossPrecisionAxis:
    """Axis 1 — cross-precision verification inside Solver. Phase 2 Step 4 implements."""

    name: str = "cross_precision"

    def applies_to(self, task: PhysicsTask) -> bool:
        # Cross-precision applies to any task with a numerical-grid solver.
        # Always True in v1 (all twelve vendored solvers use grids).
        return True

    def run(self, task: PhysicsTask, depth: RungDepth) -> AxisResult:
        raise NotImplementedError(
            "CrossPrecisionAxis.run lands in Phase 2 Step 4"
        )
```

**Verification:**

```powershell
python -c "from phoenix.verification.wobble_axis import WobbleAxis, CrossPrecisionAxis, RungDepth, AxisResult; print('OK')"
python -c "
from phoenix.verification.wobble_axis import CrossPrecisionAxis
ax = CrossPrecisionAxis()
assert ax.name == 'cross_precision'
print('skeleton OK')
"
```

```
=== STEP 2 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.3 — Step 3: Solver engine adapter

**What lands:**
- `phoenix/trinity/solver/engine.py` — the adapter that wraps the vendored `EquationSolver` registry into Trinity's pipeline. Per architecture §2.3.

**Adapter responsibilities (this step):**
1. Import the vendored registry: `from synthesis.equations.registry import auto_register_all` and `from synthesis.equations.base import EquationRegime, PhysicsContext`.
2. Cache the registry instance lazily (auto_register_all is idempotent but slow).
3. `pick_solver(task: PhysicsTask) -> EquationSolver` — uses `HamiltonianClassifier::can_handle()` per architecture §2.3 to dispatch by regime. Falls back to `task.metadata["regime_hint"]` if the classifier returns ambiguous.
4. `run_solver(task: PhysicsTask, n_grid: int) -> SolverRunResult` — invokes `solver.solve_stationary(ctx, n_states=...)` with the given grid resolution; returns a typed `SolverRunResult` capturing eigenvalues + grid metadata + wall-clock time.
5. `SolverRunResult` is a small `@dataclass(frozen=True)` defined in `engine.py`.

**Frontier-physics gate stub:** the adapter checks `task.tolerance.frontier_physics` against the dispatched solver's regime. If the regime is `WHEELER_DEWITT`, `GRAVITATIONAL_DECOHERENCE`, or `SEMICLASSICAL_GRAVITY` and the task doesn't carry the `frontier_physics` capability, the adapter raises `FrontierPhysicsRefused`. (Phase 6 wires the full Actor-based check; Phase 2 ships the typed exception + the regime check.)

**Verification:**

```powershell
python -c "
import phoenix
from phoenix.trinity.solver.engine import pick_solver, run_solver
from phoenix.trinity.data_model import PhysicsTask, ToleranceSpec
from phoenix._internal.latency import LatencyTier
# Synthetic actor stub for Phase 2; Phase 6 wires the real Actor verification
from synthesis.equations.base import PhysicsContext

ctx = PhysicsContext(mass_kg=9.11e-31, length_scale_m=4e-9, metadata={'omega': 1e15, 'n_grid_points': 400})
tolerance = ToleranceSpec(max_error_bar=1e-3, reproducibility_mode='default', latency_tier=LatencyTier.BATCH_REALTIME, frontier_physics=False)
task = PhysicsTask(physics_context=ctx, tolerance=tolerance, actor=None, request_id='req_test_001', metadata={})

solver = pick_solver(task)
print(f'picked: {type(solver).__name__}')
result = run_solver(task, n_grid=400)
print(f'eigenvalues[0] = {result.eigenvalues[0]:.4e} J')
"
```

Expected: `picked: TISESolver`, `eigenvalues[0] = 5.27e-20 J` (or near).

```
=== STEP 3 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.4 — Step 4: `CrossPrecisionAxis` full implementation

**What lands:**
- `phoenix/trinity/solver/cross_precision.py` — the cross-precision wobble logic (numpy disagreement metric between two grid resolutions).
- `phoenix/verification/wobble_axis.py` — `CrossPrecisionAxis.run()` filled in; calls `phoenix/trinity/solver/cross_precision.py` helpers + the `engine.py` adapter at `N` and `2N` grid points.

**Cross-precision logic:**
1. From the task, read `n_grid_default` (from `task.physics_context.metadata["n_grid_points"]`, default 400 if absent).
2. Run solver at `n_grid_default` → `result_low`.
3. Run solver at `2 * n_grid_default` → `result_high`.
4. Compute `error_bar_solver` = `|result_low.eigenvalues[0] - result_high.eigenvalues[0]|` (for ground-state-energy queries; eigenstate-overlap variant lands when Phase 5 wires the wobble gate fully).
5. Build the distance-matrix row: pairwise differences across the eigenvalues compared at low/high grids.
6. Return `AxisResult(axis_name="cross_precision", error_bar_contribution=..., distance_matrix_row=..., metadata={"n_grid_low": ..., "n_grid_high": ..., "wall_clock_ms_low": ..., "wall_clock_ms_high": ...})`.

**Depth handling:** `R1_FLOOR` skips cross-precision entirely (returns `AxisResult` with empty contribution). `R2_CROSS_PRECISION` and higher run the full N + 2N comparison. **PERF:** the 2× cost is the Phase 2 baseline; Phase 5's rung table tunes which depths trigger the doubling.

**Verification:**

```powershell
python -c "
import phoenix
from phoenix.verification.wobble_axis import CrossPrecisionAxis, RungDepth
from phoenix.trinity.data_model import PhysicsTask, ToleranceSpec
from phoenix._internal.latency import LatencyTier
from synthesis.equations.base import PhysicsContext

ctx = PhysicsContext(mass_kg=9.11e-31, length_scale_m=4e-9, metadata={'omega': 1e15, 'n_grid_points': 200})
tolerance = ToleranceSpec(max_error_bar=1e-3, reproducibility_mode='default', latency_tier=LatencyTier.BATCH_REALTIME, frontier_physics=False)
task = PhysicsTask(physics_context=ctx, tolerance=tolerance, actor=None, request_id='req_test_002', metadata={})

axis = CrossPrecisionAxis()
result = axis.run(task, RungDepth.R2_CROSS_PRECISION)
print(f'error_bar = {result.error_bar_contribution:.4e} J')
print(f'metadata: {result.metadata}')
"
```

Expected: `error_bar` is a small number (much less than `max_error_bar=1e-3`); the QHO ground state converges quickly at `N=200` vs `N=400`.

```
=== STEP 4 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.5 — Step 5: Pipeline orchestrator (Solver-only path)

**What lands:**
- `phoenix/trinity/pipeline.py` — the early three-subsystem orchestrator. Phase 2 ships the Solver-only path; Phase 3 extends with Control + Orchestrate.

**Orchestrator responsibilities (Phase 2 scope):**
1. `solve(task: PhysicsTask) -> CandidateAnswer` — top-level entry point.
2. **Latency-tier gate.** Inspect `task.tolerance.latency_tier`. If `BATCH_REALTIME`, proceed. If `STREAMING_REALTIME` or `PERCEPTION_REALTIME`, raise `LatencyTierNotImplemented` with a typed message naming which release will support that tier (v2 for streaming, Phase 12+ for perception).
3. Run `CrossPrecisionAxis(task, RungDepth.R2_CROSS_PRECISION)` to produce the Axis 1 result.
4. Run the solver one final time at the higher grid (Step 3's `run_solver` at `2N`) to produce the canonical `value` for `CandidateAnswer.value`. (Step 4's cross-precision logic already ran twice; we reuse the higher-grid result rather than running a third time. Optimization: cache the higher-grid result inside Step 4's logic.)
5. Build `CandidateAnswer` with: `solver_id` (the regime + dispatched solver class name), `value` (canonical eigenvalue/eigenstate), `error_bar_solver` (from Axis 1), `sigma_solver` (placeholder — same as error_bar_solver for Phase 2), `solver_kpi_bundle` (dict with wall-clock + n_grid_high + dispatched_regime), `provenance_solver` (SolverProvenance instance with full call chain).

**`SolverProvenance` shape:**
```python
@dataclass(frozen=True)
class SolverProvenance:
    """Solver-side provenance trace. Subset of full ProvenanceTrace from §1 Decision 15."""
    request_id: str
    dispatched_solver: str           # regime + class name
    n_grid_low: int
    n_grid_high: int
    wall_clock_ms_total: float
    cross_precision_axis_result: AxisResult
    phase: str = "phase_2_solver_only"   # honest about Phase 2's incomplete state
```

**Verification:**

```powershell
python -c "
import phoenix
from phoenix.trinity.pipeline import solve
from phoenix.trinity.data_model import PhysicsTask, ToleranceSpec
from phoenix._internal.latency import LatencyTier, LatencyTierNotImplemented
from synthesis.equations.base import PhysicsContext

ctx = PhysicsContext(mass_kg=9.11e-31, length_scale_m=4e-9, metadata={'omega': 1e15, 'n_grid_points': 200})
tolerance = ToleranceSpec(max_error_bar=1e-3, reproducibility_mode='default', latency_tier=LatencyTier.BATCH_REALTIME, frontier_physics=False)
task = PhysicsTask(physics_context=ctx, tolerance=tolerance, actor=None, request_id='req_test_003', metadata={})

candidate = solve(task)
print(f'solver_id: {candidate.solver_id}')
print(f'value: {candidate.value:.4e} J')
print(f'error_bar_solver: {candidate.error_bar_solver:.4e} J')
print(f'phase: {candidate.provenance_solver.phase}')

# verify the latency-tier gate
streaming_tolerance = ToleranceSpec(max_error_bar=1e-3, reproducibility_mode='default', latency_tier=LatencyTier.STREAMING_REALTIME, frontier_physics=False)
streaming_task = PhysicsTask(physics_context=ctx, tolerance=streaming_tolerance, actor=None, request_id='req_test_004', metadata={})
try:
    solve(streaming_task)
    print('FAIL: streaming task should have raised')
except LatencyTierNotImplemented as exc:
    print(f'streaming gate: OK ({exc.tier.value} -> {exc})')
"
```

Expected: `solver_id` = TISESolver, `value` near `5.27e-20 J`, `error_bar_solver` very small, `phase` = `phase_2_solver_only`. Streaming task raises `LatencyTierNotImplemented`.

```
=== STEP 5 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.6 — Step 6: `POST /v1/tasks` endpoint + integration test

**What lands:**
- `phoenix/api/routes.py` gains the `POST /v1/tasks` endpoint and supporting Pydantic request/response models.
- `tests/integration/test_solve_endpoint.py` exercises the endpoint end-to-end via `TestClient`.

**Endpoint design:**

```python
# phoenix/api/routes.py addition

from pydantic import BaseModel
from phoenix.trinity.data_model import PhysicsTask, ToleranceSpec
from phoenix.trinity.pipeline import solve
from phoenix._internal.latency import LatencyTier, LatencyTierNotImplemented


class PhysicsContextRequest(BaseModel):
    mass_kg: float
    length_scale_m: float
    metadata: dict


class ToleranceRequest(BaseModel):
    max_error_bar: float = 1e-3
    reproducibility_mode: str = "default"
    latency_tier: str = "batch_realtime"   # maps to LatencyTier enum
    frontier_physics: bool = False


class SolveRequest(BaseModel):
    physics_context: PhysicsContextRequest
    tolerance: ToleranceRequest = ToleranceRequest()
    metadata: dict = {}


@app.post("/v1/tasks")
def submit_task(req: SolveRequest):
    """Submit a physics task. Phase 2 returns Solver-only output."""
    # Construct PhysicsContext (vendored)
    from synthesis.equations.base import PhysicsContext
    ctx = PhysicsContext(
        mass_kg=req.physics_context.mass_kg,
        length_scale_m=req.physics_context.length_scale_m,
        metadata=req.physics_context.metadata,
    )

    # Map latency_tier string -> enum
    try:
        tier = LatencyTier(req.tolerance.latency_tier)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"unknown latency_tier: {req.tolerance.latency_tier}")

    tolerance = ToleranceSpec(
        max_error_bar=req.tolerance.max_error_bar,
        reproducibility_mode=req.tolerance.reproducibility_mode,
        latency_tier=tier,
        frontier_physics=req.tolerance.frontier_physics,
    )

    request_id = f"req_{uuid.uuid4()}"
    task = PhysicsTask(
        physics_context=ctx, tolerance=tolerance, actor=None,
        request_id=request_id, metadata=req.metadata,
    )

    try:
        candidate = solve(task)
    except LatencyTierNotImplemented as exc:
        raise HTTPException(status_code=501, detail=f"{exc.tier.value} not yet supported: {exc}")

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
            "request_id": candidate.provenance_solver.request_id,
            "dispatched_solver": candidate.provenance_solver.dispatched_solver,
            "phase": candidate.provenance_solver.phase,
        },
        "reproducibility_asterisk": "Phase 2 ships Solver-only; Control + Orchestrate land in Phase 3.",
    }
```

**Integration test:**

```python
# tests/integration/test_solve_endpoint.py

from fastapi.testclient import TestClient
from phoenix.api.routes import app


def test_solve_endpoint_qho_ground_state() -> None:
    client = TestClient(app)
    body = {
        "physics_context": {"mass_kg": 9.1093837015e-31, "length_scale_m": 4e-9, "metadata": {"omega": 1e15, "n_grid_points": 200}},
        "tolerance": {"max_error_bar": 1e-3, "reproducibility_mode": "default", "latency_tier": "batch_realtime", "frontier_physics": False},
        "metadata": {},
    }
    response = client.post("/v1/tasks", json=body)
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed_solver_only"
    assert payload["phase"] == "phase_2_solver_only"
    cand = payload["candidate_answer"]
    expected_e0 = 1.054571817e-34 * 1e15 * 0.5  # hbar*omega/2
    assert abs(cand["value"] - expected_e0) / expected_e0 < 0.02
    assert cand["error_bar_solver"] > 0
    assert cand["error_bar_solver"] < 1e-3   # within max_error_bar


def test_solve_endpoint_streaming_returns_501() -> None:
    client = TestClient(app)
    body = {
        "physics_context": {"mass_kg": 9.1e-31, "length_scale_m": 4e-9, "metadata": {"omega": 1e15, "n_grid_points": 200}},
        "tolerance": {"max_error_bar": 1e-3, "reproducibility_mode": "default", "latency_tier": "streaming_realtime", "frontier_physics": False},
        "metadata": {},
    }
    response = client.post("/v1/tasks", json=body)
    assert response.status_code == 501
    assert "streaming_realtime" in response.json()["detail"]
```

**Verification:**

```powershell
pytest tests/integration/test_solve_endpoint.py -v
# → 2 passed in ~2-3 seconds (one full QHO solve + one tier-rejection)

pytest tests/ -v
# → 21 passed (19 from Phase 0/1 + 2 new from Phase 2)
```

```
=== STEP 6 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.7 — Step 7: Phase 2 acceptance + version bump + push

**What lands:**

1. **Version bump:** `pyproject.toml` and `phoenix/_internal/version.py` move from `1.0.0.dev1` to `1.0.0.dev2`.
2. **Test updates:** `tests/unit/test_smoke.py` and `tests/integration/test_health.py` update their version assertions; smoke test stays.
3. **CHANGELOG entry:** new `## [1.0.0.dev2] — 2026-MM-DD` covering Phase 2 deliverables.
4. **`vendor/VENDOR_VERSION.txt` regenerated** via `python scripts/vendor_sync.py --update-version-manifest` so its `phoenix_release` tracks the package version.

**Acceptance checklist (Phase 2):**

- ✅ `phoenix/trinity/data_model.py` ships `PhysicsTask`, `CandidateAnswer`, `VerifiedAnswer`, `Result` + supporting dataclasses.
- ✅ `phoenix/_internal/latency.py` ships `LatencyTier` enum + `LatencyTierNotImplemented`.
- ✅ `phoenix/verification/wobble_axis.py` ships `WobbleAxis` Protocol + `CrossPrecisionAxis` concrete impl.
- ✅ `phoenix/trinity/solver/engine.py` adapts vendored `EquationSolver` registry; auto-dispatch via `HamiltonianClassifier::can_handle()`.
- ✅ `phoenix/trinity/solver/cross_precision.py` houses cross-precision wobble logic.
- ✅ `phoenix/trinity/pipeline.py` Solver-only orchestrator with latency-tier gate.
- ✅ `POST /v1/tasks` endpoint accepts `SolveRequest`, dispatches through pipeline, returns Solver-only response with `phase: phase_2_solver_only` provenance marker.
- ✅ `pytest tests/`: 21/21 passed.
- ✅ `pre-commit run --all-files`: ruff, ruff-format, mypy strict, smoke — all 4 Passed.
- ✅ `python -m phoenix.api --port 8003`; `POST /v1/tasks` works against the running daemon.
- ✅ `git status`: working tree clean.

**Combined verification command:**

```powershell
Set-Location C:\Phoenix

# Acceptance 1-7 via pytest
pytest tests/ -v

# Acceptance 8: pre-commit
pre-commit run --all-files

# Acceptance 9: end-to-end through the running daemon
$daemon = Start-Process -PassThru python -ArgumentList "-m", "phoenix.api", "--port", "8003" -WindowStyle Hidden
Start-Sleep -Seconds 3
try {
    $body = @{
        physics_context = @{
            mass_kg = 9.1093837015e-31
            length_scale_m = 4e-9
            metadata = @{ omega = 1e15; n_grid_points = 200 }
        }
        tolerance = @{
            max_error_bar = 1e-3
            reproducibility_mode = "default"
            latency_tier = "batch_realtime"
            frontier_physics = $false
        }
        metadata = @{}
    } | ConvertTo-Json -Depth 5
    $resp = Invoke-RestMethod -Uri http://127.0.0.1:8003/v1/tasks -Method POST -Body $body -ContentType "application/json"
    Write-Host "task_id:           $($resp.task_id)"
    Write-Host "status:            $($resp.status)"
    Write-Host "solver_id:         $($resp.candidate_answer.solver_id)"
    Write-Host "value:             $($resp.candidate_answer.value)"
    Write-Host "error_bar_solver:  $($resp.candidate_answer.error_bar_solver)"
} finally {
    Stop-Process -Id $daemon.Id -Force
}

# Acceptance 10: working tree clean after staging Phase 2's files
git status --porcelain
```

**Push:**

```powershell
git push origin main
```

```
=== STEP 7 COMPLETE — PHASE 2 SHIPPED ===
```

## 4 — What's not in Phase 2

Explicitly out of scope:

| Item | Phase | Build guide |
|---|---|---|
| Trinity Core's Control subsystem (DPDScheduler wired into pipeline) | Phase 3 | BUILDGUIDE_phoenix_v1_phase3_control_orchestrate.md |
| Trinity Core's Orchestrate subsystem (greenfield: bundle_builder, provider_client, etc.) | Phase 3 | (same) |
| Cross-control wobble (Axis 2) and cross-provider wobble (Axis 3) full impls | Phase 3 (axis classes) + Phase 5 (gate orchestration) |  |
| Verification gate's full rung table + promotion logic | Phase 5 | BUILDGUIDE_phoenix_v1_phase5_verification.md |
| Provider routing (Router subsystem) | Phase 4 | BUILDGUIDE_phoenix_v1_phase4_router.md |
| Safety gate (Actor verification, frontier-physics primary check, rate limiting) | Phase 6 | BUILDGUIDE_phoenix_v1_phase6_safety_state_queue.md |
| State backend, queue (NATS) | Phase 6 | (same) |
| Audit log, Omega Ledger, drift monitor | Phase 7 | BUILDGUIDE_phoenix_v1_phase7_audit_ledger.md |
| Admin endpoints, kill switch | Phase 8 | BUILDGUIDE_phoenix_v1_phase8_admin.md |
| LoRA adapter sandbox, MCP server, CLI commands | Phase 9 | BUILDGUIDE_phoenix_v1_phase9_adapters_mcp_cli.md |
| OTel adapter, cloud seams concrete impls, standalone binary | Phase 10 | BUILDGUIDE_phoenix_v1_phase10_observability_distribution.md |
| Final §10.7 acceptance + release | Phase 11 | BUILDGUIDE_phoenix_v1_phase11_release.md |

## 5 — Phase 3 preview

Phase 3's job is to wire Trinity Core's **Control subsystem** (vendored DPDScheduler from `synthesis/core/`) and **Orchestrate subsystem** (greenfield Phoenix code per the 2026-05-06 SynQc-greenfield revision) through the pipeline. Specifically:

- `phoenix/trinity/control/engine.py` adapts the vendored `DPDScheduler` into Trinity's pipeline. Constructs DPD block sequences appropriate to the Hamiltonian regime.
- `phoenix/trinity/control/cross_probe.py` — Axis 2 cross-control wobble, registered as `CrossControlAxis` (the second concrete `WobbleAxis` impl, slotting into `phoenix/verification/wobble_axis.py`).
- `phoenix/trinity/orchestrate/engine.py` — top-level Phoenix-native Orchestrate orchestrator (greenfield).
- `phoenix/trinity/orchestrate/{bundle_builder,provider_client,result_extractor,drift_feedback,kpi_bundle,cross_provider}.py` — the seven Phoenix-native modules per architecture §2.5.
- `phoenix/trinity/orchestrate/cross_provider.py` — Axis 3 cross-provider wobble, registered as `CrossProviderAxis` (the third concrete `WobbleAxis` impl).
- `phoenix/trinity/pipeline.py` extended: Solver → Control → Orchestrate full path. Endpoint promotes from `CandidateAnswer` to `Result`. `phase: phase_2_solver_only` marker disappears; full `Result` envelope ships.

Phase 3 is the larger of the two (Control vendoring + Orchestrate greenfield). Estimated 8–12 phase-gated steps.

## 6 — Standing rules carried from Phases 0 and 1

1. Phase gates with explicit Adam review (`=== STEP N COMPLETE ===`). No silent advancement.
2. Stop and ask on architectural ambiguity. Mark `[OPEN: ...]` and surface to Adam.
3. PERF and SAFETY callouts inline.
4. Per-section READMEs. Phase 2 updates `phoenix/trinity/`, `phoenix/trinity/solver/`, `phoenix/verification/`, `phoenix/_internal/`, `phoenix/api/` READMEs to reflect what landed.
5. Launcher updated when startup behavior changes. Phase 2 doesn't change startup; the new `/v1/tasks` endpoint joins the existing FastAPI app.
6. No OneDrive paths.
7. Live reads beat memory. Vendored API names are source-of-truth; Phase 2 tests use real names (`DisagreementType`, `DPDScheduler`, `STRONG_PROJECTIVE`).
8. **2026-05-08 v1.1 follow-up commitments honored:**
   - `WobbleAxis` Protocol parameterizes axis dispatch (this Phase ships the Protocol + first impl).
   - `CloudSeams` registry stays generic name-keyed (Phase 10 implements; Phase 2 doesn't touch).
   - `LatencyTier` enum lives in `phoenix/_internal/latency.py` with all three values; v1 routes only `BATCH_REALTIME`.
   - Front-door endpoints stay under `/v1/...` flat (perception's `/v1/perception/*` slots in later as sibling).
   - Strict no-perception-code-in-v1: Phase 2 ships zero perception scaffolding.

```
=== BUILD GUIDE COMPLETE — AWAITING ADAM REVIEW ===
```
