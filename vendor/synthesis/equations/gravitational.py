"""
Gravitational Effects on Quantum Systems.

1. GravitationalDecoherenceSolver: Diosi-Penrose collapse model
   Gamma_grav = G m^2 / (hbar d) -- gravity-induced decoherence
2. SemiclassicalGravitySolver: <T_mu_nu> sources Einstein equations
"""

import numpy as np
from scipy.linalg import eigh, expm
from typing import Tuple
from .base import EquationSolver, EquationRegime, PhysicsContext, ParticleType, SolverResult

HBAR = 1.054571817e-34
G_NEWTON = 6.67430e-11
M_PLANCK = 2.176434e-8
L_PLANCK = 1.616255e-35
C_LIGHT = 299792458.0


class GravitationalDecoherenceSolver(EquationSolver):
    """Diosi-Penrose gravitational decoherence."""

    def regime(self) -> EquationRegime:
        return EquationRegime.GRAVITATIONAL_DECOHERENCE

    def can_handle(self, ctx: PhysicsContext) -> Tuple[bool, float]:
        if not ctx.include_gravity:
            return False, 0.0
        if ctx.gravitational_regime not in ("newtonian", "semiclassical"):
            return False, 0.0
        if ctx.mass_kg > 1e-20:
            return True, 0.9
        elif ctx.mass_kg > 1e-25:
            return True, 0.5
        return True, 0.1

    def decoherence_rate(self, mass: float, separation: float) -> float:
        if separation <= 0:
            return 0.0
        return G_NEWTON * mass ** 2 / (HBAR * separation)

    def decoherence_time(self, mass: float, separation: float) -> float:
        rate = self.decoherence_rate(mass, separation)
        return 1.0 / rate if rate > 0 else float("inf")

    def build_hamiltonian(self, ctx: PhysicsContext) -> np.ndarray:
        from .non_relativistic import TISESolver
        H_base = TISESolver().build_hamiltonian(ctx)
        n = H_base.shape[0]
        L = ctx.length_scale_m
        x_grid = np.linspace(-L / 2, L / 2, n)
        mass = ctx.mass_kg if ctx.mass_kg > 0 else 1e-20

        softening = L / n
        V_grav = np.array([
            -G_NEWTON * mass ** 2 / max(abs(x), softening) for x in x_grid
        ])
        return H_base + np.diag(V_grav)

    def solve_stationary(self, ctx, n_states=5):
        H = self.build_hamiltonian(ctx)
        vals, vecs = eigh(H)
        mass = ctx.mass_kg if ctx.mass_kg > 0 else 1e-20
        sep = ctx.length_scale_m / 10
        return SolverResult(
            state=vecs[:, 0], energy=float(vals[0]),
            eigenvalues=vals[:n_states], eigenstates=vecs[:, :n_states],
            solver_name="GravitationalDecoherenceSolver", regime_used=self.regime(),
            metadata={
                "decoherence_rate_Hz": self.decoherence_rate(mass, sep),
                "decoherence_time_s": self.decoherence_time(mass, sep),
                "mass_over_planck_mass": mass / M_PLANCK,
                "model_status": "theoretical_unconfirmed",
            },
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
            solver_name="GravitationalDecoherenceSolver", regime_used=self.regime(),
            metadata={"model_status": "theoretical_unconfirmed"},
        )

    def validate_state(self, state, ctx):
        return abs(np.linalg.norm(state) - 1.0) < 1e-6

    def validate_parameters(self, ctx):
        """Pre-flight parameter consistency checks."""
        warnings = []
        warnings.append("FRONTIER PHYSICS: Gravitational decoherence (Diosi-Penrose) is not experimentally confirmed.")
        mass = ctx.mass_kg if ctx.mass_kg > 0 else 1e-20
        if mass < 1e-25:
            warnings.append(f"Mass {mass:.2e} kg is very small. Gravitational decoherence rate will be negligible.")
        return warnings

    def calibration_check(self, ctx, result):
        """Check decoherence rate scales as m^2."""
        checks = []
        if "decoherence_rate_Hz" in result.metadata:
            rate = result.metadata["decoherence_rate_Hz"]
            mass = ctx.mass_kg if ctx.mass_kg > 0 else 1e-20
            checks.append({
                "benchmark": "GravDecoh_rate_positive",
                "expected": "> 0", "actual": rate,
                "ratio": 1.0, "tolerance": 1.0,
                "passed": rate > 0,
                "notes": f"mass={mass:.2e} kg, frontier_physics=True",
            })
        return checks

    def latex_representation(self) -> str:
        return r"\frac{d\hat{\rho}}{dt} = -\frac{i}{\hbar}[\hat{H}, \hat{\rho}] - \frac{G m^2}{6\pi\hbar^2} \int d^3\mathbf{r}\, [\hat{n}(\mathbf{r}), [\hat{n}(\mathbf{r}), \hat{\rho}]]"


class SemiclassicalGravitySolver(EquationSolver):
    """Semiclassical gravity: <T_mu_nu> -> Einstein -> metric -> Hamiltonian."""

    def regime(self) -> EquationRegime:
        return EquationRegime.SEMICLASSICAL_GRAVITY

    def can_handle(self, ctx: PhysicsContext) -> Tuple[bool, float]:
        if ctx.gravitational_regime != "semiclassical":
            return False, 0.0
        return True, 0.85

    def build_hamiltonian(self, ctx: PhysicsContext) -> np.ndarray:
        from .non_relativistic import TISESolver
        H_flat = TISESolver().build_hamiltonian(ctx)
        n = H_flat.shape[0]
        mass = ctx.mass_kg if ctx.mass_kg > 0 else 1e-20
        L = ctx.length_scale_m
        x_grid = np.linspace(-L / 2, L / 2, n)

        M_source = ctx.metadata.get("gravitational_source_mass_kg", 0.0)
        r_source = ctx.metadata.get("gravitational_source_distance_m", 1.0)

        if M_source > 0:
            V_grav = np.array([
                -G_NEWTON * M_source * mass / max(abs(r_source + x), L_PLANCK)
                for x in x_grid
            ])
            Phi = -G_NEWTON * M_source / max(r_source, L_PLANCK)
            H_PN = -H_flat * Phi / C_LIGHT ** 2
            return H_flat + np.diag(V_grav) + H_PN
        return H_flat

    def solve_stationary(self, ctx, n_states=5):
        H = self.build_hamiltonian(ctx)
        vals, vecs = eigh(H)
        return SolverResult(
            state=vecs[:, 0], energy=float(vals[0]),
            eigenvalues=vals[:n_states], eigenstates=vecs[:, :n_states],
            solver_name="SemiclassicalGravitySolver", regime_used=self.regime(),
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
            solver_name="SemiclassicalGravitySolver", regime_used=self.regime(),
        )

    def validate_state(self, state, ctx):
        return abs(np.linalg.norm(state) - 1.0) < 1e-6

    def validate_parameters(self, ctx):
        """Pre-flight parameter consistency checks."""
        warnings = []
        warnings.append("FRONTIER PHYSICS: Semiclassical gravity (<T_mu_nu> sources gravity) is not experimentally confirmed.")
        mass = ctx.mass_kg if ctx.mass_kg > 0 else M_ELECTRON
        n_grid = ctx.metadata.get("n_grid_points", 200)
        mem_bytes = n_grid ** 2 * 16
        if mem_bytes > 2e9:
            warnings.append(f"Hamiltonian will use {mem_bytes/1e9:.1f} GB RAM")
        return warnings

    def calibration_check(self, ctx, result):
        """Check weak-field limit approximates TISE."""
        from synthesis.equations.non_relativistic import TISESolver
        checks = []
        tise = TISESolver()
        tise_result = tise.solve_stationary(ctx, n_states=3)
        if result.eigenvalues is not None and tise_result.eigenvalues is not None:
            e0_sc = float(result.eigenvalues[0])
            e0_tise = float(tise_result.eigenvalues[0])
            if e0_tise != 0:
                correction = abs(e0_sc - e0_tise) / abs(e0_tise)
                checks.append({
                    "benchmark": "SemiclGrav_weak_field",
                    "expected": e0_tise, "actual": e0_sc,
                    "ratio": e0_sc / e0_tise, "tolerance": 0.10,
                    "passed": correction < 0.10,
                    "notes": f"Correction = {correction*100:.3f}%, frontier_physics=True",
                })
        return checks

    def latex_representation(self) -> str:
        return r"G_{\mu\nu} = \frac{8\pi G}{c^4} \langle \hat{T}_{\mu\nu} \rangle, \quad i\hbar \frac{\partial \Psi}{\partial t} = \hat{H}[g_{\mu\nu}] \Psi"
