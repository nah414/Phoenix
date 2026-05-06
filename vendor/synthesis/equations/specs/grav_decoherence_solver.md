# Solver Spec: GravitationalDecoherenceSolver
## File: synthesis/equations/grav_decoherence_solver.py
## Regime: GRAVITATIONAL_DECOHERENCE

### FRONTIER PHYSICS: True

### Valid Parameter Ranges
- mass_kg: [1e-25, 1e-8]
- n_grid_points: [10, 200]
- length_scale_m: must be >= 6 * characteristic_length

### Benchmark 1: Mass Scaling
- Parameters: {mass_1: M, mass_2: 10*M, same geometry}
- Expected: decoherence rate ratio ~ 100 for 10x mass ratio (m^2 scaling)
- Tolerance: exponent 2.0 +/- 0.2

### Numerical Failure Modes
1. Mass too small: decoherence rate becomes negligible, indistinguishable from numerical noise
2. Rate calculation sensitive to superposition separation distance

### Cross-Solver Consistency
- None (unique regime, no other solver covers gravitational decoherence)
