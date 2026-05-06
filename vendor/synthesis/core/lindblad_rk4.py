"""
Lindblad RK4 Density Matrix Propagator

Implements the GKSL (Gorini-Kossakowski-Sudarshan-Lindblad) master equation
via 4th-order Runge-Kutta integration:

    d rho / dt = -i[H, rho] + sum_k ( L_k rho L_k^dag - 1/2 {L_k^dag L_k, rho} )

Heritage: DCQ dualdrive/core.py propagate_lindblad()

Verified against:
- Wikipedia "Lindbladian" article
- Qiskit Dynamics LindbladModel
- QuTiP mesolve
- DCQ dualdrive/core.py propagate_lindblad()

Constraints:
- Full density matrix: limited to <=15 qubits (2^15 x 2^15 = 16 GB)
- For 16-24 qubits, use synthesis/quantum/tensor_lindblad.py (TJM/MPS)
"""

import logging
import numpy as np
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


class LindbladPropagator:
    """RK4 propagator for the GKSL master equation.

    Args:
        hamiltonian: System Hamiltonian H (dim x dim complex matrix).
        lindblad_ops: List of Lindblad (jump) operators L_k.
            If empty, propagates pure unitary evolution: d rho/dt = -i[H, rho].
    """

    def __init__(self, hamiltonian: np.ndarray,
                 lindblad_ops: Optional[List[np.ndarray]] = None):
        self.H = np.asarray(hamiltonian, dtype=complex)
        self.dim = self.H.shape[0]
        self.L_ops = [np.asarray(L, dtype=complex) for L in (lindblad_ops or [])]

        # Pre-compute L_k^dag L_k for each operator
        self._LdagL = [L.conj().T @ L for L in self.L_ops]

    def _drho_dt(self, rho: np.ndarray, H: np.ndarray) -> np.ndarray:
        """Compute d rho / dt from the GKSL master equation.

        d rho/dt = -i[H, rho] + sum_k (L_k rho L_k^dag - 1/2 {L_k^dag L_k, rho})
        """
        # Unitary part: -i[H, rho]
        drho = -1j * (H @ rho - rho @ H)

        # Dissipative part: sum over Lindblad operators
        for L, LdagL in zip(self.L_ops, self._LdagL):
            drho += L @ rho @ L.conj().T - 0.5 * (LdagL @ rho + rho @ LdagL)

        return drho

    def propagate(self, rho0: np.ndarray, t_final: float, dt: float,
                  H_drive: Optional[np.ndarray] = None,
                  store_history: bool = False,
                  trace_threshold: float = 1e-6) -> Dict[str, Any]:
        """Propagate density matrix from t=0 to t=t_final using RK4.

        Args:
            rho0: Initial density matrix (dim x dim).
            t_final: Final time in seconds.
            dt: Time step in seconds.
            H_drive: Optional additional drive Hamiltonian (added to self.H).
            store_history: If True, store rho at each time step.
            trace_threshold: Renormalize trace if drift exceeds this.

        Returns:
            Dict with:
                rho_final: Final density matrix.
                final_trace: Tr(rho_final) -- should be ~1.0.
                positivity_ok: Whether all eigenvalues of rho_final >= -1e-10.
                n_steps: Number of RK4 steps taken.
                trace_renormalizations: Count of trace corrections applied.
                history: List of (t, rho) tuples if store_history=True.
        """
        rho = np.asarray(rho0, dtype=complex).copy()
        H_total = self.H + H_drive if H_drive is not None else self.H

        n_steps = int(np.ceil(t_final / dt))
        trace_renorms = 0
        history = []

        for step in range(n_steps):
            t = step * dt

            if store_history:
                history.append((t, rho.copy()))

            # RK4 integration
            k1 = dt * self._drho_dt(rho, H_total)
            k2 = dt * self._drho_dt(rho + 0.5 * k1, H_total)
            k3 = dt * self._drho_dt(rho + 0.5 * k2, H_total)
            k4 = dt * self._drho_dt(rho + k3, H_total)

            rho = rho + (k1 + 2 * k2 + 2 * k3 + k4) / 6.0

            # Trace monitoring and renormalization
            tr = np.real(np.trace(rho))
            if abs(tr - 1.0) > trace_threshold:
                rho /= tr
                trace_renorms += 1

        # Final diagnostics
        final_trace = float(np.real(np.trace(rho)))
        eigenvalues = np.real(np.linalg.eigvalsh(rho))
        positivity_ok = bool(np.all(eigenvalues >= -1e-10))

        if not positivity_ok:
            min_eig = float(np.min(eigenvalues))
            log.warning("Positivity violation: min eigenvalue = %.2e", min_eig)

        if store_history:
            history.append((t_final, rho.copy()))

        return {
            "rho_final": rho,
            "final_trace": final_trace,
            "positivity_ok": positivity_ok,
            "n_steps": n_steps,
            "trace_renormalizations": trace_renorms,
            "min_eigenvalue": float(np.min(eigenvalues)),
            "history": history if store_history else [],
        }
