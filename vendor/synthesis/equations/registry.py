"""
Dr. Frank & Eddy -- Hamiltonian Classifier & Equation Registry

Reads a PhysicsContext and selects the best-fit Schrodinger equation solver.

Decision tree:
    1. Is gravity quantum? -> Wheeler-DeWitt
    2. Is gravity semiclassical? -> Semiclassical gravity SE
    3. Is gravity Newtonian? -> Add V=-GMm/r to Hamiltonian
    4. Is v/c > 0.1? -> Dirac (spin-1/2) or Klein-Gordon (spin-0)
    5. Has spin + B field? -> Pauli equation
    6. Fine structure needed? -> Breit-Pauli
    7. Open system? -> Lindblad or Stochastic SE
    8. Classical-quantum bridge? -> Ehrenfest or WKB
    9. Default -> Standard TDSE or TISE
"""

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple, Dict, Type

from .base import (
    EquationSolver, EquationRegime, PhysicsContext,
    ParticleType, SolverResult
)

log = logging.getLogger(__name__)

RELATIVISTIC_THRESHOLD = 0.01
FULLY_RELATIVISTIC_THRESHOLD = 0.1
FINE_STRUCTURE_ALPHA = 1 / 137.036
GRAVITATIONAL_DECOHERENCE_MASS_KG = 1e-17


@dataclass
class MultiSolverResult:
    """v4.3: Result of running multiple solvers with confidence weighting.

    The confidence spectrum across solvers IS the structural envelope (GoZ Technique 5).
    A sharply peaked spectrum means the regime is well-determined.
    A broad spectrum means we're near a regime boundary.
    """
    primary: Optional[Tuple[EquationRegime, float, SolverResult]] = None
    all_results: List[Tuple[EquationRegime, float, SolverResult]] = field(default_factory=list)
    spectrum: List[Tuple[EquationRegime, float]] = field(default_factory=list)
    spectrum_sharpness: float = float("inf")
    well_determined: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


class EquationRegistry:
    """Central registry for all equation solvers."""

    def __init__(self):
        self._solvers: Dict[EquationRegime, EquationSolver] = {}
        self._classification_log: List[Dict] = []

    def register(self, solver: EquationSolver) -> None:
        regime = solver.regime()
        self._solvers[regime] = solver
        log.info("Registered solver: %s -> %s", regime.name, type(solver).__name__)

    def classify(self, ctx: PhysicsContext) -> Tuple[EquationRegime, float, str]:
        """Determine which equation form best fits this physical system."""
        candidates = []
        for regime, solver in self._solvers.items():
            can, confidence = solver.can_handle(ctx)
            if can:
                candidates.append((regime, confidence, solver))

        if not candidates:
            return (EquationRegime.NON_RELATIVISTIC_TD, 0.5,
                    "No specialized solver matched; falling back to standard TDSE.")

        candidates.sort(key=lambda x: x[1], reverse=True)
        best_regime, best_conf, best_solver = candidates[0]
        reasoning = self._build_reasoning(ctx, best_regime, best_conf, candidates)

        self._classification_log.append({
            "selected_regime": best_regime.name,
            "confidence": best_conf,
            "all_candidates": [(r.name, c) for r, c, _ in candidates],
            "reasoning": reasoning,
        })

        return best_regime, best_conf, reasoning

    def get_solver(self, regime: EquationRegime) -> Optional[EquationSolver]:
        return self._solvers.get(regime)

    def solve(self, ctx: PhysicsContext, **kwargs) -> SolverResult:
        """One-shot: classify the system and solve it."""
        regime, confidence, reasoning = self.classify(ctx)
        solver = self._solvers.get(regime)
        if solver is None:
            raise RuntimeError(f"Regime {regime.name} selected but no solver registered.")

        log.info("Solving with %s (confidence=%.2f): %s", regime.name, confidence, reasoning)

        if kwargs.get("stationary", False):
            result = solver.solve_stationary(ctx, kwargs.get("n_states", 5))
        else:
            result = solver.evolve(
                ctx, kwargs.get("initial_state"),
                kwargs.get("t_final", 1e-9), kwargs.get("dt", 1e-12),
                kwargs.get("store_history", False),
            )

        result.regime_used = regime
        result.metadata["classification_reasoning"] = reasoning
        result.metadata["classification_confidence"] = confidence
        return result

    def classify_spectrum(self, ctx: PhysicsContext) -> List[Tuple[EquationRegime, float, EquationSolver]]:
        """v4.3: Return ALL solvers sorted by confidence, not just the top one.

        The confidence spectrum IS the structural envelope (GoZ Technique 5).
        A sharply peaked spectrum (top >> runner-up) = well-determined regime.
        A broad spectrum (multiple close) = regime boundary, needs more verification.

        Args:
            ctx: Physics context to classify.

        Returns:
            List of (regime, confidence, solver) tuples sorted by confidence descending.
            Only includes candidates with confidence > 0.1.
        """
        candidates = []
        for regime, solver in self._solvers.items():
            can, confidence = solver.can_handle(ctx)
            if can and confidence > 0.1:
                candidates.append((regime, confidence, solver))
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates

    def solve_multi(self, ctx: PhysicsContext, top_n: int = 3, **kwargs) -> MultiSolverResult:
        """v4.3: Run top N solvers and weight-combine results.

        The structural envelope determines how many solvers to run and
        how to weight their outputs. Sharply peaked = trust the top solver.
        Broad = cross-validate across solvers.

        Only escalates from single-solver when sharpness < 2.0.

        Args:
            ctx: Physics context to solve.
            top_n: Maximum number of solvers to run (default 3).

        Returns:
            MultiSolverResult with individual solver outputs and spectrum analysis.
        """
        spectrum = self.classify_spectrum(ctx)
        if not spectrum:
            return MultiSolverResult(metadata={"error": "no_candidates"})

        # Compute sharpness
        if len(spectrum) >= 2:
            sharpness = spectrum[0][1] / max(spectrum[1][1], 1e-10)
        else:
            sharpness = float("inf")

        well_determined = sharpness > 2.0

        # Run solvers
        top = spectrum[:top_n]
        total_confidence = sum(conf for _, conf, _ in top)
        results = []

        for regime, confidence, solver in top:
            weight = confidence / max(total_confidence, 1e-10)
            try:
                if kwargs.get("stationary", True):
                    result = solver.solve_stationary(ctx, kwargs.get("n_states", 5))
                else:
                    result = solver.evolve(
                        ctx, kwargs.get("initial_state"),
                        kwargs.get("t_final", 1e-9), kwargs.get("dt", 1e-12),
                        kwargs.get("store_history", False),
                    )
                result.regime_used = regime
                results.append((regime, weight, result))
            except Exception as e:
                log.warning("Solver %s failed: %s", regime.name, e)

        primary = results[0] if results else None
        spectrum_tuples = [(r, c) for r, c, _ in spectrum]

        return MultiSolverResult(
            primary=primary,
            all_results=results,
            spectrum=spectrum_tuples,
            spectrum_sharpness=sharpness,
            well_determined=well_determined,
            metadata={
                "top_n": top_n,
                "n_candidates": len(spectrum),
                "n_ran": len(results),
            },
        )

    def available_regimes(self) -> List[EquationRegime]:
        return list(self._solvers.keys())

    def _build_reasoning(self, ctx, selected, confidence, candidates) -> str:
        parts = [f"Selected {selected.name} (confidence {confidence:.2f})."]
        if ctx.velocity_over_c >= FULLY_RELATIVISTIC_THRESHOLD:
            parts.append(f"v/c = {ctx.velocity_over_c:.3f} exceeds relativistic threshold.")
        elif ctx.velocity_over_c >= RELATIVISTIC_THRESHOLD:
            parts.append(f"v/c = {ctx.velocity_over_c:.3f} -- semi-relativistic regime.")
        if ctx.has_spin and ctx.has_magnetic_field:
            parts.append(f"Spin-{ctx.spin_value} in B = {ctx.magnetic_field_tesla} T.")
        if ctx.is_open_system:
            parts.append(f"Open system ({ctx.environment_coupling} coupling).")
        if ctx.include_gravity:
            parts.append(f"Gravitational regime: {ctx.gravitational_regime}.")
        if len(candidates) > 1:
            parts.append(f"Runner-up: {candidates[1][0].name} ({candidates[1][1]:.2f}).")
        return " ".join(parts)


_global_registry: Optional[EquationRegistry] = None
_registry_lock = threading.Lock()


def get_registry() -> EquationRegistry:
    global _global_registry
    if _global_registry is None:
        with _registry_lock:
            if _global_registry is None:
                _global_registry = EquationRegistry()
    return _global_registry


def auto_register_all() -> EquationRegistry:
    """Import and register all available equation solvers."""
    registry = get_registry()

    from .non_relativistic import TISESolver, TDSESolver
    registry.register(TISESolver())
    registry.register(TDSESolver())

    from .pauli import PauliSolver
    registry.register(PauliSolver())

    from .dirac import DiracSolver
    registry.register(DiracSolver())

    from .breit_pauli import BreitPauliSolver
    registry.register(BreitPauliSolver())

    from .klein_gordon import KleinGordonSolver
    registry.register(KleinGordonSolver())

    from .ehrenfest import EhrenfestSolver, WKBSolver
    registry.register(EhrenfestSolver())
    registry.register(WKBSolver())

    from .stochastic import StochasticSESolver
    registry.register(StochasticSESolver())

    from .gravitational import GravitationalDecoherenceSolver, SemiclassicalGravitySolver
    registry.register(GravitationalDecoherenceSolver())
    registry.register(SemiclassicalGravitySolver())

    from .wheeler_dewitt import WheelerDeWittSolver
    registry.register(WheelerDeWittSolver())

    from .lindblad import LindbladSolver
    registry.register(LindbladSolver())

    from .redfield import RedfieldSolver
    registry.register(RedfieldSolver())

    return registry
