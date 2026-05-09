"""IBM Quantum stub adapter (Phase 4 placeholder).

Implements :class:`BaseProviderClient` Protocol structurally. Phase 4
ships this as a stub so the Router can register IBM Quantum as a
candidate provider for routing decisions, exercise modality eligibility
+ cost-ceiling filters, and exercise the failover protocol in tests.
:meth:`submit` always raises :class:`OrchestrateProviderError` -- real
qiskit-ibm-runtime wiring lands when SDK + credential management gets
tackled deliberately (likely Phase 9 with adapters / MCP).

Per the v1 acceptance criteria (Section 10.7): IBM Eagle is the
representative IBM backend. The stub uses ``backend_name="ibm_eagle"``
so v1.x's real adapter slots in without provider_id collision.
"""

from __future__ import annotations

from phoenix.trinity.orchestrate.provider_client import (
    OrchestrateProviderError,
    ProviderRawResult,
    ProviderSubmission,
)


class IBMQuantumStub:
    """IBM Quantum (qiskit-ibm-runtime) stub adapter.

    Reports as a real superconducting-transmon provider so the Router's
    Stage 1 modality eligibility filter accepts it for tasks that target
    superconducting qubits. :meth:`is_available` returns ``True`` so
    Stage 3 health filter doesn't drop it; ops can override per-install
    by registering a different :class:`IBMQuantumStub(available=False)`
    instance.
    """

    provider_id: str = "ibm_quantum"
    backend_name: str = "ibm_eagle"
    quantum_technology: str = "superconducting"

    def __init__(self, *, available: bool = True) -> None:
        self._available = available

    def submit(self, submission: ProviderSubmission) -> ProviderRawResult:
        """Refuse submission; this is a Phase 4 stub.

        Real cloud wiring lands in a later phase. The exception carries
        the bundle_hash so Phase 4's failover protocol can record the
        attempt in :attr:`AllAlternatesExhausted.attempts`.
        """
        raise OrchestrateProviderError(
            (
                f"IBMQuantumStub.submit not yet wired (Phase 4 stub). Real "
                f"qiskit-ibm-runtime adapter lands in a focused later "
                f"phase. Tried to submit bundle_hash={submission.bundle_hash}."
            ),
            provider_id=self.provider_id,
            bundle_hash=submission.bundle_hash,
        )

    def is_available(self) -> bool:
        """Constructor-overridable; defaults to True so the Router accepts
        the stub as a routing candidate. Tests override to False to
        exercise the Stage 3 health filter."""
        return self._available

    def estimated_latency_ms(self) -> float:
        """Placeholder: ~5 seconds, the IBM Quantum cloud queue's typical
        depth at off-peak. Real Source A data lands in Phase 4 Step 5's
        intelligence layer; this attribute is a stub fallback."""
        return 5000.0
