# phoenix/admin

## Purpose
The **dev-ops backdoor** at `/v1/admin/...` per architecture v1 Section 8. Single privileged surface for inspection, diagnostics, manual interventions, and the kill switch. Gated by the `is_admin` capability (Section 7.3); ships the same audit-log discipline as user-facing endpoints. Architectural principle: **Phoenix is operable without paging the developer.** Read-only inspection by default; the mutation surface is exactly seven endpoints (kill switch engage/release, manual quarantine/restore, override, calibration run, force-revalidate adapter).

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 8 (dev-ops backdoor), Section 8.2 (endpoint surface), Section 8.3 (kill switch — persistent across restart per Section 11.5.1 resolution), Section 8.4 (read-only-by-default principle + permanent-no on baseline override per Section 11.5.2), Section 8.5 (Phoenix Cloud integration), Section 8.6 (observability beyond admin endpoints).

## Key files and their roles
| File | Role |
|---|---|
| `__init__.py` | Empty Phase 0 stub. |
| `health.py` | (Phase 8) `/v1/admin/health/detailed`, `/governor`, `/inference-status`, `/budget`. |
| `calibration.py` | (Phase 8) `/v1/admin/calibration/detail`, `/run`, `/history`. |
| `router_inspect.py` | (Phase 8) `/v1/admin/router/decisions`, `/providers/health-history`, manual quarantine/restore. |
| `verification_inspect.py` | (Phase 8) `/v1/admin/tasks-pending-review` + override invocation; `/verification/rung-distribution`. |
| `kill_switch.py` | (Phase 8) Engage/release/status; persisted state in state backend per Section 11.5.1. |
| `audit_replay.py` | (Phase 8) `/v1/admin/audit/replay`, `/ledger/integrity-report`. |

## Vendored substrate
None. `phoenix/admin/` is greenfield Phoenix code.

## Common failure modes
- `AdminPrivilegeRequired` (403) — non-admin actor calling admin endpoint.
- `KillSwitchEngaged` (503) — current operator-engaged state; new task submissions blocked until release.
- `QuarantineDurationExceeded` (400) — manual quarantine duration exceeds policy max (default cap 24 hours).
- `TaskNotPendingReview` (409) — override target task already completed or already overridden.
- `CalibrationRunInProgress` (409) — drift cycle already running.
- `AdapterNotLoaded` (404) — force-revalidate target adapter has been unloaded.

## Troubleshooting
- All admin actions are top-priority audit events with operator's full actor identity; some (kill switch, override) also land as Omega Ledger hashchain links.
- Kill switch persistence: if Phoenix refuses to start with `kill_switch_state.engaged_when_shutdown=True`, an admin must call `POST /v1/admin/kill-switch/release` to begin accepting work. Per Section 11.5.1's resolved disposition.
- Manual baseline override is a permanent NO (Section 11.5.2). Recalibration ships via the next Phoenix release's `vendor/calibration_profile.json`, not via runtime mutation.

## Tests
- `tests/unit/test_smoke.py` — asserts `phoenix.admin` imports.
- `evals/audit/` (Phase 7+) — admin actions write audit + ledger entries with operator identity.

## Recent changes
- 2026-05-06 — Phase 0: module created as empty stub.
