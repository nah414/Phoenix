"""Phase 4 Step 7 -- Router decision algorithm (seven stages)."""

from __future__ import annotations

import phoenix  # noqa: F401  -- triggers sys.path injection for vendored modules


def _qho_task(*, frontier_physics: bool = False, regime_hint: str | None = None):
    from synthesis.equations.base import PhysicsContext

    from phoenix.trinity.data_model import PhysicsTask, ToleranceSpec

    metadata = {}
    if regime_hint is not None:
        metadata["regime_hint"] = regime_hint
    ctx = PhysicsContext(
        mass_kg=9.1093837015e-31,
        length_scale_m=4e-9,
        metadata={"omega": 1e15, "n_grid_points": 200},
    )
    return PhysicsTask(
        physics_context=ctx,
        tolerance=ToleranceSpec(frontier_physics=frontier_physics),
        actor=None,
        request_id="test-router-decision",
        metadata=metadata,
    )


def _make_router():
    from phoenix.router.decision import Router
    from phoenix.router.provider_registry import build_default_registry

    return Router(build_default_registry())


def test_router_decide_qho_picks_local_simulator() -> None:
    """QHO with default policy: local simulator wins (highest fidelity, zero cost)."""
    from phoenix.router.data_model import RoutingDecision, RoutingRequest

    router = _make_router()
    decision = router.decide(RoutingRequest(task=_qho_task()))
    assert isinstance(decision, RoutingDecision)
    assert decision.primary.provider_id == "phoenix.local_simulator"
    # Alternates filtered by equivalence: no superconducting/trapped_ion is
    # equivalent to "simulation"; alternates list is empty.
    assert decision.alternates == []
    assert decision.estimated_fidelity == 1.0
    assert decision.estimated_cost_usd == 0.0


def test_router_decide_excluding_local_picks_braket() -> None:
    """Excluding local sim + cost ceiling $1: Braket wins (cheapest superconducting)."""
    from phoenix.router.data_model import RoutingRequest

    router = _make_router()
    decision = router.decide(
        RoutingRequest(
            task=_qho_task(),
            excluded_providers=["phoenix.local_simulator"],
            cost_ceiling_usd=1.00,
        )
    )
    assert decision.primary.provider_id == "aws_braket"
    # IBM is equivalent (same superconducting tech, fidelity within 10%) so
    # it's an alternate.
    alternate_ids = [a.provider_id for a in decision.alternates]
    assert "ibm_quantum" in alternate_ids


def test_router_decide_cost_ceiling_too_low_raises() -> None:
    """Tight cost ceiling rejects all candidates -> CostCeilingExceeded."""
    import pytest

    from phoenix.router.data_model import RoutingRequest
    from phoenix.router.errors import CostCeilingExceeded

    router = _make_router()
    with pytest.raises(CostCeilingExceeded) as exc_info:
        router.decide(
            RoutingRequest(
                task=_qho_task(),
                excluded_providers=["phoenix.local_simulator"],
                cost_ceiling_usd=0.50,
            )
        )
    # Cheapest violator should be Braket ($0.66) since IonQ is much more.
    assert exc_info.value.provider_id == "aws_braket"
    assert exc_info.value.cheapest_estimate_usd > 0.50


def test_router_frontier_physics_defense_in_depth_refuses() -> None:
    """SCG regime_hint without frontier_physics permission -> refused at Stage 4."""
    import pytest

    from phoenix.router.data_model import RoutingRequest
    from phoenix.trinity.solver.engine import FrontierPhysicsRefused

    router = _make_router()
    task = _qho_task(frontier_physics=False, regime_hint="SEMICLASSICAL_GRAVITY")
    with pytest.raises(FrontierPhysicsRefused) as exc_info:
        router.decide(RoutingRequest(task=task))
    assert exc_info.value.regime_name == "SEMICLASSICAL_GRAVITY"


def test_router_health_filter_drops_degraded() -> None:
    """Stage 3 drops DEGRADED providers; ranking proceeds with the remainder."""
    from phoenix.router.data_model import RoutingRequest

    router = _make_router()
    # Force IBM degraded (would normally be picked over IonQ on cost, but we
    # exclude local sim so the test exercises the cloud subset).
    router.registry.mark_degraded("ibm_quantum")
    decision = router.decide(
        RoutingRequest(
            task=_qho_task(),
            excluded_providers=["phoenix.local_simulator"],
            cost_ceiling_usd=20.00,
        )
    )
    # IBM dropped; Braket cheaper than IonQ -> primary.
    assert decision.primary.provider_id == "aws_braket"
    assert "ibm_quantum" not in [a.provider_id for a in decision.alternates]


def test_router_preferred_providers_boost() -> None:
    """preferred_providers adds +0.1 to the score; pushes IonQ above local sim."""
    from phoenix.router.data_model import RoutingRequest

    router = _make_router()
    decision = router.decide(
        RoutingRequest(
            task=_qho_task(),
            preferred_providers=["ionq"],
        )
    )
    assert decision.primary.provider_id == "ionq"


def test_router_decision_provenance_contains_per_stage_filters() -> None:
    """Stage 7: decision_provenance records per-stage rationale."""
    from phoenix.router.data_model import RoutingRequest

    router = _make_router()
    decision = router.decide(RoutingRequest(task=_qho_task()))
    prov = decision.decision_provenance
    assert "stages_applied" in prov
    stage_names = {s.get("stage") for s in prov["stages_applied"]}
    assert "modality_eligibility" in stage_names
    assert "user_policy" in stage_names
    assert "provider_health" in stage_names
    assert "ranking" in stage_names
    assert "ranking_weights" in prov
    assert prov["pricing_stale"] is False
    assert prov["candidates_considered"] == 4
