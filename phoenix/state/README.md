# phoenix/state

## Purpose
**State backend** per architecture v1 Section 1 Decision 31. SQLite by default (zero-config, in-process); Postgres opt-in via config flag for org deployments needing concurrency, replication, or audit-grade durability. State backend chosen at startup, not switchable at runtime. Holds: actor permissions registry, persisted kill-switch state, solve cost ledger (per-actor 24-hour budget tracking), audit events, pending-review queue, ledger entries.

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 1 Decision 31 (SQLite default, Postgres opt-in), Section 4.7 (`solve_cost_ledger` table for cost-ceiling enforcement), Section 7.3 (`actor_permissions` registry append-only), Section 7.7 (`pending_review_queue`), Section 8.3 (`kill_switch_state` persistence per resolved Section 11.5.1), Section 10.3 (file layout naming the migrations and table list).

## Key files and their roles
| File | Role |
|---|---|
| `__init__.py` | Empty Phase 0 stub. |
| `backend_protocol.py` | (Phase 6) Abstract `StateBackend` interface. |
| `sqlite_backend.py` | (Phase 6) Default zero-config implementation. |
| `postgres_backend.py` | (Phase 6) Opt-in for org deployments needing concurrency, replication, audit-grade durability. |
| `migrations/` | (Phase 6) Schema migrations versioned with Phoenix releases. |

## Migration tables (per Section 10.3)
- `solve_cost_ledger` — per-solve cost accounting (Section 4.7); sum over rolling 24-hour window drives the per-actor and per-org ceiling enforcement.
- `kill_switch_state` — engaged/released state with operator identity; persisted across restart per Section 11.5.1's resolution.
- `actor_permissions` — append-only permission grants/revocations (auditable history of who-had-what-when).
- `audit_events` — structured event log; the `audit` module's persistent destination.
- `pending_review_queue` — tasks awaiting `HUMAN_REVIEW` operator override per Section 7.7.

## Vendored substrate
None. `phoenix/state/` is greenfield Phoenix code.

## Common failure modes
- `StateBackendUnavailable` — SQLite locked or Postgres unreachable; safety gate fails closed.
- `MigrationConflict` — backend's schema version doesn't match the running Phoenix release.
- `ConcurrentModification` — Postgres-only, when two Phoenix processes attempt to update the same row; resolved via Postgres advisory locks.

## Troubleshooting
- State backend chosen at startup via `~/.phoenix/config.yaml`'s `state_backend` field (`sqlite` default; `postgres` opt-in with connection string). Switching mid-run requires restart.
- Per-actor 24-hour budget: query the `solve_cost_ledger` for the rolling window; default ceilings come from Section 4.7 and are overridable per-install.

## Tests
- `tests/unit/test_smoke.py` — asserts `phoenix.state` imports.
- `evals/cost_ceiling/` (Phase 6+) — `solve_cost_ledger` accumulates correctly under concurrent solves.

## Recent changes
- 2026-05-06 — Phase 0: module created as empty stub.
