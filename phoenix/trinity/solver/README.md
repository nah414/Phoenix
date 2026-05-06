# phoenix/trinity/solver

## Purpose
Trinity Core's **Solver** subsystem — the equation-level layer. Vendors twelve calibrated equation solvers from dr-frank-and-eddy `synthesis/equations/` v6.6 (TISE, TDSE, Pauli, Dirac, Klein-Gordon, Breit-Pauli, Ehrenfest+WKB, Stochastic SE, Gravitational Decoherence, Wheeler-DeWitt, Semiclassical Gravity, plus Lindblad and Redfield). Per architecture Section 2.3, Solver takes a `PhysicsTask` and produces a `CandidateAnswer` with cross-precision wobble (Axis 1).

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 2.3 (Solver subsystem), Section 2.6 (cross-precision Axis 1 in the wobble protocol), Section 6.4 (rung table — when Axis 1 fires).

## Key files and their roles
| File | Role |
|---|---|
| `__init__.py` | Empty Phase 0 stub. |
| `engine.py` | (Phase 2) Adapts the vendored `EquationSolver` registry into Trinity's pipeline; uses `HamiltonianClassifier::can_handle()` for solver auto-selection. |
| `cross_precision.py` | (Phase 5) Axis 1 wobble — runs the chosen solver at two grid resolutions (default `N` and `2N`) and produces `error_bar_solver`. |

## Vendored substrate
Vendors `synthesis/equations/` from dr-frank-and-eddy v6.6 verbatim into `vendor/synthesis/equations/`:
- 12 equation solvers + `base.py::EquationSolver` ABC + `registry.py::HamiltonianClassifier` + `llm_context.py` + per-solver `specs/`.
- Calibration profile at `vendor/calibration_profile.json` (frozen JSON manifest, hashed in every result's provenance).

## Common failure modes
None yet — Phase 0 skeleton stub. From Phase 2+: cross-precision disagreement beyond `max_error_bar` triggers `WOBBLE` agreement type (Section 2.3); `FrontierPhysicsRefused` raises if the actor lacks `frontier_physics` permission and Wheeler-DeWitt or gravitational solvers are selected.

## Troubleshooting
Module is empty in Phase 0.

## Tests
- `tests/unit/test_smoke.py` — asserts `phoenix.trinity.solver` imports.
- `tests/tier1/` (Phase 2+) — analytical benchmarks (HO-1, ISW-1, H1S-1) gate every Phoenix release per Section 1 Decision 17's Tier-1 battery.

## Recent changes
- 2026-05-06 — Phase 0: module created as empty stub.
