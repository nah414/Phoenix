# evals/ledger/

## Purpose
Evaluations that verify the Omega Ledger's hashchain stays valid under every operation Phoenix performs. The ledger is append-only and tamper-evident: a single corrupted link breaks the chain, and the integrity walk surfaces the position. Architecture v1 Section 1 Decision 15 commits to bit-exact replay support; this directory's job is to prove the chain semantics hold under solves, failovers, overrides, kill-switch events, enrollments, and revocations.

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 1 Decision 15 (hashchained provenance with bit-exact replay), Section 6.7 (verification provenance composition), Section 8.2 (`/v1/admin/ledger/integrity-report`).

## Phase
This directory is populated in Phase 7 (audit + ledger + drift). Phase 0 ships only this README placeholder.

## What evals will land here
- A solve writes one ledger entry; its `prior_entry_hash` matches the previous entry's hash.
- An operator override produces a ledger entry tagged `OVERRIDE_BY_OPERATOR` whose hashchain link is valid.
- A kill switch engage/release pair lands as `KILL_SWITCH_ENGAGED` and `KILL_SWITCH_RELEASED` entries with the operator's actor signature.
- Org enrollment and install revocation produce `ENROLLMENT` and `REVOCATION` entries.
- A deliberately corrupted ledger entry (test fixture) is detected by `GET /v1/admin/ledger/integrity-report` with the broken position pinpointed.
- Concurrent writes (Postgres backend) preserve order via advisory locks; no two entries claim the same `prior_entry_hash`.
- Long-window holding: a ledger entry from N months ago can still be walked correctly after Phoenix has shipped multiple v1.x patches in between.

## Recent changes
- 2026-05-06 — Phase 0: placeholder created.
