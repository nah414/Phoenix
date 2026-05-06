"""
Schrodinger Equation Family -- Unified Solver Registry

Auto-selects the correct equation form based on the physical system.
Integrates with the intelligence router, Eye of Sauron, and Omega Ledger.

Usage:
    from synthesis.equations import get_registry, auto_register_all, PhysicsContext

    registry = auto_register_all()
    ctx = PhysicsContext(n_particles=1, velocity_over_c=0.0)
    result = registry.solve(ctx, initial_state=psi0, t_final=1e-9, dt=1e-12)
    print(f"Used: {result.regime_used.name}")
"""

from .base import (
    EquationRegime, ParticleType, PhysicsContext,
    SolverResult, EquationSolver,
)
from .registry import (
    EquationRegistry, get_registry, auto_register_all,
)

__all__ = [
    "EquationRegime", "ParticleType", "PhysicsContext",
    "SolverResult", "EquationSolver",
    "EquationRegistry", "get_registry", "auto_register_all",
]
