"""
Dirac Equation Solver -- fully relativistic spin-1/2 fermions.

ihbar d_psi/dt = (c alpha.p + beta mc^2 + V) psi

psi is a 4-component Dirac spinor. Naturally includes spin.
Use when v/c > 0.1 or for inner electrons of heavy atoms.
"""

import numpy as np
from scipy.linalg import eigh, expm
from typing import Tuple
from .base import EquationSolver, EquationRegime, PhysicsContext, ParticleType, SolverResult

HBAR = 1.054571817e-34
M_ELECTRON = 9.1093837015e-31
C_LIGHT = 299792458.0
E_CHARGE = 1.602176634e-19

sigma_x = np.array([[0, 1], [1, 0]], dtype=complex)
sigma_y = np.array([[0, -1j], [1j, 0]], dtype=complex)
sigma_z = np.array([[1, 0], [0, -1]], dtype=complex)
I2 = np.eye(2, dtype=complex)
Z2 = np.zeros((2, 2), dtype=complex)

ALPHA_X = np.block([[Z2, sigma_x], [sigma_x, Z2]])
ALPHA_Y = np.block([[Z2, sigma_y], [sigma_y, Z2]])
ALPHA_Z = np.block([[Z2, sigma_z], [sigma_z, Z2]])
BETA = np.block([[I2, Z2], [Z2, -I2]])
I4 = np.eye(4, dtype=complex)


class DiracSolver(EquationSolver):
    """Dirac equation for fully relativistic spin-1/2 fermions."""

    def regime(self) -> EquationRegime:
        return EquationRegime.DIRAC

    def can_handle(self, ctx: PhysicsContext) -> Tuple[bool, float]:
        if ctx.spin_value not in (0.0, 0.5):
            return False, 0.0
        if ctx.particle_type == ParticleType.SPIN_ZERO_BOSON:
            return False, 0.0
        if ctx.particle_type == ParticleType.GEOMETRY:
            return False, 0.0
        if ctx.velocity_over_c >= 0.1:
            return True, 0.95
        elif ctx.velocity_over_c >= 0.01:
            return True, 0.7
        return True, 0.2

    def build_hamiltonian(self, ctx: PhysicsContext) -> np.ndarray:
        n_grid = ctx.metadata.get("n_grid_points", 100)
        mass = ctx.mass_kg if ctx.mass_kg > 0 else M_ELECTRON
        L = ctx.length_scale_m
        dx = L / n_grid

        p_upper = np.full(n_grid - 1, -1j * HBAR / (2 * dx))
        p_lower = np.full(n_grid - 1, 1j * HBAR / (2 * dx))
        P_x = np.diag(p_upper, 1) + np.diag(p_lower, -1)

        H_kinetic = C_LIGHT * np.kron(P_x, ALPHA_X)
        H_mass = mass * C_LIGHT ** 2 * np.kron(np.eye(n_grid), BETA)

        V = np.zeros(n_grid)
        if "potential" in ctx.metadata:
            V = ctx.metadata["potential"]
        H_pot = np.kron(np.diag(V), I4)

        return H_kinetic + H_mass + H_pot

    def solve_stationary(self, ctx, n_states=5):
        H = self.build_hamiltonian(ctx)
        eigenvalues, eigenstates = eigh(H)
        mass = ctx.mass_kg if ctx.mass_kg > 0 else M_ELECTRON
        rest_energy = mass * C_LIGHT ** 2

        positive_mask = eigenvalues > 0
        pos_vals = eigenvalues[positive_mask]
        pos_states = eigenstates[:, positive_mask]

        return SolverResult(
            state=pos_states[:, 0] if len(pos_vals) > 0 else eigenstates[:, 0],
            energy=float(pos_vals[0]) if len(pos_vals) > 0 else float(eigenvalues[0]),
            eigenvalues=pos_vals[:n_states] if len(pos_vals) >= n_states else eigenvalues[:n_states],
            eigenstates=pos_states[:, :n_states] if pos_states.shape[1] >= n_states else eigenstates[:, :n_states],
            solver_name="DiracSolver", regime_used=self.regime(),
            metadata={"rest_energy_J": rest_energy, "spectrum_type": "positive_energy"},
        )

    def evolve(self, ctx, initial_state, t_final, dt, store_history=False):
        H = self.build_hamiltonian(ctx)
        U = expm(-1j * H * dt / HBAR)
        n_steps = max(1, int(t_final / dt))
        state = initial_state.copy().astype(complex)
        history = [state.copy()] if store_history else None
        for step in range(n_steps):
            state = U @ state
            norm = np.linalg.norm(state)
            if norm > 0:
                state /= norm
            if store_history and step % max(1, n_steps // 100) == 0:
                history.append(state.copy())
        return SolverResult(
            state=state, state_history=np.array(history) if history else None,
            solver_name="DiracSolver", regime_used=self.regime(),
            metadata={"n_steps": n_steps, "dt": dt, "spinor_components": 4},
        )

    def validate_state(self, state, ctx):
        return abs(np.linalg.norm(state) - 1.0) < 1e-6

    def estimated_memory_bytes(self, ctx):
        n = ctx.metadata.get("n_grid_points", 100)
        return (4 * n) ** 2 * 16

    def gpu_beneficial(self, ctx):
        return ctx.metadata.get("n_grid_points", 100) > 100

    def latex_representation(self) -> str:
        return r"\left( i\hbar \gamma^\mu \partial_\mu - mc \right) \psi = 0"

    def validate_parameters(self, ctx):
        """Pre-flight parameter consistency checks."""
        warnings = []
        mass = ctx.mass_kg if ctx.mass_kg > 0 else M_ELECTRON
        n_grid = ctx.metadata.get("n_grid_points", 100)
        total_dim = 4 * n_grid
        mem_bytes = total_dim ** 2 * 16
        if mem_bytes > 4e9:
            warnings.append(f"Dirac Hamiltonian will use {mem_bytes/1e9:.1f} GB RAM")
        if n_grid > 200:
            warnings.append("Dirac solver with n_grid>200 creates >1GB matrices. Consider reducing.")
        return warnings

    def calibration_check(self, ctx, result):
        """Check rest energy against m*c^2."""
        checks = []
        mass = ctx.mass_kg if ctx.mass_kg > 0 else M_ELECTRON
        expected_rest = mass * C_LIGHT ** 2
        if result.eigenvalues is not None and len(result.eigenvalues) >= 1:
            actual_E0 = float(result.eigenvalues[0])
            ratio = actual_E0 / expected_rest if expected_rest > 0 else float('inf')
            checks.append({
                "benchmark": "Dirac_rest_energy",
                "expected": expected_rest, "actual": actual_E0,
                "ratio": ratio, "tolerance": 0.20,
                "passed": abs(ratio - 1.0) < 0.20,
                "notes": "Positive-energy branch, 1D discretization adds confinement energy",
            })
        return checks
