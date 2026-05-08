"""Pure post-processor: :class:`ProviderRawResult` -> :class:`ExtractedResult`.

Per architecture v1 Section 2.5: ``result_extractor`` translates a
provider's raw output into Phoenix-uniform observables and KPI fields.
Pure function; no I/O.

Phase 3 ships extraction logic for the local-simulator's
``"classical_density_matrix"`` shape only. Phase 4 extends to cloud-quantum
shot histograms (Qiskit counts dict, Braket measurement_counts, IonQ shot
batches) once the cloud adapters land.

The :class:`KPIBundle` returned here populates
:attr:`Result.kpi_bundle_orchestrate` (the final Orchestrate-side bundle).
Control's :attr:`VerifiedAnswer.kpi_bundle_control` (already populated by
:func:`run_dpd`) and Solver's KPI bundle (Phase 5+ when the gate composes
multi-layer KPIs) are kept separate -- the layered design lets the
verification gate inspect each layer's KPIs independently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from phoenix.trinity.orchestrate.kpi_bundle import KPIBundle, KPIStatus

if TYPE_CHECKING:
    from phoenix.trinity.data_model import VerifiedAnswer
    from phoenix.trinity.orchestrate.provider_client import ProviderRawResult


@dataclass(frozen=True)
class ExtractedResult:
    """Normalized extraction output from a provider's raw result.

    Fields:
        value: The observable Phoenix returns to the caller. Phase 3's
            local-simulator path returns the trace-expectation reported by
            the simulator (always ~1.0 for unit-trace :math:`\\rho`,
            placeholder for the real observable extraction Phase 5 wires).
            Cloud adapters in Phase 4 return the measured expectation
            value or shot-derived statistic.
        kpi_bundle: Phoenix-native :class:`KPIBundle` per Section 2.5,
            populated from the raw result + the originating
            :class:`VerifiedAnswer`.
        extra_metadata: Free-form dict for any extraction context worth
            logging (e.g. raw shot counts, expectation-value variance).
            Lands in :class:`OrchestrateProvenance.metadata`.
    """

    value: Any
    kpi_bundle: KPIBundle
    extra_metadata: dict[str, Any] = field(default_factory=dict)


def _classify_status(fidelity: float) -> KPIStatus:
    """Phase 3 fidelity-based classification (mirrors Control's logic).

    Conservative defaults; Phase 5's verification gate may override based
    on cross-axis disagreement. The architecturally-correct place for the
    final classification is the gate composer (Phase 5); Phase 3 ships a
    per-axis classification so each layer's KPIBundle has a sensible
    status.
    """
    if fidelity >= 0.95:
        return KPIStatus.OK
    if fidelity >= 0.5:
        return KPIStatus.WARN
    return KPIStatus.FAIL


def extract(
    raw: ProviderRawResult,
    verified: VerifiedAnswer,
) -> ExtractedResult:
    """Translate a :class:`ProviderRawResult` + :class:`VerifiedAnswer`
    into a Phoenix-uniform :class:`ExtractedResult`.

    Phase 3 extraction:

    - ``value``: ``raw.raw_data["expectation_value"]`` if present (the
      local-simulator path); otherwise falls back to the trace of
      ``verified.rho_verified`` (always ~1.0 for valid :math:`\\rho`).
    - ``KPIBundle.fidelity``: ``1.0 - verified.dpd_result.total_backaction``
      clipped to :math:`[0, 1]`. Cloud adapters in Phase 4 will source
      fidelity from provider-reported metadata when available.
    - ``KPIBundle.latency_us``: Echoes ``raw.latency_us``.
    - ``KPIBundle.backaction``: Echoes ``verified.dpd_result.total_backaction``.
    - ``KPIBundle.shots_used`` / ``shot_budget``: Both from
      ``raw.shots_used`` (Phase 3 placeholder; Phase 4's Router computes
      a real budget).
    - ``KPIBundle.status``: Fidelity-classified per :func:`_classify_status`.

    Pure function, no I/O. Phase 5's gate composer may override the status
    based on cross-axis disagreement.
    """
    raw_data = raw.raw_data

    if "expectation_value" in raw_data:
        value: Any = raw_data["expectation_value"]
    else:
        # Defensive fallback: the trace of a valid density matrix is 1.0.
        # Phase 4 adapters always populate expectation_value; this branch
        # is reachable only for misbehaving local-simulator stubs.
        value = float(np.real(np.trace(np.asarray(verified.rho_verified))))

    fidelity = float(np.clip(1.0 - verified.dpd_result.total_backaction, 0.0, 1.0))
    bundle = KPIBundle(
        fidelity=fidelity,
        latency_us=raw.latency_us,
        backaction=float(verified.dpd_result.total_backaction),
        shots_used=raw.shots_used,
        shot_budget=raw.shots_used,  # Phase 3 placeholder; Phase 4 Router computes
        status=_classify_status(fidelity),
    )

    return ExtractedResult(
        value=value,
        kpi_bundle=bundle,
        extra_metadata={
            "rho_dim": int(raw_data.get("rho_dim", 0)),
            "provider_id": raw.provider_id,
            "backend_name": raw.backend_name,
        },
    )
