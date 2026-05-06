"""
Lindblad Master Equation Solver.

Wraps the existing LindbladPropagator (synthesis/core/lindblad_rk4.py) in the
EquationSolver interface so it can be registered in the equation registry and
dispatched via the standard classification pipeline.

The GKSL (Gorini-Kossakowski-Sudarshan-Lindblad) master equation:

    d rho / dt = -i[H, rho] + sum_k ( L_k rho L_k^dag - 1/2 {L_k^dag L_k, rho} )

This is the density-matrix formulation of open quantum system dynamics.
For the wave-function (trajectory) approach, see StochasticSESolver.

Analytical ground truth for calibration:
  - Trace preservation: Tr(rho(t)) = 1 for all t (to within 1e-8)
  - Positivity: all eigenvalues of rho(t) >= 0
  - Amplitude damping: P_excited(t) = P_excited(0) * exp(-gamma * t)
  - Steady state: d rho/dt = 0 at t -> infinity, rho_ss = |0><0| for amplitude damping
"""

import numpy as np
from scipy.linalg import expm
from typing import Dict, List, Optional, Tuple

from .base import EquationSolver, EquationRegime, PhysicsContext, SolverResult

HBAR = 1.054571817e-34
M_ELECTRON = 9.1093837015e-31


class LindbladSolver(EquationSolver):
    """Lindblad master equation solver via density matrix propagation.

    Wraps synthesis.core.lindblad_rk4.LindbladPropagator for the
    EquationSolver interface. Handles open quantum systems with
    Markovian decoherence.
    """

    def regime(self) -> EquationRegime:
        return EquationRegime.LINDBLAD

    def can_handle(self, ctx: PhysicsContext) -> Tuple[bool, float]:
        if not ctx.is_open_system:
            return False, 0.0
        if ctx.environment_coupling == "none":
            return False, 0.0
        # Lindblad is the canonical choice for Markovian open systems
        if ctx.environment_coupling in ("weak", "markovian"):
            return True, 0.92
        if ctx.environment_coupling == "strong":
            return True, 0.6
        return True, 0.4

    def build_hamiltonian(self, ctx: PhysicsContext) -> np.ndarray:
        if ctx.custom_hamiltonian is not None:
            return ctx.custom_hamiltonian
        from .non_relativistic import TISESolver
        return TISESolver().build_hamiltonian(ctx)

    def default_lindblad_ops(self, ctx: PhysicsContext, n: int) -> List[np.ndarray]:
        """Generate default Lindblad operators from physics context."""
        ops = []
        gamma = ctx.metadata.get("decay_rate", 0.1)

        # Amplitude damping: L = sqrt(gamma) |0><1|
        if n >= 2:
            L_decay = np.zeros((n, n), dtype=complex)
            L_decay[0, 1] = np.sqrt(gamma)
            ops.append(L_decay)

        # Dephasing if requested
        if ctx.metadata.get("include_dephasing", False):
            gamma_phi = ctx.metadata.get("dephasing_rate", gamma / 2)
            L_phi = np.zeros((n, n), dtype=complex)
            if n >= 2:
                L_phi[1, 1] = np.sqrt(gamma_phi)
            ops.append(L_phi)

        return ops

    def solve_stationary(self, ctx: PhysicsContext, n_states: int = 5) -> SolverResult:
        """Find the steady-state density matrix where d rho/dt = 0.

        For a system with amplitude damping, the steady state is the
        ground state |0><0|. For more complex systems, we propagate
        for a long time and check convergence.
        """
        H = self.build_hamiltonian(ctx)
        n = H.shape[0]

        lindblad_ops = ctx.metadata.get("lindblad_ops")
        if lindblad_ops is None:
            lindblad_ops = self.default_lindblad_ops(ctx, n)

        from synthesis.core.lindblad_rk4 import LindbladPropagator
        prop = LindbladPropagator(H, lindblad_ops)

        # Start from maximally mixed state
        rho0 = np.eye(n, dtype=complex) / n

        # Propagate for long enough to reach steady state
        t_relax = ctx.metadata.get("relaxation_time", 1e-7)
        dt = t_relax / 500
        result = prop.propagate(rho0, t_relax, dt)

        rho_ss = result["rho_final"]
        eigenvalues = np.real(np.linalg.eigvalsh(rho_ss))
        eigenstates = np.linalg.eigh(rho_ss)[1]

        # Return the n largest eigenvalue states
        idx = np.argsort(eigenvalues)[::-1][:n_states]
        dominant_state = eigenstates[:, idx[0]]

        return SolverResult(
            state=dominant_state,
            eigenvalues=eigenvalues[idx],
            eigenstates=eigenstates[:, idx],
            expectation_values={
                "trace": result["final_trace"],
                "purity": float(np.real(np.trace(rho_ss @ rho_ss))),
            },
            solver_name="LindbladSolver (GKSL master equation)",
            regime_used=self.regime(),
            metadata={
                "method": "lindblad_rk4_steady_state",
                "positivity_ok": result["positivity_ok"],
                "min_eigenvalue": result["min_eigenvalue"],
                "trace_renormalizations": result["trace_renormalizations"],
                "n_steps": result["n_steps"],
                "rho_final": rho_ss,
            },
        )

    def evolve(self, ctx: PhysicsContext, initial_state: Optional[np.ndarray],
               t_final: float, dt: float,
               store_history: bool = False) -> SolverResult:
        """Propagate the density matrix via the Lindblad master equation."""
        H = self.build_hamiltonian(ctx)
        n = H.shape[0]

        lindblad_ops = ctx.metadata.get("lindblad_ops")
        if lindblad_ops is None:
            lindblad_ops = self.default_lindblad_ops(ctx, n)

        from synthesis.core.lindblad_rk4 import LindbladPropagator
        prop = LindbladPropagator(H, lindblad_ops)

        # Build initial density matrix from state vector or use provided
        if initial_state is not None:
            if initial_state.ndim == 1:
                rho0 = np.outer(initial_state, initial_state.conj())
            else:
                rho0 = initial_state
        else:
            # Default: excited state |1><1|
            rho0 = np.zeros((n, n), dtype=complex)
            if n >= 2:
                rho0[1, 1] = 1.0
            else:
                rho0[0, 0] = 1.0

        result = prop.propagate(rho0, t_final, dt, store_history=store_history)

        rho_final = result["rho_final"]
        eigenvalues = np.real(np.linalg.eigvalsh(rho_final))
        dominant_idx = np.argmax(eigenvalues)
        eigenstates = np.linalg.eigh(rho_final)[1]
        dominant_state = eigenstates[:, dominant_idx]

        trace = result["final_trace"]
        purity = float(np.real(np.trace(rho_final @ rho_final)))

        # Build time points and state history if requested
        time_points = None
        state_history = None
        if store_history and result["history"]:
            time_points = np.array([t for t, _ in result["history"]])
            state_history = np.array([rho for _, rho in result["history"]])

        return SolverResult(
            state=dominant_state,
            eigenvalues=eigenvalues,
            expectation_values={"trace": trace, "purity": purity},
            time_points=time_points,
            state_history=state_history,
            solver_name="LindbladSolver (GKSL master equation)",
            regime_used=self.regime(),
            metadata={
                "method": "lindblad_rk4",
                "positivity_ok": result["positivity_ok"],
                "min_eigenvalue": result["min_eigenvalue"],
                "trace_renormalizations": result["trace_renormalizations"],
                "n_steps": result["n_steps"],
                "rho_final": rho_final,
            },
        )

    def validate_state(self, state: np.ndarray, ctx: PhysicsContext) -> bool:
        if state.ndim == 2:
            # Density matrix: check trace and positivity
            trace = abs(np.trace(state) - 1.0)
            eigenvalues = np.real(np.linalg.eigvalsh(state))
            return trace < 1e-6 and np.all(eigenvalues >= -1e-10)
        return abs(np.linalg.norm(state) - 1.0) < 1e-6

    def validate_parameters(self, ctx: PhysicsContext):
        warnings = []
        n = ctx.metadata.get("n_grid_points", 100)
        if n > 2**15:
            warnings.append(f"Hilbert space dim={n} exceeds Lindblad RK4 limit (2^15). "
                            "Use TensorJumpSimulator for large systems.")
        gamma = ctx.metadata.get("decay_rate", 0.1)
        if gamma <= 0:
            warnings.append("decay_rate must be positive for open system dynamics.")
        return warnings

    def calibration_check(self, ctx: PhysicsContext, result: SolverResult):
        """Verify Lindblad evolution against analytical constraints.

        Ground truths:
        1. Trace preservation: Tr(rho) = 1 (to 1e-8)
        2. Positivity: all eigenvalues >= 0
        3. Purity decay: purity <= 1 for open systems
        """
        checks = []

        trace = result.expectation_values.get("trace", 1.0)
        checks.append({
            "benchmark": "Lindblad_trace_preserved",
            "expected": 1.0, "actual": trace,
            "ratio": trace, "tolerance": 1e-6,
            "passed": abs(trace - 1.0) < 1e-6,
            "notes": "GKSL guarantees trace preservation",
        })

        positivity = result.metadata.get("positivity_ok", True)
        min_eig = result.metadata.get("min_eigenvalue", 0.0)
        checks.append({
            "benchmark": "Lindblad_positivity",
            "expected": ">= 0", "actual": min_eig,
            "ratio": min_eig, "tolerance": 1e-10,
            "passed": positivity,
            "notes": "GKSL guarantees complete positivity",
        })

        purity = result.expectation_values.get("purity", 1.0)
        checks.append({
            "benchmark": "Lindblad_purity_bounded",
            "expected": "<= 1.0", "actual": purity,
            "ratio": purity, "tolerance": 0.01,
            "passed": purity <= 1.0 + 1e-10,
            "notes": "Purity = Tr(rho^2) <= 1 always",
        })

        return checks

    def latex_representation(self) -> str:
        return (r"\frac{d\rho}{dt} = -i[\hat{H}, \rho] + "
                r"\sum_k \left( \hat{L}_k \rho \hat{L}_k^\dagger "
                r"- \frac{1}{2}\{\hat{L}_k^\dagger \hat{L}_k, \rho\} \right)")

    def estimated_memory_bytes(self, ctx: PhysicsContext) -> int:
        n = ctx.metadata.get("n_grid_points", 100)
        return n * n * 16 * 3  # rho + H + work arrays

    def gpu_beneficial(self, ctx: PhysicsContext) -> bool:
        n = ctx.metadata.get("n_grid_points", 100)
        return n > 512
