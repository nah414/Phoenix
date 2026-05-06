# evals/audit/

## Purpose
Evaluations that verify Phoenix's audit log captures every required event type with the right structure, in the right order, with the right actor identity attached. Architecture v1 Section 1 Decision 16 commits to audit-grade structured logging on every Trinity Core layer transition, every router decision, every authentication check, every drift alert, every config change. This directory's job is to prove that commitment under load.

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 1 Decision 16 (audit-grade structured logging), Decision 22 (OpenTelemetry as export standard), Section 5.6 (cross-protocol `request_id` correlation), Section 8.6 (audit-log streams).

## Phase
This directory is populated in Phase 7 (audit + ledger + drift). Phase 0 ships only this README placeholder.

## What evals will land here
- Every Trinity Core subsystem transition (Solver→Control→Orchestrate) emits a structured event with the expected schema.
- Every router decision lands with full `decision_provenance` (which candidates, which were filtered at each stage, the scoring weights, the alternates list).
- Every authentication check (pass or fail) emits an event with actor fingerprint, capability checked, decision, request_id.
- Every drift alert (per-detector firing, multi-detector confirmation) lands as a top-priority event.
- Every config change emits an event naming the field, before/after values (when not secret), and the operator's actor signature.
- Every kill switch engage/release and operator override is BOTH a top-priority audit event AND an Omega Ledger hashchain link.
- Cross-protocol correlation: a single `request_id` is visible across REST → audit-log → ledger → MCP → WebSocket.
- Buffer-full failure mode: when the async writer is overwhelmed, dropped events are counted (not silently swallowed) and exposed in `/v1/admin/health/detailed`.

## Recent changes
- 2026-05-06 — Phase 0: placeholder created.
