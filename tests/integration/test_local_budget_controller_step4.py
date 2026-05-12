"""Phase 10 Step 4 -- LocalJobBudgetController integration tests.

Exercises the default :class:`JobBudgetController` impl against a
fresh SQLite state backend + a real
:class:`PermissionsRegistry`. Tests cover:

- Happy-path allowance (under all three ceilings).
- Per-solve denial (single solve > $5 default).
- Per-actor-24h denial (accumulated 24h spend exceeds $50 tier
  cap).
- Per-org-24h denial (org spend cap).
- Admin-tier no-cap path.
- ``record_solve_cost`` writes flow through to the 24h-window
  queries.
- Admin override (Step 8 prerequisite) raises ceiling correctly.
- ``org_id`` resolution per locked OPEN-5 (actor.org_id if
  present, else None means no per-org enforcement).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix._internal.cloud_seams import (
    BudgetDecision,
    LocalJobBudgetController,
)
from phoenix.safety.permissions import ActorPermissions, PermissionsRegistry
from phoenix.state.sqlite_backend import SQLiteStateBackend


@dataclass
class _FakeActor:
    """Minimal Actor stand-in for budget controller tests.

    The vendored Actor carries name/identity_fingerprint/issued_at/
    signature; we only need ``name`` (and optionally ``org_id``) for
    budget-controller logic.
    """

    name: str
    org_id: str | None = None


@pytest.fixture
def backend(tmp_path: Path) -> Iterator[SQLiteStateBackend]:
    db_path = tmp_path / "phoenix_state.db"
    b = SQLiteStateBackend(db_path=db_path)
    try:
        yield b
    finally:
        b.close()


@pytest.fixture
def registry(tmp_path: Path) -> PermissionsRegistry:
    """Permissions registry pinned to a tmp keystore (no test contamination)."""
    return PermissionsRegistry(path=tmp_path / "perms.json")


@pytest.fixture
def controller(
    backend: SQLiteStateBackend, registry: PermissionsRegistry
) -> LocalJobBudgetController:
    """Controller with explicit backend + registry + fixed clock."""
    return LocalJobBudgetController(
        state_backend=backend,
        permissions_registry=registry,
        clock=lambda: 1_700_000_000.0,
    )


# ---------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------


class TestAllowedPath:
    def test_under_all_ceilings(
        self, controller: LocalJobBudgetController, registry: PermissionsRegistry
    ) -> None:
        registry.set("bob", ActorPermissions(rate_limit_tier="default"))
        decision = controller.check_solve_budget(
            actor=_FakeActor(name="bob"),
            estimated_cost_usd=1.50,
            reproducibility_mode="default",
        )
        assert decision.allowed is True
        # default per-solve = $5; remaining = $5 - $1.50 = $3.50
        assert decision.remaining_usd == pytest.approx(3.50)
        assert decision.ceiling_applied_usd == pytest.approx(5.0)

    def test_strict_mode_higher_ceiling(
        self, controller: LocalJobBudgetController, registry: PermissionsRegistry
    ) -> None:
        """strict mode raises per-solve to $25, so $20 is allowed."""
        registry.set("bob", ActorPermissions(rate_limit_tier="default"))
        decision = controller.check_solve_budget(
            actor=_FakeActor(name="bob"),
            estimated_cost_usd=20.0,
            reproducibility_mode="strict",
        )
        assert decision.allowed is True
        assert decision.ceiling_applied_usd == pytest.approx(25.0)

    def test_admin_tier_no_actor_24h_cap(
        self, controller: LocalJobBudgetController, registry: PermissionsRegistry
    ) -> None:
        """admin-tier has per_actor_24h = None; record big history, still allowed."""
        registry.set("ash", ActorPermissions(rate_limit_tier="admin", is_admin=True))
        # Pre-load $9000 of historical spend
        controller.record_solve_cost(
            actor=_FakeActor(name="ash"),
            request_id="tx-history",
            actual_cost_usd=9000.0,
            provenance={"reproducibility_mode": "replay"},
        )
        decision = controller.check_solve_budget(
            actor=_FakeActor(name="ash"),
            estimated_cost_usd=20.0,
            reproducibility_mode="replay",
        )
        # Per-actor cap is None, so the $9020 lifetime spend doesn't matter
        assert decision.allowed is True


# ---------------------------------------------------------------------
# Per-solve denials
# ---------------------------------------------------------------------


class TestPerSolveDenials:
    def test_default_mode_exceeds_5_dollar_cap(
        self, controller: LocalJobBudgetController, registry: PermissionsRegistry
    ) -> None:
        registry.set("bob", ActorPermissions(rate_limit_tier="default"))
        decision = controller.check_solve_budget(
            actor=_FakeActor(name="bob"),
            estimated_cost_usd=10.0,
            reproducibility_mode="default",
        )
        assert decision.allowed is False
        assert "per_solve" in decision.rationale
        assert decision.ceiling_applied_usd == pytest.approx(5.0)

    def test_strict_mode_exceeds_25_dollar_cap(
        self, controller: LocalJobBudgetController, registry: PermissionsRegistry
    ) -> None:
        registry.set("bob", ActorPermissions(rate_limit_tier="default"))
        decision = controller.check_solve_budget(
            actor=_FakeActor(name="bob"),
            estimated_cost_usd=30.0,
            reproducibility_mode="strict",
        )
        assert decision.allowed is False
        assert "per_solve" in decision.rationale


# ---------------------------------------------------------------------
# Per-actor-24h denials
# ---------------------------------------------------------------------


class TestPerActor24hDenials:
    def test_default_tier_exceeds_50_dollar_24h_cap(
        self, controller: LocalJobBudgetController, registry: PermissionsRegistry
    ) -> None:
        registry.set("bob", ActorPermissions(rate_limit_tier="default"))
        # Record $48 of recent spend
        controller.record_solve_cost(
            actor=_FakeActor(name="bob"),
            request_id="tx-1",
            actual_cost_usd=48.0,
            provenance={"reproducibility_mode": "default"},
        )
        # A new $3 solve would push 24h spend to $51 (> $50)
        decision = controller.check_solve_budget(
            actor=_FakeActor(name="bob"),
            estimated_cost_usd=3.0,
            reproducibility_mode="default",
        )
        assert decision.allowed is False
        assert "per_actor_24h" in decision.rationale

    def test_elevated_tier_500_dollar_cap(
        self, controller: LocalJobBudgetController, registry: PermissionsRegistry
    ) -> None:
        """elevated tier has $500/24h cap."""
        registry.set("frank", ActorPermissions(rate_limit_tier="elevated"))
        controller.record_solve_cost(
            actor=_FakeActor(name="frank"),
            request_id="tx-bigspend",
            actual_cost_usd=499.0,
            provenance={"reproducibility_mode": "default"},
        )
        # $499 + $2 = $501 > $500
        decision = controller.check_solve_budget(
            actor=_FakeActor(name="frank"),
            estimated_cost_usd=2.0,
            reproducibility_mode="default",
        )
        assert decision.allowed is False
        assert "per_actor_24h" in decision.rationale


# ---------------------------------------------------------------------
# Per-org-24h denials
# ---------------------------------------------------------------------


class TestPerOrg24hDenials:
    def test_org_spend_exceeds_2000_cap(
        self, controller: LocalJobBudgetController, registry: PermissionsRegistry
    ) -> None:
        """Alice + a flood of teammates push the org past $2000 even
        though no single actor is over their own per-actor cap.

        Carol/dave/erin are admin-tier (no per-actor cap) so they can
        rack up org-level spend without tripping per-actor first.
        Alice (elevated tier) then tries to submit; her own 24h spend
        stays well under $500 but the org spend is at $2099, so the
        new $5 solve pushes to $2104 > $2000 per-org cap.
        """
        registry.set("alice", ActorPermissions(rate_limit_tier="elevated"))
        for name, amount in [("carol", 1000.0), ("dave", 700.0), ("erin", 399.0)]:
            registry.set(name, ActorPermissions(rate_limit_tier="admin"))
            controller.record_solve_cost(
                actor=_FakeActor(name=name, org_id="acme-corp"),
                request_id=f"tx-{name}",
                actual_cost_usd=amount,
                provenance={"reproducibility_mode": "default"},
            )
        # Org has $2099 spend; alice has $0 personal spend.
        decision = controller.check_solve_budget(
            actor=_FakeActor(name="alice", org_id="acme-corp"),
            estimated_cost_usd=5.0,
            reproducibility_mode="default",
        )
        assert decision.allowed is False
        assert "per_org_24h" in decision.rationale

    def test_no_org_skips_org_check(
        self, controller: LocalJobBudgetController, registry: PermissionsRegistry
    ) -> None:
        """Solo developer (no org_id) doesn't hit per-org check at all."""
        registry.set("solo", ActorPermissions(rate_limit_tier="elevated"))
        # Even with a massive history, no org_id means org check skipped
        controller.record_solve_cost(
            actor=_FakeActor(name="solo"),  # no org_id
            request_id="tx-solo",
            actual_cost_usd=400.0,
            provenance={"reproducibility_mode": "default"},
        )
        decision = controller.check_solve_budget(
            actor=_FakeActor(name="solo"),
            estimated_cost_usd=2.0,
            reproducibility_mode="default",
        )
        assert decision.allowed is True


# ---------------------------------------------------------------------
# Admin overrides
# ---------------------------------------------------------------------


class TestAdminOverrides:
    def test_override_raises_per_solve_ceiling(
        self,
        controller: LocalJobBudgetController,
        backend: SQLiteStateBackend,
        registry: PermissionsRegistry,
    ) -> None:
        """An admin grants bob a $100 per-solve override; $50 solve now passes."""
        registry.set("bob", ActorPermissions(rate_limit_tier="default"))
        backend.insert_budget_override(
            actor_name="bob",
            scope="per_solve",
            new_ceiling_usd=100.0,
            expires_at_unix=1_700_000_000.0 + 3600.0,  # 1h from clock
            created_by="ash",
        )
        decision = controller.check_solve_budget(
            actor=_FakeActor(name="bob"),
            estimated_cost_usd=50.0,
            reproducibility_mode="default",
        )
        assert decision.allowed is True
        # Ceiling applied was the override
        assert decision.ceiling_applied_usd == pytest.approx(100.0)

    def test_override_only_raises_never_lowers(
        self,
        controller: LocalJobBudgetController,
        backend: SQLiteStateBackend,
        registry: PermissionsRegistry,
    ) -> None:
        """Per Section 4.7: override can only INCREASE the ceiling."""
        registry.set("bob", ActorPermissions(rate_limit_tier="default"))
        # Admin tries to set a $1 ceiling (which would be a lowering)
        backend.insert_budget_override(
            actor_name="bob",
            scope="per_solve",
            new_ceiling_usd=1.0,
            expires_at_unix=1_700_000_000.0 + 3600.0,
            created_by="ash",
        )
        # $3 solve should still be allowed (default $5 wins over override $1)
        decision = controller.check_solve_budget(
            actor=_FakeActor(name="bob"),
            estimated_cost_usd=3.0,
            reproducibility_mode="default",
        )
        assert decision.allowed is True
        assert decision.ceiling_applied_usd == pytest.approx(5.0)

    def test_expired_override_ignored(
        self,
        controller: LocalJobBudgetController,
        backend: SQLiteStateBackend,
        registry: PermissionsRegistry,
    ) -> None:
        """An expired override is invisible to the controller."""
        registry.set("bob", ActorPermissions(rate_limit_tier="default"))
        backend.insert_budget_override(
            actor_name="bob",
            scope="per_solve",
            new_ceiling_usd=1000.0,
            expires_at_unix=1_700_000_000.0 - 1.0,  # already expired
            created_by="ash",
        )
        decision = controller.check_solve_budget(
            actor=_FakeActor(name="bob"),
            estimated_cost_usd=10.0,  # > $5 default, would need override
            reproducibility_mode="default",
        )
        assert decision.allowed is False
        # default ceiling, not the expired override's $1000
        assert decision.ceiling_applied_usd == pytest.approx(5.0)


# ---------------------------------------------------------------------
# Decision shape sanity
# ---------------------------------------------------------------------


def test_decision_carries_full_structure(
    controller: LocalJobBudgetController, registry: PermissionsRegistry
) -> None:
    registry.set("bob", ActorPermissions(rate_limit_tier="default"))
    decision = controller.check_solve_budget(
        actor=_FakeActor(name="bob"),
        estimated_cost_usd=2.0,
        reproducibility_mode="default",
    )
    assert isinstance(decision, BudgetDecision)
    assert isinstance(decision.allowed, bool)
    assert isinstance(decision.rationale, str)
    assert isinstance(decision.remaining_usd, float)
    assert isinstance(decision.ceiling_applied_usd, float)


def test_record_solve_cost_round_trips_to_24h_query(
    controller: LocalJobBudgetController,
    backend: SQLiteStateBackend,
    registry: PermissionsRegistry,
) -> None:
    """End-to-end: record_solve_cost writes flow into 24h-window queries."""
    registry.set("bob", ActorPermissions(rate_limit_tier="elevated"))
    controller.record_solve_cost(
        actor=_FakeActor(name="bob", org_id="acme"),
        request_id="tx-round-trip",
        actual_cost_usd=7.25,
        provenance={"reproducibility_mode": "strict"},
    )
    assert backend.query_actor_24h_spend("bob", as_of_unix=1_700_000_000.0) == pytest.approx(7.25)
    assert backend.query_org_24h_spend("acme", as_of_unix=1_700_000_000.0) == pytest.approx(7.25)
