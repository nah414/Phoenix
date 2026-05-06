"""
Non-relativistic Schrodinger equation solvers.

TISE: H psi = E psi (eigenvalue problem)
TDSE: ihbar d_psi/dt = H psi (time evolution, split-step Fourier)
"""

import numpy as np
from scipy.linalg import eigh, expm
from scipy.sparse.linalg import eigsh

from .base import EquationSolver, EquationRegime, PhysicsContext, ParticleType, SolverResult

HBAR = 1.054571817e-34
M_ELECTRON = 9.1093837015e-31
EV_TO_JOULE = 1.602176634e-19


class TISESolver(EquationSolver):
    """Time-Independent Schrodinger Equation: H psi_n = E_n psi_n"""

    def regime(self) -> EquationRegime:
        return EquationRegime.NON_RELATIVISTIC_TI

    def can_handle(self, ctx: PhysicsContext) -> tuple:
        if ctx.velocity_over_c >= 0.01:
            return False, 0.0
        if ctx.is_open_system:
            return False, 0.0
        if ctx.include_gravity and ctx.gravitational_regime in ("semiclassical", "quantum"):
            return False, 0.0
        if ctx.particle_type in (ParticleType.FIELD, ParticleType.GEOMETRY):
            return False, 0.0
        return True, 0.85

    def build_hamiltonian(self, ctx: PhysicsContext) -> np.ndarray:
        mass = ctx.mass_kg if ctx.mass_kg > 0 else M_ELECTRON
        if ctx.custom_hamiltonian is not None:
            return ctx.custom_hamiltonian

        n_grid = ctx.metadata.get("n_grid_points", 200)
        omega = ctx.metadata.get("omega", 1e15)  # v6.0: default omega=1e15 (Option C)

        # AUTO-SIZE GRID (Option C): ensure grid spans the wave function
        x_0 = np.sqrt(HBAR / (mass * omega))
        L_min = 8 * x_0  # 8x characteristic length = safe margin
        L = max(ctx.length_scale_m, L_min)

        # Store actual L used so callers know if auto-sizing occurred
        ctx.metadata["_actual_grid_extent"] = L
        if L > ctx.length_scale_m:
            import logging
            logging.getLogger(__name__).info(
                "Auto-sized grid from %.2e to %.2e m (x_0=%.2e m)",
                ctx.length_scale_m, L, x_0,
            )

        dx = L / n_grid

        diag = np.full(n_grid, 2.0) * (HBAR ** 2) / (2 * mass * dx ** 2)
        off_diag = np.full(n_grid - 1, -1.0) * (HBAR ** 2) / (2 * mass * dx ** 2)
        H = np.diag(diag) + np.diag(off_diag, 1) + np.diag(off_diag, -1)

        x = np.linspace(-L / 2, L / 2, n_grid)
        V = 0.5 * mass * omega ** 2 * x ** 2
        H += np.diag(V)
        return H

    def solve_stationary(self, ctx: PhysicsContext, n_states: int = 5) -> SolverResult:
        H = self.build_hamiltonian(ctx)
        if H.shape[0] <= 500:
            eigenvalues, eigenstates = eigh(H)
        else:
            eigenvalues, eigenstates = eigsh(H, k=min(n_states, H.shape[0] - 2), which="SM")
            idx = np.argsort(eigenvalues)
            eigenvalues, eigenstates = eigenvalues[idx], eigenstates[:, idx]

        return SolverResult(
            state=eigenstates[:, 0], energy=float(eigenvalues[0]),
            eigenvalues=eigenvalues[:n_states], eigenstates=eigenstates[:, :n_states],
            solver_name="TISESolver", regime_used=self.regime(),
        )

    def evolve(self, ctx, initial_state, t_final, dt, store_history=False):
        raise NotImplementedError("TISE is stationary -- use TDSESolver for time evolution.")

    def validate_state(self, state, ctx):
        return abs(np.linalg.norm(state) - 1.0) < 1e-6

    def latex_representation(self) -> str:
        return r"\hat{H} \Psi_n = E_n \Psi_n"

    def validate_parameters(self, ctx):
        """Pre-flight parameter consistency checks. Returns list of warning strings."""
        warnings = []
        mass = ctx.mass_kg if ctx.mass_kg > 0 else M_ELECTRON
        n_grid = ctx.metadata.get("n_grid_points", 200)
        omega = ctx.metadata.get("omega", 1e12)
        L = ctx.length_scale_m
        dx = L / n_grid

        import numpy as np
        x_0 = np.sqrt(HBAR / (mass * omega))
        if L < 6 * x_0:
            warnings.append(
                f"GRID TOO SMALL: L={L:.2e}m but x_0={x_0:.2e}m. "
                f"Need L >= {6*x_0:.2e}m. Eigenvalues will be boundary-dominated."
            )
        if dx > x_0 / 5:
            warnings.append(f"GRID TOO COARSE: dx={dx:.2e}m, need < {x_0/5:.2e}m")

        mem_bytes = n_grid ** 2 * 16
        if mem_bytes > 2e9:
            warnings.append(f"Hamiltonian will use {mem_bytes/1e9:.1f} GB RAM")
        return warnings

    def calibration_check(self, ctx, result):
        """Post-solve check against known analytical solutions."""
        omega = ctx.metadata.get("omega", 1e12)
        mass = ctx.mass_kg if ctx.mass_kg > 0 else M_ELECTRON
        expected_E0 = HBAR * omega * 0.5
        checks = []
        if result.eigenvalues is not None and len(result.eigenvalues) >= 1:
            actual_E0 = float(result.eigenvalues[0])
            ratio_E0 = actual_E0 / expected_E0 if expected_E0 > 0 else float('inf')
            checks.append({
                "benchmark": "QHO_ground_state",
                "expected": expected_E0, "actual": actual_E0,
                "ratio": ratio_E0, "tolerance": 0.01,
                "passed": abs(ratio_E0 - 1.0) < 0.01,
            })
            if len(result.eigenvalues) >= 3:
                ratio_E1_E0 = float(result.eigenvalues[1]) / actual_E0
                ratio_E2_E0 = float(result.eigenvalues[2]) / actual_E0
                checks.append({
                    "benchmark": "QHO_level_ratio_E1_E0",
                    "expected": 3.0, "actual": ratio_E1_E0,
                    "ratio": ratio_E1_E0 / 3.0, "tolerance": 0.01,
                    "passed": abs(ratio_E1_E0 - 3.0) < 0.03,
                })
                checks.append({
                    "benchmark": "QHO_level_ratio_E2_E0",
                    "expected": 5.0, "actual": ratio_E2_E0,
                    "ratio": ratio_E2_E0 / 5.0, "tolerance": 0.01,
                    "passed": abs(ratio_E2_E0 - 5.0) < 0.05,
                })
        return checks


class TDSESolver(EquationSolver):
    """Time-Dependent Schrodinger Equation: ihbar d_psi/dt = H psi"""

    def regime(self) -> EquationRegime:
        return EquationRegime.NON_RELATIVISTIC_TD

    def can_handle(self, ctx: PhysicsContext) -> tuple:
        if ctx.velocity_over_c >= 0.01:
            return True, 0.3
        if ctx.is_open_system:
            return False, 0.0
        if ctx.include_gravity and ctx.gravitational_regime == "quantum":
            return False, 0.0
        if ctx.has_spin and ctx.has_magnetic_field:
            return True, 0.4
        return True, 0.9

    def build_hamiltonian(self, ctx: PhysicsContext) -> np.ndarray:
        if ctx.custom_hamiltonian is not None:
            return ctx.custom_hamiltonian
        return TISESolver().build_hamiltonian(ctx)

    def solve_stationary(self, ctx, n_states=5):
        return TISESolver().solve_stationary(ctx, n_states)

    def evolve(self, ctx, initial_state, t_final, dt, store_history=False):
        H = self.build_hamiltonian(ctx)
        n_steps = max(1, int(t_final / dt))

        # Precompute propagator for time-independent H
        U = expm(-1j * H * dt / HBAR)

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
            solver_name="TDSESolver", regime_used=self.regime(),
            metadata={"n_steps": n_steps, "dt": dt},
        )

    def validate_state(self, state, ctx):
        return abs(np.linalg.norm(state) - 1.0) < 1e-6

    def estimated_memory_bytes(self, ctx):
        n = ctx.metadata.get("n_grid_points", 200)
        return n * n * 16

    def gpu_beneficial(self, ctx):
        return ctx.metadata.get("n_grid_points", 200) > 1000

    def latex_representation(self) -> str:
        return r"i\hbar \frac{\partial}{\partial t} \Psi(\mathbf{r},t) = \left[ -\frac{\hbar^2}{2m} \nabla^2 + V(\mathbf{r},t) \right] \Psi(\mathbf{r},t)"

    def validate_parameters(self, ctx):
        """Pre-flight parameter consistency checks."""
        warnings = TISESolver().validate_parameters(ctx)
        omega = ctx.metadata.get("omega", 1e12)
        if ctx.time_scale_s > 0:
            min_dt = 1e-17  # safe for omega=1e15
            if ctx.time_scale_s / 1000 > HBAR / (HBAR * omega * 5):
                warnings.append(
                    f"Time step may be too large for omega={omega:.1e}. "
                    f"Recommend dt << {HBAR / (HBAR * omega * 5):.2e} s"
                )
        return warnings

    def calibration_check(self, ctx, result):
        """TDSE stationary must match TISE eigenvalues."""
        tise = TISESolver()
        tise_result = tise.solve_stationary(ctx, n_states=5)
        checks = []
        if result.eigenvalues is not None and tise_result.eigenvalues is not None:
            import numpy as np
            n_compare = min(len(result.eigenvalues), len(tise_result.eigenvalues), 5)
            for i in range(n_compare):
                tdse_e = float(result.eigenvalues[i])
                tise_e = float(tise_result.eigenvalues[i])
                ratio = tdse_e / tise_e if tise_e != 0 else float('inf')
                checks.append({
                    "benchmark": f"TDSE_vs_TISE_E{i}",
                    "expected": tise_e, "actual": tdse_e,
                    "ratio": ratio, "tolerance": 0.001,
                    "passed": abs(ratio - 1.0) < 0.001,
                })
        return checks
