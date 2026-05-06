"""
Drive-Probe-Drive (DPD) Quantum Control Engine

Heritage: DCQ scheduler.py -> SynQc Temporal Dynamics Series

The DPD primitive:
  1. Drive D1 -- Apply control Hamiltonian H_D1(t) for duration tau_D1
  2. Probe P  -- Weak measurement via Kraus operators (strength epsilon)
  3. Drive D2 -- Adaptive correction based on probe outcome

Weak measurement model (Kraus operators):
  For probe strength epsilon in [0, 1] coupling to sigma_z:
    K_0 = sqrt((1 + sqrt(1-eps^2))/2) * |0><0| + sqrt((1 - sqrt(1-eps^2))/2) * |1><1|
    K_1 = sqrt((1 - sqrt(1-eps^2))/2) * |0><0| + sqrt((1 + sqrt(1-eps^2))/2) * |1><1|
  These satisfy sum(K_k^dag K_k) = I (completeness).
  epsilon=0 -> no disturbance; epsilon=1 -> projective measurement.

Drift prediction integration:
  Uses ml/drift_ensemble.py DriftEnsemble.predict() to forecast
  parameter drift and adjust D2 drive accordingly.
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)


class ProbeType(Enum):
    """Type of mid-circuit probe."""
    STRONG_PROJECTIVE = "strong_projective"
    WEAK_MEASUREMENT = "weak_measurement"
    ANCILLA_BASED = "ancilla_based"
    NONE = "none"


@dataclass
class DPDBlock:
    """Single Drive-Probe-Drive sequence definition.

    Args:
        drive1_hamiltonian: Control Hamiltonian for D1 phase.
        drive1_duration: Duration of D1 in seconds.
        probe_type: Type of probe measurement.
        probe_strength: epsilon in [0, 1]. 0 = no probe, 1 = projective.
        probe_duration: Duration of probe in seconds.
        probe_observable: Observable to measure (default: sigma_z).
        drive2_hamiltonian: Control Hamiltonian for D2 phase (None = same as D1).
        drive2_duration: Duration of D2 in seconds (None = same as D1).
        adaptive: If True, D2 parameters are adjusted based on probe outcome.
        label: Human-readable label for this block.
    """
    drive1_hamiltonian: np.ndarray = field(default_factory=lambda: np.eye(2, dtype=complex))
    drive1_duration: float = 1e-9
    probe_type: ProbeType = ProbeType.WEAK_MEASUREMENT
    probe_strength: float = 0.1
    probe_duration: float = 1e-10
    probe_observable: Optional[np.ndarray] = None  # Default: sigma_z
    drive2_hamiltonian: Optional[np.ndarray] = None
    drive2_duration: Optional[float] = None
    adaptive: bool = False
    label: str = ""

    def __post_init__(self):
        if self.probe_observable is None:
            self.probe_observable = np.array([[1, 0], [0, -1]], dtype=complex)
        if self.drive2_hamiltonian is None:
            self.drive2_hamiltonian = self.drive1_hamiltonian.copy()
        if self.drive2_duration is None:
            self.drive2_duration = self.drive1_duration


@dataclass
class DPDResult:
    """Result of executing a DPD schedule."""
    final_state: np.ndarray                          # Final density matrix
    probe_outcomes: List[Dict[str, Any]]             # Per-block probe results
    mutual_information: List[float]                   # Info gained per probe
    total_backaction: float                           # Cumulative state disturbance
    timing: Dict[str, float]                         # Wall-clock timing breakdown
    trace_preservation: float                        # Final Tr(rho) -- should be ~1
    positivity_check: bool                           # All eigenvalues >= -1e-10
    n_blocks: int                                    # Number of DPD blocks executed
    drift_corrections: List[Dict[str, Any]]          # Drift prediction adjustments
    metadata: Dict[str, Any] = field(default_factory=dict)


def _build_kraus_operators(epsilon: float, dim: int = 2) -> List[np.ndarray]:
    """Build Kraus operators for weak sigma_z measurement.

    For probe strength epsilon in [0, 1]:
      K_0 = diag(sqrt((1+s)/2), sqrt((1-s)/2))  where s = sqrt(1 - eps^2)
      K_1 = diag(sqrt((1-s)/2), sqrt((1+s)/2))

    Satisfies K_0^dag K_0 + K_1^dag K_1 = I.
    At epsilon=0: K_0 = I, K_1 = 0 (no measurement).
    At epsilon=1: K_0 = |0><0|, K_1 = |1><1| (projective).
    """
    eps = np.clip(epsilon, 0.0, 1.0)

    # At epsilon=1, use exact projective operators to avoid numerical issues
    if eps >= 1.0 - 1e-12:
        K0 = np.array([[1, 0], [0, 0]], dtype=complex)  # |0><0|
        K1 = np.array([[0, 0], [0, 1]], dtype=complex)  # |1><1|
        return [K0, K1]

    s = np.sqrt(1.0 - eps ** 2)

    a = np.sqrt((1.0 + s) / 2.0)
    b = np.sqrt((1.0 - s) / 2.0)

    K0 = np.diag([a, b]).astype(complex)
    K1 = np.diag([b, a]).astype(complex)

    return [K0, K1]


def _apply_kraus(rho: np.ndarray, kraus_ops: List[np.ndarray]) -> Tuple[np.ndarray, int, List[float]]:
    """Apply Kraus measurement to density matrix.

    Returns:
        (post_measurement_rho, outcome_index, outcome_probabilities)

    The outcome is sampled probabilistically:
        p_k = Tr(K_k rho K_k^dag)
        rho_k = K_k rho K_k^dag / p_k
    """
    probs = []
    for K in kraus_ops:
        rho_k = K @ rho @ K.conj().T
        probs.append(float(np.real(np.trace(rho_k))))

    # Normalize probabilities
    total = sum(probs)
    if total > 0:
        probs = [p / total for p in probs]

    # Sample outcome
    outcome = np.random.choice(len(kraus_ops), p=probs)

    # Post-measurement state
    K = kraus_ops[outcome]
    rho_post = K @ rho @ K.conj().T
    tr = np.real(np.trace(rho_post))
    if tr > 1e-15:
        rho_post /= tr

    return rho_post, int(outcome), probs


def _mutual_information_from_probs(probs: List[float]) -> float:
    """Estimate mutual information from measurement outcome probabilities.

    I(S;P) ~ H(P) = -sum p_k log2(p_k) for the measurement outcomes.
    This is an upper bound on the actual mutual information.
    """
    info = 0.0
    for p in probs:
        if p > 1e-15:
            info -= p * np.log2(p)
    return info


def _fidelity(rho_before: np.ndarray, rho_after: np.ndarray) -> float:
    """State fidelity F(rho, sigma) for the backaction measure."""
    # For density matrices: F = (Tr sqrt(sqrt(rho) sigma sqrt(rho)))^2
    # Simplified for near-pure states: F ~ Tr(rho * sigma)
    return float(np.real(np.trace(rho_before @ rho_after)))


class DPDScheduler:
    """Build and execute multi-block DPD sequences.

    Args:
        backend: Hardware backend for platform-specific parameters.
        inter_block_latency: Dead time between blocks in seconds.
        use_drift_prediction: If True, query ml/drift_ensemble.py for corrections.
    """

    def __init__(self, backend=None,
                 inter_block_latency: float = 0.0,
                 use_drift_prediction: bool = False):
        self.backend = backend
        self.inter_block_latency = inter_block_latency
        self.use_drift_prediction = use_drift_prediction
        self._blocks: List[DPDBlock] = []
        self._drift_ensemble = None

        if use_drift_prediction:
            try:
                from ml.drift_ensemble import DriftEnsemble
                self._drift_ensemble = DriftEnsemble()
                log.info("Drift prediction enabled for DPD scheduler")
            except Exception as e:
                log.warning("Drift prediction unavailable: %s", e)
                self._drift_ensemble = None

    def add_block(self, block: DPDBlock) -> "DPDScheduler":
        """Add a DPD block to the schedule."""
        self._blocks.append(block)
        return self

    def clear(self) -> "DPDScheduler":
        """Clear all blocks."""
        self._blocks.clear()
        return self

    @property
    def n_blocks(self) -> int:
        return len(self._blocks)

    def build_schedule(self) -> List[Dict[str, Any]]:
        """Build timed event list from blocks.

        Returns list of events with absolute start times.
        """
        events = []
        t_cursor = 0.0

        for i, block in enumerate(self._blocks):
            # D1 drive
            events.append({
                "type": "drive",
                "phase": "D1",
                "block_index": i,
                "t_start": t_cursor,
                "duration": block.drive1_duration,
                "hamiltonian": block.drive1_hamiltonian,
                "label": block.label,
            })
            t_cursor += block.drive1_duration

            # Probe
            if block.probe_type != ProbeType.NONE:
                events.append({
                    "type": "probe",
                    "block_index": i,
                    "t_start": t_cursor,
                    "duration": block.probe_duration,
                    "probe_type": block.probe_type.value,
                    "probe_strength": block.probe_strength,
                    "observable": block.probe_observable,
                })
                t_cursor += block.probe_duration

            # D2 drive
            events.append({
                "type": "drive",
                "phase": "D2",
                "block_index": i,
                "t_start": t_cursor,
                "duration": block.drive2_duration,
                "hamiltonian": block.drive2_hamiltonian,
                "adaptive": block.adaptive,
            })
            t_cursor += block.drive2_duration

            # Inter-block latency
            if i < len(self._blocks) - 1 and self.inter_block_latency > 0:
                t_cursor += self.inter_block_latency

        return events

    def execute(self, rho0: np.ndarray, dt: float = 1e-12) -> DPDResult:
        """Execute the full DPD schedule on an initial density matrix.

        Args:
            rho0: Initial density matrix.
            dt: RK4 time step for Lindblad propagation.

        Returns:
            DPDResult with final state, probe outcomes, timing, etc.
        """
        from .lindblad_rk4 import LindbladPropagator

        t_wall_start = time.perf_counter()
        rho = np.asarray(rho0, dtype=complex).copy()
        dim = rho.shape[0]

        # Get Lindblad operators from backend
        lindblad_ops = []
        if self.backend is not None:
            n_qubits = int(np.log2(dim))
            lindblad_ops = self.backend.default_lindblad_ops(n_qubits)

        probe_outcomes = []
        mutual_infos = []
        drift_corrections = []
        cumulative_backaction = 0.0

        for i, block in enumerate(self._blocks):
            # ---- D1 Drive ----
            prop = LindbladPropagator(block.drive1_hamiltonian, lindblad_ops)
            d1_result = prop.propagate(rho, block.drive1_duration, dt)
            rho = d1_result["rho_final"]

            # ---- Probe ----
            probe_info = {"block": i, "type": block.probe_type.value}

            if block.probe_type == ProbeType.NONE:
                probe_info["outcome"] = None
                probe_info["probabilities"] = []
                mutual_infos.append(0.0)

            elif block.probe_type == ProbeType.WEAK_MEASUREMENT:
                rho_before = rho.copy()
                kraus_ops = _build_kraus_operators(block.probe_strength, dim)
                rho, outcome, probs = _apply_kraus(rho, kraus_ops)

                info = _mutual_information_from_probs(probs)
                backaction = 1.0 - _fidelity(rho_before, rho)
                cumulative_backaction += backaction

                probe_info["outcome"] = outcome
                probe_info["probabilities"] = probs
                probe_info["mutual_information"] = info
                probe_info["backaction"] = backaction
                mutual_infos.append(info)

            elif block.probe_type == ProbeType.STRONG_PROJECTIVE:
                rho_before = rho.copy()
                # Projective: epsilon = 1.0
                kraus_ops = _build_kraus_operators(1.0, dim)
                rho, outcome, probs = _apply_kraus(rho, kraus_ops)

                info = _mutual_information_from_probs(probs)
                backaction = 1.0 - _fidelity(rho_before, rho)
                cumulative_backaction += backaction

                probe_info["outcome"] = outcome
                probe_info["probabilities"] = probs
                probe_info["mutual_information"] = info
                probe_info["backaction"] = backaction
                mutual_infos.append(info)

            elif block.probe_type == ProbeType.ANCILLA_BASED:
                # Ancilla-based: model as weak measurement on extended system
                # Simplified: treat as weak with ancilla overhead factor
                rho_before = rho.copy()
                kraus_ops = _build_kraus_operators(block.probe_strength * 0.8, dim)
                rho, outcome, probs = _apply_kraus(rho, kraus_ops)

                info = _mutual_information_from_probs(probs)
                backaction = 1.0 - _fidelity(rho_before, rho)
                cumulative_backaction += backaction

                probe_info["outcome"] = outcome
                probe_info["probabilities"] = probs
                probe_info["mutual_information"] = info
                probe_info["backaction"] = backaction
                mutual_infos.append(info)

            probe_outcomes.append(probe_info)

            # ---- Drift prediction (if enabled and adaptive) ----
            drift_correction = None
            if block.adaptive and self._drift_ensemble is not None:
                try:
                    # Build feature vector from probe outcome and state
                    diag = np.real(np.diag(rho))
                    features = np.array([[diag.tolist()]])  # (1, 1, F)
                    drift_pred = self._drift_ensemble.predict(features)
                    drift_correction = {
                        "block": i,
                        "predicted_drift": float(drift_pred[0]),
                        "applied": True,
                    }
                    drift_corrections.append(drift_correction)
                    log.debug("Drift correction block %d: %.4f", i, drift_pred[0])
                except Exception as e:
                    log.debug("Drift prediction skipped for block %d: %s", i, e)

            # ---- D2 Drive ----
            H_d2 = block.drive2_hamiltonian
            if drift_correction is not None and drift_correction["applied"]:
                # Apply drift correction as small perturbation to D2 Hamiltonian
                scale = 1.0 + drift_correction["predicted_drift"] * 0.01
                H_d2 = H_d2 * scale

            prop_d2 = LindbladPropagator(H_d2, lindblad_ops)
            d2_result = prop_d2.propagate(rho, block.drive2_duration, dt)
            rho = d2_result["rho_final"]

        # ---- Final diagnostics ----
        t_wall = time.perf_counter() - t_wall_start
        final_trace = float(np.real(np.trace(rho)))
        eigenvalues = np.real(np.linalg.eigvalsh(rho))
        positivity_ok = bool(np.all(eigenvalues >= -1e-10))

        return DPDResult(
            final_state=rho,
            probe_outcomes=probe_outcomes,
            mutual_information=mutual_infos,
            total_backaction=cumulative_backaction,
            timing={"wall_seconds": round(t_wall, 6)},
            trace_preservation=final_trace,
            positivity_check=positivity_ok,
            n_blocks=len(self._blocks),
            drift_corrections=drift_corrections,
            metadata={
                "backend": self.backend.get_params().name if self.backend else "none",
                "inter_block_latency": self.inter_block_latency,
                "drift_prediction_enabled": self.use_drift_prediction,
                "dt": dt,
            },
        )
