"""Phase 10 Step 3 -- cost-ceiling defaults + resolver tests.

Covers :func:`resolve_ceilings` over every (mode, tier) combination
in the Section 4.7 default table, plus the three env-var override
paths and their invalid-value tolerance.
"""

from __future__ import annotations

import pytest

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix.safety.cost_ceilings import CeilingTriple, resolve_ceilings


# ---------------------------------------------------------------------
# Section 4.7 default table
# ---------------------------------------------------------------------


class TestDefaultPerSolveLadder:
    @pytest.mark.parametrize(
        "mode,expected_ceiling",
        [
            ("default", 5.0),
            ("strict", 25.0),
            ("replay", 50.0),
        ],
    )
    def test_per_solve_default_ladder(self, mode: str, expected_ceiling: float) -> None:
        triple = resolve_ceilings(reproducibility_mode=mode, actor_tier="default", env={})
        assert triple.per_solve_usd == expected_ceiling


class TestDefaultPerActor24h:
    @pytest.mark.parametrize(
        "tier,expected",
        [
            ("default", 50.0),
            ("elevated", 500.0),
            ("admin", None),
        ],
    )
    def test_per_actor_default_ladder(self, tier: str, expected: float | None) -> None:
        triple = resolve_ceilings(reproducibility_mode="default", actor_tier=tier, env={})
        assert triple.per_actor_24h_usd == expected


class TestDefaultPerOrg:
    def test_per_org_default(self) -> None:
        triple = resolve_ceilings(reproducibility_mode="default", actor_tier="default", env={})
        assert triple.per_org_24h_usd == 2000.0


# ---------------------------------------------------------------------
# Unknown mode / tier fallbacks
# ---------------------------------------------------------------------


class TestUnknownFallbacks:
    def test_unknown_mode_falls_back_to_strictest(self) -> None:
        triple = resolve_ceilings(reproducibility_mode="bogus", actor_tier="default", env={})
        # Strictest per-solve ceiling is $5 (default mode)
        assert triple.per_solve_usd == 5.0

    def test_unknown_tier_falls_back_to_default_tier(self) -> None:
        triple = resolve_ceilings(reproducibility_mode="default", actor_tier="bogus", env={})
        # default-tier per-actor cap = $50
        assert triple.per_actor_24h_usd == 50.0


# ---------------------------------------------------------------------
# Env-var overrides
# ---------------------------------------------------------------------


class TestEnvOverrides:
    def test_per_solve_env_override(self) -> None:
        triple = resolve_ceilings(
            reproducibility_mode="default",
            actor_tier="default",
            env={"PHOENIX_PER_SOLVE_CEILING_USD": "1.50"},
        )
        assert triple.per_solve_usd == 1.50

    def test_per_actor_env_override(self) -> None:
        triple = resolve_ceilings(
            reproducibility_mode="default",
            actor_tier="default",
            env={"PHOENIX_PER_ACTOR_24H_CEILING_USD": "999.99"},
        )
        assert triple.per_actor_24h_usd == 999.99

    def test_per_org_env_override(self) -> None:
        triple = resolve_ceilings(
            reproducibility_mode="default",
            actor_tier="default",
            env={"PHOENIX_PER_ORG_24H_CEILING_USD": "10000.0"},
        )
        assert triple.per_org_24h_usd == 10000.0

    def test_admin_tier_env_override_replaces_none(self) -> None:
        """admin-tier defaults to None (no cap); env override CAN re-cap admin."""
        triple = resolve_ceilings(
            reproducibility_mode="default",
            actor_tier="admin",
            env={"PHOENIX_PER_ACTOR_24H_CEILING_USD": "5000.0"},
        )
        assert triple.per_actor_24h_usd == 5000.0

    def test_invalid_env_value_falls_back_to_default(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Garbage env-var values log a warning + don't crash."""
        triple = resolve_ceilings(
            reproducibility_mode="default",
            actor_tier="default",
            env={"PHOENIX_PER_SOLVE_CEILING_USD": "not-a-number"},
        )
        assert triple.per_solve_usd == 5.0  # default
        # Logged a warning
        assert any("PHOENIX_PER_SOLVE_CEILING_USD" in r.message for r in caplog.records)

    def test_negative_env_value_falls_back_to_default(self) -> None:
        triple = resolve_ceilings(
            reproducibility_mode="default",
            actor_tier="default",
            env={"PHOENIX_PER_SOLVE_CEILING_USD": "-100"},
        )
        assert triple.per_solve_usd == 5.0  # default; negative rejected

    def test_zero_env_value_falls_back_to_default(self) -> None:
        triple = resolve_ceilings(
            reproducibility_mode="default",
            actor_tier="default",
            env={"PHOENIX_PER_SOLVE_CEILING_USD": "0"},
        )
        assert triple.per_solve_usd == 5.0  # default; zero rejected

    def test_empty_env_value_uses_default(self) -> None:
        triple = resolve_ceilings(
            reproducibility_mode="strict",
            actor_tier="default",
            env={"PHOENIX_PER_SOLVE_CEILING_USD": ""},
        )
        assert triple.per_solve_usd == 25.0  # strict default


# ---------------------------------------------------------------------
# Triple shape
# ---------------------------------------------------------------------


def test_ceiling_triple_is_frozen() -> None:
    triple = CeilingTriple(per_solve_usd=5.0, per_actor_24h_usd=50.0, per_org_24h_usd=2000.0)
    with pytest.raises(Exception):
        triple.per_solve_usd = 999.0  # type: ignore[misc]


def test_resolve_uses_os_environ_when_env_not_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default env source is os.environ."""
    monkeypatch.setenv("PHOENIX_PER_SOLVE_CEILING_USD", "7.50")
    triple = resolve_ceilings(reproducibility_mode="default", actor_tier="default")
    assert triple.per_solve_usd == 7.50
