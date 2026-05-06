# evals/frontier_physics/

## Purpose
Evaluations that verify Phoenix refuses frontier-physics requests (Wheeler-DeWitt, Gravitational Decoherence, Semiclassical Gravity) when the actor lacks the `frontier_physics` capability — at multiple layers, defense-in-depth. Architecture v1 Section 7.4 step 6 puts the primary check in the safety gate; Section 4.4 stage 4 puts a defense-in-depth re-check in the Router. This directory's job is to prove both fire correctly and that the typed `FrontierPhysicsRefused` error includes the regime in the audit log.

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 1 Decision 7 (Wheeler-DeWitt + gravitational solvers gated as `frontier_physics`), Section 7.3 (`ActorPermissions.frontier_physics` flag default False), Section 7.4 step 6 (safety gate's frontier-physics deep check), Section 4.4 Stage 4 (Router's defense-in-depth re-check), Section 2.4 (Control subsystem's gate at the engine boundary — third layer of defense).

## Phase
This directory is populated in Phase 7 (audit + ledger + drift). Phase 0 ships only this README placeholder. (Note: the safety-gate primary check itself lands in Phase 6; the Router re-check in Phase 4. The eval validates the composition once both are in place.)

## What evals will land here
- An actor with `frontier_physics=False` submitting a Wheeler-DeWitt task fails with `403 FrontierPhysicsRefused`; the regime is named in the audit-log entry; Trinity Core is never invoked.
- The same task with `frontier_physics=True` proceeds normally; the regime is logged but no refusal fires.
- Defense-in-depth: even if a hypothetical bug in the safety gate let a frontier task through, the Router's Stage 4 check would still refuse it. Synthetic test that injects past the safety gate confirms the Router's re-check fires.
- Triple-defense: even past the Router, Control's frontier gate (Section 2.4) refuses at the engine boundary. Triple defense-in-depth is intentional; the eval verifies all three layers fire independently.
- `FrontierPhysicsRefused` audit event includes the resolved regime name (`WHEELER_DEWITT`, `GRAVITATIONAL_DECOHERENCE`, or `SEMICLASSICAL_GRAVITY`) so ops can grep for specific frontier requests.

## Recent changes
- 2026-05-06 — Phase 0: placeholder created.
