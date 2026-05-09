"""Phase 4 Step 8 -- FailoverProtocol + walking + simulator fallback."""

from __future__ import annotations

import phoenix  # noqa: F401  -- triggers sys.path injection for vendored modules


def test_quarantine_marks_degraded_with_iso_timestamp() -> None:
    from phoenix.router.failover import FailoverProtocol
    from phoenix.router.provider_registry import ProviderHealth, build_default_registry

    reg = build_default_registry()
    failover = FailoverProtocol(reg)
    until = failover.quarantine("ibm_quantum")
    assert until.endswith("+00:00")  # ISO 8601 UTC suffix
    entry = reg.get("ibm_quantum")
    assert entry is not None
    assert entry.health is ProviderHealth.DEGRADED
    assert entry.degraded_until == until


def test_quarantine_doubles_per_failure() -> None:
    """Exponential backoff: t = base * 2^(n-1)."""
    from phoenix.router.failover import FailoverProtocol
    from phoenix.router.provider_registry import build_default_registry

    reg = build_default_registry()
    failover = FailoverProtocol(reg, base_quarantine_seconds=10, max_quarantine_seconds=10000)
    s1 = failover._next_quarantine_seconds("ibm_quantum")
    s2 = failover._next_quarantine_seconds("ibm_quantum")
    s3 = failover._next_quarantine_seconds("ibm_quantum")
    assert s1 == 10
    assert s2 == 20
    assert s3 == 40


def test_quarantine_capped_at_max() -> None:
    from phoenix.router.failover import FailoverProtocol
    from phoenix.router.provider_registry import build_default_registry

    reg = build_default_registry()
    failover = FailoverProtocol(reg, base_quarantine_seconds=100, max_quarantine_seconds=300)
    # 1: 100, 2: 200, 3: 400 -> capped 300, 4: 800 -> capped 300, ...
    quarantines = [failover._next_quarantine_seconds("ibm_quantum") for _ in range(5)]
    assert quarantines == [100, 200, 300, 300, 300]


def test_reset_failures_resets_backoff() -> None:
    from phoenix.router.failover import FailoverProtocol
    from phoenix.router.provider_registry import build_default_registry

    reg = build_default_registry()
    failover = FailoverProtocol(reg, base_quarantine_seconds=10)
    failover._next_quarantine_seconds("ibm_quantum")
    failover._next_quarantine_seconds("ibm_quantum")  # count=2 now
    failover.reset_failures("ibm_quantum")
    next_s = failover._next_quarantine_seconds("ibm_quantum")
    assert next_s == 10  # Back to base


def test_attempt_with_failover_walks_to_simulator_fallback() -> None:
    """When all RoutingDecision candidates are stubs that always fail, the
    simulator fallback path runs and returns a successful raw result."""
    from phoenix.providers.quantum import IBMQuantumStub
    from phoenix.router.data_model import ProviderSelection, RoutingDecision
    from phoenix.router.failover import FailoverProtocol
    from phoenix.router.provider_registry import build_default_registry
    from phoenix.trinity.orchestrate.provider_client import ProviderSubmission

    reg = build_default_registry()
    failover = FailoverProtocol(reg)
    # Construct a RoutingDecision with only IBM stub (always raises).
    ibm = ProviderSelection(
        provider_id="ibm_quantum",
        backend_name="ibm_eagle",
        quantum_technology="superconducting",
        client=IBMQuantumStub(),
    )
    decision = RoutingDecision(primary=ibm, alternates=[])

    # A submission for the simulator fallback path: classical_density_matrix.
    import numpy as np

    rho = np.eye(2, dtype=complex) / 2.0
    submission = ProviderSubmission(
        bundle_kind="classical_density_matrix",
        payload={"rho": rho.tolist(), "rho_dim": 2, "dpd_n_blocks": 1},
        shots=1024,
        bundle_hash="testfallback00000",
    )
    raw, used_selection = failover.attempt_with_failover(
        decision, submission, allow_simulator_fallback=True
    )
    # Simulator fallback succeeded.
    assert used_selection.provider_id == "phoenix.local_simulator"
    assert used_selection.selection_metadata.get("phase") == "phase_4_simulator_fallback"
    assert raw.cloud_shots_recorded is False
    # IBM marked degraded after the failed attempt.
    ibm_entry = reg.get("ibm_quantum")
    assert ibm_entry is not None
    from phoenix.router.provider_registry import ProviderHealth

    assert ibm_entry.health is ProviderHealth.DEGRADED


def test_attempt_with_failover_raises_when_no_fallback_allowed() -> None:
    """All alternates fail + allow_simulator_fallback=False -> AllAlternatesExhausted."""
    import pytest

    from phoenix.providers.quantum import IBMQuantumStub
    from phoenix.router.data_model import ProviderSelection, RoutingDecision
    from phoenix.router.errors import AllAlternatesExhausted
    from phoenix.router.failover import FailoverProtocol
    from phoenix.router.provider_registry import build_default_registry
    from phoenix.trinity.orchestrate.provider_client import ProviderSubmission

    reg = build_default_registry()
    failover = FailoverProtocol(reg)
    ibm = ProviderSelection(
        provider_id="ibm_quantum",
        backend_name="ibm_eagle",
        quantum_technology="superconducting",
        client=IBMQuantumStub(),
    )
    decision = RoutingDecision(primary=ibm, alternates=[])
    submission = ProviderSubmission(
        bundle_kind="classical_density_matrix",
        payload={},
        shots=1,
        bundle_hash="testfailx0000000",
    )
    with pytest.raises(AllAlternatesExhausted) as exc_info:
        failover.attempt_with_failover(decision, submission, allow_simulator_fallback=False)
    # The attempts list captures the failure with quarantine_until_utc.
    assert len(exc_info.value.attempts) >= 1
    assert exc_info.value.attempts[0]["provider_id"] == "ibm_quantum"
