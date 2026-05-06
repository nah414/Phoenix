# phoenix/trinity/orchestrate

## Purpose
Trinity Core's **Orchestrate** subsystem — the hardware-orchestration layer. Vendors SynQc TDS Core's `scheduler`, `probes`, `demod`, `adapt` modules and the `BaseProviderClient` interface verbatim per architecture Section 1 Decision 37. Per Section 2.5, Orchestrate takes a `VerifiedAnswer` plus a `ProviderSelection` from the Router and produces a `Result` with cross-provider wobble (Axis 3).

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 2.5 (Orchestrate subsystem), Section 2.6 (cross-provider Axis 3), Section 4 (Router which selects the provider for Orchestrate to invoke), Section 6.4 (rung table — when Axis 3 fires).

## Key files and their roles
| File | Role |
|---|---|
| `__init__.py` | Empty Phase 0 stub. |
| `engine.py` | (Phase 3) Adapts SynQc `scheduler/probes/demod/adapt` into Trinity's pipeline. |
| `cross_provider.py` | (Phase 5) Axis 3 wobble — runs the same bundle on two providers (typically chosen primary + local simulator) and produces `error_bar_orchestrate`. |
| `kpi_bundle.py` | (Phase 3) Typed `KPIBundle` aggregator — `fidelity`, `latency_us`, `backaction`, `shots_used`, `shot_budget`, `status`. |

## Vendored substrate
Vendors SynQc TDS Core's modules verbatim into `vendor/synqc_tds/`:
- `scheduler.py` — time-ordered DPD bundle scheduler.
- `probes/` — typed probe catalog (strong projective, weak continuous, ancilla-mediated).
- `demod.py` — IQ demodulation + feature extraction.
- `adapt.py` — Kalman/Bayesian drift tracking.
- `provider_clients/` — `BaseProviderClient` Protocol + concrete adapters for IBM Quantum, AWS Braket, IonQ.

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
