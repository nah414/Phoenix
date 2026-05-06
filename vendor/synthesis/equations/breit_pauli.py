"""
Breit-Pauli Fine Structure Corrections.

H_BP = H_NR + H_MV + H_Darwin + H_SO

H_MV = -p^4/(8m^3c^2)     (mass-velocity correction)
H_Darwin = (pi*hbar^2/2m^2c^2) delta^3(r)  (Darwin term)
H_SO = (1/2m^2c^2r) dV/dr L.S  (spin-orbit)

Leading-order relativistic corrections from Foldy-Wouthuysen transformation.
"""

import numpy as np
from scipy.linalg import eigh, expm
from typing import Tuple
from .base import EquationSolver, EquationRegime, PhysicsContext, SolverResult

HBAR = 1.054571817e-34
M_ELECTRON = 9.1093837015e-31
C_LIGHT = 299792458.0


class BreitPauliSolver(EquationSolver):
    """Breit-Pauli fine structure corrections as perturbation."""

    def regime(self) -> EquationRegime:
        return EquationRegime.BREIT_PAULI

    def can_handle(self, ctx: PhysicsContext) -> Tuple[bool, float]:
        if not ctx.include_fine_structure:
            return False, 0.0
        if ctx.velocity_over_c >= 0.1:
            return False, 0.0
        if ctx.velocity_over_c >= 0.01:
            return True, 0.88
        return True, 0.5

    def build_hamiltonian(self, ctx: PhysicsContext) -> np.ndarray:
        from .non_relativistic import TISESolver
        H_NR = TISESolver().build_hamiltonian(ctx)
        n = H_NR.shape[0]
        mass = ctx.mass_kg if ctx.mass_kg > 0 else M_ELECTRON
        dx = ctx.length_scale_m / n

        diag_T = np.full(n, 2.0) * HBAR ** 2 / (2 * mass * dx ** 2)
        off_T = np.full(n - 1, -1.0) * HBAR ** 2 / (2 * mass * dx ** 2)
        T = np.diag(diag_T) + np.diag(off_T, 1) + np.diag(off_T, -1)
        H_MV = -T @ T / (2 * mass * C_LIGHT ** 2)

        return H_NR + H_MV

    def solve_stationary(self, ctx, n_states=5):
        H = self.build_hamiltonian(ctx)
        vals, vecs = eigh(H)
        return SolverResult(
            state=vecs[:, 0], energy=float(vals[0]),
            eigenvalues=vals[:n_states], eigenstates=vecs[:, :n_states],
            solver_name="BreitPauliSolver", regime_used=self.regime(),
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
            solver_name="BreitPauliSolver", regime_used=self.regime(),
        )

    def validate_state(self, state, ctx):
        return abs(np.linalg.norm(state) - 1.0) < 1e-6

    def latex_representation(self) -> str:
        return r"\hat{H}_{BP} = \hat{H}_0 - \frac{\hat{p}^4}{8m^3c^2} + \frac{\hbar^2}{8m^2c^2} \nabla^2 V + \frac{1}{2m^2c^2} \frac{1}{r} \frac{dV}{dr} \hat{\mathbf{L}} \cdot \hat{\mathbf{S}}"

    def validate_parameters(self, ctx):
        """Pre-flight parameter consistency checks."""
        warnings = []
        if ctx.velocity_over_c > 0.1:
            warnings.append(
                f"v/c={ctx.velocity_over_c:.2f} > 0.1: Breit-Pauli perturbative corrections "
                f"break down. Use full DiracSolver instead."
            )
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
        """Verify corrections are perturbative (< 1% of TISE eigenvalues)."""
        from synthesis.equations.non_relativistic import TISESolver
        checks = []
        tise = TISESolver()
        tise_result = tise.solve_stationary(ctx, n_states=5)
        if result.eigenvalues is not None and tise_result.eigenvalues is not None:
            n_compare = min(len(result.eigenvalues), len(tise_result.eigenvalues), 3)
            for i in range(n_compare):
                bp_e = float(result.eigenvalues[i])
                tise_e = float(tise_result.eigenvalues[i])
                if tise_e != 0:
                    correction_pct = abs(bp_e - tise_e) / abs(tise_e)
                    checks.append({
                        "benchmark": f"BP_perturbative_E{i}",
                        "expected": tise_e, "actual": bp_e,
                        "ratio": bp_e / tise_e, "tolerance": 0.01,
                        "passed": correction_pct < 0.01,
                        "notes": f"Correction = {correction_pct*100:.3f}%",
                    })
        return checks
