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
    """Phase 5: real sigma_z observable extraction.

    Phase 3 returned ``trace(rho)`` (always 1.0 for valid density
    matrices); Phase 5 replaced this with ``Tr(rho * sigma_z) =
    rho[0,0] - rho[1,1]``. For the maximally-mixed state I/2 this is
    0.0 (not 1.0 as Phase 3 would have returned). Adding an explicit
    ``observable="identity"`` to the payload restores the trace
    behavior.
    """
    import numpy as np

    from phoenix.providers.classical.local_simulator import LocalClassicalSimulator
    from phoenix.trinity.orchestrate.provider_client import ProviderSubmission

    sim = LocalClassicalSimulator()
    # Maximally-mixed rho: <sigma_z> = 0 in Phase 5; trace = 1 with identity.
    rho = np.eye(2, dtype=complex) / 2.0
    sub_default = ProviderSubmission(
        bundle_kind="classical_density_matrix",
        payload={"rho": rho.tolist(), "rho_dim": 2, "dpd_n_blocks": 1},
        shots=2048,
        bundle_hash="deadbeefcafe1234",
    )
    res_default = sim.submit(sub_default)
    assert abs(res_default.raw_data["expectation_value"] - 0.0) < 1e-10
    assert res_default.raw_data["observable"] == "sigma_z"
    assert res_default.raw_data["rho_dim"] == 2
    assert res_default.shots_used == 2048
    assert res_default.cloud_shots_recorded is False

    # Identity observable restores the Phase 3 trace behavior.
    sub_identity = ProviderSubmission(
        bundle_kind="classical_density_matrix",
        payload={
            "rho": rho.tolist(),
            "rho_dim": 2,
            "dpd_n_blocks": 1,
            "observable": "identity",
        },
        shots=1,
        bundle_hash="identityobs00000",
    )
    res_identity = sim.submit(sub_identity)
    assert abs(res_identity.raw_data["expectation_value"] - 1.0) < 1e-10
    assert res_identity.raw_data["observable"] == "identity"


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
