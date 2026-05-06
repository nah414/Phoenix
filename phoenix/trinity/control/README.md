# phoenix/trinity/control

## Purpose
Trinity Core's **Control** subsystem — the control-level layer. Vendors the Drive-Probe-Drive engine from dr-frank-and-eddy `synthesis/core/` v6.6 (`DPDEngine`, `LindbladPropagator`, `ProbeModel`, `HardwareBackend` for four quantum modalities). Per architecture Section 2.4, Control takes a `CandidateAnswer` from Solver and produces a `VerifiedAnswer` with cross-control wobble (Axis 2 — probe-strength sweep).

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 2.4 (Control subsystem), Section 2.6 (cross-control Axis 2), Section 6.4 (rung table — when Axis 2 fires).

## Key files and their roles
| File | Role |
|---|---|
| `__init__.py` | Empty Phase 0 stub. |
| `engine.py` | (Phase 3) Adapts the vendored `DPDEngine` into Trinity's pipeline; constructs DPD block sequences appropriate to the candidate's Hamiltonian regime (open-system Lindblad-mediated; closed-system unitary). |
| `cross_probe.py` | (Phase 5) Axis 2 wobble — runs the DPD sequence at two probe strengths (default ε₁=0.1 weak, ε₂=0.5 information-optimal) and produces `error_bar_control`. |

## Vendored substrate
Vendors `synthesis/core/` from dr-frank-and-eddy v6.6 verbatim into `vendor/synthesis/core/`:
- `dpd_engine.py`, `lindblad_rk4.py`, `probe_model.py`, `hardware_backends.py`.
- Provable error suppression: `p_eff = p_phys × (1 − η_DD) × (1 − η_probe) × (1 − η_clock)`; each suppression mechanism independently tunable.
- Hardware modality params for superconducting, trapped-ion, NMR, telecom-photonic.

## Common failure modes
None yet — Phase 0 skeleton stub. From Phase 3+: probe-strength disagreement beyond solver's predicted uncertainty raises `WOBBLE` (Section 2.4); `FrontierPhysicsRefused` if `frontier_physics=True` is set on the task and the actor lacks the permission.

## Troubleshooting
Module is empty in Phase 0.

## Tests
- `tests/unit/test_smoke.py` — asserts `phoenix.trinity.control` imports.
- `tests/tier1/` (Phase 3+) — RABI-1 benchmark exercises the DPD pipeline.

## Recent changes
- 2026-05-06 — Phase 0: module created as empty stub.
