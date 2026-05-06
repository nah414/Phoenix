# evals/cost_ceiling/

## Purpose
Evaluations that verify Phoenix's cost-ceiling enforcement allows what it should and denies what it shouldn't, at every enforcement point: pre-solve check (Router Stage 2), mid-pipeline check (verification gate's promotion decision), post-solve accounting (state backend's `solve_cost_ledger` table). The 2026-05-06 architectural addition specified default ceilings ($5/$25/$50 per-solve by mode; $50/$500/unlimited per-actor-24h by tier; $2000 per-org-24h); this directory proves they hold under realistic concurrent load.

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 4.7 (cost estimation + default budgets + three enforcement points + admin override path), Section 6.4 (verification gate's promotion-vs-ceiling rule + `DEGRADED_BUDGET_BOUND` agreement type), Section 7.5 (rate-limit-tier-aligned per-actor-24h ceilings).

## Phase
This directory is populated in Phase 4 (Router) onward; the verification-gate-side checks land in Phase 5; the post-solve accounting in Phase 6. Phase 0 ships only this README placeholder.

## What evals will land here
- A solve estimated to exceed `per_solve_ceiling` is rejected at Router Stage 2 with `CostCeilingExceeded` (not `NoEligibleProvidersError` — the user sees the actual reason).
- A solve under the per-solve ceiling but exceeding the actor's 24-hour cumulative budget is rejected with the same typed error and the right rationale.
- A solve whose verification promotion (e.g. R3 → R4) would push past the per-solve ceiling: the gate skips promotion and ships `agreement_type=DEGRADED_BUDGET_BOUND` with a `budget_bound_skipped_axis` provenance field. The result still ships; the user can re-submit at higher ceiling.
- An admin actor's `POST /v1/admin/budget/override` grants a temporary budget bump; the override is itself a top-priority audit event and Omega Ledger link.
- Override never *removes* a ceiling — `new_ceiling_usd=null` is rejected; only finite values accepted.
- Org-level cumulative ceiling: a single misbehaving install in an org cannot exhaust the org's 24-hour budget for everyone (because both per-actor AND per-org checks fire on every solve).

## Recent changes
- 2026-05-06 — Phase 0: placeholder created.
