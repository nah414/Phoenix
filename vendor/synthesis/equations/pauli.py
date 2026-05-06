"""
Pauli Equation Solver -- spin-1/2 particles in EM fields.

ihbar d_psi/dt = H_Pauli psi
H_Pauli = (p - eA)^2 / 2m + ePhi - (ehbar/2m) sigma.B + H_SO

Regime: Semi-relativistic. Includes Zeeman + spin-orbit coupling.
"""

import numpy as np
from scipy.linalg import eigh, expm
from typing import Tuple
from .base import EquationSolver, EquationRegime, PhysicsContext, ParticleType, SolverResult

HBAR = 1.054571817e-34
M_ELECTRON = 9.1093837015e-31
E_CHARGE = 1.602176634e-19
MU_BOHR = 9.2740100783e-24

SIGMA_X = np.array([[0, 1], [1, 0]], dtype=complex)
SIGMA_Y = np.array([[0, -1j], [1j, 0]], dtype=complex)
SIGMA_Z = np.array([[1, 0], [0, -1]], dtype=complex)
I2 = np.eye(2, dtype=complex)


class PauliSolver(EquationSolver):
    """Pauli equation for spin-1/2 particles in electromagnetic fields."""

    def regime(self) -> EquationRegime:
        return EquationRegime.PAULI

    def can_handle(self, ctx: PhysicsContext) -> Tuple[bool, float]:
        if not ctx.has_spin or ctx.spin_value != 0.5:
            return False, 0.0
        if not (ctx.has_magnetic_field or ctx.has_electric_field):
            return False, 0.0
        if ctx.velocity_over_c >= 0.1:
            return True, 0.3
        if ctx.is_open_system:
            return False, 0.0
        confidence = 0.95 if not ctx.include_spin_orbit else 0.92
        return True, confidence

    def build_hamiltonian(self, ctx: PhysicsContext) -> np.ndarray:
        n_grid = ctx.metadata.get("n_grid_points", 100)
        mass = ctx.mass_kg if ctx.mass_kg > 0 else M_ELECTRON
        L = ctx.length_scale_m
        dx = L / n_grid

        diag_T = np.full(n_grid, 2.0) * HBAR ** 2 / (2 * mass * dx ** 2)
        off_T = np.full(n_grid - 1, -1.0) * HBAR ** 2 / (2 * mass * dx ** 2)
        T = np.diag(diag_T) + np.diag(off_T, 1) + np.diag(off_T, -1)

        V = np.zeros(n_grid)
        if "potential" in ctx.metadata:
            V = ctx.metadata["potential"]
        elif ctx.has_electric_field:
            x = np.linspace(-L / 2, L / 2, n_grid)
            E_field = ctx.metadata.get("electric_field_V_per_m", 1e6)
            V = -E_CHARGE * E_field * x

        H_spatial = T + np.diag(V)

        Bx = ctx.metadata.get("Bx", 0.0)
        By = ctx.metadata.get("By", 0.0)
        Bz = ctx.magnetic_field_tesla
        H_Zeeman = -MU_BOHR * (Bx * SIGMA_X + By * SIGMA_Y + Bz * SIGMA_Z)

        H_full = np.kron(H_spatial, I2) + np.kron(np.eye(n_grid), H_Zeeman)

        if ctx.include_spin_orbit:
            c = 299792458.0
            prefactor = HBAR / (4 * mass ** 2 * c ** 2)
            dVdx = np.zeros(n_grid)
            dVdx[1:-1] = (V[2:] - V[:-2]) / (2 * dx)
            p_upper = np.full(n_grid - 1, -1j * HBAR / (2 * dx))
            p_lower = np.full(n_grid - 1, 1j * HBAR / (2 * dx))
            P = np.diag(p_upper, 1) + np.diag(p_lower, -1)
            spatial_part = prefactor * np.diag(dVdx) @ P
            H_full += np.kron(spatial_part, SIGMA_Y)

        return H_full

    def solve_stationary(self, ctx, n_states=5):
        H = self.build_hamiltonian(ctx)
        eigenvalues, eigenstates = eigh(H)
        return SolverResult(
            state=eigenstates[:, 0], energy=float(eigenvalues[0]),
            eigenvalues=eigenvalues[:n_states], eigenstates=eigenstates[:, :n_states],
            solver_name="PauliSolver", regime_used=self.regime(),
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
            solver_name="PauliSolver", regime_used=self.regime(),
            metadata={"n_steps": n_steps, "dt": dt},
        )

    def validate_state(self, state, ctx):
        return abs(np.linalg.norm(state) - 1.0) < 1e-6

    def estimated_memory_bytes(self, ctx):
        n = ctx.metadata.get("n_grid_points", 100)
        return (2 * n) ** 2 * 16

    def gpu_beneficial(self, ctx):
        return ctx.metadata.get("n_grid_points", 100) > 200

    def latex_representation(self) -> str:
        return r"i\hbar \frac{\partial}{\partial t} \chi = \left[ \frac{1}{2m} \left( \hat{\mathbf{p}} - \frac{e}{c}\mathbf{A} \right)^2 + e\phi - \frac{e\hbar}{2mc} \boldsymbol{\sigma} \cdot \mathbf{B} \right] \chi"

    def validate_parameters(self, ctx):
        """Pre-flight parameter consistency checks."""
        warnings = []
        mass = ctx.mass_kg if ctx.mass_kg > 0 else M_ELECTRON
        n_grid = ctx.metadata.get("n_grid_points", 100)
        omega = ctx.metadata.get("omega", 1e12)
        L = ctx.length_scale_m

        import numpy as np
        x_0 = np.sqrt(HBAR / (mass * omega))
        if L < 6 * x_0:
            warnings.append(
                f"GRID TOO SMALL: L={L:.2e}m but x_0={x_0:.2e}m. "
                f"Need L >= {6*x_0:.2e}m."
            )
        total_dim = 2 * n_grid
        mem_bytes = total_dim ** 2 * 16
        if mem_bytes > 2e9:
            warnings.append(f"Pauli Hamiltonian will use {mem_bytes/1e9:.1f} GB RAM")
        return warnings

    def calibration_check(self, ctx, result):
        """Check Zeeman splitting against analytical value."""
        checks = []
        B = ctx.magnetic_field_tesla
        if B > 0 and result.eigenvalues is not None and len(result.eigenvalues) >= 2:
            g_factor = 2.0023
            expected_split = g_factor * MU_BOHR * B
            # Find first pair split
            e0 = float(result.eigenvalues[0])
            e1 = float(result.eigenvalues[1])
            actual_split = abs(e1 - e0)
            ratio = actual_split / expected_split if expected_split > 0 else float('inf')
            checks.append({
                "benchmark": "Zeeman_splitting",
                "expected": expected_split, "actual": actual_split,
                "ratio": ratio, "tolerance": 0.02,
                "passed": abs(ratio - 1.0) < 0.02,
                "notes": f"B={B}T, g={g_factor}",
            })
        return checks
