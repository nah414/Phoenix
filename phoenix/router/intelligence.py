"""Hardware intelligence layer (Phase 4 -- Source A only).

Per architecture v1 Section 4.6: the Router's intelligence layer combines
three sources to estimate per-provider fidelity, latency, and cost for a
given task:

- **Source A (static)** -- :class:`HardwareParams` from the vendored
  ``synthesis/core/hardware_backends.py``. T1, T2, gate error rates,
  qubit frequency, anharmonicity, probe latency, max qubits, native gate
  set. Per-modality baseline estimates.
- **Source B (live telemetry)** -- live per-qubit T1/T2, per-gate error
  rates, queue depth from provider APIs. Polled on configurable cadence.
  **Phase 4 deferred.** Lands at Phase 7 with the state backend.
- **Source C (ledger history)** -- past :class:`Result` records with
  measured fidelity / latency / backaction. Empirical performance on
  similar tasks. **Phase 4 deferred.** Lands at Phase 7 with the Omega
  Ledger.

Phase 4 ships Source A only. The Router's Stage 6 (ranking) consumes the
estimates this layer produces; Stage 2 (filters) compares against the
user's ``cost_ceiling_usd``, ``latency_budget_ms``, ``fidelity_floor``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from phoenix.router.pricing import estimate_cost_usd as _pricing_estimate

if TYPE_CHECKING:
    from synthesis.core.hardware_backends import HardwareParams

    from phoenix.router.provider_registry import ProviderEntry

log = logging.getLogger(__name__)


# Map quantum_technology strings to vendored hardware-backend identifiers.
# The vendored get_backend() takes the lowercase identifier directly; this
# map exists to normalize our taxonomy to theirs and to handle the
# "simulation" case (no hardware backend).
_TECH_TO_BACKEND_NAME: dict[str, str] = {
    "superconducting": "superconducting",
    "trapped_ion": "trapped_ion",
    "photonic": "telecom_photonic",
    "nmr": "nmr",
    # "simulation" has no hardware backend; the Local sim has no decoherence
    # to estimate. Handled separately below.
}


def _hardware_params_for(quantum_technology: str) -> HardwareParams | None:
    """Resolve :class:`HardwareParams` for a given technology string.

    Returns ``None`` for ``"simulation"`` (no decoherence model) and for
    any technology not in the vendored backends. Caller treats ``None`` as
    "perfect fidelity, sub-millisecond latency" -- the local-simulator
    profile.
    """
    backend_name = _TECH_TO_BACKEND_NAME.get(quantum_technology)
    if backend_name is None:
        return None
    try:
        from synthesis.core.hardware_backends import get_backend
    except ImportError:
        log.warning("Vendored hardware_backends not available; using simulation defaults.")
        return None
    try:
        backend = get_backend(backend_name)
        return backend.get_params()
    except (KeyError, ValueError) as exc:
        log.warning("get_backend(%s) failed: %s; using simulation defaults.", backend_name, exc)
        return None


def estimate_fidelity(entry: ProviderEntry) -> float:
    """Estimate per-solve state fidelity for a provider.

    Phase 4 (Source A only): derived from the vendored
    :class:`HardwareParams.gate_error_rate` and ``two_qubit_error_rate``,
    averaged over an assumed Phase 4 default circuit shape (10
    single-qubit gates + 1 two-qubit gate per QHO solve). The local
    simulator returns ``1.0`` (perfect fidelity).

    Phase 7 extends with Sources B (live telemetry) and C (measured
    fidelities from past Results).
    """
    params = _hardware_params_for(entry.client.quantum_technology)
    if params is None:
        return 1.0
    # Phase 4 placeholder circuit shape: 10 single-qubit + 1 two-qubit gate.
    # Multiplicative independent error model: F ~ (1-eps_1q)^10 * (1-eps_2q).
    f_1q = (1.0 - params.gate_error_rate) ** 10
    f_2q = 1.0 - params.two_qubit_error_rate
    return float(max(0.0, min(1.0, f_1q * f_2q)))


def estimate_latency_ms(entry: ProviderEntry) -> float:
    """Estimate wall-clock latency in milliseconds for a provider.

    Phase 4 falls back to :meth:`BaseProviderClient.estimated_latency_ms`
    on the underlying client. The stub adapters report sane defaults
    (IBM ~5000 ms, Braket ~3000 ms, IonQ ~10000 ms, local sim 0.1 ms).
    Phase 7 will source live queue-depth-aware latencies from Source B.
    """
    return float(entry.client.estimated_latency_ms())


def estimate_cost_usd(entry: ProviderEntry) -> float:
    """Estimate per-solve dollar cost for a provider.

    Delegates to :func:`phoenix.router.pricing.estimate_cost_usd` which
    consults ``pricing/pricing_v1.json``. Phase 4 emits a static
    per-provider estimate; Phase 7+ shot-aware.
    """
    return _pricing_estimate(entry.client.provider_id)


def estimate_all(
    entry: ProviderEntry,
) -> tuple[float, float, float]:
    """Convenience: returns ``(fidelity, latency_ms, cost_usd)`` triple.

    The Router's Stage 6 ranking consumes all three at once; Stage 2
    filters compare each individually against the user's policy
    envelope.
    """
    return (
        estimate_fidelity(entry),
        estimate_latency_ms(entry),
        estimate_cost_usd(entry),
    )
