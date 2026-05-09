"""Phase 4 Steps 4 + 5 + 6 -- pricing + intelligence + equivalence."""

from __future__ import annotations

import phoenix  # noqa: F401  -- triggers sys.path injection for vendored modules


# ----- Pricing -----


def test_pricing_loads_and_has_all_4_providers() -> None:
    from phoenix.router.pricing import load_pricing

    data = load_pricing()
    assert "providers" in data
    provider_ids = set(data["providers"].keys())
    assert provider_ids == {"phoenix.local_simulator", "ibm_quantum", "aws_braket", "ionq"}


def test_estimate_cost_usd_returns_documented_estimates() -> None:
    from phoenix.router.pricing import estimate_cost_usd

    assert estimate_cost_usd("phoenix.local_simulator") == 0.0
    assert estimate_cost_usd("ibm_quantum") > 0.0
    assert estimate_cost_usd("aws_braket") > 0.0
    assert estimate_cost_usd("ionq") > 0.0
    # Unknown provider returns 0.0 rather than raising.
    assert estimate_cost_usd("not_in_table") == 0.0


def test_pricing_staleness_check_on_fresh_data() -> None:
    from phoenix.router.pricing import is_pricing_stale

    stale, days = is_pricing_stale()
    assert stale is False
    assert days >= 0


# ----- Intelligence layer -----


def test_intelligence_estimates_for_each_default_provider() -> None:
    """Source A (HardwareParams) produces real differentiation across modalities."""
    from phoenix.router.intelligence import estimate_all
    from phoenix.router.provider_registry import build_default_registry

    reg = build_default_registry()
    estimates = {e.provider_id: estimate_all(e) for e in reg.all_entries()}

    # Local sim: perfect fidelity, sub-millisecond, free.
    fid_sim, lat_sim, cost_sim = estimates["phoenix.local_simulator"]
    assert fid_sim == 1.0
    assert lat_sim < 1.0
    assert cost_sim == 0.0

    # Cloud providers: less than perfect fidelity (gate errors).
    fid_ibm, _, cost_ibm = estimates["ibm_quantum"]
    assert 0.9 < fid_ibm < 1.0
    assert cost_ibm > 0.0

    # IonQ trapped_ion has higher fidelity than IBM/Braket superconducting
    # in the vendored HardwareParams.
    fid_ionq, _, _ = estimates["ionq"]
    assert fid_ionq > fid_ibm  # trapped_ion gate errors are lower


# ----- Equivalence registry -----


def test_equivalence_same_tech_same_fidelity() -> None:
    """IBM and Braket are both superconducting with similar fidelity -> equivalent."""
    from phoenix.router.equivalence_registry import is_equivalent
    from phoenix.router.provider_registry import build_default_registry

    reg = build_default_registry()
    ibm = reg.get("ibm_quantum")
    braket = reg.get("aws_braket")
    assert ibm is not None and braket is not None
    assert (
        is_equivalent(
            ibm,
            braket,
            primary_fidelity=0.98,
            alternate_fidelity=0.97,
        )
        is True
    )


def test_equivalence_different_tech_rejected() -> None:
    """Trapped ion is not equivalent to superconducting under conservative defaults."""
    from phoenix.router.equivalence_registry import is_equivalent
    from phoenix.router.provider_registry import build_default_registry

    reg = build_default_registry()
    ibm = reg.get("ibm_quantum")
    ionq = reg.get("ionq")
    assert ibm is not None and ionq is not None
    assert (
        is_equivalent(
            ibm,
            ionq,
            primary_fidelity=0.98,
            alternate_fidelity=0.99,
        )
        is False
    )  # tech mismatch overrides fidelity


def test_equivalence_fidelity_tolerance_enforced() -> None:
    """Alternate fidelity 20% below primary -> rejected (default tolerance 10%)."""
    from phoenix.router.equivalence_registry import is_equivalent
    from phoenix.router.provider_registry import build_default_registry

    reg = build_default_registry()
    ibm = reg.get("ibm_quantum")
    braket = reg.get("aws_braket")
    assert ibm is not None and braket is not None
    # 0.85 vs primary 0.98: ratio 0.867, threshold 0.882; below -> rejected.
    assert (
        is_equivalent(
            ibm,
            braket,
            primary_fidelity=0.98,
            alternate_fidelity=0.85,
        )
        is False
    )


def test_filter_equivalent_alternates_drops_mismatches() -> None:
    """Walks an alternates list and returns only equivalent ones."""
    from phoenix.router.equivalence_registry import filter_equivalent_alternates
    from phoenix.router.provider_registry import build_default_registry

    reg = build_default_registry()
    ibm = reg.get("ibm_quantum")
    braket = reg.get("aws_braket")
    ionq = reg.get("ionq")
    assert ibm is not None and braket is not None and ionq is not None
    equivalent = filter_equivalent_alternates(
        ibm,
        [braket, ionq],
        primary_fidelity=0.98,
        alternate_fidelities={"aws_braket": 0.97, "ionq": 0.99},
    )
    # IonQ filtered out (different tech); Braket survives.
    eq_ids = [e.provider_id for e in equivalent]
    assert eq_ids == ["aws_braket"]
