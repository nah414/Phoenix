"""Phase 4 Step 3 -- ProviderRegistry + health states."""

from __future__ import annotations

import phoenix  # noqa: F401  -- triggers sys.path injection for vendored modules


def test_default_registry_has_4_providers() -> None:
    from phoenix.router.provider_registry import build_default_registry

    reg = build_default_registry()
    assert len(reg) == 4
    ids = {e.provider_id for e in reg.all_entries()}
    assert ids == {"phoenix.local_simulator", "ibm_quantum", "aws_braket", "ionq"}


def test_health_state_round_trip() -> None:
    from phoenix.router.provider_registry import ProviderHealth, build_default_registry

    reg = build_default_registry()
    assert all(e.health is ProviderHealth.HEALTHY for e in reg.all_entries())

    reg.mark_degraded("ibm_quantum", degraded_until_utc="2026-05-08T20:00:00Z")
    assert reg.get("ibm_quantum").health is ProviderHealth.DEGRADED  # type: ignore[union-attr]
    assert "ibm_quantum" not in {e.provider_id for e in reg.healthy_entries()}

    reg.mark_offline("aws_braket")
    assert reg.get("aws_braket").health is ProviderHealth.OFFLINE  # type: ignore[union-attr]

    reg.mark_healthy("ibm_quantum")
    assert reg.get("ibm_quantum").health is ProviderHealth.HEALTHY  # type: ignore[union-attr]


def test_unknown_provider_id_lookups_return_none() -> None:
    from phoenix.router.provider_registry import build_default_registry

    reg = build_default_registry()
    assert reg.get("not_registered") is None
    assert "not_registered" not in reg
    assert "ibm_quantum" in reg


def test_health_mutation_on_unknown_raises() -> None:
    import pytest

    from phoenix.router.provider_registry import build_default_registry

    reg = build_default_registry()
    with pytest.raises(KeyError):
        reg.mark_degraded("not_registered")
