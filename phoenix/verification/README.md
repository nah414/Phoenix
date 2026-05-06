# phoenix/verification

## Purpose
The **verification gate** that orchestrates Phoenix's mandatory three-axis wobble protocol per architecture v1 Section 6. Sits *around* Trinity Core, not inside it: the gate decides which axes fire and at what depth (R1–R5 rung table), invokes Trinity Core's three subsystems repeatedly with different parameters, and composes the final `DisagreementFinding` into the `Result` envelope. Drift-aware promotion: if any drift detector is in `drift_warning` state at task submission, the gate auto-promotes one rung beyond `max_error_bar`'s nominal selection. Cost-ceiling-bound promotion: if the next axis run would exceed the per-solve ceiling, the gate skips it and ships `agreement_type=DEGRADED_BUDGET_BOUND`.

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 6 (verification gate), Section 6.2 (vendored DisagreementFinding + classifier), Section 6.3 (three-axis protocol), Section 6.4 (five-rung adaptive depth + promotion vs cost ceiling), Section 6.5 (drift integration), Section 6.6 (WebSocket events: task.verification.promoted/.demoted), Section 6.7 (provenance composition).

## Key files and their roles
| File | Role |
|---|---|
| `__init__.py` | Empty Phase 0 stub. |
| `gate.py` | (Phase 5) Wobble protocol orchestrator — picks rung, dispatches axes to Trinity Core, composes Result. |
| `rung_table.py` | (Phase 5) Five-rung adaptive depth table (R1=floor through R5=replicated). |
| `promotion.py` | (Phase 5) Promotion/demotion logic; drift-aware bumps; cost-ceiling-bound truncation. |
| `agreement_classifier.py` | (Phase 5) Extends vendored `DisagreementFinding` with physics-wobble values (`NUMERICAL_DRIFT`, `BACKACTION_SENSITIVE`, `PROVIDER_DIVERGENT`, `DEGRADED`, `DEGRADED_BUDGET_BOUND`). |
| `provenance.py` | (Phase 5) `VerificationProvenance` composer per Section 6.7. |

## Vendored substrate
Vendors `wobble/disagreement_types.py` and `wobble/disagreement_classifier.py` from dr-frank-and-eddy v6.6 verbatim into `vendor/wobble/`. Critical design property: the full pairwise `distance_matrix` is preserved, never collapsed to a scalar (`DO NOT COLLAPSE` invariant from the vendored code).

## Common failure modes
- `IrreducibleNumericalDrift` — Axis 1 disagreement exceeds `max_error_bar` even at R5.
- `IrreducibleBackactionSensitivity` — Axis 2 disagreement exceeds budget at R5.
- `ProviderDivergence` — Axis 3 disagreement exceeds budget at R5.
- `MaxRungReached` — R5 still wobbles; suggested action is `HUMAN_REVIEW` (Section 7.7's override flow).
- `DriftStateUnavailable` — drift monitor failure; verification refuses to proceed in fail-closed mode (Section 6.8).

## Troubleshooting
- Verification gate emits structured WebSocket events on every promotion/demotion (Section 5.3 + 6.6). Subscribe to `/v1/ws/tasks/{task_id}/stream` to follow live.
- Tasks in pending review queue (`MaxRungReached`): inspect via `GET /v1/admin/tasks-pending-review`; override via `POST /v1/admin/tasks-pending-review/{id}/override`.
- Histogram of recent rung selections: `GET /v1/admin/verification/rung-distribution` — "most tasks landing at R5" indicates either tight error budgets or suspected drift.

## Tests
- `tests/unit/test_smoke.py` — asserts `phoenix.verification` imports.
- `evals/audit/` (Phase 7+) — VerificationProvenance is captured for every solve.
- `evals/replay/` (Phase 7+) — strict-mode replay reproduces verification depth exactly.

## Recent changes
- 2026-05-06 — Phase 0: module created as empty stub.
