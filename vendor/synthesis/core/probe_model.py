"""
Probe Information-Backaction Trade-off Model

From SynQc TDS Technical Archive Section 2.2:
"Very weak probes (small epsilon, short tau_P) -> negligible information,
 negligible disturbance. Strong probes -> significant mutual information
 but strong backaction. Identified a useful sweet spot regime."

This module calculates the information gain vs state disturbance
for different probe configurations, helping find the optimal
probe strength for a given experiment.
"""

import numpy as np
from dataclasses import dataclass
from typing import Dict, Any


@dataclass
class ProbeTradeoff:
    """Result of a probe trade-off analysis."""
    probe_strength: float       # epsilon (0 = no probe, 1 = projective)
    probe_duration: float       # tau_P in seconds
    mutual_information: float   # Bits of information gained
    backaction_fidelity: float  # Fidelity loss (0 = no disturbance, 1 = full collapse)
    sweet_spot_score: float     # Information / backaction ratio
    regime: str                 # "negligible", "sweet_spot", "strong", "projective"


class ProbeModel:
    """Calculate information-backaction trade-offs for quantum probes.

    The model assumes a qubit system with a probe channel that couples
    to the system observable sigma_z with strength epsilon for duration tau_P.

    Mutual information: I(S;P) ~ epsilon^2 * tau_P / (2 ln 2) for weak probes
    Backaction: F_loss ~ 1 - epsilon^2 * tau_P^2 / 4 for weak probes

    The sweet spot maximizes I(S;P) / F_loss.
    """

    def __init__(self, system_dim: int = 2):
        self.dim = system_dim

    def analyze(self, probe_strength: float, probe_duration: float,
                system_frequency: float = 1e9) -> ProbeTradeoff:
        """Analyze a single probe configuration."""
        eps = probe_strength
        tau = probe_duration

        # Mutual information (weak probe approximation)
        info = (eps ** 2 * tau) / (2 * np.log(2)) if eps < 0.5 else np.log2(self.dim)

        # Backaction (fidelity loss)
        backaction = 1.0 - (eps ** 2 * tau ** 2 / 4) if eps < 0.5 else 1.0 - 1e-10
        backaction = max(0.0, min(1.0, backaction))

        # Sweet spot score
        score = info / max(1.0 - backaction, 1e-12) if backaction < 1.0 else 0.0

        # Classify regime
        if eps < 0.01:
            regime = "negligible"
        elif eps < 0.3:
            regime = "sweet_spot"
        elif eps < 0.8:
            regime = "strong"
        else:
            regime = "projective"

        return ProbeTradeoff(
            probe_strength=eps, probe_duration=tau,
            mutual_information=info, backaction_fidelity=backaction,
            sweet_spot_score=score, regime=regime,
        )

    def find_sweet_spot(self, probe_duration: float,
                        n_points: int = 100) -> ProbeTradeoff:
        """Find the optimal probe strength for a given duration."""
        best = None
        for i in range(1, n_points):
            eps = i / n_points
            result = self.analyze(eps, probe_duration)
            if best is None or result.sweet_spot_score > best.sweet_spot_score:
                best = result
        return best

    def sweep(self, eps_range=(0.01, 0.99), n_points: int = 50,
              probe_duration: float = 1e-9) -> list:
        """Sweep probe strength and return trade-off curve."""
        results = []
        for i in range(n_points):
            eps = eps_range[0] + (eps_range[1] - eps_range[0]) * i / (n_points - 1)
            results.append(self.analyze(eps, probe_duration))
        return results
