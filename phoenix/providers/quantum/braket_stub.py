"""AWS Braket stub adapter (Phase 4 placeholder).

Implements :class:`BaseProviderClient` Protocol structurally. Phase 4
ships this as a stub; real ``amazon-braket-sdk`` wiring lands in a focused
later phase. Same pattern as :class:`IBMQuantumStub`.

Per the v1 acceptance criteria (Section 10.7): Rigetti Aspen-M-3 (via
Braket) is the representative Braket backend; the stub uses
``backend_name="rigetti_aspen_m_3"`` and ``quantum_technology=
"superconducting"`` so the Router's Stage 1 modality filter accepts it
for the same task surface as :class:`IBMQuantumStub`. Phase 4's
:class:`EquivalenceRegistry` (Step 6) marks them as failover-equivalent
under the conservative defaults.
"""

from __future__ import annotations

from phoenix.trinity.orchestrate.provider_client import (
    OrchestrateProviderError,
    ProviderRawResult,
    ProviderSubmission,
)


class BraketStub:
    """AWS Braket (amazon-braket-sdk) stub adapter."""

    provider_id: str = "aws_braket"
    backend_name: str = "rigetti_aspen_m_3"
    quantum_technology: str = "superconducting"

    def __init__(self, *, available: bool = True) -> None:
        self._available = available

    def submit(self, submission: ProviderSubmission) -> ProviderRawResult:
        raise OrchestrateProviderError(
            (
                f"BraketStub.submit not yet wired (Phase 4 stub). Real "
                f"amazon-braket-sdk adapter lands in a focused later "
                f"phase. Tried to submit bundle_hash={submission.bundle_hash}."
            ),
            provider_id=self.provider_id,
            bundle_hash=submission.bundle_hash,
        )

    def is_available(self) -> bool:
        return self._available

    def estimated_latency_ms(self) -> float:
        """Placeholder: ~3 seconds, Braket's typical queue depth for
        Rigetti Aspen at off-peak."""
        return 3000.0
