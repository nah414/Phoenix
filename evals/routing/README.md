# evals/routing/

## Purpose
Evaluations that verify the Router's seven-stage decision algorithm records full `decision_provenance` for every routing decision (which candidates were considered, which were filtered at each stage, the scoring weights used, the alternates list), and that failover behaves correctly under degraded provider state. Architecture v1 Section 4 commits to the routing being explicit and audit-able; this directory's job is to prove it.

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 4 (Router), Section 4.4 (seven-stage decision algorithm + decision_provenance), Section 4.5 (multi-provider failover), Section 4.6 (hardware-intelligence layer — three sources), Section 4.8 (router behavior under reproducibility-strict and replay).

## Phase
This directory is populated in Phase 4 (Router). Phase 0 ships only this README placeholder.

## What evals will land here
- Every routing decision lands with the seven-stage breakdown in `decision_provenance`.
- A deliberately degraded provider (manually quarantined via `POST /v1/admin/providers/{id}/manual-quarantine`) is filtered at Stage 3.
- Failover to the next alternate when the primary's invocation fails mid-execution; both decisions land in `provenance.routing_decisions` (a list, not a single value).
- Provider-equivalence rules (the conservative defaults: same `quantum_technology` + same qubit count + fidelity within 10%) plus manual override registry — see Section 11.2.1 for the open research question about general equivalence.
- Replay-mode replay verifies the same provider is still available; raises `ReplayProviderUnavailable` cleanly when it isn't.
- Cross-provider verification (Axis 3 wobble): Section 6 invokes the Router with `excluded_providers` set to the primary's choice for a second-provider lookup; the Router records this as a separate routing decision and Section 4 doesn't know it's the verification run.

## Recent changes
- 2026-05-06 — Phase 0: placeholder created.
