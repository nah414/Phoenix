"""
Wheeler-DeWitt Equation Solver -- quantum gravity frontier.

H|Psi> = 0 (Hamiltonian constraint -- no external time)

Minisuperspace approximation: wave function of the universe depends
on scale factor a and optional scalar field phi.

THIS IS RESEARCH-GRADE FRONTIER PHYSICS. Results carry
"frontier_physics": True in all metadata.
"""

import numpy as np
from scipy.linalg import eigh
from scipy.sparse.linalg import eigsh
from typing import Tuple
from enum import Enum, auto
from .base import EquationSolver, EquationRegime, PhysicsContext, ParticleType, SolverResult

HBAR = 1.054571817e-34
G_NEWTON = 6.67430e-11
C_LIGHT = 299792458.0
M_PLANCK = 2.176434e-8
L_PLANCK = 1.616255e-35


class FactorOrdering(Enum):
    DEWITT = auto()
    LAPLACIAN = auto()
    SYMMETRIC = auto()


class WheelerDeWittSolver(EquationSolver):
    """Wheeler-DeWitt equation in minisuperspace."""

    def __init__(self, factor_ordering: FactorOrdering = FactorOrdering.LAPLACIAN):
        self.factor_ordering = factor_ordering

    def regime(self) -> EquationRegime:
        return EquationRegime.WHEELER_DEWITT

    def can_handle(self, ctx: PhysicsContext) -> Tuple[bool, float]:
        if ctx.gravitational_regime != "quantum":
            return False, 0.0
        if ctx.particle_type != ParticleType.GEOMETRY:
            return False, 0.0
        return True, 0.95

    def build_hamiltonian(self, ctx: PhysicsContext) -> np.ndarray:
        n_alpha = ctx.metadata.get("n_alpha_points", 200)
        n_phi = ctx.metadata.get("n_phi_points", 0)
        alpha_range = ctx.metadata.get("alpha_range", (-5.0, 5.0))
        Lambda = ctx.metadata.get("cosmological_constant", 1e-52)
        k = ctx.metadata.get("spatial_curvature", 1)

        if n_phi == 0:
            return self._build_1d(n_alpha, alpha_range, Lambda, k)
        return self._build_2d(n_alpha, n_phi, alpha_range, Lambda, k, ctx)

    def _build_1d(self, n, alpha_range, Lambda, k):
        alpha_min, alpha_max = alpha_range
        dalpha = (alpha_max - alpha_min) / n
        alpha = np.linspace(alpha_min, alpha_max, n)

        kinetic_diag = np.full(n, 2.0 / dalpha ** 2)
        kinetic_off = np.full(n - 1, -1.0 / dalpha ** 2)
        H = np.diag(kinetic_diag) + np.diag(kinetic_off, 1) + np.diag(kinetic_off, -1)

        V = -k * np.exp(2 * alpha) + (Lambda / 3) * np.exp(4 * alpha)
        return -H + np.diag(V)

    def _build_2d(self, n_alpha, n_phi, alpha_range, Lambda, k, ctx):
        alpha_min, alpha_max = alpha_range
        phi_range = ctx.metadata.get("phi_range", (-5.0, 5.0))
        dalpha = (alpha_max - alpha_min) / n_alpha
        dphi = (phi_range[1] - phi_range[0]) / n_phi
        alpha = np.linspace(alpha_min, alpha_max, n_alpha)
        phi = np.linspace(phi_range[0], phi_range[1], n_phi)

        d2_alpha = (np.diag(np.full(n_alpha, -2.0)) +
                    np.diag(np.ones(n_alpha - 1), 1) +
                    np.diag(np.ones(n_alpha - 1), -1)) / dalpha ** 2
        K_alpha = np.kron(d2_alpha, np.eye(n_phi))

        d2_phi = (np.diag(np.full(n_phi, -2.0)) +
                  np.diag(np.ones(n_phi - 1), 1) +
                  np.diag(np.ones(n_phi - 1), -1)) / dphi ** 2
        K_phi = np.kron(np.diag(np.exp(4 * alpha)), d2_phi)

        N_total = n_alpha * n_phi
        V_2d = np.zeros(N_total)
        scalar_mass = ctx.metadata.get("scalar_field_mass", 0.0)
        for i in range(n_alpha):
            for j in range(n_phi):
                idx = i * n_phi + j
                V_2d[idx] = (-k * np.exp(2 * alpha[i]) +
                             (Lambda / 3) * np.exp(4 * alpha[i]) +
                             0.5 * scalar_mass ** 2 * phi[j] ** 2 * np.exp(6 * alpha[i]))
        return K_alpha + K_phi + np.diag(V_2d)

    def solve_stationary(self, ctx, n_states=5):
        H = self.build_hamiltonian(ctx)
        if H.shape[0] <= 1000:
            eigenvalues, eigenstates = eigh(H)
        else:
            eigenvalues, eigenstates = eigsh(H, k=min(n_states * 2, H.shape[0] - 2), sigma=0.0)
            idx = np.argsort(np.abs(eigenvalues))
            eigenvalues, eigenstates = eigenvalues[idx], eigenstates[:, idx]

        return SolverResult(
            state=eigenstates[:, 0], energy=0.0,
            eigenvalues=eigenvalues[:n_states], eigenstates=eigenstates[:, :n_states],
            solver_name="WheelerDeWittSolver (minisuperspace)", regime_used=self.regime(),
            metadata={
                "frontier_physics": True,
                "model_status": "research_grade_no_experimental_confirmation",
                "factor_ordering": self.factor_ordering.name,
                "constraint_violation": float(np.min(np.abs(eigenvalues))),
                "warning": "Wheeler-DeWitt has no unique formulation. Results are exploratory.",
            },
        )

    def evolve(self, ctx, initial_state, t_final, dt, store_history=False):
        raise NotImplementedError(
            "Wheeler-DeWitt has no external time parameter. "
            "Use solve_stationary() to find physical states."
        )

    def validate_state(self, state, ctx):
        H = self.build_hamiltonian(ctx)
        expectation = float(np.real(state.conj() @ H @ state))
        norm = np.linalg.norm(state)
        return abs(expectation) < 0.01 * abs(float(np.max(np.abs(H)))) and abs(norm - 1.0) < 1e-6

    def hartle_hawking_boundary(self, ctx: PhysicsContext) -> np.ndarray:
        n = ctx.metadata.get("n_alpha_points", 200)
        alpha_range = ctx.metadata.get("alpha_range", (-5.0, 5.0))
        alpha = np.linspace(alpha_range[0], alpha_range[1], n)
        a = np.exp(alpha)
        psi_hh = np.exp(-a ** 2 / 2)
        psi_hh /= np.linalg.norm(psi_hh)
        return psi_hh

    def vilenkin_tunneling_boundary(self, ctx: PhysicsContext) -> np.ndarray:
        n = ctx.metadata.get("n_alpha_points", 200)
        alpha_range = ctx.metadata.get("alpha_range", (-5.0, 5.0))
        alpha = np.linspace(alpha_range[0], alpha_range[1], n)
        a = np.exp(alpha)
        Lambda = ctx.metadata.get("cosmological_constant", 1e-52)
        a_turn = np.sqrt(3 / max(Lambda, 1e-100))
        psi_v = np.zeros(n, dtype=complex)
        for i, ai in enumerate(a):
            if ai > a_turn:
                S = np.sqrt(Lambda / 3) * ai ** 2
                psi_v[i] = (1.0 / np.sqrt(max(ai, 1e-10))) * np.exp(1j * S)
            else:
                kappa = np.sqrt(max(a_turn ** 2 - ai ** 2, 0))
                psi_v[i] = np.exp(-kappa)
        norm = np.linalg.norm(psi_v)
        if norm > 0:
            psi_v /= norm
        return psi_v

    def estimated_memory_bytes(self, ctx):
        n_alpha = ctx.metadata.get("n_alpha_points", 200)
        n_phi = ctx.metadata.get("n_phi_points", 0)
        if n_phi == 0:
            return n_alpha ** 2 * 16
        N = n_alpha * n_phi
        return N ** 2 * 16

    def validate_parameters(self, ctx):
        """Pre-flight parameter consistency checks."""
        warnings = []
        warnings.append("FRONTIER PHYSICS: Wheeler-DeWitt results depend on factor ordering choice.")
        warnings.append(f"Current ordering: {self.factor_ordering.name}")
        n_alpha = ctx.metadata.get("n_alpha_points", 200)
        n_phi = ctx.metadata.get("n_phi_points", 0)
        if n_phi > 0:
            total_dim = n_alpha * n_phi
        else:
            total_dim = n_alpha
        mem_bytes = total_dim ** 2 * 16
        if mem_bytes > 2e9:
            warnings.append(f"WDW Hamiltonian will use {mem_bytes/1e9:.1f} GB RAM")
        return warnings

    def calibration_check(self, ctx, result):
        """Check constraint satisfaction: H|Psi> should be ~0."""
        import numpy as np
        checks = []
        if result.eigenvalues is not None and len(result.eigenvalues) >= 1:
            min_eigenvalue = float(np.min(np.abs(result.eigenvalues)))
            checks.append({
                "benchmark": "WDW_constraint_satisfaction",
                "expected": 0.0, "actual": min_eigenvalue,
                "ratio": min_eigenvalue, "tolerance": 1e-6,
                "passed": min_eigenvalue < 1e-6,
                "notes": f"Factor ordering: {self.factor_ordering.name}, frontier_physics=True",
            })
        if result.eigenvalues is not None and len(result.eigenvalues) >= 3:
            # Check that first few constraint eigenvalues are near zero
            near_zero_count = sum(1 for e in result.eigenvalues[:5] if abs(float(e)) < 1e-3)
            checks.append({
                "benchmark": "WDW_near_zero_states",
                "expected": ">= 1", "actual": near_zero_count,
                "ratio": 1.0, "tolerance": 1.0,
                "passed": near_zero_count >= 1,
                "notes": "At least one state should satisfy H|Psi> approx 0",
            })
        return checks

    def latex_representation(self) -> str:
        return r"\hat{H} |\Psi\rangle = 0, \quad -\frac{\hbar^2}{2} G_{ijkl} \frac{\delta^2 \Psi}{\delta h_{ij} \delta h_{kl}} + \sqrt{h}\, {}^{(3)}\!R\, \Psi = 0"
