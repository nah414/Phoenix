"""
SynQc Core -- Drive-Probe-Drive Quantum Control Engine

Heritage: Dual-Clocking Qubits (DCQ) -> SynQc Temporal Dynamics Series

The DPD primitive is the fundamental control unit:
  1. Drive D1 -- Apply control Hamiltonian H_D1(t) for duration tau_D1
  2. Probe P  -- Measure/interrogate (strength epsilon, duration tau_P)
  3. Drive D2 -- Adjust control based on probe result

This module provides:
  - DPDBlock: Single drive-probe-drive sequence definition
  - DPDScheduler: Multi-block sequence builder with timing
  - DPDResult: Full execution result with audit data
  - LindbladPropagator: RK4 density matrix evolution (GKSL equation)
  - ProbeModel: Information-backaction trade-off calculator
  - HardwareBackend: Abstract base for platform-specific parameters
    Implementations: Superconducting, TrappedIon, NMR, TelecomPhotonic

Relationship to other synthesis modules:
  - synthesis/quantum/engine.py: Gate-level simulator (statevector/density matrix)
  - synthesis/quantum/tensor_lindblad.py: TJM Lindblad (MPS, 16-24 qubits)
  - synthesis/equations/: Schrodinger equation family (12 solvers)
  - This module: Timing-aware control with Lindblad RK4 (full density matrix, <=15 qubits)
"""

from .dpd_engine import DPDBlock, DPDScheduler, DPDResult, ProbeType
from .lindblad_rk4 import LindbladPropagator
from .probe_model import ProbeModel
from .hardware_backends import (
    HardwareBackend, get_backend, list_backends,
    SuperconductingBackend, TrappedIonBackend, NMRBackend, TelecomPhotonicBackend,
)

__all__ = [
    "DPDBlock", "DPDScheduler", "DPDResult", "ProbeType",
    "LindbladPropagator",
    "ProbeModel",
    "HardwareBackend", "get_backend", "list_backends",
    "SuperconductingBackend", "TrappedIonBackend", "NMRBackend", "TelecomPhotonicBackend",
]
