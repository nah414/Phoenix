# Solver Spec: BreitPauliSolver
## File: synthesis/equations/breit_pauli_solver.py
## Regime: BREIT_PAULI

### Valid Parameter Ranges
- mass_kg: [1e-31, 1e-8]
- velocity_over_c: [0, 0.1]
- n_grid_points: [50, 500]
- length_scale_m: must be >= 6 * characteristic_length

### Benchmark 1: Perturbative Corrections
- Parameters: {same potential as TISE benchmark, v/c ~ 0.05}
- Expected: eigenvalue difference from TISE < 1% of TISE value
- Tolerance: correction magnitude < 1% of base eigenvalue

### Numerical Failure Modes
1. v/c > 0.1 breaks perturbative expansion: higher-order terms become non-negligible
2. Mixing of correction orders produces inconsistent results at boundary of validity

### Cross-Solver Consistency
- Eigenvalues close to TISE values with small relativistic correction
