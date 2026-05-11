"""Hardware intelligence layer (Phase 4 + Phase 7 Step 9 drift feedback).

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
  similar tasks. **Phase 7 Step 9 ships the drift-feedback half:** when
  the :class:`DriftDetector` fires a snapshot naming a provider in its
  ``firing_detectors``, the router lowers that provider's
  ``estimated_fidelity`` multiplier per §4.6 ("a provider that drifted
  three releases ago has lower estimated_fidelity than its self-reported
  number"). Empirical-fidelity-from-past-Results lands later.

Phase 7 Step 9's drift hook is the OPEN-6 forward-compat seam from
Phase 6b paying its dividend: :func:`register_for_drift_updates` is
called once at FastAPI lifespan startup and registers
:func:`on_drift_snapshot` as a second
:meth:`DriftDetector.register_drift_callback` caller (the first was
the verification gate's auto-promote consumer in Phase 6b).
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from phoenix.router.pricing import estimate_cost_usd as _pricing_estimate

if TYPE_CHECKING:
    from synthesis.core.hardware_backends import HardwareParams

    from phoenix.router.provider_registry import ProviderEntry
    from phoenix.verification.drift_detector import DriftSnapshot

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase 7 Step 9 -- per-provider drift multiplier.
#
# When the DriftDetector reports a firing detector named "<provider>_drift"
# (the v1 naming convention), the router lowers that provider's fidelity
# score by the multiplier corresponding to the snapshot's state. Multipliers
# refresh on every drift cycle; absent providers default to 1.0 (no
# penalty). The module-level dict is guarded by an RLock so concurrent
# router queries don't see partial updates.
#
# Multiplier values per locked Phase 6b drift-state ladder:
# - "healthy"                    -> 1.00 (no penalty, provider clean)
# - "warning"                    -> 0.90 (~10% confidence haircut)
# - "high_confidence_warning"    -> 0.70 (~30% haircut; degraded routing)

_DRIFT_STATE_MULTIPLIER: dict[str, float] = {
    "healthy": 1.0,
    "warning": 0.90,
    "high_confidence_warning": 0.70,
}
_DEFAULT_DRIFT_MULTIPLIER = 1.0

# Suffix the v1 drift-detector naming convention uses for provider-scoped
# detectors. A detector named "ibm_brisbane_drift" targets provider_id
# "ibm_brisbane". v1.x can promote this to a structured field on
# :class:`DriftSnapshot` (e.g. a ``firing_providers`` list) when more
# detector kinds enter the picture.
_PROVIDER_DETECTOR_SUFFIX = "_drift"

_drift_multipliers: dict[str, float] = {}
_drift_multipliers_lock = threading.RLock()


def _detector_to_provider_id(detector_name: str) -> str | None:
    """Map a detector name to the provider it targets, or None.

    A detector named ``"ibm_brisbane_drift"`` targets provider_id
    ``"ibm_brisbane"``. Returns None for detectors not following the
    ``<provider_id>_drift`` convention (e.g. cross-provider validators).
    """
    if detector_name.endswith(_PROVIDER_DETECTOR_SUFFIX):
        stripped = detector_name[: -len(_PROVIDER_DETECTOR_SUFFIX)]
        if stripped:
            return stripped
    return None


def drift_multiplier_for(provider_id: str) -> float:
    """Return the current drift multiplier for ``provider_id``.

    Used by :func:`estimate_fidelity` to scale the static fidelity
    estimate. Returns ``1.0`` (no penalty) when no drift snapshot
    has fired for this provider.

    Thread-safe: reads under the module lock so concurrent router
    queries see consistent values during a multi-update cycle.
    """
    with _drift_multipliers_lock:
        return _drift_multipliers.get(provider_id, _DEFAULT_DRIFT_MULTIPLIER)


def on_drift_snapshot(snapshot: DriftSnapshot) -> None:
    """Drift-detector callback -- update per-provider multipliers.

    Called by :class:`DriftDetector` on every cycle. The snapshot's
    ``firing_detectors`` list names which detectors crossed threshold;
    we translate the provider-scoped ones into per-provider multipliers
    keyed by :data:`_DRIFT_STATE_MULTIPLIER[snapshot.state]`.

    **Reset semantics:** when ``firing_detectors`` is empty (or contains
    no provider-scoped detectors), every previously-penalized provider
    is reset to 1.0. This means a single "healthy" cycle is sufficient
    to lift a prior haircut; v1.x can add a sticky-penalty window if
    flapping becomes a real issue.

    Never raises -- per the Phase 7 Step 1 fire-and-forget audit
    contract, drift-callback failures must not propagate into the
    detector's cycle path.
    """
    try:
        penalty = _DRIFT_STATE_MULTIPLIER.get(snapshot.state, _DEFAULT_DRIFT_MULTIPLIER)
        targeted: dict[str, float] = {}
        for detector_name in snapshot.firing_detectors:
            provider_id = _detector_to_provider_id(detector_name)
            if provider_id is None:
                continue
            targeted[provider_id] = penalty
        with _drift_multipliers_lock:
            # Reset the table: providers no longer in firing_detectors
            # return to 1.0 on a single healthy cycle.
            _drift_multipliers.clear()
            _drift_multipliers.update(targeted)
        if targeted:
            log.info(
                "Router drift multipliers updated: state=%s targeted=%s",
                snapshot.state,
                targeted,
            )
    except Exception:
        log.exception("on_drift_snapshot failed; multipliers unchanged")


def reset_drift_multipliers() -> None:
    """Clear all drift multipliers. Test isolation helper."""
    with _drift_multipliers_lock:
        _drift_multipliers.clear()


def register_for_drift_updates() -> None:
    """Register :func:`on_drift_snapshot` with the singleton drift detector.

    Called from the FastAPI lifespan at daemon startup (Phase 7 Step 9
    -- the OPEN-6 forward-compat seam paying its dividend). Idempotent:
    re-entry on lifespan restarts (TestClient repeatedly enters/exits
    the lifespan) checks whether the callback is already attached
    before registering.
    """
    from phoenix.verification.drift_detector import get_detector

    detector = get_detector()
    # DriftDetector.register_drift_callback appends unconditionally;
    # we dedupe at this layer so multiple lifespan starts don't
    # register the same callable twice.
    if on_drift_snapshot in detector._callbacks:
        return
    detector.register_drift_callback(on_drift_snapshot)


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

    **Phase 7 Step 9 -- drift-feedback scaling (§4.6 Source C
    half):** the per-provider drift multiplier from the most recent
    :class:`DriftSnapshot` scales the static estimate. A provider
    whose ``<provider_id>_drift`` detector is in ``firing_detectors``
    of a ``warning`` snapshot gets a ~10% haircut; a
    ``high_confidence_warning`` snapshot gets ~30%. Clean providers
    (multiplier 1.0) are unaffected. Phase 7 v1 still leaves
    empirical-fidelity-from-past-Results (the rest of Source C) for
    a later phase.
    """
    params = _hardware_params_for(entry.client.quantum_technology)
    if params is None:
        static_fidelity = 1.0
    else:
        # Phase 4 placeholder circuit shape: 10 single-qubit + 1 two-qubit gate.
        # Multiplicative independent error model: F ~ (1-eps_1q)^10 * (1-eps_2q).
        f_1q = (1.0 - params.gate_error_rate) ** 10
        f_2q = 1.0 - params.two_qubit_error_rate
        static_fidelity = f_1q * f_2q

    drift_scale = drift_multiplier_for(entry.client.provider_id)
    return float(max(0.0, min(1.0, static_fidelity * drift_scale)))


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
