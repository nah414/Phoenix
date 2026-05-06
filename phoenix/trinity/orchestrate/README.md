# phoenix/trinity/orchestrate

## Purpose
Trinity Core's **Orchestrate** subsystem — the hardware-orchestration layer. **Greenfield Phoenix code** per architecture v1 Section 2.5 and Decision 37 (revised 2026-05-06). SynQc TDS Core is a *design reference* for the contracts here; no SynQc code is vendored or imported. Per Section 2.5, Orchestrate takes a `VerifiedAnswer` from Control plus a `ProviderSelection` from the Router and produces a `Result` with cross-provider wobble (Axis 3).

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 2.5 (Orchestrate subsystem — greenfield), Section 2.6 (cross-provider Axis 3), Section 4 (Router which selects the provider for Orchestrate to invoke), Section 6.4 (rung table — when Axis 3 fires), Section 1 Decision 37 (SynQc TDS Core as design reference, not vendoring source).

## Key files and their roles
| File | Role |
|---|---|
| `__init__.py` | Empty Phase 0 stub. |
| `engine.py` | (Phase 3) Top-level orchestrator: takes (`VerifiedAnswer`, `ProviderSelection`) → runs the orchestration pipeline → returns `Result`. |
| `bundle_builder.py` | (Phase 3) Translates `VerifiedAnswer` into a provider-specific submission (Qiskit circuit, Braket task, IonQ shot batch, classical-sim Hamiltonian). Pure translation, no I/O. |
| `provider_client.py` | (Phase 3) `BaseProviderClient` Protocol + dispatch to per-provider concrete adapters under `phoenix/providers/`. Connection management, submission, polling, raw-result return. |
| `result_extractor.py` | (Phase 3) Provider raw results → Phoenix-uniform observables and KPI fields. Pure post-processing, no I/O. |
| `drift_feedback.py` | (Phase 3) Emits drift signals to the Router intelligence (Section 4.6) and the drift detector (Section 6.5). |
| `cross_provider.py` | (Phase 5) Axis 3 wobble — runs the same bundle on two providers (typically chosen primary + local simulator) and produces `error_bar_orchestrate`. |
| `kpi_bundle.py` | (Phase 3) Typed `KPIBundle` aggregator — `fidelity`, `latency_us`, `backaction`, `shots_used`, `shot_budget`, `status`. |

## Vendored substrate
**None directly.** Orchestrate is greenfield Phoenix code; nothing in this directory is vendored.

The hardware-modality constants (T1, T2, gate errors, native gate sets per modality) are sourced from the *vendored* `vendor/synthesis/core/hardware_backends.py` (frank-data); per-provider adapter classes live under `phoenix/providers/{quantum,classical,...}/` and provide concrete overrides. So Orchestrate *consumes* vendored data without itself being vendored.

## SynQc TDS Core as design reference
The architecture's prior v0 commitment to vendoring SynQc files was reversed in the 2026-05-06 revision after Phase 1 build-guide drafting found SynQc's actual source structure (FastAPI service with auth/Redis/agents/jobs) unsuitable for verbatim vendoring. SynQc serves as a design reference: its concerns (scheduling, probes, demodulation, drift adaptation, provider dispatch) inform Phoenix's module breakdown, but Phoenix's modules are organized by Phoenix's task lifecycle, not SynQc's terminology. See Section 1 Decision 37 and Section 2.5 for the full rationale.

## Common failure modes
None yet — Phase 0 skeleton stub. From Phase 3+:
- Cloud/simulator disagreement beyond shot-noise expectation raises `WOBBLE` (Section 2.5).
- Provider degradation triggers failover to alternates per Section 4.5; `cloud_shots_recorded=True` provenance flag set when any cloud provider was invoked (Section 2.2's reproducibility asterisk).

## Troubleshooting
Module is empty in Phase 0.

## Tests
- `tests/unit/test_smoke.py` — asserts `phoenix.trinity.orchestrate` imports.
- `tests/tier1/` (Phase 3+) — provider-routed tests exercise the full Trinity Core pipeline.

## Recent changes
- 2026-05-06 — Phase 0: module created as empty stub.
