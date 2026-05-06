# Solver Spec: TISESolver
## File: synthesis/equations/tise_solver.py
## Regime: NON_RELATIVISTIC_TI

### Valid Parameter Ranges
- mass_kg: [1e-31, 1e-8]
- velocity_over_c: [0, 0.01]
- n_grid_points: [50, 2000]
- length_scale_m: must be >= 6 * characteristic_length

### Benchmark 1: Quantum Harmonic Oscillator Ground State
- Parameters: {omega: 1e15, mass_kg: 9.109e-31, L: 4e-9, n_grid: 400}
- Expected: E_0 = 5.273e-20 J, E_1/E_0 = 3.0, E_2/E_0 = 5.0
- Tolerance: 1%

### Benchmark 2: Particle in Box
- Parameters: {L: 1e-9, mass_kg: 9.109e-31, V: 0, n_grid: 200}
- Expected: E_1 = 6.024e-20 J, E_2/E_1 = 4.0, E_3/E_1 = 9.0
- Tolerance: 1%

### Numerical Failure Modes
1. Grid too small: eigenvalues match PIB instead of QHO (potential not captured)
2. Grid too coarse: non-physical eigenvalues appear in spectrum

### Cross-Solver Consistency
- TDSE.solve_stationary() must match TISE eigenvalues within 0.1%
