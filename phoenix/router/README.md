# phoenix/router

## Purpose
The **provider routing and hardware-intelligence layer** per architecture v1 Section 4. Takes a `RoutingRequest` (task + user policy) and returns a `RoutingDecision` (chosen provider + alternates + rationale + cost/latency/fidelity estimates + decision provenance). The Router never runs jobs itself — Trinity Core's Orchestrate subsystem invokes the chosen `BaseProviderClient` and reports failures back to the Router for failover handling. Cost-ceiling enforcement (Section 4.7) and pricing-staleness soft-warn live here.

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 4 (Router), Section 4.4 (seven-stage decision algorithm), Section 4.5 (multi-provider failover), Section 4.6 (hardware-intelligence layer), Section 4.7 (cost estimation + ceiling enforcement + staleness policy), Section 4.8 (router behavior under reproducibility-strict and replay).

## Key files and their roles
| File | Role |
|---|---|
| `__init__.py` | Empty Phase 0 stub. |
| `decision.py` | (Phase 4) Seven-stage routing algorithm: modality eligibility → user policy filter → provider health filter → frontier-physics gate → reproducibility constraint → ranking → decision provenance. |
| `provider_registry.py` | (Phase 4) Per-provider health, queue depth, last-calibration timestamp, reliability history. |
| `intelligence.py` | (Phase 4) Three-source fidelity/latency/cost estimator (vendored `HardwareParams` + live telemetry + historical ledger). |
| `failover.py` | (Phase 4) Multi-provider failover protocol with exponential-backoff health quarantine. |
| `pricing/pricing_v1.json` | (Phase 4) Versioned per-provider pricing data; refreshable via `phoenix admin pricing-update`. |

## Vendored substrate
Two layered abstractions vendored:
- **Frankenstein 1.0 `ProviderAdapter` ABC** from `integration/providers/base.py` — universal provider interface across 19 quantum providers + 12 classical.
- **SynQc TDS `BaseProviderClient` Protocol** + `ProviderLiveResult` dataclass — experiment-preset interface used by Trinity Core's Orchestrate subsystem.

`phoenix/router/` orchestrates these; it is greenfield Phoenix code on top of the vendored interfaces.

## Common failure modes
- `NoEligibleProvidersError` — every candidate filtered out at some Stage 1–5.
- `CostCeilingExceeded` — estimated cost exceeds per-solve, per-actor-24h, or per-org-24h ceilings (Section 4.7).
- `FrontierPhysicsRefused` — defense-in-depth re-check at Stage 4 (primary check is in Section 7).
- `ReplayProviderUnavailable` — replay mode requires the original provider, which is currently degraded or offline.
- `IntelligenceUnavailable` — all three intelligence sources failed to respond.
- `AllAlternatesExhausted` — every alternate from the original `RoutingDecision.alternates` list also failed.

## Troubleshooting
- Routing decisions land in the audit log with full `decision_provenance` per Section 4.4 Stage 7. Inspect via the dev-ops backdoor: `GET /v1/admin/router/decisions`.
- Manual provider quarantine: `POST /v1/admin/providers/{id}/manual-quarantine` when ops has out-of-band knowledge (e.g., maintenance announcement) telemetry hasn't caught up to.
- Pricing-data staleness >90 days fires a soft warning in the Result envelope; refresh out-of-band via `phoenix admin pricing-update`.

## Tests
- `tests/unit/test_smoke.py` — asserts `phoenix.router` imports.
- `evals/routing/` (Phase 4+) — provenance recording correctness; failover behavior; cost-ceiling decisions.
- `evals/cost_ceiling/` (Phase 4+) — ceiling enforcement allows/denies per Section 4.7's defaults.

## Recent changes
- 2026-05-06 — Phase 0: module created as empty stub.
