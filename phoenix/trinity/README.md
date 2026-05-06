# phoenix/trinity

## Purpose
**Trinity Core** — Phoenix's physics heart. Three peer engines (Solver, Control, Orchestrate) composed along a shared pipeline that takes a `PhysicsTask` in and returns a `Result` with full provenance and three-axis wobble verification. Phase 0 ships empty stubs for the three subsystems; Phase 1 vendors the substrate; Phases 2–3 wire them through the pipeline.

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 2 (Trinity Core deep dive), Section 1 Decisions 4–6 (Trinity Core lock), Section 2.1 (pipeline at a glance), Section 2.2 (shared data model: `PhysicsTask`, `CandidateAnswer`, `VerifiedAnswer`, `Result`), Section 2.6 (three-axis wobble composition).

## Key files and their roles
| File | Role |
|---|---|
| `__init__.py` | Empty Phase 0 stub. |
| `data_model.py` | (Phase 2) Typed dataclasses: `PhysicsTask`, `CandidateAnswer`, `VerifiedAnswer`, `Result`. |
| `pipeline.py` | (Phase 3) The three-subsystem pipeline orchestrator. |
| `solver/` | Solver subsystem — wraps vendored `synthesis/equations/` (twelve solvers). |
| `control/` | Control subsystem — wraps vendored `synthesis/core/` (DPD engine + Lindblad). |
| `orchestrate/` | Orchestrate subsystem — wraps vendored SynQc TDS Core. |

## Vendored substrate
Trinity Core's Solver and Control subsystems vendor portions of dr-frank-and-eddy at the pinned frank-data commit. See `vendor/VENDOR_VERSION.txt` for the pinned commit and `vendor/synthesis/` for the actual code (lands in Phase 1). Per Section 11.7.1, vendoring is verbatim including imports through v1.

Trinity Core's Orchestrate subsystem is **greenfield Phoenix code** per the 2026-05-06 architecture revision — it lives entirely under `phoenix/trinity/orchestrate/`, not in `vendor/`. SynQc TDS Core is a *design reference* for Orchestrate's contracts; Phoenix never vendors or imports from SynQc.

## Common failure modes
None yet — Phase 0 skeleton stub. Section 2 catalogs expected failure modes that surface in Phases 2–3 (e.g., `FrontierPhysicsRefused` for ungated Wheeler-DeWitt requests, `WOBBLE` agreement type for cross-axis disagreement).

## Troubleshooting
Module is empty in Phase 0. Once Phase 2+ lands: every Phoenix solve writes a per-axis `RunRecord` to `provenance.per_axis_runs` per Section 6.7, so failures can be diagnosed by walking the recorded run history.

## Tests
- `tests/unit/test_smoke.py` — asserts `phoenix.trinity` imports.
- `tests/tier1/` (Phase 2+) — five Tier-1 benchmarks (HO-1, ISW-1, H1S-1, RABI-1, SCG-1) execute end-to-end through Trinity Core.

## Recent changes
- 2026-05-06 — Phase 0: module created as empty stub.
