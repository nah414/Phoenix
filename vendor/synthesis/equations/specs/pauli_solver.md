# Solver Spec: PauliSolver
## File: synthesis/equations/pauli_solver.py
## Regime: PAULI

### Valid Parameter Ranges
- mass_kg: [1e-31, 1e-8]
- velocity_over_c: [0, 0.01]
- n_grid_points: [50, 500]
- length_scale_m: must be >= 6 * characteristic_length

### Benchmark 1: Zeeman Splitting
- Parameters: {B: 1.0 T, mass_kg: 9.109e-31}
- Expected: Delta_E = g * mu_B * B = 1.855e-23 J
- Tolerance: 2%

### Benchmark 2: Zero-Field Degeneracy Match
- Parameters: {B: 0, same potential as TISE benchmark}
- Expected: each TISE eigenvalue appears with 2x degeneracy (spin up/down)
- Tolerance: 0.1%

### Numerical Failure Modes
1. Memory: 2N x 2N matrices scale quadratically; n_grid > 500 may exhaust RAM
2. Spin-orbit coupling errors: incorrect sigma matrix construction corrupts spectrum

### Cross-Solver Consistency
- B=0 eigenvalues match TISE with 2x degeneracy within 0.1%
