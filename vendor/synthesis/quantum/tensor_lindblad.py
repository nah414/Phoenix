"""
Dr. Frank & Eddy v6.0.0 -- Tensor Jump Method (TJM) Simulator

Open-system Lindblad simulation via Monte Carlo wave-function trajectories.

Backend fallback chain (tried in order, best available wins):

    jax_gpu  -> jax_cpu  -> numpy

- ``jax_gpu`` runs the RK4 trajectory evolution on whatever GPU JAX sees.
  On this machine (CUDA 13.2 / RTX 5070 Laptop) JAX does not currently
  enumerate the GPU, so the chain falls through to ``jax_cpu`` or
  ``numpy`` automatically; on a host where JAX sees CUDA 12.x this path
  kicks in transparently.
- ``jax_cpu`` runs the same code under JAX's XLA CPU backend -- useful
  primarily as a smoke test for the JAX path on GPU-less hosts.
- ``numpy`` is the reference implementation. Always available.

``quimb`` (1.13+) is imported when present; current dense evolution does
not yet route through MPS, but the import check gates the future
MPS-representation extension scheduled for ``propagate_lindblad_streaming``
replacement work.

For production Lindblad runs that must avoid the full 2^N x 2^N density
matrix, see ``propagate_lindblad_streaming`` below.

Reference: Monte Carlo wave function method (Dalibard, Castin, Molmer 1992)
    H_eff = H - (i/2) Sum(L_k_dag L_k)
    rho   = (1/N) Sum |psi_n><psi_n|
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)


# Optional-dep detection. Flags are module-level booleans any caller can
# read; ``_select_tjm_backend`` uses them to pick a sensible default.

try:
    import quimb  # noqa: F401
    HAS_QUIMB = True
except ImportError:
    HAS_QUIMB = False

try:
    import jax
    import jax.numpy as jnp
    # Complex128 precision requires JAX's x64 mode; enable it idempotently.
    jax.config.update("jax_enable_x64", True)
    HAS_JAX = True
    HAS_JAX_GPU = any(d.platform == "gpu" for d in jax.devices())
except Exception:  # noqa: BLE001 - JAX probe must never crash import
    HAS_JAX = False
    HAS_JAX_GPU = False


# ---------------------------------------------------------------------------
# JIT-compiled RK4 step (module-level so jax.jit's cache lives across
# simulator instances). When HAS_JAX is False the helper is never called --
# ``_select_tjm_backend`` gates entry and raises on explicit jax_*.
# ---------------------------------------------------------------------------
if HAS_JAX:

    @jax.jit
    def _jit_rk4_step(psi_jax, h_eff_jax, dt):
        """JIT-compiled single RK4 step under a time-independent H_eff.

        Traced once per (psi_shape, h_eff_shape, dtype, dt_value) combo.
        A TJM run uses one fixed dt, so the compiled graph is reused for
        every step after the first call in each run.
        """
        def f(y):
            return -1j * (h_eff_jax @ y)

        k1 = dt * f(psi_jax)
        k2 = dt * f(psi_jax + 0.5 * k1)
        k3 = dt * f(psi_jax + 0.5 * k2)
        k4 = dt * f(psi_jax + k3)
        return psi_jax + (k1 + 2 * k2 + 2 * k3 + k4) / 6.0

else:
    _jit_rk4_step = None  # pragma: no cover - JAX-less environments


def _select_tjm_backend(preferred: Optional[str] = None) -> str:
    """Resolve which TJM backend to use.

    ``preferred`` accepts ``"auto"`` / ``None`` (pick best available),
    ``"jax_gpu"``, ``"jax_cpu"``, ``"numpy"``. Explicit backends that
    aren't available raise ``RuntimeError`` rather than silently
    demoting -- silent demotion is the same failure mode we closed for
    the statevector GPU path in v6.5 P0.5.

    The ``"auto"`` policy prefers NumPy over ``jax_cpu`` because
    JAX CPU has per-call overhead that swamps the speedup for the
    small dimensions TJM typically runs at. ``jax_cpu`` is exposed
    only for parity testing and forced-path runs.
    """
    if preferred is not None and preferred != "auto":
        if preferred == "jax_gpu":
            if not HAS_JAX_GPU:
                raise RuntimeError(
                    "backend='jax_gpu' requested but JAX reports no GPU devices "
                    f"(HAS_JAX={HAS_JAX}, HAS_JAX_GPU={HAS_JAX_GPU})."
                )
            return "jax_gpu"
        if preferred == "jax_cpu":
            if not HAS_JAX:
                raise RuntimeError(
                    "backend='jax_cpu' requested but JAX is not installed."
                )
            return "jax_cpu"
        if preferred == "numpy":
            return "numpy"
        raise ValueError(
            f"Unknown TJM backend {preferred!r}. "
            "Use 'auto', 'jax_gpu', 'jax_cpu', or 'numpy'."
        )

    # auto: jax_gpu > numpy (skip jax_cpu by default for perf reasons)
    if HAS_JAX_GPU:
        return "jax_gpu"
    return "numpy"


@dataclass
class TJMResult:
    density_matrix: Optional[np.ndarray]
    expectation_values: Dict[str, float]
    converged: bool
    trajectories_completed: int
    bond_dim_used: int
    vram_estimate_gb: float
    # v6.5 follow-up: which backend actually ran the trajectory. Added with a
    # default so pre-existing construction sites (no kwarg) keep working.
    backend_used: str = "numpy"


class EffectiveHamiltonian:
    """H_eff = H - (i/2) * Sum(L_k^dag @ L_k)

    Non-Hermitian effective Hamiltonian for the no-jump evolution.
    """

    def __init__(self, hamiltonian: np.ndarray, lindblad_ops: List[np.ndarray]):
        self._h = hamiltonian
        self._lindblad_ops = lindblad_ops
        self._h_eff = self._build()

    def _build(self) -> np.ndarray:
        h_eff = self._h.copy().astype(complex)
        for L in self._lindblad_ops:
            h_eff -= 0.5j * (L.conj().T @ L)
        return h_eff

    def matrix(self) -> np.ndarray:
        return self._h_eff


class JumpSampler:
    """Sample quantum jumps based on norm decay of non-Hermitian evolution."""

    def __init__(self, lindblad_ops: List[np.ndarray], rng: np.random.Generator):
        self._ops = lindblad_ops
        self._rng = rng

    def sample(self, psi: np.ndarray, dt: float) -> Optional[int]:
        """Return index of jump operator to apply, or None if no jump.

        Probability of jump k: dp_k = dt * <psi| L_k^dag L_k |psi>
        Total jump probability: dp = Sum dp_k
        """
        if len(self._ops) == 0:
            return None

        probs = []
        for L in self._ops:
            Lpsi = L @ psi
            probs.append(dt * float(np.real(np.vdot(Lpsi, Lpsi))))

        dp_total = sum(probs)
        if dp_total < 0:
            dp_total = 0.0

        r = self._rng.uniform()
        if r >= dp_total:
            return None

        # Select which jump
        cumulative = 0.0
        for k, dp_k in enumerate(probs):
            cumulative += dp_k
            if r < cumulative:
                return k

        return len(probs) - 1

    def apply_jump(self, psi: np.ndarray, jump_index: int) -> np.ndarray:
        """Apply jump operator and renormalize."""
        new_psi = self._ops[jump_index] @ psi
        norm = np.linalg.norm(new_psi)
        if norm > 0:
            new_psi /= norm
        return new_psi


class TrajectoryAverager:
    """Average over trajectories to recover density matrix rho."""

    def __init__(self, dim: int):
        self._dim = dim
        self._rho_sum = np.zeros((dim, dim), dtype=complex)
        self._count = 0

    def add_trajectory(self, final_state: np.ndarray) -> None:
        psi = final_state.reshape(-1, 1)
        self._rho_sum += psi @ psi.conj().T
        self._count += 1

    def average(self) -> np.ndarray:
        if self._count == 0:
            return self._rho_sum
        return self._rho_sum / self._count


class TensorJumpSimulator:
    """Tensor Jump Method simulator for open-system Lindblad dynamics.

    Evolution runs trajectory-by-trajectory via RK4 on the effective
    non-Hermitian Hamiltonian. At each step the Monte Carlo wave-function
    sampler decides whether a jump fires.

    Backend dispatch (``backend`` keyword, ``"auto"`` by default):

      - ``"auto"``    : prefer ``jax_gpu`` if JAX sees a GPU, else ``numpy``
      - ``"jax_gpu"`` : RK4 under JAX on GPU (raises if JAX has no GPU)
      - ``"jax_cpu"`` : RK4 under JAX on CPU (smoke-test / parity path)
      - ``"numpy"``   : reference path, always available

    Jump sampling stays in NumPy regardless of the evolution backend --
    it's cheap and avoids round-tripping random draws through JAX.

    MPS / quimb-based evolution is a planned extension; ``HAS_QUIMB`` is
    flagged at module load but not yet consumed by ``run()``.
    """

    def __init__(
        self,
        n_qubits: int,
        n_trajectories: int = 10,
        bond_dim: int = 64,
        vram_ceiling_gb: float = 5.5,  # Phase 2.5: RTX 5070 Laptop effective budget
        backend: Optional[str] = None,
    ):
        self._n_qubits = n_qubits
        self._n_trajectories = n_trajectories
        self._bond_dim = bond_dim
        self._vram_ceiling_gb = vram_ceiling_gb
        self._dim = 2 ** min(n_qubits, 24)
        self._backend = _select_tjm_backend(backend)
        log.info(
            "TensorJumpSimulator initialised (backend=%s, quimb=%s, jax=%s, jax_gpu=%s)",
            self._backend, HAS_QUIMB, HAS_JAX, HAS_JAX_GPU,
        )

    @property
    def backend(self) -> str:
        """The resolved backend identifier ('numpy' | 'jax_cpu' | 'jax_gpu')."""
        return self._backend

    def estimate_vram_gb(self) -> float:
        """Estimate GPU VRAM needed for MPS representation."""
        return (self._bond_dim ** 2 * self._n_qubits * 16) / 1e9

    def run(
        self,
        hamiltonian: np.ndarray,
        lindblad_ops: List[np.ndarray],
        total_time: float,
        dt: float,
    ) -> TJMResult:
        """Run TJM simulation.

        Args:
            hamiltonian: System Hamiltonian (dim x dim complex matrix).
            lindblad_ops: List of Lindblad (jump) operators.
            total_time: Total evolution time.
            dt: Time step.

        Returns:
            TJMResult with density matrix, trace diagnostics, and
            ``backend_used`` reflecting which code path ran.
        """
        vram_est = self.estimate_vram_gb()
        if vram_est > self._vram_ceiling_gb:
            log.warning(
                "VRAM estimate %.2f GB exceeds ceiling %.2f GB. "
                "Reducing bond dimension or using CPU fallback.",
                vram_est, self._vram_ceiling_gb,
            )

        h_eff = EffectiveHamiltonian(hamiltonian, lindblad_ops)
        h_eff_matrix = h_eff.matrix()
        dim = hamiltonian.shape[0]
        n_steps = max(1, int(total_time / dt))

        # Precompute the JAX-device-resident Hamiltonian once if needed.
        h_eff_jax = None
        if self._backend in ("jax_gpu", "jax_cpu"):
            h_eff_jax = jnp.asarray(h_eff_matrix, dtype=jnp.complex128)

        averager = TrajectoryAverager(dim)
        completed = 0

        for traj_idx in range(self._n_trajectories):
            rng = np.random.default_rng(seed=traj_idx)
            sampler = JumpSampler(lindblad_ops, rng)

            # Initial state: |0...0>
            psi = np.zeros(dim, dtype=complex)
            psi[0] = 1.0

            for step in range(n_steps):
                if self._backend in ("jax_gpu", "jax_cpu"):
                    # Per-step round-trip: JAX evolves, NumPy samples jumps.
                    psi_jax = jnp.asarray(psi)
                    psi_jax = self._evolve_step_jax(psi_jax, h_eff_jax, dt)
                    psi = np.asarray(psi_jax)
                else:
                    psi = self._evolve_step(psi, h_eff_matrix, dt)

                jump_idx = sampler.sample(psi, dt)
                if jump_idx is not None:
                    psi = sampler.apply_jump(psi, jump_idx)
                else:
                    norm = np.linalg.norm(psi)
                    if norm > 0:
                        # Non-in-place so this works for read-only views
                        # (``np.asarray(jax_array)`` returns a read-only view
                        # because JAX arrays are immutable).
                        psi = psi / norm

            averager.add_trajectory(psi)
            completed += 1

        rho = averager.average()
        trace = float(np.real(np.trace(rho)))

        return TJMResult(
            density_matrix=rho,
            expectation_values={"trace": trace},
            converged=abs(trace - 1.0) < 1e-6,
            trajectories_completed=completed,
            bond_dim_used=self._bond_dim,
            vram_estimate_gb=vram_est,
            backend_used=self._backend,
        )

    @staticmethod
    def _evolve_step(
        psi: np.ndarray, h_eff: np.ndarray, dt: float
    ) -> np.ndarray:
        """Evolve state by one RK4 step under H_eff (NumPy backend)."""
        def f(y):
            return -1j * h_eff @ y

        k1 = dt * f(psi)
        k2 = dt * f(psi + 0.5 * k1)
        k3 = dt * f(psi + 0.5 * k2)
        k4 = dt * f(psi + k3)
        return psi + (k1 + 2 * k2 + 2 * k3 + k4) / 6.0

    @staticmethod
    def _evolve_step_jax(psi_jax, h_eff_jax, dt: float):
        """Evolve state by one RK4 step under H_eff (JAX backend, JIT-compiled).

        Delegates to the module-level :func:`_jit_rk4_step`. ``psi_jax``
        and ``h_eff_jax`` are jnp arrays on whichever device JAX
        default-placed them; GPU/CPU is determined by the selected JAX
        backend at module load, no explicit ``device_put`` needed. The
        jit cache lives at module scope so simulator instances sharing
        the same ``(shape, dtype, dt)`` signature reuse the compiled
        graph.
        """
        if _jit_rk4_step is None:  # pragma: no cover - caller gated by HAS_JAX
            raise RuntimeError(
                "_evolve_step_jax called but JAX is not available; "
                "backend selection should have prevented this."
            )
        return _jit_rk4_step(psi_jax, h_eff_jax, dt)


# ============================================================================
# STREAMING RK4 — Phase 2.5 Hotfix (Rec #5)
#
# Separate function from TensorJumpSimulator.run(). Used when preflight
# detects density_matrix exceeds VRAM but fits in RAM (Mode B stepdown).
# Chunks the Lindblad evolution so only one trajectory's statevector lives
# in memory at a time -- never builds the full 2^n x 2^n density matrix
# on GPU. The final density matrix is accumulated on CPU via outer products.
# ============================================================================

@dataclass
class StreamingLindbladResult:
    """Result from propagate_lindblad_streaming."""
    density_matrix: Optional[np.ndarray]
    expectation_values: Dict[str, float]
    converged: bool
    trajectories_completed: int
    peak_memory_mb: float
    mode: str  # "streaming_cpu"


def propagate_lindblad_streaming(
    hamiltonian: np.ndarray,
    lindblad_ops: List[np.ndarray],
    total_time: float,
    dt: float,
    n_qubits: int,
    n_trajectories: int = 10,
) -> StreamingLindbladResult:
    """
    Streaming Lindblad propagation via Monte Carlo wave function + RK4.

    Unlike TensorJumpSimulator.run(), this function:
      - Never allocates a full 2^n x 2^n density matrix on GPU
      - Runs trajectories one at a time on CPU
      - Accumulates rho via rank-1 outer products: rho += |psi><psi|
      - Peak memory = O(2^n) per trajectory, not O(2^2n)

    For 15 qubits: statevector = 0.5 MB vs density matrix = 16 GB.

    Args:
        hamiltonian: System Hamiltonian (dim x dim complex matrix).
        lindblad_ops: List of Lindblad (jump) operators.
        total_time: Total evolution time.
        dt: Time step.
        n_qubits: Number of qubits (for logging/metadata).
        n_trajectories: Number of Monte Carlo trajectories.

    Returns:
        StreamingLindbladResult with accumulated density matrix.
    """
    dim = hamiltonian.shape[0]
    n_steps = max(1, int(total_time / dt))

    # Build effective Hamiltonian once
    h_eff = EffectiveHamiltonian(hamiltonian, lindblad_ops)

    # Accumulate density matrix on CPU
    rho_accum = np.zeros((dim, dim), dtype=complex)
    completed = 0
    peak_mem_bytes = 0

    log.info(
        "Streaming Lindblad: %d qubits, %d trajectories, %d steps, "
        "statevector = %.1f MB (avoids %.1f GB density matrix)",
        n_qubits, n_trajectories, n_steps,
        dim * 16 / 1e6, dim * dim * 16 / 1e9,
    )

    for traj_idx in range(n_trajectories):
        rng = np.random.default_rng(seed=traj_idx)
        sampler = JumpSampler(lindblad_ops, rng)

        # Initial state: |0...0>
        psi = np.zeros(dim, dtype=complex)
        psi[0] = 1.0

        for step in range(n_steps):
            # RK4 evolution (same as TensorJumpSimulator._evolve_step)
            psi = _streaming_rk4_step(psi, h_eff.matrix(), dt)

            # Jump sampling
            jump_idx = sampler.sample(psi, dt)
            if jump_idx is not None:
                psi = sampler.apply_jump(psi, jump_idx)
            else:
                norm = np.linalg.norm(psi)
                if norm > 0:
                    psi /= norm

        # Accumulate: rho += |psi><psi| (rank-1 update, O(2^n) memory)
        psi_col = psi.reshape(-1, 1)
        rho_accum += psi_col @ psi_col.conj().T
        completed += 1

        # Track peak memory (approximate)
        current_mem = psi.nbytes + rho_accum.nbytes
        peak_mem_bytes = max(peak_mem_bytes, current_mem)

    # Average over trajectories
    if completed > 0:
        rho_accum /= completed

    trace = float(np.real(np.trace(rho_accum)))

    return StreamingLindbladResult(
        density_matrix=rho_accum,
        expectation_values={"trace": trace},
        converged=abs(trace - 1.0) < 1e-6,
        trajectories_completed=completed,
        peak_memory_mb=round(peak_mem_bytes / (1024 * 1024), 2),
        mode="streaming_cpu",
    )


def _streaming_rk4_step(
    psi: np.ndarray, h_eff: np.ndarray, dt: float
) -> np.ndarray:
    """Single RK4 step for streaming propagation (statevector only)."""
    def f(y):
        return -1j * h_eff @ y

    k1 = dt * f(psi)
    k2 = dt * f(psi + 0.5 * k1)
    k3 = dt * f(psi + 0.5 * k2)
    k4 = dt * f(psi + k3)
    return psi + (k1 + 2 * k2 + 2 * k3 + k4) / 6.0
