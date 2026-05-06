# Solver Spec: SemiclassicalGravitySolver
## File: synthesis/equations/semiclassical_gravity_solver.py
## Regime: SEMICLASSICAL_GRAVITY

### FRONTIER PHYSICS: True

### Valid Parameter Ranges
- mass_kg: [1e-31, 1e-8]
- n_grid_points: [20, 200]
- length_scale_m: must be >= 6 * characteristic_length

### Benchmark 1: Weak Field Limit
- Parameters: {weak gravitational potential, same grid as TISE}
- Expected: eigenvalues approximate TISE + small G*m correction term
- Tolerance: correction term order-of-magnitude correct

### Numerical Failure Modes
1. Strong field regime breaks semiclassical approximation: backreaction diverges
2. Self-consistent iteration (Schrodinger-Newton) may fail to converge

### Cross-Solver Consistency
- Weak field limit eigenvalues match TISE within expected G*m correction magnitude
