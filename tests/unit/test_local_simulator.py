"""Phase 3 Step 6 -- LocalClassicalSimulator provider adapter."""

from __future__ import annotations

import phoenix  # noqa: F401  -- triggers sys.path injection for vendored modules


def test_local_simulator_attributes() -> None:
    from phoenix.providers.classical.local_simulator import LocalClassicalSimulator

    sim = LocalClassicalSimulator()
    assert sim.provider_id == "phoenix.local_simulator"
    assert sim.backend_name == "local_density_matrix"
    assert sim.quantum_technology == "simulation"
    assert sim.is_available() is True
    assert sim.estimated_latency_ms() > 0


def test_local_simulator_submit_returns_unit_trace_for_valid_rho() -> None:
    """A valid density matrix has trace == 1; the simulator's expectation
    measurement returns that trace."""
    import numpy as np

    from phoenix.providers.classical.local_simulator import LocalClassicalSimulator
    from phoenix.trinity.orchestrate.provider_client import ProviderSubmission

    sim = LocalClassicalSimulator()
    rho = np.eye(2, dtype=complex) / 2.0  # tr=1
    sub = ProviderSubmission(
        bundle_kind="classical_density_matrix",
        payload={"rho": rho.tolist(), "rho_dim": 2, "dpd_n_blocks": 1},
        shots=2048,
        bundle_hash="deadbeefcafe1234",
    )
    res = sim.submit(sub)
    assert abs(res.raw_data["expectation_value"] - 1.0) < 1e-10
    assert res.raw_data["rho_dim"] == 2
    assert res.shots_used == 2048
    assert res.cloud_shots_recorded is False  # no cloud invoked


def test_local_simulator_rejects_unknown_bundle_kind() -> None:
    """Anything other than 'classical_density_matrix' -> OrchestrateProviderError."""
    from phoenix.providers.classical.local_simulator import LocalClassicalSimulator
    from phoenix.trinity.orchestrate.provider_client import (
        OrchestrateProviderError,
        ProviderSubmission,
    )

    sim = LocalClassicalSimulator()
    bad = ProviderSubmission(
        bundle_kind="qiskit_circuit",
        payload={},
        shots=10,
        bundle_hash="x",
    )
    try:
        sim.submit(bad)
    except OrchestrateProviderError as exc:
        assert exc.provider_id == "phoenix.local_simulator"
        assert exc.bundle_hash == "x"
        return
    raise AssertionError("Expected OrchestrateProviderError for unknown bundle_kind")
