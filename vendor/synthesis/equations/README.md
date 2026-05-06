# synthesis/equations

## Purpose
Twelve equation solvers implementing the full Schrodinger equation family. Each solver inherits from `EquationSolver` and provides `can_handle()` (regime scoring), `build_hamiltonian()`, `solve()`, `validate_parameters()`, and `calibration_check()` methods. The registry auto-selects the best solver from a `PhysicsContext`.

## Files
| File | Description |
|------|-------------|
| `base.py` | Abstract base class `EquationSolver`, `PhysicsContext` dataclass, `EquationRegime` and `ParticleType` enums |
| `non_relativistic.py` | `TISESolver` (eigenvalue problem H psi = E psi) and `TDSESolver` (time evolution via split-step Fourier) |
| `pauli.py` | `PauliSolver` -- spin-1/2 particles in EM fields with Zeeman + spin-orbit coupling |
| `dirac.py` | `DiracSolver` -- fully relativistic spin-1/2 fermions using 4-component Dirac spinors |
| `klein_gordon.py` | `KleinGordonSolver` -- relativistic spin-0 bosons (pions, Higgs) via second-order wave equation |
| `breit_pauli.py` | `BreitPauliSolver` -- fine structure corrections (mass-velocity, Darwin term, spin-orbit) from Foldy-Wouthuysen transformation |
| `ehrenfest.py` | `EhrenfestSolver` -- classical-quantum bridge tracking Ehrenfest theorem; WKB semiclassical approximation |
| `stochastic.py` | `StochasticSESolver` -- Monte Carlo wave function method for open quantum systems; delegates to TJM for 16+ qubits |
| `gravitational.py` | `GravitationalDecoherenceSolver` (Diosi-Penrose collapse) and `SemiclassicalGravitySolver` |
| `wheeler_dewitt.py` | `WheelerDeWittSolver` -- quantum gravity frontier (H\|Psi>=0, minisuperspace approximation) |
| `registry.py` | `HamiltonianClassifier` -- decision tree that selects the best solver based on `PhysicsContext` |
| `llm_context.py` | Generates equation family context strings for injection into Qwen3 LLM system prompts |

## Key Classes/Functions
- `EquationSolver` (ABC) -- base interface with `can_handle(ctx) -> (bool, confidence)`, `build_hamiltonian()`, `solve()`, `validate_parameters()`, `calibration_check()`
- `PhysicsContext` -- dataclass describing the physical system (mass, spin, velocity, fields, gravity regime, etc.)
- `EquationRegime` -- enum of 14 regimes from `NON_RELATIVISTIC_TI` through `WHEELER_DEWITT`
- `HamiltonianClassifier` -- thread-safe registry; priority-ordered decision tree for solver selection
- `FactorOrdering` -- enum for Wheeler-DeWitt factor ordering choices (DeWitt, Laplacian, Symmetric)

## Testing
```bash
python -m pytest tests/test_equations.py -v
python -m pytest tests/test_equation_solvers.py -v
```

## Notes
- All solvers share physical constants defined in CLAUDE.md -- they must match exactly across files
- `validate_parameters()` and `calibration_check()` were added in v6.0 to every solver
- Wheeler-DeWitt and gravitational decoherence always flag `frontier_physics: True` in results
- Solver confidence scoring: each `can_handle()` returns a float 0.0-1.0; the registry picks the highest
- The `specs/` subdirectory has its own README with per-solver spec cards
- Stochastic SE delegates to `synthesis/quantum/tensor_lindblad.py` for systems with 16+ qubits
