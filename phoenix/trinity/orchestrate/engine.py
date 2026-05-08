"""Trinity Core Orchestrate subsystem -- top-level engine.

Per architecture v1 Section 2.5: ``orchestrate`` sequences the six
Phase-3 modules (bundle_builder -> provider_client.submit -> result_extractor
-> drift_feedback -> KPIBundle composition) and produces the
:class:`Result` envelope plus an :class:`OrchestrateProvenance` for the
trace.

Greenfield Phoenix code per Decision 37 (2026-05-06 SynQc-greenfield
revision). The error-bar combiner is Section 11.1.1's quadrature placeholder:

.. math::

    \\text{error\\_bar} = \\sqrt{\\sigma_{\\text{solver}}^2 + \\sigma_{\\text{control}}^2 + \\sigma_{\\text{orchestrate}}^2}

Phase 3's pipeline runs at :attr:`RungDepth.R3_TWO_AXES` so
:math:`\\sigma_{\\text{orchestrate}} = 0` (Axis 3 fires at R4+, Phase 5).
The combiner formula assumes layer-error independence; Section 11.1.1
flags this as approximately-but-not-exactly true and defers refinement to
v1.1 with empirical covariance data. Per-axis bars are recorded in
:class:`ProvenanceTrace` so the future analysis has data.

The agreement_type mapping is Phase 3 minimal: any successful return is
``DisagreementType.HEDGED_CONSENSUS`` (the closest-fit existing enum value
for "all axes agree within tolerance"). Phase 5's ``agreement_classifier``
extends the enum (Section 6.2) with ``NUMERICAL_DRIFT``,
``BACKACTION_SENSITIVE``, ``PROVIDER_DIVERGENT``, ``DEGRADED``,
``DEGRADED_BUDGET_BOUND``; the Phase 3 mapping is a temporary stand-in
documented in the Phase 3 plan's R5 risk.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from phoenix.trinity.data_model import OrchestrateProvenance, Result
from phoenix.trinity.orchestrate import bundle_builder, drift_feedback, result_extractor
from phoenix.trinity.orchestrate.drift_feedback import DriftSignal
from phoenix.trinity.orchestrate.provider_client import (
    OrchestrateProviderError,
    ProviderError,
)

if TYPE_CHECKING:
    from wobble.disagreement_types import DisagreementType

    from phoenix.router.data_model import ProviderSelection
    from phoenix.trinity.data_model import VerifiedAnswer


class OrchestrateEngineError(Exception):
    """Base for Orchestrate engine failures."""


def orchestrate(
    verified: VerifiedAnswer,
    selection: ProviderSelection,
    *,
    request_id: str,
    error_bar_solver: float,
    error_bar_control: float,
    solver_id: str,
    shots: int = 1024,
    tolerance_max_error_bar: float = 1e-3,
) -> tuple[Result, OrchestrateProvenance]:
    """Top-level Orchestrate orchestrator.

    Sequences the six Phase-3 modules:

    1. :func:`bundle_builder.build_submission` -- pure translation, no I/O.
    2. :meth:`selection.client.submit` -- provider dispatch (synchronous in
       Phase 3 against :class:`LocalClassicalSimulator`; cloud adapters in
       Phase 4 add async polling).
    3. :func:`result_extractor.extract` -- pure post-processing; produces
       observable + :class:`KPIBundle`.
    4. :func:`drift_feedback.emit_drift_signal` -- buffer-only in Phase 3.
    5. Quadrature-combine the three layer error bars per Section 11.1.1.
    6. Build the :class:`Result` envelope + :class:`OrchestrateProvenance`.

    Returns ``(result, orchestrate_provenance)``. The pipeline orchestrator
    (Step 8) composes the provenance into the full :class:`ProvenanceTrace`
    by stitching the three layers' provenances.

    SAFETY: provider failures are caught and re-raised as
    :class:`OrchestrateProviderError` carrying provider_id + bundle_hash
    so Step 9's HTTP 502 mapping has full context.
    """
    # Lazy import to avoid module-load cost on fresh clones (vendor/ may
    # not be populated until Phase 1 vendor sync runs).
    from wobble.disagreement_types import DisagreementType

    t_start = time.perf_counter()

    # 1. Build the submission.
    submission = bundle_builder.build_submission(
        verified, selection, shots=shots, request_id=request_id
    )

    # 2. Dispatch via the provider client.
    try:
        raw = selection.client.submit(submission)
    except OrchestrateProviderError:
        # Pre-tagged with provider_id + bundle_hash; let it propagate.
        raise
    except ProviderError as exc:
        # Generic ProviderError -- wrap with the bundle context the front
        # door (Step 9) needs to map to HTTP 502.
        raise OrchestrateProviderError(
            (
                f"Provider {selection.provider_id!r} submit() failed: {exc}. "
                f"bundle_hash={submission.bundle_hash}."
            ),
            provider_id=selection.provider_id,
            bundle_hash=submission.bundle_hash,
        ) from exc

    # 3. Extract Phoenix-uniform result.
    extracted = result_extractor.extract(raw, verified)

    wall_clock_ms = (time.perf_counter() - t_start) * 1000.0

    # 4. Emit drift signal (buffer-only in Phase 3; consumers at Phases 4 + 7).
    drift_feedback.emit_drift_signal(
        DriftSignal(
            request_id=request_id,
            provider_id=selection.provider_id,
            measured_fidelity=extracted.kpi_bundle.fidelity,
            measured_latency_us=extracted.kpi_bundle.latency_us,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            solver_id=solver_id,
        )
    )

    # 5. Quadrature combine. Phase 3: error_bar_orchestrate = 0.0 (Axis 3
    # at R4+, Phase 5). Section 11.1.1 OPEN; per-axis bars recorded in
    # provenance for v1.1 covariance refinement.
    error_bar_orchestrate = 0.0
    error_bar = math.sqrt(error_bar_solver**2 + error_bar_control**2 + error_bar_orchestrate**2)

    # Phase 3 sigma placeholder: same as error_bar. Phase 5's gate composer
    # computes the proper distance-matrix-derived sigma across all axes.
    sigma = error_bar

    # Phase 3 agreement_type mapping per plan's R5 risk: HEDGED_CONSENSUS
    # for "all axes agree within tolerance"; UNKNOWN if combined error bar
    # exceeds tolerance. Phase 5's agreement_classifier extends the enum
    # with the architecture-spec values (NUMERICAL_DRIFT, etc.).
    if error_bar < tolerance_max_error_bar:
        agreement_type: DisagreementType = DisagreementType.HEDGED_CONSENSUS
    else:
        agreement_type = DisagreementType.UNKNOWN

    # 6. Build Result + OrchestrateProvenance.
    orchestrate_provenance = OrchestrateProvenance(
        request_id=request_id,
        provider_id=selection.provider_id,
        backend_name=selection.backend_name,
        quantum_technology=selection.quantum_technology,
        shots_used=raw.shots_used,
        latency_us=raw.latency_us,
        bundle_hash=submission.bundle_hash,
        cloud_shots_recorded=raw.cloud_shots_recorded,
        wall_clock_ms=wall_clock_ms,
        phase="phase_3_solver_control_orchestrate",
    )

    # Result is built without ProvenanceTrace -- the pipeline orchestrator
    # (Step 8) stitches solver + control + orchestrate provenances and
    # attaches the trace by replacing the field. Phase 3 Step 7 returns
    # a Result with provenance=None and lets Step 8 own composition.
    result = Result(
        value=extracted.value,
        error_bar=error_bar,
        sigma=sigma,
        agreement_type=agreement_type,
        kpi_bundle_orchestrate=extracted.kpi_bundle,
        provenance=None,
    )

    return result, orchestrate_provenance
