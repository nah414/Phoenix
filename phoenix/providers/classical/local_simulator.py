"""LocalClassicalSimulator -- Phase 3's only concrete BaseProviderClient.

Per architecture v1 Section 2.5: every Phoenix solve dispatches through a
:class:`BaseProviderClient`. Phase 3 ships exactly one concrete impl so the
pipeline can run end-to-end without Qiskit / Braket / IonQ SDKs. Phase 4
adds cloud quantum adapters as siblings under
``phoenix/providers/{quantum,cognition,cloud_gpu}/``.

The local simulator is a "passthrough adapter": Solver and Control already
ran upstream, so this adapter does not re-run physics. It takes the
verified :math:`\\rho` from the bundle's payload and synthesizes a
"fake measurement" outcome -- specifically the trace expectation, which
is :math:`\\approx 1.0` for any valid density matrix. Phase 5 wires real
observable extraction (project against a user-specified observable from
the task) once the wider observable surface lands.

The :attr:`cloud_shots_recorded` flag is hardcoded ``False`` on every
:class:`ProviderRawResult` from this adapter (no cloud invoked). Phase 4's
cloud adapters set ``True``, and Phoenix's reproducibility-asterisk
machinery (Section 1 Decision 20) propagates the flag to
:class:`ProvenanceTrace` so audit-log readers see the asterisk wherever
the trace is inspected.
"""

from __future__ import annotations

import time

import numpy as np

from phoenix.trinity.orchestrate.provider_client import (
    OrchestrateProviderError,
    ProviderRawResult,
    ProviderSubmission,
)


def _build_observable(name: str, *, dim: int) -> np.ndarray:
    """Construct a 2x2 (or higher-dim) observable matrix by name.

    Phase 5 supports the four canonical 2x2 observables: identity,
    sigma_x, sigma_y, sigma_z. For dim > 2, only the identity case is
    supported in Phase 5; a higher-dim sigma_z is interpreted as
    diag(1, -1, 1, -1, ...) for completeness but the QHO Tier-1 case
    only exercises dim=2.

    v1.x will extend with arbitrary user-specified Hermitian matrices
    passed as nested lists in the task's observable field.
    """
    name_lower = name.lower()
    if name_lower == "identity":
        return np.eye(dim, dtype=complex)
    if dim != 2 and name_lower in ("sigma_x", "sigma_y", "sigma_z"):
        # Higher-dim Pauli embedding -- for QHO Tier-1 this branch is
        # unreached (dim=2 always). Provide a diagonal sigma_z for
        # completeness; sigma_x/sigma_y > 2x2 are ill-defined without
        # a basis choice.
        if name_lower == "sigma_z":
            diag = np.array([(-1) ** i for i in range(dim)], dtype=complex)
            return np.diag(diag)
        # sigma_x / sigma_y for dim > 2: fall back to identity to avoid
        # surfacing physics ambiguity at the local-sim layer; v1.x
        # canonicalizes via the task grammar.
        return np.eye(dim, dtype=complex)
    if name_lower == "sigma_z":
        return np.array([[1.0, 0.0], [0.0, -1.0]], dtype=complex)
    if name_lower == "sigma_x":
        return np.array([[0.0, 1.0], [1.0, 0.0]], dtype=complex)
    if name_lower == "sigma_y":
        return np.array([[0.0, -1.0j], [1.0j, 0.0]], dtype=complex)
    # Unknown observable name: fall back to identity (returns trace=1.0
    # for valid rho, matching Phase 3 behavior). Phase 5 is honest about
    # the fallback via the returned raw_data["observable"] echo.
    return np.eye(dim, dtype=complex)


class LocalClassicalSimulator:
    """Phase 3's only concrete :class:`BaseProviderClient`.

    Wraps the existing Solver+Control path: by the time Orchestrate calls
    :meth:`submit`, the upstream subsystems have already produced a
    verified :math:`\\rho` (carried in
    :attr:`ProviderSubmission.payload["rho"]`). This adapter just measures
    the trace and reports it as the expectation value.

    Phase 5 expansion: the adapter will accept an observable (sigma_z,
    sigma_x, custom Hermitian) from the task and project :math:`\\rho`
    against it. For Phase 3 the trace is sufficient to exercise the full
    pipeline shape end-to-end.
    """

    provider_id: str = "phoenix.local_simulator"
    backend_name: str = "local_density_matrix"
    quantum_technology: str = "simulation"

    def submit(self, submission: ProviderSubmission) -> ProviderRawResult:
        """Decode :math:`\\rho` from the bundle, compute trace, return raw result.

        Raises :class:`OrchestrateProviderError` only if the payload shape
        is unrecognized (defensive; the bundle_builder constrains this).
        Always sets ``cloud_shots_recorded=False`` -- no cloud invoked.
        """
        if submission.bundle_kind != "classical_density_matrix":
            raise OrchestrateProviderError(
                (
                    f"LocalClassicalSimulator does not support "
                    f"bundle_kind={submission.bundle_kind!r}; only "
                    f"'classical_density_matrix' is supported in Phase 3."
                ),
                provider_id=self.provider_id,
                bundle_hash=submission.bundle_hash,
            )

        payload = submission.payload
        if "rho" not in payload:
            raise OrchestrateProviderError(
                "LocalClassicalSimulator: payload missing 'rho' key.",
                provider_id=self.provider_id,
                bundle_hash=submission.bundle_hash,
            )

        # Decode rho back to a complex ndarray. JSON serialization via
        # ndarray.tolist() preserves the complex structure as nested
        # [real, imag]-pair lists in newer numpy versions, but older
        # serializations may yield plain Python complex. Phase 3 only
        # round-trips locally, so np.asarray with dtype=complex handles
        # both shapes uniformly.
        t_start = time.perf_counter()
        rho = np.asarray(payload["rho"], dtype=complex)

        # Phase 5: real observable projection (replaces Phase 3's trace
        # placeholder). Default observable is sigma_z; the bundle_builder
        # can add ``"observable": "sigma_x" | "sigma_y" | "sigma_z" |
        # "identity"`` to the payload to override. v1.x extends to
        # user-specified Hermitian matrices via the task grammar's
        # observable field.
        observable_name = str(payload.get("observable", "sigma_z"))
        observable = _build_observable(observable_name, dim=rho.shape[0])
        expectation_value = float(np.real(np.trace(rho @ observable)))

        latency_us = (time.perf_counter() - t_start) * 1e6

        return ProviderRawResult(
            raw_data={
                "expectation_value": expectation_value,
                "rho_dim": int(rho.shape[0]),
                "observable": observable_name,
            },
            shots_used=submission.shots,
            latency_us=latency_us,
            provider_id=self.provider_id,
            backend_name=self.backend_name,
            cloud_shots_recorded=False,  # No cloud invoked.
        )

    def is_available(self) -> bool:
        """Always available -- no external dependency."""
        return True

    def estimated_latency_ms(self) -> float:
        """Sub-millisecond; the trace computation is bounded by rho dim."""
        return 0.1
