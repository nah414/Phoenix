# synthesis/core

## Purpose
Drive-Probe-Drive (DPD) quantum verification engine. Implements the DPD primitive (Drive D1 -> Probe -> Drive D2) with weak measurement via Kraus operators, Lindblad master equation integration, and hardware-specific backend parameters for 4 quantum computing platforms.

## Files
| File | Description |
|------|-------------|
| `dpd_engine.py` | DPD quantum control engine (411 lines) -- Kraus operator weak measurements, adaptive D2 correction, drift prediction integration via `ml/drift_ensemble.py` |
| `lindblad_rk4.py` | GKSL (Lindblad) master equation propagator via 4th-order Runge-Kutta (131 lines). Verified against QuTiP mesolve, Qiskit Dynamics, DCQ dualdrive. |
| `probe_model.py` | Information-backaction trade-off calculator (95 lines) -- finds the sweet spot between probe information gain and state disturbance |
| `hardware_backends.py` | Hardware-specific parameters for 4 DCQ platforms: Superconducting, TrappedIon, NMR, TelecomPhotonic -- T1/T2, gate errors, native gates, qubit frequencies |

## Key Classes/Functions
- `DPDEngine` -- orchestrates Drive-Probe-Drive sequences with configurable probe types (strong projective, weak measurement, ancilla-based)
- `ProbeType` -- enum for mid-circuit probe types
- `LindbladPropagator` -- RK4 integrator for `d rho/dt = -i[H, rho] + sum_k(L_k rho L_k^dag - 1/2{L_k^dag L_k, rho})`
- `HardwareBackend` -- abstract base for platform backends; concrete: `SuperconductingBackend`, `TrappedIonBackend`, `NMRBackend`, `TelecomPhotonicBackend`
- `HardwareParams` -- dataclass: T1, T2, gate errors, qubit frequency, anharmonicity, probe latency, max qubits, native gate set
- `ProbeModel` -- calculates mutual information vs backaction fidelity for a given probe strength epsilon
- `ProbeTradeoff` -- result dataclass with sweet_spot_score and regime classification

## Cross-references
- `dpd_engine.py` uses `lindblad_rk4.py` for density matrix propagation during drive phases
- `dpd_engine.py` uses `probe_model.py` for information-backaction calculations
- `dpd_engine.py` uses `hardware_backends.py` for platform-specific parameters
- `dpd_engine.py` integrates with `ml/drift_ensemble.py` for drift prediction in D2 correction
- `backend/api.py` `/api/solver/invoke` calls DPD when `dpd_enabled=True` in the request
- UI `DPDPanel.tsx` provides the frontend for configuring and running DPD sequences
- UI `QuantumLabView.tsx` has a DPD toggle checkbox that auto-runs DPD after solver invocation

## Testing
```bash
python -m pytest tests/test_dpd_engine.py -v
python -m pytest tests/test_lindblad.py -v
```

## Notes
- Lindblad propagator limited to <=15 qubits (full density matrix). For 16-24 qubits, use `synthesis/quantum/tensor_lindblad.py` (TJM/MPS)
- Probe strength epsilon=0 gives no disturbance; epsilon=1 gives projective measurement
- Kraus operators satisfy completeness: `sum(K_k^dag K_k) = I`
- Heritage: DCQ scheduler.py -> SynQc Temporal Dynamics Series

## Recent Changes (April 2026 -- v6.1.1)
- DPD integrated into `/api/solver/invoke` endpoint via `dpd_enabled` flag in the request body
- `DPDPanel.tsx` completely redesigned with pipeline context banner showing WHERE DPD fits, WHEN it fires, and WHAT results mean
- DPDPanel now has three documented operation modes: standalone, solver-attached (auto after solver invoke), workflow-auto (fires during Third Space open-system queries)
- DPDPanel guide section added with probe type descriptions and verification status display
- DPDResultInline component extracted in `QuantumLabView.tsx` for inline DPD results after solver runs
