# Phoenix Changelog

All notable changes to Phoenix are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) loosely; Phoenix's
release cadence is phase-gated rather than calendar-gated, so entries
correspond to phase landings rather than fixed-interval releases.

Version semantics: Phoenix follows [PEP 440](https://peps.python.org/pep-0440/).
Pre-release builds during build-guide phases use `1.0.0.dev<N>` where `<N>`
is the phase number (`1.0.0.dev0` = Phase 0, `1.0.0.dev1` = Phase 1, etc.).
Once Phoenix enters integration testing it moves to `1.0.0a0` (alpha 0),
`1.0.0b0` (beta), `1.0.0rc0` (release candidate), and finally `1.0.0` for
the stable release. PEP 440 compliance is required by setuptools and lets
Phoenix interoperate with pip, uv, and the broader Python tooling ecosystem.

---

## [1.0.0.dev4] — 2026-05-08

Phase 4 shipped. Trinity Core's pipeline now routes through a real Router
subsystem (Section 4) — the Phase 3 placeholder helper is retired. Six
new modules under `phoenix/router/` plus three quantum provider stubs
under `phoenix/providers/quantum/`. The seven-stage routing algorithm is
fully wired; failover protocol with exponential-backoff quarantine; cost
ceiling enforcement at Stage 2; defense-in-depth frontier-physics
re-check at Stage 4; reproducibility-mode REPLAY pinning at Stage 5.

### Locked scope decisions (2026-05-08)

1. **Stub-only cloud adapters.** IBM/Braket/IonQ stubs ship as
   Protocol-conforming classes that raise `OrchestrateProviderError` on
   `submit`. No cloud SDKs added to `pyproject.toml`. Real
   qiskit-ibm-runtime / amazon-braket-sdk / ionq wiring lands in a
   focused later phase (likely Phase 9 with adapters / MCP) when
   credential management gets tackled deliberately.
2. **Drift drain defers to Phase 7.** Phase 4's intelligence layer uses
   only Source A (static `HardwareParams` from vendored
   `hardware_backends.py`). Sources B (live telemetry) and C (ledger
   history) need Phase 7 backing to be useful.
3. **Equivalence registry shipped.** `phoenix/router/equivalence_registry.py`
   with conservative defaults per Section 4.5 (same `quantum_technology`
   + fidelity within 10%). Section 11.2.1 stays open; v1.x adds
   richer equivalence rules.

### What landed (commits b1780d2 → eb20d36)

- **`RoutingRequest` dataclass + `ReproducibilityMode` enum**
  (`phoenix/router/data_model.py`): forward-compat input shape for
  `Router.decide`. Phase 3 already shipped `RoutingDecision` +
  `ProviderSelection`; Phase 4 adds `RoutingRequest` and the typed
  `ReproducibilityMode` enum honoring DEFAULT and REPLAY (STRICT
  Phase 7).
- **Router error types** (`phoenix/router/errors.py`):
  `NoEligibleProvidersError`, `CostCeilingExceeded`,
  `ReplayProviderUnavailable`, `AllAlternatesExhausted` — each carrying
  the structured context Phase 9's HTTP status mapping needs.
- **Three quantum provider stubs**
  (`phoenix/providers/quantum/{ibm_stub,braket_stub,ionq_stub}.py`):
  IBM Eagle (superconducting), Braket Rigetti Aspen-M-3
  (superconducting), IonQ Forte (trapped_ion). Constructor-overridable
  `available` flag for testing the Stage 3 health filter.
- **`ProviderRegistry`** (`phoenix/router/provider_registry.py`):
  per-process state-of-the-world. `ProviderHealth` enum (HEALTHY /
  DEGRADED / OFFLINE) + `ProviderEntry` mutable dataclass. Mark methods
  for failover. `build_default_registry()` registers the 4 default
  providers (LocalSim + 3 stubs).
- **Pricing v1 data + loader** (`phoenix/router/pricing/pricing_v1.json`
  + `phoenix/router/pricing.py`): static per-provider cost estimates with
  `_metadata` (data_freshness_utc, stale_after_days=90 per Section 11.2.2
  RESOLVED). `load_pricing`, `estimate_cost_usd`, `is_pricing_stale`.
- **Intelligence layer Source A** (`phoenix/router/intelligence.py`):
  `estimate_fidelity` derived from vendored `HardwareParams`
  (gate_error_rate + two_qubit_error_rate via Phase 4 placeholder
  circuit shape: 10 1q + 1 2q gates). `estimate_latency_ms` falls back
  to client's reported value. `estimate_cost_usd` delegates to pricing.
- **Equivalence registry** (`phoenix/router/equivalence_registry.py`):
  conservative defaults per Section 4.5. `is_equivalent` and
  `filter_equivalent_alternates` consumed by Stage 6's alternate
  filtering and Step 8's failover walk.
- **Router decision algorithm** (`phoenix/router/decision.py`):
  `Router.decide(RoutingRequest) -> RoutingDecision` running all seven
  stages (Section 4.4). Stage 1 modality whitelist; Stage 4 frontier
  early raise; Stage 2 cost / latency / fidelity / excluded filters with
  CostCeilingExceeded specialization; Stage 3 health filter; Stage 5
  REPLAY pinning; Stage 6 weighted ranking with deterministic tie-break;
  Stage 7 decision_provenance with per-stage rationale + ranking weights
  + pricing staleness.
- **Failover protocol** (`phoenix/router/failover.py`):
  `FailoverProtocol` class with exponential-backoff quarantine.
  `quarantine` (public; pipeline calls it at the orchestrate boundary),
  `reset_failures` for ops, `attempt_with_failover` for self-contained
  submit-level walks. Defaults: 5 min base, doubles per failure, capped
  at 1 hour.
- **Pipeline integration** (`phoenix/trinity/pipeline.py`): module-level
  Router + FailoverProtocol singletons (lazy via
  `_get_router`/`_get_failover`). `_build_routing_request` translates
  `PhysicsTask` to `RoutingRequest`. `_orchestrate_with_failover` walks
  `decision.primary` + `alternates` on failures, falls back to
  `LocalClassicalSimulator` when `allow_simulator_fallback=True`.
  `solve()`'s Layer 3 replaces the Phase 3 `_build_default_provider_selection`
  helper with real Router routing.

### Tests

- 75 tests passing (was 50 at end of Phase 3; +25 from Phase 4).
  - 4 new unit-test files: `test_provider_registry.py` (4 tests),
    `test_router_decision.py` (7 tests covering all seven stages),
    `test_failover.py` (6 tests including exponential-backoff math and
    simulator fallback walk), `test_intelligence_pricing.py` (8 tests
    combining pricing + intelligence + equivalence).
  - Phase 0/1/2/3 baseline tests pass unchanged through the new
    Router-routed pipeline path.
- Pre-commit hooks: ruff, ruff-format, mypy strict, pytest smoke -- all
  4 pass.

### Out of scope for Phase 4 (explicit deferrals)

- Real cloud SDK wiring (qiskit-ibm-runtime, amazon-braket-sdk, ionq) --
  focused later phase.
- Sources B (live provider telemetry) + C (ledger history) for the
  intelligence layer -- Phase 7 with state backend + Omega Ledger.
- Drift buffer drain scheduler -- Phase 7.
- Phase 9 HTTP status mapping for new Router error types
  (`CostCeilingExceeded` -> 402, `ReplayProviderUnavailable` -> 410,
  `AllAlternatesExhausted` -> 503) -- Phase 9 admin/MCP work.
- Verification gate's secondary routing requests for Axis 3
  (cross-provider wobble) -- Phase 5.
- Cost ceiling per-actor-per-day budget enforcement -- Phase 7+ (needs
  ledger backing for cumulative tracking).
- `phoenix/router/pricing/pricing_v1.json` package_data config so the
  JSON ships in the wheel -- Phase 11 release work.

### Known placeholders (per plan risk register)

All deferrals or known limitations, none blocking:

- Stub adapters' `submit()` raises `OrchestrateProviderError`; failover
  always falls through to `LocalClassicalSimulator` for any cloud-routed
  task. Real cloud calls cost money and need credentials; deferred.
- Phase 4 ranking circuit shape (10 1q + 1 2q gates) is a placeholder;
  Phase 7 wires shot-aware estimates from real KPIBundles.
- Pricing rates are placeholders; ops refresh via `phoenix admin
  pricing-update` (Phase 8 endpoint).
- Drift buffer is unbounded in Phase 4 (R6); Phase 4's Router doesn't
  consume it -- Phase 7 will.

### Version + manifest

- `pyproject.toml`, `phoenix/_internal/version.py`: `1.0.0.dev3` ->
  `1.0.0.dev4`.
- `vendor/VENDOR_VERSION.txt` regenerated (`phoenix_release: 1.0.0.dev4`,
  `vendor_synced_at` refreshed).

---

## [1.0.0.dev3] — 2026-05-08

Phase 3 shipped. Trinity Core's Control and Orchestrate subsystems are
now wired through the pipeline end-to-end. `POST /v1/tasks` returns the
architecturally-correct `Result` envelope (top-level value, error_bar,
sigma, agreement_type, kpi_bundle_orchestrate, three-layer ProvenanceTrace
with cloud_shots_recorded mirror per Section 1 Decision 20). The
`phase_2_solver_only` honesty marker is retired; everything reads
`phase_3_solver_control_orchestrate`. Six of seven Orchestrate modules
ship in Phase 3; `cross_provider.py` and `CrossProviderAxis` (Axis 3)
defer to Phase 5 alongside the verification gate's rung table.

### Locked scope decisions (2026-05-08, executed)

1. Axis 3 fully deferred to Phase 5. No `cross_provider.py`, no
   `CrossProviderAxis` class in Phase 3. Aligns with the orchestrate/README
   timeline.
2. `LocalClassicalSimulator` is the only Phase 3 `BaseProviderClient` impl,
   landing at `phoenix/providers/classical/local_simulator.py`. Phase 4
   adds cloud quantum adapters as siblings.
3. Default verification depth = `R3_TWO_AXES`. The pipeline runs Axis 1 +
   Axis 2 by default. Phase 5's rung-table promotion logic is not in scope.
4. Typed `KPIBundle` introduced; `data_model.py` field types tightened from
   `dict[str, Any]` to `KPIBundle` per Section 2.5.

### What landed (commits 3c910d8 → eb20d36)

- **Typed `KPIBundle`** (`phoenix/trinity/orchestrate/kpi_bundle.py`):
  Phoenix-native frozen dataclass with `fidelity`, `latency_us`,
  `backaction`, `shots_used`, `shot_budget`, `status` per Section 2.5.
  `KPIStatus` enum: `OK` / `WARN` / `FAIL`.
- **Data model tightening** (`phoenix/trinity/data_model.py`):
  `VerifiedAnswer.dpd_result` (`Any` → `DPDResult`),
  `VerifiedAnswer.kpi_bundle_control` and `Result.kpi_bundle_orchestrate`
  (`dict[str, Any]` → `KPIBundle`), `ProvenanceTrace.control` and
  `.orchestrate` (`Any` → typed). New `ControlProvenance` and
  `OrchestrateProvenance` dataclasses.
- **Control engine adapter** (`phoenix/trinity/control/engine.py`):
  `run_dpd(candidate, *, probe_strength=0.1,
  hardware_modality="superconducting") -> ControlRunResult`. Wraps the
  vendored `DPDScheduler.execute()` against a `|0⟩⟨0|` placeholder
  density matrix (Phase 5 wires the real eigenstate). SAFETY: raises
  `ControlVerificationError` on `trace_preservation` drift > 1e-3 or
  positivity violation.
- **Cross-control wobble (Axis 2)** (`phoenix/trinity/control/cross_probe.py`
  + `phoenix/verification/wobble_axis.py`): trace-distance metric
  `T(ρ₁, ρ₂) = (1/2) Σ |λᵢ(ρ₁ - ρ₂)|` per the plan's R1 risk decision.
  `CrossControlAxis` registers as the second concrete `WobbleAxis`
  Protocol impl. R3+ runs eps=0.1 + eps=0.5 sweep; the weak-probe leg
  doubles as the canonical run for the pipeline (PERF win ~1-2 s saved).
  Optional `prior_high_grid_result` constructor injection lets the
  pipeline skip a redundant solver call.
- **Orchestrate scaffolding** (4 new modules under
  `phoenix/trinity/orchestrate/`): `provider_client.py` (BaseProviderClient
  Protocol + ProviderSubmission/RawResult dataclasses + ProviderError
  hierarchy), `bundle_builder.py` (pure translator with deterministic
  16-char SHA-256 bundle hash), `result_extractor.py` (raw result + KPI
  composer), `drift_feedback.py` (in-memory DriftSignal buffer for
  Phase 4's Router intelligence layer to drain).
- **Router data model** (`phoenix/router/data_model.py`):
  `RoutingDecision` + `ProviderSelection` typed dataclasses;
  forward-compat with Phase 4's Router producer.
- **LocalClassicalSimulator** (`phoenix/providers/classical/local_simulator.py`):
  the only Phase 3 concrete `BaseProviderClient`. Synchronous trace
  expectation against the verified ρ; `cloud_shots_recorded=False`.
- **Orchestrate engine** (`phoenix/trinity/orchestrate/engine.py`):
  top-level `orchestrate(verified, selection, ...)` that sequences
  bundle_builder → provider_client.submit → result_extractor →
  drift_feedback and produces `(Result, OrchestrateProvenance)`.
  Quadrature combiner per Section 11.1.1 placeholder; agreement_type
  mapping per the vendored `DisagreementType` enum (HEDGED_CONSENSUS /
  UNKNOWN; Phase 5 extends).
- **Three-layer pipeline** (`phoenix/trinity/pipeline.py`):
  `solve(task) -> Result` (return type promoted from `CandidateAnswer`).
  Default depth `R2_CROSS_PRECISION` → `R3_TWO_AXES`. Layer 1 runs Axis
  1 (cross-precision); Layer 2 runs Axis 2 (cross-control) with
  prior_high_grid_result injection; Layer 3 dispatches Orchestrate via
  default `_build_default_provider_selection(task)` pointing at
  `LocalClassicalSimulator`. Provenance composition stitches all three
  sub-traces into `ProvenanceTrace` with `cloud_shots_recorded` mirror.
- **POST /v1/tasks promotion** (`phoenix/api/routes.py`): response shape
  changes from `candidate_answer`-wrapped to top-level `value`,
  `error_bar`, `sigma`, `agreement_type`, `kpi_bundle_orchestrate`,
  flattened `provenance` with solver/control/orchestrate sub-blocks.
  HTTP 422 added for `ControlVerificationError`; HTTP 502 added for
  `OrchestrateProviderError`.

### Tests

- 50 tests passing (was 34 at end of Phase 2; +16 from Phase 3).
  - 5 new unit-test files: `test_kpi_bundle.py` (3 tests),
    `test_control_engine.py` (3 tests including a synthetic-injection
    `ControlVerificationError` exercise), `test_cross_control_axis.py`
    (4 tests including the prior_high_grid_result PERF path),
    `test_local_simulator.py` (3 tests including unknown bundle_kind
    refusal), `test_orchestrate_engine.py` (3 tests including a
    BrokenProvider stub for failure propagation).
  - Phase 2 `test_pipeline.py` and `test_solve_endpoint.py` adjusted in
    Steps 8+9 to assert the new Result envelope shape.
- Pre-commit hooks: ruff, ruff-format, mypy strict, pytest smoke -- all
  4 pass.

### Out of scope for Phase 3 (explicit deferrals)

- Cross-provider wobble (Axis 3) and `cross_provider.py` -- Phase 5
  alongside the verification gate's rung-table orchestrator.
- Real eigenstate plumbing (replaces the `|0⟩⟨0|` placeholder; surfaces
  non-zero Axis 2 trace distance signal) -- Phase 5.
- Real observable extraction at the local simulator (replaces the trace
  expectation placeholder) -- Phase 5.
- Cloud quantum providers (IBM Eagle / Braket / IonQ) and the Router
  producer -- Phase 4.
- Adaptive rung selection driven by `max_error_bar` -- Phase 5.
- Tasks list / get / replay / approve_promotion / cancel endpoints --
  Phase 3+ once the ledger backs them.
- WebSocket events (Section 5.3) -- Phase 5+ with the gate.
- Actor verification at the front door -- Phase 6.

### Open tensions touching Phase 3 (per plan risk register)

All deferrals or known-placeholders, none blocking:

- **R1 (cross-control metric):** Phase 3 ships trace distance; metric name
  in `CrossControlDisagreement.metric` and `AxisResult.metadata` so Phase
  5's gate composer can introspect or override.
- **R2 (DPD initial ρ placeholder):** `|0⟩⟨0|` in dim=2; Phase 5 wires
  multi-state ρ from the high-grid `SolverRunResult`.
- **R4 (quadrature combiner):** Section 11.1.1 OPEN; Phase 3 ships the v0
  placeholder, per-axis bars recorded in provenance for v1.1 covariance
  refinement.
- **R5 (agreement_type mapping):** Phase 3 maps
  "all axes agree within tolerance" → `HEDGED_CONSENSUS`; Phase 5's
  `agreement_classifier` extends the vendored enum with the
  architecture-spec values.
- **R6 (drift buffer unbounded):** Phase 4's Router intelligence layer
  will drain on schedule; Phase 4 may add ring-buffer semantics.

### Version + manifest

- `pyproject.toml`, `phoenix/_internal/version.py`: `1.0.0.dev2` ->
  `1.0.0.dev3`.
- `vendor/VENDOR_VERSION.txt` regenerated (`phoenix_release: 1.0.0.dev3`,
  `vendor_synced_at` refreshed, `dr_frank_and_eddy_commit` unchanged).

---

## [1.0.0.dev2] — 2026-05-08

Phase 2 shipped. Trinity Core's Solver subsystem is wired through the front
door end-to-end. `POST /v1/tasks` accepts a `SolveRequest`, dispatches via
the vendored `HamiltonianClassifier`, runs cross-precision wobble (Axis 1)
at `RungDepth.R2_CROSS_PRECISION`, and returns a `CandidateAnswer` with
the `phase: phase_2_solver_only` honesty marker. Phase 3 promotes the
return type to a full `Result` envelope once Control + Orchestrate land.

### What landed (commits ba1100d → this release)

- **Trinity Core data model** (`phoenix/trinity/data_model.py`): seven
  frozen dataclasses -- `ToleranceSpec`, `SolverProvenance`,
  `ProvenanceTrace`, `PhysicsTask`, `CandidateAnswer`, `VerifiedAnswer`,
  `Result` -- plus their supporting types. `agreement_type:
  DisagreementType` per the 2026-05-08 drift correction (vendored class
  name, not the v1.0 spec drift `AgreementType`).
- **Latency tier dial** (`phoenix/_internal/latency.py`): `LatencyTier`
  enum with `BATCH_REALTIME` / `STREAMING_REALTIME` /
  `PERCEPTION_REALTIME` plus `LatencyTierNotImplemented` typed exception.
  v1 routes only `BATCH_REALTIME`; the other two are
  defined-but-not-routable per the v1.1 follow-up locked 2026-05-08.
- **`WobbleAxis` Protocol** (`phoenix/verification/wobble_axis.py`): the
  Protocol contract that parameterizes Phase 5's verification gate, plus
  `RungDepth` enum, `AxisResult` dataclass, and the first concrete impl
  `CrossPrecisionAxis`. Perception extension's three axes at Phase 20 plug
  in as additional `WobbleAxis` impls without forking the gate.
- **Solver engine adapter** (`phoenix/trinity/solver/engine.py`): wraps the
  vendored `EquationSolver` registry into Phoenix's `PhysicsTask` ->
  dispatched-solver flow. `pick_solver()` honors `regime_hint` override
  on `PhysicsTask.metadata`; `run_solver()` runs at a specified grid
  resolution and returns a typed `SolverRunResult`. Frontier-physics
  regime gate raises `FrontierPhysicsRefused` for Wheeler-DeWitt /
  Gravitational Decoherence / Semiclassical Gravity without
  `frontier_physics=True` permission (architecture Decision 7).
- **Cross-precision wobble logic** (`phoenix/trinity/solver/cross_precision.py`):
  pure-function `compute_cross_precision_disagreement(low, high)` that
  preserves the full pairwise distance row alongside the scalar per
  Section 6.2's DO-NOT-COLLAPSE invariant.
- **Trinity Core pipeline** (`phoenix/trinity/pipeline.py`): `solve(task)
  -> CandidateAnswer` orchestrates the Solver-only path. Latency-tier
  gate refuses non-routable tiers with typed exceptions naming the
  release that ships support. Reuses Step 4's high-grid `SolverRunResult`
  (stashed in `AxisResult.metadata["high_grid_result"]`) to extract the
  canonical value -- saves one solver invocation per solve.
- **Front-door endpoint** (`phoenix/api/routes.py`): `POST /v1/tasks`
  accepts `SolveRequest`, returns Solver-only response with
  `reproducibility_asterisk`. Status code mapping: 200 success, 400 bad
  latency_tier or no eligible solver, 403 frontier-physics refused, 501
  latency tier defined-but-not-routable.

### Tests

- 34 tests passing (was 19 at end of Phase 1; +15 from Phase 2).
  - Phase 2 unit tests: 3 (CrossPrecisionAxis) + 5 (pipeline) = 8.
  - Phase 2 integration tests: 7 (POST /v1/tasks).
- Pre-commit hooks: ruff, ruff-format, mypy strict, pytest smoke -- all 4 pass.

### Out of scope for Phase 2 (explicit deferrals)

- Cross-control wobble (Axis 2) and cross-provider wobble (Axis 3) full
  impls land in Phase 3 (axis classes) + Phase 5 (gate orchestration).
- Verification gate's full rung table (R1-R5) and adaptive promotion
  logic land in Phase 5.
- Tasks list / get / replay / approve_promotion / cancel endpoints land
  in Phase 3+ once the ledger backs them.
- WebSocket events (Section 5.3) land with the verification gate at Phase 5+.
- Actor-verification at the front door lands in Phase 6.

### Version + manifest

- `pyproject.toml`, `phoenix/_internal/version.py`: `1.0.0.dev1` ->
  `1.0.0.dev2`.
- `vendor/VENDOR_VERSION.txt` regenerated (`phoenix_release: 1.0.0.dev2`,
  `vendor_synced_at: 2026-05-08T18:05:38+00:00`, `dr_frank_and_eddy_commit`
  unchanged at `fa074e5e...`).

---

## [Architecture v1.1 follow-up] — 2026-05-08

Documentation-only follow-up to the 2026-05-07 v1.1 revision. Phoenix-the-package stays at `1.0.0.dev1`; no implementation impact. Captures five architectural decisions Adam approved on 2026-05-08 that future-proof v1 for the perception extension without writing perception code, plus three spec-vs-source drift corrections.

### Architectural future-proofing additions

- **`WobbleAxis` Protocol parameterization (Section 6.3, locks Phase 5 design intent).** The verification gate is parameterized by a list of `WobbleAxis` Protocol implementations rather than hardcoding three named methods. v1 ships three concrete impls (`CrossPrecisionAxis`, `CrossControlAxis`, `CrossProviderAxis`) in `phoenix/verification/wobble_axis.py`. Perception extension's Phase 20 axes (`CrossModalityAxis`, `CrossFrameAxis`, `CrossCanonicalAxis` per the perception plan) plug in as additional `WobbleAxis` impls without forking the gate. Same machinery, different axes. Section 10.3 phoenix/verification/ file list updated with the new `wobble_axis.py` entry.

- **`CloudSeams` generic name-keyed registry (Section 10.3.1).** Refactored from three hardcoded slots (`auth: HttpAuthExtractor`, `audit: AuditLogExporter`, `budget: JobBudgetController`) to a generic dict keyed by name with `register(name, impl)` / `get(name)` / `names()` methods. Default constructor still registers v1's three seams; v1.x extensions register additional seams without core changes. The perception extension's optional fourth seam (`canonical_library` for hosted retention-SLA-bearing canonical-example libraries) plugs in via the same `register()` API. Protocol contracts and SAFETY guarantees unchanged.

- **`LatencyTier` enum (Section 1, post-Decision-28 paragraph).** Three tiers encoded as a single enum in `phoenix/_internal/latency.py`: `BATCH_REALTIME` (v1, routable), `STREAMING_REALTIME` (v2, defined-but-not-routable), `PERCEPTION_REALTIME` (v1.1 perception phase, defined-but-not-routable). v1 routes only `BATCH_REALTIME`; raises typed `LatencyTierNotImplemented` for the other two. Routing layer accepts the tier as a parameter from day one so the perception extension at Phase 12+ doesn't have to retroactively add an enum value or churn callers. **Section 11.14.7 (perception real-time latency tier) RESOLVED** by this enum; open-tension count drops 17 → 16.

- **Front-door namespacing (Section 5).** Decision recorded: `/v1/...` flat with implicit physics semantics. Perception slots in as `/v1/perception/*` sibling (per the perception plan). No spec change required — current spec already commits to this — but recorded for clarity.

- **Strict no-perception-code-in-v1 discipline.** Decision recorded: v1 ships zero perception-shaped code. The v1.1 spec sections (11.14, 10.8) are the only acknowledgments. No empty `phoenix/perception/` or `phoenix/sensors/` directories during v1; perception phase 12 build guide drafts only after v1 Phase 5 milestone per the existing perception plan guardrail.

### Spec-vs-source drift corrections

Three architectural drifts between spec and the actual vendored substrate, surfaced during Phase 1 execution and logged for follow-up:

- **`AgreementType` → `DisagreementType`** (Section 2.2 Result envelope, Section 6.2 vendored types block + Phoenix extension block, prose around line 1165). Spec called the wobble enum `AgreementType`; vendored frank-data has `class DisagreementType(Enum)`. Spec drifted; vendored is source-of-truth per Section 11.7.1's verbatim-through-v1 disposition. Phase 1 tests already use the vendored name; spec now follows reality. Field name `agreement_type` kept (it describes the semantic concept); type renamed to `DisagreementType` (matches the vendored class).
- **`DPDEngine` → `DPDScheduler`** (Section 0 Control description, Section 10.3 phoenix/trinity/control/engine.py description). Same shape: spec drifted from vendored `class DPDScheduler` in `synthesis/core/dpd_engine.py`.
- **`ProbeType.STRONG` → `ProbeType.STRONG_PROJECTIVE`** (Phase 1 build guide content was updated during Phase 1 execution; no architecture spec drift to correct since the spec uses prose "strong projective", not the enum value name). Logged for completeness.

### Spec consistency cleanup (bonus)

- **Section 0 Orchestrate paragraph** updated to reflect the 2026-05-06 SynQc-greenfield revision. The 2026-05-06 commit updated Section 2.5 (Orchestrate as greenfield) but missed Section 0's intro paragraph, which still claimed "Orchestrate vendors the SynQc TDS Core framework." Now correctly describes Orchestrate as greenfield Phoenix code with seven Phoenix-native modules and SynQc as design reference.

### README count update

- README "Documents" section: tension count updated 17 → 16 (with 11.14.7 resolution noted).

### Process notes

- Five architectural decisions and three drift corrections all approved by Adam on 2026-05-08 via a structured-options review of the v1.1 follow-up scope.
- Seven decision points in total: A (verification gate parameterization), B (cloud seams generic registry), C (API namespacing), D (`LatencyTier` enum), E (strict no-perception-code), F (spec drifts), G (commit shape: two commits — this is the second).
- v1.1 is now a two-step revision: 2026-05-07 captured the perception extension scope and 7 tensions; 2026-05-08 locked the v1-side future-proofing and resolved the 7th tension. v1's Phase 0 → Phase 11 build pipeline remains unchanged.

---

## [Architecture v1.1] — 2026-05-07

Architecture-only revision; no package version bump. Phoenix-the-package stays at `1.0.0.dev1`. This entry documents the v1.0 → v1.1 spec revision triggered by the perception harness extension plan locking.

### Added

- **`PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md`** — extension plan locked at v1, 692 lines. All 21 open questions from the v0 draft resolved with Adam's recorded dispositions. Plan positions the perception harness as a v1.x extension landing at Phase 12 onwards (after v1 ships at Phase 11), reusing 70-80% of v1's substrate (vendored Sanskrit codec, grammar substrate, wobble framework, Actor authentication, Omega Ledger pattern, cloud seams). Six Sanskrit techniques scoped: kāraka, chanda, vivakṣā, anuvṛtti, paribhāṣā, lakṣaṇa-lakṣya. Plus Penrose spatial tilings and the IP-defensible novel temporal pulse-coding work (Phase 16).
- **`PHOENIX_ARCHITECTURE_v1.md` Section 11.14** — new tension category for perception extension. Seven entries: 11.14.1 (placement, RESOLVED v1.1), 11.14.2 (substrate vendoring scope, build-guide territory), 11.14.3 (sensor ingest layer placement, RESOLVED v1.1), 11.14.4 (canonical example library storage, RESOLVED v1.1), 11.14.5 (Penrose hardware integration, RESOLVED v1.1 with Q5.2 scalability constraint), 11.14.6 (perception verification axes count, deferred v1.x perception milestone), 11.14.7 (perception real-time latency tier, recommended for documentation).
- **`PHOENIX_ARCHITECTURE_v1.md` Section 10.8** — perception harness extension acceptance criterion. v1.1 acceptance now includes: all perception phases (12-22) shipped per the plan; Tier-1 perception calibration battery passing for supported weather modes; three-axis perception wobble verification producing typed Results; Penrose pulse-train simulator demonstrating ≥20% reconstruction-error reduction at 20% rain corruption; Phase 16 Q5.2 scalability gate (mock hardware driver swap test); front-door endpoints (REST, WebSocket, CLI, MCP) exercising the perception pipeline end-to-end; cross-protocol audit-log correlation working.
- **`PHOENIX_ARCHITECTURE_v1.md` document header** — v1.1 revision date (2026-05-07) and revision summary added to the status line and date timeline.
- **`PHOENIX_ARCHITECTURE_v1.md` Section 0** — new v1.1 transition paragraph documenting the perception extension's positioning as v1.x extension and the documentation-only nature of the revision.
- **`README.md` Documents section** — new "Future extension planning (not part of locked v1)" subsection pointing to `PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md`. Status table row added: "Perception harness extension | LOCKED v1 plan — awaiting Phase 11 release for Phase 12 build guide drafting".

### Changed

- **`PHOENIX_ARCHITECTURE_v1.md` Section 11.9** (Summary of dispositions) — updated to reflect the 7 new tensions added in v1.1. Open-tension count: 14 (v1.0) → 17 (v1.1: 14 + 3 unresolved from v1.1's 11.14.x catalog).

### Critical scalability constraint locked (Q5.2 expansion)

Adam's Q5.2 disposition added a binding architectural principle to Phase 16 that did not exist in the v0 plan: the Penrose temporal pulse-coding simulator must be architected so hardware integration in Phoenix v2 lands as a new driver implementing the same Protocol interfaces, not as a rewrite. Phase 16's deliverables now explicitly include `LidarTransmitter`, `LidarReceiver`, and `InterferenceModel` Protocols defined in `phoenix/perception/penrose/temporal/interfaces.py`. Phase 16's stop gate explicitly tests Protocol compatibility via a mock hardware driver swap. This constraint is binding from Phase 16 day one, not a v2 retrofit, and is recorded in both the perception plan v1 (Section 5, Phase 16) and the architecture v1.1 (Section 11.14.5).

### Process notes

- 21 open questions from `PHOENIX_PERCEPTION_HARNESS_PLAN_v0.md` Section 12 reviewed by Adam on 2026-05-07; all 21 dispositions recorded in v1's Section 12. Adam approved all 21 of Claude's recommendations, with one explicit expansion on Q5.2 (the scalability-on-top constraint).
- v0 of the plan (`PHOENIX_PERCEPTION_HARNESS_PLAN_v0.md`) preserved as historical record alongside v1; may be deleted at Adam's discretion.
- v1.1 is a documentation-only revision. v1.0's load-bearing structure (seven layers, three peer engines in Trinity Core, mandatory three-axis quantum wobble, hashchained provenance, Phoenix Cloud commercial path, all v1 acceptance criteria from Section 10.7) is unchanged. No v1 implementation impact; Phase 0 → Phase 11 build pipeline proceeds unchanged. Perception extension work begins at Phase 12 only after v1 reaches its Phase 5 verification-gate milestone.

---

## [1.0.0.dev1] — 2026-05-06

Phase 1 lands. The vendored substrate from `dr-frank-and-eddy` is real,
calibrated, and exercisable through Phoenix's package boundary. Trinity
Core's Solver and Control subsystems now have the substrate they need
when Phases 2–3 wire them through the pipeline; Orchestrate stays
greenfield per the 2026-05-06 architecture revision.

### Added

- **Step 0 — frank-data housekeeping.** Adam's lab bench at
  `C:\frank-data\` cleaned: 4 untracked items moved to git
  (DrFrankEddy_Capabilities_Overview_for_Ash.md, evolution/candidates/epoch_0001/),
  `electron-debug.log*` added to `.gitignore`. Commit `fa074e5` on
  `nah414/dr-frank-and-eddy/wave-a-through-f-merge`. Prep for the
  vendor source clone.
- **Step 1 — vendor source workspace.** `C:\Phoenix-vendor-source\frank-data\`
  cloned at commit `fa074e5` (sibling of `C:\Phoenix\`, never inside).
  `Phoenix-vendor-source/` added to Phoenix `.gitignore` defensively.
- **Step 2 — vendor sync infrastructure.** `scripts/vendor_sync.py`
  (~370 lines, type-annotated, mypy-strict-clean) + `scripts/vendor_manifest.json`.
  Eight typed errors, four frozen dataclasses, five CLI modes
  (default / --validate-only / --dry-run / --target / --update-version-manifest /
  --generate-calibration). Admin-gate placeholder via `PHOENIX_ADMIN_OVERRIDE=1`
  env var (Phase 6 replaces with safety-gate Actor check).
- **Step 3 — frank-data substrate vendored.** 56 files / 379 KB copied
  per the manifest's 11 mappings: `vendor/synthesis/equations/` (29
  files, 12 solvers + base + registry + llm_context + 12 specs +
  README + __init__), `vendor/synthesis/core/` (6: dpd_engine,
  lindblad_rk4, probe_model, hardware_backends + README + __init__),
  `vendor/synthesis/quantum/tensor_lindblad.py`, `vendor/grammar/`
  (11: 6 grammar files + 5 codec files), `vendor/actor/actor.py`,
  `vendor/wobble/` (6 files). `pyproject.toml` `[tool.ruff]` gains
  `extend-exclude = ["vendor"]` so vendored substrate keeps upstream
  formatting verbatim.
- **Step 4 — sys.path injection.** `phoenix/__init__.py` defines
  `_inject_vendor_path()` that appends `C:\Phoenix\vendor\` to
  `sys.path` on package load. Vendored modules now import at their
  upstream paths (`from synthesis.equations.base import EquationSolver`).
  `[[tool.mypy.overrides]]` block silences mypy on `synthesis.*`,
  `wobble.*`, `grammar.*`, `actor.*` (the vendored code has no type
  stubs and is excluded from analysis already).
- **Step 5 — calibration profile generation.** `vendor/calibration_profile.json`
  (3.9 KB) ships with the source-side calibration suite results: 32/32
  tests passing in 1.4 seconds, all module-level physical constants
  captured (HBAR, M_ELECTRON, MU_BOHR, C_LIGHT, G_NEWTON, EV_TO_JOULE),
  source commit + branch + ISO timestamp. `vendor/VENDOR_VERSION.txt`'s
  `calibration_profile_hash` field now populated.
- **Step 6 — Tier-1 + invariants + DPD test infrastructure.** 13 new
  Phoenix-side tests across three directories:
  - `tests/tier1/`: 5 nominal Tier-1 benchmarks (HO-1 QHO, ISW-1 PIB,
    H1S-1 Dirac, RABI-1 Pauli/Zeeman, SCG-1 weak-field gravity).
  - `tests/invariants/`: 4 grammar invariants (load, safe-load
    discipline, generate-then-parse round-trip, bounded generation).
  - `tests/dpd/`: 4 DPDScheduler structural tests.
  - Runtime deps grow: `numpy>=1.26,<3.0`, `scipy>=1.11,<2.0`,
    `pyyaml>=6.0,<7.0`.
- **Step 7 — Phase 1 acceptance + push.** Version bumps `1.0.0.dev0` →
  `1.0.0.dev1` in `pyproject.toml`, `phoenix/_internal/version.py`,
  test assertions. `vendor/VENDOR_VERSION.txt` regenerated with
  `phoenix_release: 1.0.0.dev1`. `/v1/health` end-to-end check confirms
  daemon serves dev1 with full vendor manifest.

### Changed

- `phoenix.__version__`: `1.0.0.dev0` → `1.0.0.dev1`.
- `vendor/VENDOR_VERSION.txt`: all four hash fields populated (was
  Phase 0 placeholder with empty values).
- `tests/unit/test_smoke.py::test_internal_version_module`: now asserts
  `vendor_synced_at`, `dr_frank_and_eddy_commit`, `calibration_profile_hash`
  are non-empty (Phase 0 had asserted them empty as the placeholder).
- `tests/integration/test_health.py::test_health_returns_200_and_expected_shape`:
  same flip on the `/v1/health` response shape assertions.

### Open architectural drifts (logged for follow-up before Phase 5)

Three spec-vs-source naming drifts surfaced during Phase 1 execution
when the test code touched the actual vendored API:

1. **`AgreementType` (spec §6.2) vs `DisagreementType` (vendored).**
   The architecture spec names the wobble enum `AgreementType` with
   extended physics-wobble values; the actual vendored `wobble/disagreement_types.py`
   has `class DisagreementType(Enum)` (the upstream cognition-wobble name).
2. **`DPDEngine` (spec) vs `DPDScheduler` (vendored).** Spec references
   `DPDEngine`; the actual vendored class in `synthesis/core/dpd_engine.py`
   is `DPDScheduler`.
3. **`ProbeType.STRONG` (spec implied) vs `ProbeType.STRONG_PROJECTIVE`
   (vendored).** The vendored enum value spells out `STRONG_PROJECTIVE`,
   `WEAK_MEASUREMENT`, `ANCILLA_BASED`, `NONE`.

Phase 1's Phoenix-side tests use the real (vendored) class names and
pass. A single spec-drift-correction commit before Phase 5 (verification
gate work) will resolve all three: rename in spec, alias on Phoenix
side, or accept upstream names as authoritative.

### Acceptance

Phase 1 acceptance (build guide §3.7):
- ✅ `vendor/` populated with frank-data content; `VENDOR_VERSION.txt`
  has all four fields (phoenix_release, vendor_synced_at,
  dr_frank_and_eddy_commit, calibration_profile_hash) with real values.
- ✅ `vendor/calibration_profile.json` exists, hash matches `VENDOR_VERSION.txt`.
- ✅ `python -c "from synthesis.equations.base import EquationSolver"` works.
- ✅ `pytest tests/tier1/`: 5/5 (HO-1, ISW-1, H1S-1, RABI-1, SCG-1).
- ✅ `pytest tests/invariants/`: 4/4 grammar invariants.
- ✅ `pytest tests/dpd/`: 4/4 DPD structural tests.
- ✅ `pytest tests/`: 19/19 combined.
- ✅ `pre-commit run --all-files`: ruff, ruff-format, mypy strict, smoke -- all 4 Passed.
- ✅ `python -m phoenix.api --port 8003`: daemon boots; `GET /v1/health`
  returns `phoenix_version=1.0.0.dev1` and the full vendor manifest.
- ✅ `git status`: working tree clean after Step 7 commit.

### Process notes

- 7 phase-gated commits + 1 housekeeping commit in `frank-data`. Each
  Phoenix-side step ended at `=== STEP N COMPLETE — AWAITING ADAM REVIEW ===`;
  no auto-advancement.
- Build-guide sequencing fix: Phase 1's Step 4 (sys.path) and Step 5
  (calibration generation) both surfaced live in execution rather than
  ahead of time -- they were `[OPEN: ...]` items in the Phase 1 build
  guide that resolved as the code was written.

---

## [1.0.0.dev0] — 2026-05-06

The repository skeleton lands. No physics yet; this release is the foundation
that subsequent phases build on. All eight Phase 0 build-guide steps executed
through phase-gated review with Adam; final acceptance verified end-to-end.

### Added

- **Architecture specification** at v1 (`PHOENIX_ARCHITECTURE_v1.md`, ~2,900
  lines covering Trinity Core's three subsystems, the seven wrapping layers,
  mandatory three-axis wobble verification, hashchained Omega Ledger
  provenance, end-to-end cost-ceiling enforcement, the Phoenix Cloud
  commercial path, and 14 catalogued open design tensions).
- **Phase 0 build guide** (`BUILDGUIDE_phoenix_v1_phase0_skeleton.md`)
  directing the eight-step skeleton work with phase-gated reviews between
  each step.
- **Top-level scaffolding** (Step 1): `pyproject.toml` with pinned upper
  bounds and `>=3.11,<3.14` Python constraint; `requirements.lock`
  placeholder for Phase 1's `uv` lockfile; `.gitignore`, `.gitattributes`,
  `.pre-commit-config.yaml` (ruff + mypy strict + smoke-test), `CHANGELOG.md`.
- **`phoenix/` package skeleton** (Step 2): 26 directories with `__init__.py`,
  one per architectural Section. Two non-empty: `phoenix/__init__.py` exports
  `__version__`; `phoenix/_internal/version.py` defines the constant +
  `read_vendor_version()`.
- **`vendor/` scaffold** (Step 3): directory + `VENDOR_VERSION.txt`
  placeholder with `phoenix_release: 1.0.0.dev0` and four hash fields empty
  (Phase 1 vendor sync populates).
- **Launcher chain** (Step 4): `scripts/launch.bat`, `scripts/launch.sh`,
  `scripts/create_shortcut.ps1` — Phoenix daemon on port 8003 (port 8002
  reserved for dr-frank-and-eddy).
- **29 per-section READMEs** (Step 5): 21 across `phoenix/`+`vendor/` +
  8 in the `evals/` audit/debug scaffold (audit, ledger, replay, drift,
  routing, cost_ceiling, frontier_physics).
- **Test infrastructure + FastAPI daemon** (Steps 6+7 combined due to
  inter-step dependency): `tests/unit/test_smoke.py` (3 tests),
  `tests/integration/test_health.py` (2 tests using FastAPI TestClient),
  `phoenix/api/routes.py` (FastAPI app exposing `GET /v1/health`),
  `phoenix/api/__main__.py` (`python -m phoenix.api --port 8003`),
  `phoenix/api/error_envelope.py` (typed envelope dataclass per §5.2).

### Changed

- **LICENSE**: switched from MIT (auto-generated at repo creation) to Apache 2.0
  per architecture v1 Decision 34 — open source plus the patent grant as
  belt-and-suspenders against future patent claims on calibration methodology.
- **README**: expanded from the one-line repo-creation placeholder to a
  project-shaped overview with the v1 status table and pointers to the
  architecture spec and the Phase 0 build guide.

### Fixed

- **PEP 440 version compliance**: setuptools rejected the originally-chosen
  `1.0.0-phase0` literal as not-PEP-440. All artifacts now use `1.0.0.dev0`
  (Phase 0 development pre-release per PEP 440); subsequent phases use
  `1.0.0.dev1`, `1.0.0.dev2`, etc.
- **Python version constraint**: widened from `>=3.11,<3.13` to `>=3.11,<3.14`
  to accommodate the actual development environment (Python 3.13.9). The
  upper-bound discipline from the dep-tightening pass is preserved (3.14
  stays gated until validated).

### Acceptance

Phase 0 acceptance criteria from build guide §3.8 (15 items):

- ✅ `python -c "import phoenix; print(phoenix.__version__)"` returns `1.0.0.dev0`.
- ✅ `pytest tests/` passes 5/5 (3 unit + 2 integration via FastAPI TestClient).
- ✅ `pytest evals/ --collect-only` exits 5 ("no tests collected") — expected
  Phase 0 state since `evals/` ships placeholder READMEs only.
- ✅ `python -m phoenix.api --port 8003` boots the daemon; `/v1/health`
  responds with the expected contract; `/v1/openapi.json` serves OpenAPI 3.1
  with the matching version.
- ✅ All 29 READMEs present and non-empty.
- ✅ `pre-commit install` placed the hook; `pre-commit run --all-files` clean
  on all four hooks (ruff, ruff-format, mypy strict, pytest smoke-test).
- ✅ Working tree clean after commits.
- ⏸ Interactive: browser at `http://127.0.0.1:8003/docs` (Adam-driven).
- ⏸ Interactive: `scripts/launch.bat` end-to-end (Adam-driven).
- ⏸ Interactive: `scripts/create_shortcut.ps1` + double-click flow (Adam-driven).

### Process notes

- Step 6 and Step 7 were combined into a single commit because the build
  guide's sequencing was wrong: the integration test depends on the daemon
  code. Documented in the commit message; a future build-guide revision
  should re-sequence (or merge) the two steps.

### Architecture revision (2026-05-06, post Phase 0)

- **`PHOENIX_ARCHITECTURE_v1.md` revised** to remove the v0 spec's
  internal contradiction around SynQc TDS Core. Decision 37 originally
  said "code skeleton, not literal git fork" but other places in the
  spec (§1 Decision 4, §2.5, §10.2) said SynQc was vendored verbatim
  alongside frank-data. The revised spec aligns with Decision 37: SynQc
  is a *design reference* for Trinity Core's Orchestrate subsystem;
  Orchestrate is greenfield Phoenix code under
  `phoenix/trinity/orchestrate/`, not vendored.
- **Affected sections:** §1 Decisions 4, 5, 7, 9, 37 (reworded);
  §2.5 (rewritten — Orchestrate as greenfield with Phoenix-native
  module breakdown: bundle_builder, provider_client, result_extractor,
  drift_feedback, cross_provider, kpi_bundle, engine);
  §10.1 (drops `vendor/synqc_tds/` from directory tree);
  §10.2 (drops the SynQc TDS vendoring table; updates VENDOR_VERSION
  format to remove `synqc_tds_commit` field);
  §10.3 (`phoenix/trinity/orchestrate/` description specifies the
  seven Phoenix-native modules);
  §10.4 (vendor sync script takes only frank-data as input);
  preamble adds a v1-revision transition note.
- **Phase 0 README updates:** `phoenix/trinity/orchestrate/README.md`,
  `phoenix/trinity/README.md`, `phoenix/providers/README.md`,
  `vendor/README.md` updated to match the revised spec.
- **`vendor/VENDOR_VERSION.txt`** drops the `synqc_tds_commit` field.
- **Phase 0 build guide** stale `synqc_tds_commit:` example updated.
- **Phase 1 build guide drafted** at `BUILDGUIDE_phoenix_v1_phase1_vendor_sync.md`
  reflecting the simpler frank-data-only scope (8 phase-gated steps,
  vs. 9 in the original draft that included a SynQc-vendoring step).
- **Discovery driver:** Phase 1 build-guide drafting against actual
  source state (the SynQc zip in Adam's Downloads) found that SynQc's
  module structure (`backend/synqc_backend/` FastAPI service) didn't
  match the v0 spec's named files (`scheduler.py`, `probes/`,
  `demod.py`, `adapt.py`). Live reads beat memory.

The architecture's load-bearing structure (seven layers, three peer
engines, mandatory three-axis wobble, hashchained provenance, Phoenix
Cloud commercial path, fourteen open tensions, all v1 acceptance
criteria) is unchanged. The revision narrows the substrate that
Phoenix vendors and clarifies that Orchestrate is Phoenix-native code
informed by SynQc patterns, not vendored from SynQc.
