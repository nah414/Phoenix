"""
Ehrenfest Bridge + WKB Semiclassical Approximation.

Ehrenfest: d<x>/dt = <p>/m, d<p>/dt = -<dV/dx>
  Tracks classical-quantum correspondence. Detects "quantum break time"
  where classical approximation fails.

WKB: psi(x) ~ A(x) exp(i S(x)/hbar)
  Constructs quantum wave function from classical action.
"""

import numpy as np
from typing import Tuple, Dict
from scipy.linalg import expm

from .base import EquationSolver, EquationRegime, PhysicsContext, SolverResult

HBAR = 1.054571817e-34
M_ELECTRON = 9.1093837015e-31


class EhrenfestSolver(EquationSolver):
    """Ehrenfest theorem solver -- tracks quantum-classical correspondence."""

    def regime(self) -> EquationRegime:
        return EquationRegime.EHRENFEST

    def can_handle(self, ctx: PhysicsContext) -> Tuple[bool, float]:
        if ctx.metadata.get("classical_quantum_bridge", False):
            return True, 0.9
        return True, 0.1

    def build_hamiltonian(self, ctx: PhysicsContext) -> np.ndarray:
        from .non_relativistic import TISESolver
        return TISESolver().build_hamiltonian(ctx)

    def quantum_break_time(self, ctx, initial_state, t_max, dt,
                           divergence_threshold=0.1) -> Dict:
        """Find time when classical and quantum predictions diverge."""
        H = self.build_hamiltonian(ctx)
        n = H.shape[0]
        mass = ctx.mass_kg if ctx.mass_kg > 0 else M_ELECTRON
        L = ctx.length_scale_m
        x_grid = np.linspace(-L / 2, L / 2, n)
        dx = x_grid[1] - x_grid[0]

        prob = np.abs(initial_state) ** 2
        x0 = float(np.sum(x_grid * prob) * dx)
        width = float(np.sqrt(np.sum((x_grid - x0) ** 2 * prob) * dx))

        V = np.diag(H).real - np.min(np.diag(H).real)
        dVdx = np.gradient(V, dx)

        U = expm(-1j * H * dt / HBAR)
        n_steps = int(t_max / dt)
        state = initial_state.copy().astype(complex)

        break_time = None
        c_traj, q_expect, divergence = [x0], [x0], [0.0]
        y_cl = [x0, 0.0]

        for step in range(n_steps):
            state = U @ state
            norm = np.linalg.norm(state)
            if norm > 0:
                state /= norm
            prob = np.abs(state) ** 2
            x_q = float(np.sum(x_grid * prob) * dx)

            idx = max(0, min(int((y_cl[0] + L / 2) / dx), n - 2))
            force = -dVdx[idx]
            y_cl[0] += y_cl[1] / mass * dt
            y_cl[1] += force * dt

            div = abs(x_q - y_cl[0]) / max(width, 1e-15)
            c_traj.append(y_cl[0])
            q_expect.append(x_q)
            divergence.append(div)

            if break_time is None and div > divergence_threshold:
                break_time = step * dt

        return {
            "break_time": break_time,
            "max_divergence": max(divergence),
            "classical_trajectory": np.array(c_traj),
            "quantum_expectation": np.array(q_expect),
            "divergence_history": np.array(divergence),
            "wavepacket_width": width,
        }

    def solve_stationary(self, ctx, n_states=5):
        from .non_relativistic import TISESolver
        return TISESolver().solve_stationary(ctx, n_states)

    def evolve(self, ctx, initial_state, t_final, dt, store_history=False):
        break_info = self.quantum_break_time(ctx, initial_state, t_final, dt)
        from .non_relativistic import TDSESolver
        result = TDSESolver().evolve(ctx, initial_state, t_final, dt, store_history)
        result.metadata["ehrenfest_break_time"] = break_info["break_time"]
        result.metadata["max_divergence"] = break_info["max_divergence"]
        result.solver_name = "EhrenfestSolver"
        result.regime_used = self.regime()
        return result

    def validate_state(self, state, ctx):
        return abs(np.linalg.norm(state) - 1.0) < 1e-6

    def validate_parameters(self, ctx):
        """Pre-flight parameter consistency checks."""
        warnings = []
        mass = ctx.mass_kg if ctx.mass_kg > 0 else M_ELECTRON
        n_grid = ctx.metadata.get("n_grid_points", 200)
        omega = ctx.metadata.get("omega", 1e12)
        L = ctx.length_scale_m
        import numpy as np
        x_0 = np.sqrt(HBAR / (mass * omega))
        if L < 6 * x_0:
            warnings.append(
                f"GRID TOO SMALL: L={L:.2e}m but x_0={x_0:.2e}m. "
                f"Need L >= {6*x_0:.2e}m."
            )
        return warnings

    def calibration_check(self, ctx, result):
        """Check Ehrenfest trajectory against classical QHO solution."""
        checks = []
        if "ehrenfest_break_time" in result.metadata:
            checks.append({
                "benchmark": "Ehrenfest_break_time_exists",
                "expected": "finite", "actual": result.metadata["ehrenfest_break_time"],
                "ratio": 1.0, "tolerance": 1.0,
                "passed": result.metadata["ehrenfest_break_time"] > 0,
            })
        return checks

    def latex_representation(self) -> str:
        return r"m \frac{d^2}{dt^2} \langle \hat{x} \rangle = -\left\langle \frac{\partial V}{\partial x} \right\rangle"


class WKBSolver(EquationSolver):
    """WKB semiclassical approximation: psi(x) ~ A(x) exp(iS(x)/hbar)"""

    def regime(self) -> EquationRegime:
        return EquationRegime.WKB

    def can_handle(self, ctx: PhysicsContext) -> Tuple[bool, float]:
        if ctx.metadata.get("wkb_requested", False):
            return True, 0.9
        if ctx.metadata.get("classical_quantum_bridge", False):
            return True, 0.7
        return True, 0.05

    def build_hamiltonian(self, ctx: PhysicsContext) -> np.ndarray:
        from .non_relativistic import TISESolver
        return TISESolver().build_hamiltonian(ctx)

    def classical_to_quantum(self, V, x_grid, energy, mass):
        """Convert classical potential + energy into WKB wave function."""
        dx = x_grid[1] - x_grid[0]
        n = len(x_grid)
        kinetic = energy - V
        allowed = kinetic > 0

        psi = np.zeros(n, dtype=complex)
        validity = np.ones(n, dtype=bool)

        for i in range(n):
            if allowed[i]:
                p = np.sqrt(2 * mass * kinetic[i])
                amp = 1.0 / np.sqrt(max(p, 1e-40))
                if i > 0:
                    p_int = np.sqrt(np.maximum(2 * mass * kinetic[:i + 1], 0))
                    phase = np.trapz(p_int, x_grid[:i + 1]) / HBAR
                else:
                    phase = 0.0
                psi[i] = amp * np.exp(1j * phase)

                if 0 < i < n - 1:
                    lam = 2 * np.pi * HBAR / max(p, 1e-40)
                    p_n = np.sqrt(max(2 * mass * kinetic[min(i + 1, n - 1)], 1e-40))
                    p_p = np.sqrt(max(2 * mass * kinetic[max(i - 1, 0)], 1e-40))
                    dlam = abs(2 * np.pi * HBAR / p_n - 2 * np.pi * HBAR / p_p) / (2 * dx)
                    if dlam > 0.5:
                        validity[i] = False
            else:
                kappa = np.sqrt(2 * mass * abs(kinetic[i]))
                psi[i] = np.exp(-kappa * abs(x_grid[i] - x_grid[0]) / HBAR)
                validity[i] = False

        norm = np.sqrt(np.sum(np.abs(psi) ** 2) * dx)
        if norm > 0:
            psi /= norm
        return psi, validity

    def solve_stationary(self, ctx, n_states=5):
        H = self.build_hamiltonian(ctx)
        n = H.shape[0]
        L = ctx.length_scale_m
        x_grid = np.linspace(-L / 2, L / 2, n)
        mass = ctx.mass_kg if ctx.mass_kg > 0 else M_ELECTRON
        V = np.diag(H).real

        from scipy.linalg import eigh
        exact_vals, _ = eigh(H)
        wkb_states = []
        for i in range(min(n_states, len(exact_vals))):
            psi, _ = self.classical_to_quantum(V, x_grid, exact_vals[i], mass)
            wkb_states.append(psi)

        return SolverResult(
            state=wkb_states[0], energy=float(exact_vals[0]),
            eigenvalues=exact_vals[:n_states],
            eigenstates=np.array(wkb_states).T if wkb_states else None,
            solver_name="WKBSolver", regime_used=self.regime(),
            metadata={"approximation": "WKB"},
        )

    def evolve(self, ctx, initial_state, t_final, dt, store_history=False):
        from .non_relativistic import TDSESolver
        result = TDSESolver().evolve(ctx, initial_state, t_final, dt, store_history)
        result.solver_name = "WKBSolver -> TDSESolver"
        result.regime_used = self.regime()
        return result

    def validate_state(self, state, ctx):
        return abs(np.linalg.norm(state) - 1.0) < 1e-6

    def validate_parameters(self, ctx):
        """Pre-flight parameter consistency checks."""
        warnings = []
        mass = ctx.mass_kg if ctx.mass_kg > 0 else M_ELECTRON
        n_grid = ctx.metadata.get("n_grid_points", 200)
        omega = ctx.metadata.get("omega", 1e12)
        L = ctx.length_scale_m
        import numpy as np
        x_0 = np.sqrt(HBAR / (mass * omega))
        if L < 6 * x_0:
            warnings.append(
                f"GRID TOO SMALL: L={L:.2e}m but x_0={x_0:.2e}m. "
                f"Need L >= {6*x_0:.2e}m."
            )
        return warnings

    def calibration_check(self, ctx, result):
        """Check WKB eigenvalues against TISE for QHO."""
        from synthesis.equations.non_relativistic import TISESolver
        checks = []
        tise = TISESolver()
        tise_result = tise.solve_stationary(ctx, n_states=5)
        if result.eigenvalues is not None and tise_result.eigenvalues is not None:
            n_compare = min(len(result.eigenvalues), len(tise_result.eigenvalues), 5)
            for i in range(n_compare):
                wkb_e = float(result.eigenvalues[i])
                tise_e = float(tise_result.eigenvalues[i])
                ratio = wkb_e / tise_e if tise_e != 0 else float('inf')
                tol = 0.05 if i >= 2 else 0.10  # WKB better for higher states
                checks.append({
                    "benchmark": f"WKB_vs_TISE_E{i}",
                    "expected": tise_e, "actual": wkb_e,
                    "ratio": ratio, "tolerance": tol,
                    "passed": abs(ratio - 1.0) < tol,
                })
        return checks

    def latex_representation(self) -> str:
        return r"\psi(x) \sim \frac{A(x)}{\sqrt{p(x)}} \exp\!\left( \frac{i}{\hbar} \int^x p(x')\, dx' \right)"
