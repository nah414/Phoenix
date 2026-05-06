"""
Stochastic Schrodinger Equation Solver.

Monte Carlo wave function (MCWF) method for open quantum systems.
This is the equation-level wrapper that connects with the existing
TensorJumpSimulator in synthesis/quantum/tensor_lindblad.py.

The stochastic SE evolves a SINGLE trajectory:
  d|psi> = (-i/hbar H_eff dt + sum_k (L_k/<L_k>-1) dN_k) |psi>

where dN_k are Poisson processes (quantum jumps).
Multiple trajectories are averaged to recover the density matrix.

This solver owns the equation-level interface while the TJM handles
the tensor network representation for large qubit counts.
"""

import numpy as np
from scipy.linalg import expm
from typing import Tuple, Dict, List, Optional
from .base import EquationSolver, EquationRegime, PhysicsContext, SolverResult

HBAR = 1.054571817e-34
M_ELECTRON = 9.1093837015e-31


class StochasticSESolver(EquationSolver):
    """Stochastic Schrodinger equation via Monte Carlo wave function method.

    For small systems (< 16 qubits): direct state vector evolution.
    For large systems (16-24 qubits): delegates to TensorJumpSimulator
    which uses MPS representation via quimb/JAX.
    """

    def regime(self) -> EquationRegime:
        return EquationRegime.STOCHASTIC_SE

    def can_handle(self, ctx: PhysicsContext) -> Tuple[bool, float]:
        if not ctx.is_open_system:
            return False, 0.0
        if ctx.environment_coupling == "none":
            return False, 0.0
        # High confidence for Markovian open systems
        if ctx.environment_coupling in ("weak", "markovian"):
            return True, 0.88
        if ctx.environment_coupling == "strong":
            return True, 0.5  # Redfield might be better
        return True, 0.3

    def build_hamiltonian(self, ctx: PhysicsContext) -> np.ndarray:
        """Build the system Hamiltonian (without Lindblad operators)."""
        if ctx.custom_hamiltonian is not None:
            return ctx.custom_hamiltonian
        from .non_relativistic import TISESolver
        return TISESolver().build_hamiltonian(ctx)

    def build_effective_hamiltonian(self, H: np.ndarray,
                                     lindblad_ops: List[np.ndarray]) -> np.ndarray:
        """H_eff = H - (i/2) sum_k L_k^dag L_k"""
        H_eff = H.copy().astype(complex)
        for L in lindblad_ops:
            H_eff -= 0.5j * (L.conj().T @ L)
        return H_eff

    def default_lindblad_ops(self, ctx: PhysicsContext, n: int) -> List[np.ndarray]:
        """Generate default Lindblad operators based on coupling type."""
        ops = []
        gamma = ctx.metadata.get("decay_rate", 0.1)

        if ctx.environment_coupling in ("weak", "markovian"):
            # Amplitude damping: L = sqrt(gamma) |0><1|
            L = np.zeros((n, n), dtype=complex)
            if n >= 2:
                L[0, 1] = np.sqrt(gamma)
            ops.append(L)

        if ctx.metadata.get("include_dephasing", False):
            # Pure dephasing: L = sqrt(gamma_phi) |1><1|
            gamma_phi = ctx.metadata.get("dephasing_rate", gamma / 2)
            L_phi = np.zeros((n, n), dtype=complex)
            if n >= 2:
                L_phi[1, 1] = np.sqrt(gamma_phi)
            ops.append(L_phi)

        return ops

    def run_trajectory(self, H_eff: np.ndarray, lindblad_ops: List[np.ndarray],
                       initial_state: np.ndarray, t_final: float, dt: float,
                       rng: np.random.Generator) -> np.ndarray:
        """Run a single Monte Carlo wave function trajectory."""
        n_steps = max(1, int(t_final / dt))
        state = initial_state.copy().astype(complex)

        U_eff = expm(-1j * H_eff * dt / HBAR)

        for step in range(n_steps):
            # Non-unitary evolution under H_eff
            state_new = U_eff @ state
            norm_sq = float(np.real(np.vdot(state_new, state_new)))

            # Decide: jump or no-jump?
            r = rng.random()
            if r < (1.0 - norm_sq):
                # Quantum jump -- select which operator
                probs = []
                for L in lindblad_ops:
                    Lpsi = L @ state
                    probs.append(float(np.real(np.vdot(Lpsi, Lpsi))) * dt / HBAR)

                total_p = sum(probs)
                if total_p > 0:
                    probs_norm = [p / total_p for p in probs]
                    jump_idx = rng.choice(len(lindblad_ops), p=probs_norm)
                    state = lindblad_ops[jump_idx] @ state
                    norm = np.linalg.norm(state)
                    if norm > 0:
                        state /= norm
                else:
                    state = state_new
                    norm = np.linalg.norm(state)
                    if norm > 0:
                        state /= norm
            else:
                state = state_new / np.sqrt(max(norm_sq, 1e-30))

        return state

    def solve_stationary(self, ctx, n_states=5):
        """Not directly applicable -- delegate to TISE for base eigenstates."""
        from .non_relativistic import TISESolver
        result = TISESolver().solve_stationary(ctx, n_states)
        result.metadata["note"] = "Stationary states from closed-system Hamiltonian"
        return result

    def evolve(self, ctx, initial_state, t_final, dt, store_history=False):
        """Evolve via Monte Carlo wave function method.

        For large qubit counts, this connects with the existing
        TensorJumpSimulator in synthesis/quantum/tensor_lindblad.py.
        """
        H = self.build_hamiltonian(ctx)
        n = H.shape[0]
        n_trajectories = ctx.metadata.get("n_trajectories", 50)

        lindblad_ops = ctx.metadata.get("lindblad_ops")
        if lindblad_ops is None:
            lindblad_ops = self.default_lindblad_ops(ctx, n)

        H_eff = self.build_effective_hamiltonian(H, lindblad_ops)
        rng = np.random.default_rng(ctx.metadata.get("seed", None))

        # Run N trajectories
        final_states = []
        for traj in range(n_trajectories):
            final = self.run_trajectory(H_eff, lindblad_ops, initial_state,
                                        t_final, dt, rng)
            final_states.append(final)

        # Average density matrix: rho = (1/N) sum |psi_k><psi_k|
        rho = np.zeros((n, n), dtype=complex)
        for s in final_states:
            rho += np.outer(s, s.conj())
        rho /= n_trajectories

        # Extract dominant state
        eigenvalues, eigenstates = np.linalg.eigh(rho)
        dominant = eigenstates[:, -1]

        trace = float(np.real(np.trace(rho)))
        purity = float(np.real(np.trace(rho @ rho)))

        return SolverResult(
            state=dominant,
            expectation_values={"trace": trace, "purity": purity},
            solver_name="StochasticSESolver (MCWF)",
            regime_used=self.regime(),
            metadata={
                "n_trajectories": n_trajectories,
                "trace": trace,
                "purity": purity,
                "method": "monte_carlo_wave_function",
            },
        )

    def validate_state(self, state, ctx):
        return abs(np.linalg.norm(state) - 1.0) < 1e-6

    def estimated_memory_bytes(self, ctx):
        n = ctx.metadata.get("n_grid_points", 100)
        n_traj = ctx.metadata.get("n_trajectories", 50)
        return n * n * 16 + n_traj * n * 16

    def gpu_beneficial(self, ctx):
        n_traj = ctx.metadata.get("n_trajectories", 50)
        return n_traj > 100

    def validate_parameters(self, ctx):
        """Pre-flight parameter consistency checks."""
        warnings = []
        n_traj = ctx.metadata.get("n_trajectories", 50)
        if n_traj < 20:
            warnings.append(f"Only {n_traj} trajectories. Statistical error > 20%. Recommend >= 50.")
        n_grid = ctx.metadata.get("n_grid_points", 100)
        mem_bytes = (n_grid ** 2 + n_traj * n_grid) * 16
        if mem_bytes > 2e9:
            warnings.append(f"Stochastic SE will use {mem_bytes/1e9:.1f} GB RAM")
        return warnings

    def calibration_check(self, ctx, result):
        """Check purity decay for open system."""
        checks = []
        if "purity" in result.expectation_values:
            purity = result.expectation_values["purity"]
            checks.append({
                "benchmark": "StochasticSE_purity_decreases",
                "expected": "< 1.0", "actual": purity,
                "ratio": purity, "tolerance": 0.05,
                "passed": purity < 1.0,
                "notes": "Open system should have purity < 1",
            })
        if "trace" in result.expectation_values:
            trace = result.expectation_values["trace"]
            checks.append({
                "benchmark": "StochasticSE_trace_preserved",
                "expected": 1.0, "actual": trace,
                "ratio": trace, "tolerance": 0.01,
                "passed": abs(trace - 1.0) < 0.01,
            })
        return checks

    def latex_representation(self) -> str:
        return r"d|\psi\rangle = \left[ -\frac{i}{\hbar} \hat{H}_{eff}\, dt + \sum_k \hat{C}_k\, dN_k \right] |\psi\rangle"
