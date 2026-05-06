# Solver Spec: KleinGordonSolver
## File: synthesis/equations/klein_gordon_solver.py
## Regime: KLEIN_GORDON

### Valid Parameter Ranges
- mass_kg: [1e-31, 1e-8]
- velocity_over_c: [0.01, 1.0]
- n_grid_points: [20, 500]
- length_scale_m: must be >= 6 * characteristic_length

### Benchmark 1: Rest Energy
- Parameters: {mass_kg: 9.109e-31, V: 0}
- Expected: E ~ m*c^2, matches Dirac rest energy within 5%
- Tolerance: 5%

### Numerical Failure Modes
1. Second-order time discretization instability: leapfrog or Verlet schemes can diverge if dt too large
2. Negative probability density: Klein-Gordon charge density is not positive-definite

### Cross-Solver Consistency
- Rest energy matches Dirac solver within 5%
