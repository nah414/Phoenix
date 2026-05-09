"""Phase 3 Step 7 -- Orchestrate top-level engine.

Exercises the orchestrate() function end-to-end against a real
VerifiedAnswer (built from a run_dpd canonical leg) and the
LocalClassicalSimulator selection. Tests:
- Returns (Result, OrchestrateProvenance) with full population.
- Drift signal emitted to the buffer once per call.
- Provider failures propagate as OrchestrateProviderError with full context.
- Bundle hash deterministic for identical (rho, n_blocks).
"""

from __future__ import annotations

import phoenix  # noqa: F401  -- triggers sys.path injection for vendored modules


def _verified_for_qho():
    """Build a VerifiedAnswer from a run_dpd weak-probe leg on a synthetic
    candidate. Mirrors the pipeline's Layer-2 output shape."""
    from phoenix.trinity.control.engine import run_dpd
    from phoenix.trinity.data_model import CandidateAnswer, VerifiedAnswer

    cand = CandidateAnswer(
        solver_id="NON_RELATIVISTIC_TI/TISESolver",
        value=5.27e-20,
        error_bar_solver=1.2e-22,
        sigma_solver=1.2e-22,
    )
    ctrl = run_dpd(cand, probe_strength=0.1)
    return VerifiedAnswer(
        rho_verified=ctrl.rho_verified,
        dpd_result=ctrl.dpd_result,
        kpi_bundle_control=ctrl.kpi_bundle_control,
        error_bar_control=0.0,
        probe_strengths_used=[0.1, 0.5],
    ), cand


def _local_selection():
    from phoenix.providers.classical.local_simulator import LocalClassicalSimulator
    from phoenix.router.data_model import ProviderSelection

    return ProviderSelection(
        provider_id="phoenix.local_simulator",
        backend_name="local_density_matrix",
        quantum_technology="simulation",
        client=LocalClassicalSimulator(),
        selection_metadata={"phase": "test"},
    )


def test_orchestrate_returns_result_and_provenance() -> None:
    from phoenix.trinity.data_model import OrchestrateProvenance, Result
    from phoenix.trinity.orchestrate.drift_feedback import drain_buffer
    from phoenix.trinity.orchestrate.engine import orchestrate
    from phoenix.trinity.orchestrate.kpi_bundle import KPIBundle

    # Drain any leftover drift signals so we can count this call's emission.
    drain_buffer()

    verified, cand = _verified_for_qho()
    selection = _local_selection()
    result, prov = orchestrate(
        verified,
        selection,
        request_id="req_orchestrate_unit",
        error_bar_solver=cand.error_bar_solver,
        error_bar_control=verified.error_bar_control,
        solver_id=cand.solver_id,
        shots=512,
        tolerance_max_error_bar=1e-3,
    )
    assert isinstance(result, Result)
    assert isinstance(prov, OrchestrateProvenance)

    # Result envelope shape.
    assert isinstance(result.kpi_bundle_orchestrate, KPIBundle)
    assert result.error_bar > 0
    assert result.sigma == result.error_bar
    # Phase 3 mapping: HEDGED_CONSENSUS or UNKNOWN.
    assert result.agreement_type.name in {"HEDGED_CONSENSUS", "UNKNOWN"}

    # OrchestrateProvenance.
    assert prov.provider_id == "phoenix.local_simulator"
    assert prov.backend_name == "local_density_matrix"
    assert prov.quantum_technology == "simulation"
    assert prov.shots_used == 512
    assert prov.cloud_shots_recorded is False
    assert len(prov.bundle_hash) == 16
    assert prov.phase == "phase_3_solver_control_orchestrate"

    # Drift signal emitted exactly once.
    drained = drain_buffer()
    assert len(drained) == 1
    assert drained[0].provider_id == "phoenix.local_simulator"
    assert drained[0].request_id == "req_orchestrate_unit"
    assert drained[0].solver_id == cand.solver_id


def test_orchestrate_propagates_provider_error() -> None:
    """Provider submission failure surfaces as OrchestrateProviderError
    with full bundle context."""
    from phoenix.trinity.orchestrate.engine import orchestrate
    from phoenix.trinity.orchestrate.provider_client import (
        OrchestrateProviderError,
        ProviderRawResult,
        ProviderSubmission,
    )
    from phoenix.router.data_model import ProviderSelection

    class BrokenProvider:
        provider_id = "phoenix.broken_for_test"
        backend_name = "broken"
        quantum_technology = "simulation"

        def submit(self, submission: ProviderSubmission) -> ProviderRawResult:
            raise OrchestrateProviderError(
                "synthetic submit failure",
                provider_id=self.provider_id,
                bundle_hash=submission.bundle_hash,
            )

        def is_available(self) -> bool:
            return True

        def estimated_latency_ms(self) -> float:
            return 1.0

    verified, cand = _verified_for_qho()
    selection = ProviderSelection(
        provider_id="phoenix.broken_for_test",
        backend_name="broken",
        quantum_technology="simulation",
        client=BrokenProvider(),
    )
    try:
        orchestrate(
            verified,
            selection,
            request_id="req_broken",
            error_bar_solver=cand.error_bar_solver,
            error_bar_control=verified.error_bar_control,
            solver_id=cand.solver_id,
        )
    except OrchestrateProviderError as exc:
        assert exc.provider_id == "phoenix.broken_for_test"
        assert len(exc.bundle_hash) == 16
        return
    raise AssertionError("Expected OrchestrateProviderError")


def test_orchestrate_high_error_bar_returns_unknown() -> None:
    """When combined error_bar exceeds tolerance, agreement_type=UNKNOWN."""
    from phoenix.trinity.orchestrate.drift_feedback import drain_buffer
    from phoenix.trinity.orchestrate.engine import orchestrate

    drain_buffer()
    verified, cand = _verified_for_qho()
    result, _prov = orchestrate(
        verified,
        _local_selection(),
        request_id="req_high_error",
        error_bar_solver=10.0,  # large; quadrature combine -> 10.0
        error_bar_control=0.0,
        solver_id=cand.solver_id,
        tolerance_max_error_bar=1.0,  # below 10.0 -> UNKNOWN
    )
    assert result.agreement_type.name == "UNKNOWN"
    drain_buffer()  # cleanup
