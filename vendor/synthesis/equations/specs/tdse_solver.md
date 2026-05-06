# Solver Spec: TDSESolver
## File: synthesis/equations/tdse_solver.py
## Regime: NON_RELATIVISTIC_TD

### Valid Parameter Ranges
- mass_kg: [1e-31, 1e-8]
- velocity_over_c: [0, 0.01]
- n_grid_points: [50, 2000]
- length_scale_m: must be >= 6 * characteristic_length

### Benchmark 1: Stationary State Match to TISE
- Parameters: same as TISE QHO benchmark
- Expected: eigenvalues match TISE within 0.1%
- Tolerance: 0.1%

### Benchmark 2: Coherent State Revival
- Parameters: {coherent state in QHO, period T = 2*pi/omega}
- Expected: overlap |<psi(0)|psi(T)>|^2 > 0.95 after one period
- Tolerance: overlap threshold 0.95

### Numerical Failure Modes
1. Time step too large: Crank-Nicolson becomes unstable or inaccurate
2. Normalization drift: |<psi|psi>| deviates from 1.0 over long propagation

### Numerical Notes (v6.5)

Time step `dt` at the orchestration layer is now clamped against a safety
floor derived from the Hamiltonian's characteristic frequency. If
`metadata["omega"]` is set, the floor is `dt <= 1 / (20 * omega)`. If a
magnetic field is set (Zeeman case), the floor is `dt <= 1 / (20 * omega_L)`
where `omega_L = g * mu_B * B / hbar` with g = 2 for electrons. Without
either, the user's requested `t_final / 1000` is used and a DEBUG log line
notes the floor was not sharpened.

The implementation lives in `orchestration/workflow_engine.py::_compute_safe_dt`;
the regression benchmark is `tests/test_time_evolution_calibration.py`.

### Cross-Solver Consistency
- Must match TISE eigenvalues via solve_stationary() within 0.1%
