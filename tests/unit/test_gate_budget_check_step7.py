"""Phase 10 Step 7 -- verification gate's pre-promotion budget check.

Exercises the ``_axis_3_would_exceed_ceiling`` decision and the
provenance field that surfaces a budget-skipped axis. Two layers:

1. **Helper-level tests** for ``_axis_3_would_exceed_ceiling``
   over a synthetic VerificationGate -- the cheapest-alt + per-solve
   ceiling arithmetic without needing a full solve pipeline.
2. **Field-level tests** that
   :class:`VerificationProvenance.budget_bound_skipped_axis` exists,
   defaults to None, and accepts the canonical string token.
"""

from __future__ import annotations

import pytest

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix.trinity.data_model import (
    OrchestrateProvenance,
    PhysicsTask,
    ToleranceSpec,
    VerificationProvenance,
)
from phoenix.verification.gate import VerificationGate
from synthesis.equations.base import PhysicsContext


# ---------------------------------------------------------------------
# VerificationProvenance field
# ---------------------------------------------------------------------


class TestProvenanceField:
    def test_default_is_none(self) -> None:
        prov = VerificationProvenance(
            request_id="tx",
            initial_rung="R3_TWO_AXES",
            final_rung="R3_TWO_AXES",
        )
        assert prov.budget_bound_skipped_axis is None

    def test_accepts_canonical_token(self) -> None:
        prov = VerificationProvenance(
            request_id="tx",
            initial_rung="R3_TWO_AXES",
            final_rung="R3_TWO_AXES",
            budget_bound=True,
            budget_bound_skipped_axis="cross_provider_axis",
        )
        assert prov.budget_bound is True
        assert prov.budget_bound_skipped_axis == "cross_provider_axis"


# ---------------------------------------------------------------------
# Helper: _axis_3_would_exceed_ceiling
# ---------------------------------------------------------------------


def _build_task(mode: str = "default") -> PhysicsTask:
    return PhysicsTask(
        request_id="tx-test",
        actor=None,
        physics_context=PhysicsContext(
            mass_kg=9.1093837015e-31,
            length_scale_m=4e-9,
            metadata={"omega": 1e15, "n_grid_points": 200},
        ),
        tolerance=ToleranceSpec(max_error_bar=1e-3, reproducibility_mode=mode),
        metadata={},
    )


def _build_orch_prov(provider_id: str) -> OrchestrateProvenance:
    return OrchestrateProvenance(
        request_id="tx-test",
        provider_id=provider_id,
        backend_name="test",
        quantum_technology="classical_simulator",
        shots_used=1024,
        latency_us=10.0,
        bundle_hash="abc",
        cloud_shots_recorded=False,
        wall_clock_ms=1.0,
        phase="phase_3_solver_control_orchestrate",
    )


class TestAxis3CeilingPredicate:
    @pytest.fixture
    def gate(self) -> VerificationGate:
        """A bare gate; we only call _axis_3_would_exceed_ceiling, no full solve."""
        from phoenix.router.decision import Router
        from phoenix.router.provider_registry import build_default_registry

        return VerificationGate(router=Router(registry=build_default_registry()))

    def test_local_sim_primary_default_mode_within_ceiling(self, gate: VerificationGate) -> None:
        """Primary = local sim ($0); cheapest alt = $0 (also local sim
        is filtered out as primary, so cheapest non-primary = $0.66 ibm).
        Per-solve ceiling default mode = $5. Total < $5 -> proceed.
        """
        task = _build_task(mode="default")
        prov = _build_orch_prov("phoenix.local_simulator")
        assert gate._axis_3_would_exceed_ceiling(task, None, prov) is False

    def test_expensive_primary_trips_default_ceiling(self, gate: VerificationGate) -> None:
        """Primary = ionq ($10.27); cheapest non-primary alt = $0
        (local simulator). $10.27 + $0 > $5 default -> SKIP.
        """
        task = _build_task(mode="default")
        prov = _build_orch_prov("ionq")
        assert gate._axis_3_would_exceed_ceiling(task, None, prov) is True

    def test_strict_mode_raises_ceiling_above_ionq(self, gate: VerificationGate) -> None:
        """strict-mode ceiling = $25; ionq + $0 alt = $10.27 < $25 -> proceed."""
        task = _build_task(mode="strict")
        prov = _build_orch_prov("ionq")
        assert gate._axis_3_would_exceed_ceiling(task, None, prov) is False

    def test_lookup_failure_falls_back_to_proceed(self, gate: VerificationGate) -> None:
        """A broken provenance (no provider_id attr) -> proceed (False).

        Defense-in-depth: the budget pre-check must not block routing
        on a lookup failure; post-solve accounting fires anyway.
        """
        task = _build_task(mode="default")

        class _BadProv:
            pass  # no provider_id

        assert gate._axis_3_would_exceed_ceiling(task, None, _BadProv()) is False
