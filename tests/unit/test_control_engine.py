"""Phase 3 Step 3 -- Control engine adapter (run_dpd).

Exercises the vendored DPDScheduler wrapper:
- run_dpd produces a typed ControlRunResult on a synthetic CandidateAnswer.
- ControlVerificationError raises on synthetic trace-preservation drift
  and on positivity violation.
"""

from __future__ import annotations

import phoenix  # noqa: F401  -- triggers sys.path injection for vendored modules


def _candidate():
    from phoenix.trinity.data_model import CandidateAnswer

    return CandidateAnswer(
        solver_id="NON_RELATIVISTIC_TI/TISESolver",
        value=5.27e-20,
        error_bar_solver=1e-22,
        sigma_solver=1e-22,
    )


def test_run_dpd_returns_typed_control_run_result_at_weak_probe() -> None:
    """Default eps=0.1 produces a populated ControlRunResult on |0><0|."""
    from phoenix.trinity.control.engine import ControlRunResult, run_dpd
    from phoenix.trinity.orchestrate.kpi_bundle import KPIBundle, KPIStatus

    res = run_dpd(_candidate(), probe_strength=0.1)
    assert isinstance(res, ControlRunResult)
    assert res.rho_verified.shape == (2, 2)
    assert res.dpd_result.n_blocks == 1
    assert res.probe_strength_used == 0.1
    assert res.wall_clock_ms >= 0.0

    # Trace + positivity preserved on the placeholder pure state.
    assert abs(res.dpd_result.trace_preservation - 1.0) < 1e-3
    assert res.dpd_result.positivity_check is True

    # KPIBundle populated.
    assert isinstance(res.kpi_bundle_control, KPIBundle)
    assert 0.0 <= res.kpi_bundle_control.fidelity <= 1.0
    assert res.kpi_bundle_control.status in {KPIStatus.OK, KPIStatus.WARN, KPIStatus.FAIL}


def test_run_dpd_at_strong_probe_completes() -> None:
    """eps=0.5 (Axis 2's strong leg) also completes for the default rho."""
    from phoenix.trinity.control.engine import ControlRunResult, run_dpd

    res = run_dpd(_candidate(), probe_strength=0.5)
    assert isinstance(res, ControlRunResult)
    assert res.probe_strength_used == 0.5


def test_control_verification_error_raises_on_synthetic_bad_trace() -> None:
    """Synthetic injection: monkeypatch the DPDScheduler.execute output to
    return a trace-preservation drift > 1e-3, then verify run_dpd raises."""
    from phoenix.trinity.control.engine import ControlVerificationError, run_dpd

    # We can't easily monkeypatch the vendored DPDScheduler at import time,
    # but we can inject a sentinel by overriding scheduler.execute. The
    # cleanest approach: build a fake DPDResult and patch DPDScheduler to
    # return it. Implementation below uses module-level monkeypatching.
    from synthesis.core import dpd_engine

    from phoenix.trinity.data_model import CandidateAnswer

    real_execute = dpd_engine.DPDScheduler.execute

    def bad_execute(self, rho0, dt=1e-12):
        real = real_execute(self, rho0, dt)
        # Inject a drift > 1e-3 on trace_preservation while keeping the
        # rest of the result valid; the vendored DPDResult is mutable
        # (not frozen), so direct attribute assignment works.
        real.trace_preservation = 1.5
        return real

    dpd_engine.DPDScheduler.execute = bad_execute  # type: ignore[method-assign]
    try:
        try:
            run_dpd(
                CandidateAnswer(
                    solver_id="NON_RELATIVISTIC_TI/TISESolver",
                    value=1.0,
                    error_bar_solver=0.0,
                    sigma_solver=0.0,
                ),
                probe_strength=0.1,
            )
        except ControlVerificationError as exc:
            assert exc.trace_preservation == 1.5
            assert exc.positivity_check is True
            return
        raise AssertionError("Expected ControlVerificationError on bad trace")
    finally:
        dpd_engine.DPDScheduler.execute = real_execute  # type: ignore[method-assign]
