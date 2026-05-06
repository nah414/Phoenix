"""
Hardware-specific backend parameters for DPD experiments.

Each backend provides platform-specific physical parameters:
- T1, T2 coherence times
- Gate error rates and native gate set
- Default Lindblad operators for decoherence
- Probe latency characteristics
- Qubit frequencies and anharmonicities

From DCQ provider registry: 4 backends (superconducting, trapped-ion, NMR, telecom).
"""

import numpy as np
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List


@dataclass
class HardwareParams:
    """Physical parameters for a specific hardware platform."""
    name: str
    platform: str
    t1_seconds: float               # Energy relaxation time
    t2_seconds: float               # Dephasing time
    gate_error_rate: float          # Single-qubit gate error
    two_qubit_error_rate: float     # Two-qubit gate error
    native_gates: List[str]         # Native gate set
    qubit_frequency_ghz: float      # Qubit transition frequency
    anharmonicity_mhz: float        # Anharmonicity (transmon)
    readout_error: float            # Measurement error rate
    probe_latency_ns: float         # Time to perform a mid-circuit probe
    max_qubits: int                 # Maximum supported qubits


class HardwareBackend(ABC):
    """Abstract base for hardware backends."""

    @abstractmethod
    def get_params(self) -> HardwareParams:
        """Return hardware-specific parameters."""
        pass

    @abstractmethod
    def default_lindblad_ops(self, n_qubits: int = 1) -> List[np.ndarray]:
        """Return default Lindblad operators for decoherence."""
        pass

    @abstractmethod
    def native_hamiltonian(self, n_qubits: int = 1) -> np.ndarray:
        """Return the default system Hamiltonian."""
        pass


class SuperconductingBackend(HardwareBackend):
    """Superconducting transmon qubit backend.

    Reference: IBM Quantum, Google Sycamore
    DCQ provider: dcq_superconducting
    """

    def get_params(self) -> HardwareParams:
        return HardwareParams(
            name="Superconducting Transmon",
            platform="superconducting",
            t1_seconds=100e-6,
            t2_seconds=50e-6,
            gate_error_rate=1e-3,
            two_qubit_error_rate=1e-2,
            native_gates=["X", "SX", "Rz", "CX"],
            qubit_frequency_ghz=5.0,
            anharmonicity_mhz=-330,
            readout_error=1e-2,
            probe_latency_ns=500,
            max_qubits=24,
        )

    def default_lindblad_ops(self, n_qubits: int = 1) -> List[np.ndarray]:
        ops = []
        params = self.get_params()
        gamma_1 = 1.0 / params.t1_seconds  # T1 decay rate
        gamma_phi = 1.0 / params.t2_seconds - 1.0 / (2 * params.t1_seconds)  # Pure dephasing

        sigma_minus = np.array([[0, 1], [0, 0]], dtype=complex)
        sigma_z = np.array([[1, 0], [0, -1]], dtype=complex)

        for q in range(n_qubits):
            # T1 decay: sqrt(gamma_1) * sigma_-
            L1 = np.sqrt(gamma_1) * sigma_minus
            ops.append(L1)
            # Pure dephasing: sqrt(gamma_phi/2) * sigma_z
            if gamma_phi > 0:
                Lphi = np.sqrt(gamma_phi / 2) * sigma_z
                ops.append(Lphi)
        return ops

    def native_hamiltonian(self, n_qubits: int = 1) -> np.ndarray:
        params = self.get_params()
        omega = params.qubit_frequency_ghz * 2 * np.pi  # GHz -> rad/ns
        sigma_z = np.array([[1, 0], [0, -1]], dtype=complex)
        return 0.5 * omega * sigma_z


class TrappedIonBackend(HardwareBackend):
    """Trapped-ion qubit backend. Reference: IonQ, Quantinuum."""

    def get_params(self) -> HardwareParams:
        return HardwareParams(
            name="Trapped Ion",
            platform="trapped_ion",
            t1_seconds=10.0,     # Seconds -- much longer than SC
            t2_seconds=1.0,
            gate_error_rate=1e-4,
            two_qubit_error_rate=1e-3,
            native_gates=["Rx", "Ry", "Rz", "XX"],
            qubit_frequency_ghz=12.6,
            anharmonicity_mhz=0,  # N/A for ions
            readout_error=1e-3,
            probe_latency_ns=10000,  # Slower readout
            max_qubits=24,
        )

    def default_lindblad_ops(self, n_qubits: int = 1) -> List[np.ndarray]:
        params = self.get_params()
        gamma_phi = 1.0 / params.t2_seconds
        sigma_z = np.array([[1, 0], [0, -1]], dtype=complex)
        return [np.sqrt(gamma_phi / 2) * sigma_z] * n_qubits

    def native_hamiltonian(self, n_qubits: int = 1) -> np.ndarray:
        omega = self.get_params().qubit_frequency_ghz * 2 * np.pi
        sigma_z = np.array([[1, 0], [0, -1]], dtype=complex)
        return 0.5 * omega * sigma_z


class NMRBackend(HardwareBackend):
    """Nuclear magnetic resonance qubit backend."""

    def get_params(self) -> HardwareParams:
        return HardwareParams(
            name="NMR",
            platform="nmr",
            t1_seconds=1.0,
            t2_seconds=0.1,
            gate_error_rate=1e-2,
            two_qubit_error_rate=5e-2,
            native_gates=["Rx", "Ry", "Rz"],
            qubit_frequency_ghz=0.5,
            anharmonicity_mhz=0,
            readout_error=5e-2,
            probe_latency_ns=1000000,  # Very slow
            max_qubits=10,
        )

    def default_lindblad_ops(self, n_qubits: int = 1) -> List[np.ndarray]:
        params = self.get_params()
        gamma_1 = 1.0 / params.t1_seconds
        sigma_minus = np.array([[0, 1], [0, 0]], dtype=complex)
        return [np.sqrt(gamma_1) * sigma_minus] * n_qubits

    def native_hamiltonian(self, n_qubits: int = 1) -> np.ndarray:
        omega = self.get_params().qubit_frequency_ghz * 2 * np.pi
        sigma_z = np.array([[1, 0], [0, -1]], dtype=complex)
        return 0.5 * omega * sigma_z


class TelecomPhotonicBackend(HardwareBackend):
    """Telecom-wavelength photonic qubit backend."""

    def get_params(self) -> HardwareParams:
        return HardwareParams(
            name="Telecom Photonic",
            platform="telecom_photonic",
            t1_seconds=1e-3,  # Photon loss limited
            t2_seconds=1e-3,
            gate_error_rate=5e-3,
            two_qubit_error_rate=5e-2,
            native_gates=["Rx", "Ry", "BS", "PS"],  # Beam splitter, phase shifter
            qubit_frequency_ghz=193.1,  # 1550 nm telecom C-band
            anharmonicity_mhz=0,
            readout_error=1e-2,
            probe_latency_ns=100,  # Fast optical readout
            max_qubits=24,
        )

    def default_lindblad_ops(self, n_qubits: int = 1) -> List[np.ndarray]:
        params = self.get_params()
        gamma = 1.0 / params.t1_seconds
        annihilation = np.array([[0, 1], [0, 0]], dtype=complex)
        return [np.sqrt(gamma) * annihilation] * n_qubits

    def native_hamiltonian(self, n_qubits: int = 1) -> np.ndarray:
        omega = self.get_params().qubit_frequency_ghz * 2 * np.pi
        sigma_z = np.array([[1, 0], [0, -1]], dtype=complex)
        return 0.5 * omega * sigma_z


# ---- Backend Registry ----

_BACKENDS = {
    "superconducting": SuperconductingBackend,
    "trapped_ion": TrappedIonBackend,
    "nmr": NMRBackend,
    "telecom_photonic": TelecomPhotonicBackend,
}


def get_backend(name: str) -> HardwareBackend:
    """Get a hardware backend by name."""
    cls = _BACKENDS.get(name.lower())
    if cls is None:
        available = ", ".join(_BACKENDS.keys())
        raise ValueError(f"Unknown backend: {name}. Available: {available}")
    return cls()


def list_backends() -> list:
    """List all available hardware backends with their parameters."""
    return [cls().get_params() for cls in _BACKENDS.values()]
