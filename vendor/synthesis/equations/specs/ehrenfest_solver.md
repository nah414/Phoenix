# Solver Spec: EhrenfestSolver
## File: synthesis/equations/ehrenfest_solver.py
## Regime: EHRENFEST

### Valid Parameter Ranges
- mass_kg: [1e-31, 1e-8]
- velocity_over_c: [0, 0.01]
- n_grid_points: [50, 1000]
- length_scale_m: must be >= 6 * characteristic_length

### Benchmark 1: QHO Trajectory
- Parameters: {QHO potential, initial displacement x_0, period T = 2*pi/omega}
- Expected: <x>(T/4) ~ 0, <p>(T/4) = -m*omega*x_0
- Tolerance: 5%

### Numerical Failure Modes
1. Past Ehrenfest break time: results are meaningless for anharmonic potentials
2. Wavepacket spreading invalidates classical trajectory correspondence

### Cross-Solver Consistency
- QHO trajectory <x>(t), <p>(t) matches TDSE expectation values within 5%
