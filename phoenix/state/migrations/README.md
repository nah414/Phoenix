# `phoenix/state/migrations/`

Python-callable schema migrations applied by
`phoenix.state.migrations.runner` at backend construction time.
Each migration module declares `VERSION` (monotonic int),
`DESCRIPTION` (one-liner), and `apply(conn, phoenix_release)` /
`revert(conn)` functions; the runner records applied versions in
the `schema_version` metadata table for idempotency.

| Version | Module | Subject |
|---|---|---|
| 1 | `phase6b_initial.py` | Six initial tables (kill switch, solve cost ledger, audit events, pending review queue, actor permissions, drift state snapshot). |
| 2 | `phase7_ledger.py` | `ledger_entries` table backing the Omega Ledger (Section 6.7). |
| 3 | `phase10_cost_ledger.py` | Extends `solve_cost_ledger` with `org_id` / `reproducibility_mode` / `provenance_json` columns + creates `budget_overrides` table (Section 4.7 cost-ceiling enforcement). |

Per locked Phase 6b open-item 1 (2026-05-10): migrations are
Python-callable (not pure SQL) so the runner can dispatch SQLite
vs. Postgres dialect inside each `apply` body via
`isinstance(conn, sqlite3.Connection)`. Phase 6b Step 3 added
Postgres dialect support; both dialects share table shapes but
differ in types + identity-column syntax (see
`phase6b_initial.py` docstring for the catalog).

Architectural reference: `PHOENIX_ARCHITECTURE_v1.md` Section 10.3
(state backend table inventory), Section 6.7 (ledger schema),
Section 4.7 (cost-ledger schema).
