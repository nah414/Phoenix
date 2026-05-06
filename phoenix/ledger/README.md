# phoenix/ledger

## Purpose
**Hashchained provenance store** — Phoenix's Omega Ledger per architecture v1 Section 1 Decision 15. Every Phoenix solve produces a ledger entry containing: input hash, calibration profile hash, library-version manifest hash, full Trinity Core trace, output hash, prior-entry hash. The chain is append-only and tamper-evident. The replay subsystem reconstructs any historical solve from its ledger entry under three reproducibility modes (`default`, `strict`, `replay`).

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 1 Decisions 15 (hashchained provenance with bit-exact replay), 19–21 (three reproducibility modes; bit-exact for deterministic portion of pipeline; cloud shots recorded once), Section 6.7 (verification provenance composition), Section 8.2 (`/v1/admin/ledger/integrity-report`).

## Key files and their roles
| File | Role |
|---|---|
| `__init__.py` | Empty Phase 0 stub. |
| `omega_ledger.py` | (Phase 7) Vendored Omega Ledger pattern, extended for replay support. SHA-256 hashchain. |
| `entry_types.py` | (Phase 7) Typed ledger-entry shapes: `SOLVE`, `OVERRIDE_BY_OPERATOR`, `KILL_SWITCH_ENGAGED`, `KILL_SWITCH_RELEASED`, `ENROLLMENT`, `REVOCATION`, `PROPOSED_BY_AGENT`. |
| `replay_engine.py` | (Phase 7) Section 1 Decision 19's replay path. Refuses to run if `requirements.lock` doesn't match the ledger entry's recorded versions. |

## Vendored substrate
Vendors the Omega Ledger pattern from dr-frank-and-eddy. Phoenix extends with replay-mode support; the underlying SHA-256 hashchain semantics are preserved.

## Common failure modes
- `ReplayDivergence` — replay re-execution produced a different hash than recorded; pinpoints which `RunRecord` diverged.
- `LedgerCorruption` — hashchain walk detects a broken link; Section 8.2's `/v1/admin/ledger/integrity-report` surfaces the position.
- `ReplayProviderUnavailable` — strict/replay mode requires the original provider, which is degraded.
- `AdapterVersionMismatch` — strict-mode replay sees a different LoRA adapter fingerprint than recorded.

## Troubleshooting
- **Cloud-shot reproducibility limit** (Section 1 Decision 20): cloud-quantum shots are intrinsically nondeterministic and recorded once. Replay reads from the recorded shots rather than re-running on hardware. The Result envelope's `provenance.cloud_shots_recorded=True` flag makes this explicit.
- **Default mode** has no replay guarantee; `strict` adds bit-exact local replay; `replay` mode re-executes and verifies before returning. Strict and replay force single-threaded BLAS and disable some vectorization, costing 15–30% wall-clock vs default.
- Ledger integrity walk: `GET /v1/audit/ledger/verify` (any `can_submit_tasks` actor) or `GET /v1/admin/ledger/integrity-report` (admin, fuller report).

## Tests
- `tests/unit/test_smoke.py` — asserts `phoenix.ledger` imports.
- `evals/ledger/` (Phase 7+) — hashchain stays valid under all operations.
- `evals/replay/` (Phase 7+) — strict and replay modes produce bit-exact match for the deterministic portion. Long-window replay test (Phase 0 acceptance §10.7): 6+ months between original and replay across CI hardware + clean Linux container + clean macOS runner.

## Recent changes
- 2026-05-06 — Phase 0: module created as empty stub.
