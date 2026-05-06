# Solver Spec: WKBSolver
## File: synthesis/equations/wkb_solver.py
## Regime: WKB

### Valid Parameter Ranges
- mass_kg: [1e-31, 1e-8]
- velocity_over_c: [0, 0.01]
- n_grid_points: [50, 1000]
- length_scale_m: must be >= 6 * characteristic_length

### Benchmark 1: QHO Eigenvalues
- Parameters: {QHO potential, same as TISE benchmark}
- Expected: eigenvalues match TISE within 5% for n >= 2
- Tolerance: 5%

### Numerical Failure Modes
1. Breaks at classical turning points: connection formulae introduce errors
2. Less accurate for ground state (n=0): WKB is semiclassical, worst for low quantum numbers

### Cross-Solver Consistency
- Matches TISE for QHO eigenvalues (WKB is exact for QHO)
