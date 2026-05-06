# Solver Spec: DiracSolver
## File: synthesis/equations/dirac_solver.py
## Regime: DIRAC

### Valid Parameter Ranges
- mass_kg: [1e-31, 1e-8]
- velocity_over_c: [0.01, 1.0]
- n_grid_points: [20, 200]
- length_scale_m: must be >= 6 * characteristic_length

### Benchmark 1: Electron Rest Energy
- Parameters: {mass_kg: 9.109e-31, V: 0}
- Expected: E = m*c^2 = 8.187e-14 J
- Tolerance: 5%

### Benchmark 2: Fine Structure Scaling
- Parameters: {Z: 1 vs Z: 2, hydrogen-like potential}
- Expected: fine structure correction scales as Z^2 between Z=1 and Z=2
- Tolerance: 10%

### Numerical Failure Modes
1. Negative energy spectrum contamination: spurious states from Dirac sea leak into positive spectrum
2. Memory: 4N x 4N matrices; n_grid > 200 may exhaust RAM

### Cross-Solver Consistency
- Free particle rest energy matches Klein-Gordon within 5%
