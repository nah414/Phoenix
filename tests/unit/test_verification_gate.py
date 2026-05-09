"""Phase 5 Step 6 -- VerificationGate orchestrator end-to-end."""

from __future__ import annotations

import phoenix  # noqa: F401  -- triggers sys.path injection for vendored modules

HBAR = 1.054571817e-34


def _qho_task(*, max_error_bar: float = 1e-3, frontier_physics: bool = False):
    from synthesis.equations.base import PhysicsContext

    from phoenix.trinity.data_model import PhysicsTask, ToleranceSpec

    ctx = PhysicsContext(
        mass_kg=9.1093837015e-31,
        length_scale_m=4e-9,
        metadata={"omega": 1e15, "n_grid_points": 200},
    )
    return PhysicsTask(
        physics_context=ctx,
        tolerance=ToleranceSpec(
            max_error_bar=max_error_bar,
            frontier_physics=frontier_physics,
        ),
        actor=None,
        request_id="test-gate",
    )


def _make_gate():
    from phoenix.router.decision import Router
    from phoenix.router.provider_registry import build_default_registry
    from phoenix.verification.gate import VerificationGate

    return VerificationGate(Router(build_default_registry()))


def test_gate_qho_default_ships_full_result_with_provenance() -> None:
    """QHO at default tolerance (1e-3) selects R3, runs Axes 1+2, returns Result."""
    from phoenix.trinity.data_model import (
        ControlProvenance,
        OrchestrateProvenance,
        ProvenanceTrace,
        Result,
        SolverProvenance,
        VerificationProvenance,
    )

    gate = _make_gate()
    result = gate.verify(_qho_task(max_error_bar=1e-3))

    assert isinstance(result, Result)
    assert isinstance(result.provenance, ProvenanceTrace)
    assert isinstance(result.provenance.solver, SolverProvenance)
    assert isinstance(result.provenance.control, ControlProvenance)
    assert isinstance(result.provenance.orchestrate, OrchestrateProvenance)
    assert isinstance(result.provenance.verification, VerificationProvenance)

    vp = result.provenance.verification
    assert vp.initial_rung == "R3_TWO_AXES"
    assert vp.final_rung == "R3_TWO_AXES"
    assert vp.promotions == 0
    assert vp.drift_state == "healthy"
    assert vp.budget_bound is False
    # Distance matrix has at least Axis 1's row + Axis 2's row.
    assert len(vp.distance_matrix) >= 2
    # Sigma is non-negative (Section 6.2 wobble formula).
    assert vp.wobble_score_sigma >= 0.0


def test_gate_loose_tolerance_selects_lower_rung() -> None:
    """max_error_bar=1e-2 selects R2 (Axis 1 only); Axis 2 skipped."""
    gate = _make_gate()
    result = gate.verify(_qho_task(max_error_bar=1e-2))
    vp = result.provenance.verification
    assert vp is not None
    # R2 means Axis 2 isn't run at all -> control provenance is None.
    assert vp.initial_rung == "R2_CROSS_PRECISION"
    assert result.provenance.control is None


def test_gate_strict_tolerance_selects_higher_rung() -> None:
    """max_error_bar=1e-5 selects R4 (three axes)."""
    gate = _make_gate()
    result = gate.verify(_qho_task(max_error_bar=1e-5))
    vp = result.provenance.verification
    assert vp is not None
    assert vp.initial_rung == "R4_THREE_AXES"


def test_gate_drift_unavailable_propagates() -> None:
    """When read_drift_state raises, the gate doesn't catch it.

    The gate imports read_drift_state via ``from ... import``; we patch
    the binding inside the gate module rather than the source module.
    """
    import pytest

    from phoenix.verification import gate as gate_module
    from phoenix.verification.drift_state import DriftStateUnavailable

    gate = _make_gate()
    original = gate_module.read_drift_state

    def raising_read():
        raise DriftStateUnavailable(
            "synthetic test failure",
            unavailable_detectors=["test_detector"],
        )

    gate_module.read_drift_state = raising_read
    try:
        with pytest.raises(DriftStateUnavailable):
            gate.verify(_qho_task())
    finally:
        gate_module.read_drift_state = original


def test_gate_distance_matrix_preserves_per_axis_rows() -> None:
    """Section 6.2 DO-NOT-COLLAPSE: per-axis rows preserved in provenance."""
    gate = _make_gate()
    result = gate.verify(_qho_task(max_error_bar=1e-3))
    vp = result.provenance.verification
    assert vp is not None
    # Axis 1 row carries multiple per-eigenvalue entries (5 from
    # n_states=5); Axis 2 row carries a single trace-distance scalar.
    rows = vp.distance_matrix
    assert any(len(row) > 1 for row in rows), "Axis 1 row should be > 1 element"
