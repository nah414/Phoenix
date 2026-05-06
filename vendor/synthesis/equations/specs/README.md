# Solver Spec Cards

## Purpose
Each file in this directory is a compact reference card for one of the 12 equation solvers
in `synthesis/equations/`. Claude Code reads the relevant spec card **before** modifying
any solver code.

## Created
v6.0 Build Guide, Phase A, Step 2 (April 4, 2026)

## Contents

| File | Solver | Regime | Frontier? |
|------|--------|--------|-----------|
| tise_solver.md | TISESolver | NON_RELATIVISTIC_TI | No |
| tdse_solver.md | TDSESolver | NON_RELATIVISTIC_TD | No |
| pauli_solver.md | PauliSolver | PAULI | No |
| dirac_solver.md | DiracSolver | DIRAC | No |
| klein_gordon_solver.md | KleinGordonSolver | KLEIN_GORDON | No |
| breit_pauli_solver.md | BreitPauliSolver | BREIT_PAULI | No |
| ehrenfest_solver.md | EhrenfestSolver | EHRENFEST | No |
| wkb_solver.md | WKBSolver | WKB | No |
| stochastic_se_solver.md | StochasticSESolver | STOCHASTIC_SE | No |
| grav_decoherence_solver.md | GravitationalDecoherenceSolver | GRAVITATIONAL_DECOHERENCE | Yes |
| semiclassical_gravity_solver.md | SemiclassicalGravitySolver | SEMICLASSICAL_GRAVITY | Yes |
| wheeler_dewitt_solver.md | WheelerDeWittSolver | WHEELER_DEWITT | Yes |

## Each Card Contains
- Valid parameter ranges (mass, grid points, length scale)
- Calibration benchmarks with expected values and tolerances
- Numerical failure modes to watch for
- Cross-solver consistency relationships

## Usage
Before modifying any solver in `synthesis/equations/`:
1. Read `PHYSICS_REFERENCE.md` (project root) for full physics context
2. Read the solver's spec card here for quick reference
3. Run `validate_parameters()` after changes to check consistency
