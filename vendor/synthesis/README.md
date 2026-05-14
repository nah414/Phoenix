# `vendor/synthesis/`

Vendored physics-solver substrate that Phoenix's Trinity Core
Solver engine consumes. Three sub-packages:

| Package | What it ships |
|---|---|
| `core/` | DPD (decoherence-by-probe) engine, hardware backends, Lindblad RK4 integrator, probe model -- the numerical primitives. |
| `equations/` | Twelve physics-equation specs (TISE, TDSE, Dirac, Klein-Gordon, Lindblad, Redfield, Ehrenfest, semi-classical gravity, Wheeler-DeWitt, etc.) + a registry for dispatching by regime. |
| `quantum/` | Tensor-network Lindblad solver bridging the equations layer to quantum-circuit-shaped providers. |

Per Section 11.7.1's vendor-verbatim discipline, every file in this
tree keeps its upstream formatting unchanged. Phoenix's solver
engine (`phoenix/trinity/solver/`) imports `synthesis.*` via the
sys.path injection wired in `phoenix/__init__.py`.

Architectural reference: `PHOENIX_ARCHITECTURE_v1.md` Section 2
(Trinity Core), Section 2.3 (Solver engine), Section 11.7.1
(vendor discipline).
