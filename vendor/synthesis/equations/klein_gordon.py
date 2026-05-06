"""
Klein-Gordon Equation Solver -- relativistic spin-0 bosons.

(1/c^2)(d^2 psi/dt^2) - nabla^2 psi + (mc/hbar)^2 psi = 0

Second-order in time (unlike Dirac). Describes photons (scalar approx),
pions, Higgs boson, and other spin-0 particles.

Rewritten as first-order system:
    d/dt [psi, phi] = [[0, 1], [c^2 nabla^2 - (mc^2/hbar)^2, 0]] [psi, phi]
where phi = d_psi/dt.
"""

import numpy as np
from scipy.linalg import eigh, expm
from typing import Tuple
from .base import EquationSolver, EquationRegime, PhysicsContext, ParticleType, SolverResult

HBAR = 1.054571817e-34
M_ELECTRON = 9.1093837015e-31
C_LIGHT = 299792458.0


class KleinGordonSolver(EquationSolver):
    """Klein-Gordon equation for relativistic spin-0 bosons."""

    def regime(self) -> EquationRegime:
        return EquationRegime.KLEIN_GORDON

    def can_handle(self, ctx: PhysicsContext) -> Tuple[bool, float]:
        if ctx.particle_type == ParticleType.SPIN_ZERO_BOSON:
            if ctx.velocity_over_c >= 0.1:
                return True, 0.95
            return True, 0.6
        if ctx.spin_value == 0.0 and ctx.velocity_over_c >= 0.1:
            if ctx.particle_type != ParticleType.GEOMETRY:
                return True, 0.7
        return False, 0.0

    def build_hamiltonian(self, ctx: PhysicsContext) -> np.ndarray:
        """Build the first-order system matrix for KG equation.

        Returns a 2N x 2N matrix for the coupled [psi, phi] system.
        """
        n_grid = ctx.metadata.get("n_grid_points", 100)
        mass = ctx.mass_kg if ctx.mass_kg > 0 else M_ELECTRON
        L = ctx.length_scale_m
        dx = L / n_grid

        # Laplacian (second derivative, tridiagonal)
        lap_diag = np.full(n_grid, -2.0) / dx ** 2
        lap_off = np.full(n_grid - 1, 1.0) / dx ** 2
        LAP = np.diag(lap_diag) + np.diag(lap_off, 1) + np.diag(lap_off, -1)

        # Mass term: (mc/hbar)^2
        mass_term = (mass * C_LIGHT / HBAR) ** 2

        # Potential
        V = np.zeros(n_grid)
        if "potential" in ctx.metadata:
            V = ctx.metadata["potential"]

        # Spatial operator: c^2 * LAP - mass_term - V
        A = C_LIGHT ** 2 * LAP - mass_term * np.eye(n_grid) - np.diag(V)

        # First-order system: d/dt [psi, phi] = [[0, I], [A, 0]] [psi, phi]
        Z = np.zeros((n_grid, n_grid))
        I_n = np.eye(n_grid)
        H_system = np.block([[Z, I_n], [A, Z]])

        return H_system

    def solve_stationary(self, ctx, n_states=5):
        """Find KG energy eigenvalues.

        E^2 = p^2 c^2 + m^2 c^4 -- both positive and negative energy solutions.
        """
        n_grid = ctx.metadata.get("n_grid_points", 100)
        mass = ctx.mass_kg if ctx.mass_kg > 0 else M_ELECTRON
        L = ctx.length_scale_m
        dx = L / n_grid

        lap_diag = np.full(n_grid, -2.0) / dx ** 2
        lap_off = np.full(n_grid - 1, 1.0) / dx ** 2
        LAP = np.diag(lap_diag) + np.diag(lap_off, 1) + np.diag(lap_off, -1)

        mass_term = (mass * C_LIGHT / HBAR) ** 2
        KG_operator = -C_LIGHT ** 2 * HBAR ** 2 * LAP + mass_term * HBAR ** 2 * np.eye(n_grid)

        eigenvalues_sq, eigenstates = eigh(KG_operator)
        # E^2 eigenvalues -> E = sqrt(E^2) for positive branch
        E_positive = np.sqrt(np.maximum(eigenvalues_sq, 0))

        return SolverResult(
            state=eigenstates[:, 0], energy=float(E_positive[0]),
            eigenvalues=E_positive[:n_states], eigenstates=eigenstates[:, :n_states],
            solver_name="KleinGordonSolver", regime_used=self.regime(),
            metadata={"spectrum_type": "positive_energy_branch", "E_squared": eigenvalues_sq[:n_states].tolist()},
        )

    def evolve(self, ctx, initial_state, t_final, dt, store_history=False):
        """Evolve KG equation as first-order system."""
        H = self.build_hamiltonian(ctx)
        n_grid = H.shape[0] // 2

        # Initial state: [psi, d_psi/dt=0]
        if len(initial_state) == n_grid:
            state = np.zeros(2 * n_grid, dtype=complex)
            state[:n_grid] = initial_state
        else:
            state = initial_state.copy().astype(complex)

        n_steps = max(1, int(t_final / dt))
        U = expm(H * dt)  # No -i/hbar factor -- already first-order system

        history = [state[:n_grid].copy()] if store_history else None
        for step in range(n_steps):
            state = U @ state
            if store_history and step % max(1, n_steps // 100) == 0:
                history.append(state[:n_grid].copy())

        return SolverResult(
            state=state[:n_grid],
            state_history=np.array(history) if history else None,
            solver_name="KleinGordonSolver", regime_used=self.regime(),
            metadata={"n_steps": n_steps, "dt": dt, "equation_order": 2},
        )

    def validate_state(self, state, ctx):
        return np.linalg.norm(state) > 0

    def estimated_memory_bytes(self, ctx):
        n = ctx.metadata.get("n_grid_points", 100)
        return (2 * n) ** 2 * 16

    def gpu_beneficial(self, ctx):
        return ctx.metadata.get("n_grid_points", 100) > 200

    def latex_representation(self) -> str:
        return r"\frac{1}{c^2} \frac{\partial^2 \psi}{\partial t^2} - \nabla^2 \psi + \frac{m^2 c^2}{\hbar^2} \psi = 0"

    def validate_parameters(self, ctx):
        """Pre-flight parameter consistency checks."""
        warnings = []
        mass = ctx.mass_kg if ctx.mass_kg > 0 else M_ELECTRON
        n_grid = ctx.metadata.get("n_grid_points", 100)
        total_dim = 2 * n_grid  # KG uses 2n system
        mem_bytes = total_dim ** 2 * 16
        if mem_bytes > 2e9:
            warnings.append(f"KG system matrix will use {mem_bytes/1e9:.1f} GB RAM")
        return warnings

    def calibration_check(self, ctx, result):
        """Check rest energy matches m*c^2."""
        checks = []
        mass = ctx.mass_kg if ctx.mass_kg > 0 else M_ELECTRON
        expected_rest = mass * C_LIGHT ** 2
        if result.eigenvalues is not None and len(result.eigenvalues) >= 1:
            actual_E0 = float(result.eigenvalues[0])
            ratio = actual_E0 / expected_rest if expected_rest > 0 else float('inf')
            checks.append({
                "benchmark": "KG_rest_energy",
                "expected": expected_rest, "actual": actual_E0,
                "ratio": ratio, "tolerance": 0.05,
                "passed": abs(ratio - 1.0) < 0.05,
            })
        return checks
