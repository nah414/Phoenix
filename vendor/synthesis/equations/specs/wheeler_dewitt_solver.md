# Solver Spec: WheelerDeWittSolver
## File: synthesis/equations/wheeler_dewitt_solver.py
## Regime: WHEELER_DEWITT

### FRONTIER PHYSICS: True

### Valid Parameter Ranges
- Factor ordering: DeWitt, Laplacian, Symmetric
- n_grid_points: [20, 200]
- length_scale_m: must be >= 6 * characteristic_length

### Benchmark 1: Constraint Satisfaction
- Parameters: {minisuperspace model, n_grid: 100}
- Expected: H @ psi residual norm < 1e-6
- Tolerance: residual < 1e-6

### Benchmark 2: Factor Ordering Comparison
- Parameters: {same potential, all three orderings}
- Expected: results differ < 20% across DeWitt, Laplacian, Symmetric orderings
- Tolerance: 20%

### Numerical Failure Modes
1. Factor ordering ambiguity: different orderings yield physically distinct solutions
2. Normalization in minisuperspace: no standard inner product; normalization is model-dependent

### Cross-Solver Consistency
- None (unique regime, no other solver covers quantum cosmology)
