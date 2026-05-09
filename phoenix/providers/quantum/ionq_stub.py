"""IonQ stub adapter (Phase 4 placeholder).

Implements :class:`BaseProviderClient` Protocol structurally. Phase 4
ships this as a stub; real ``ionq`` SDK wiring lands in a focused later
phase. Same pattern as :class:`IBMQuantumStub` and :class:`BraketStub`.

Per the v1 acceptance criteria (Section 10.7): IonQ Forte is the
representative IonQ backend; ``quantum_technology="trapped_ion"``
distinguishes it from the superconducting providers, so the Router's
Stage 1 modality filter routes trapped-ion-favorable tasks here. Phase
4's :class:`EquivalenceRegistry` (Step 6) does NOT mark IonQ as
failover-equivalent to IBM/Braket under the conservative defaults
(different ``quantum_technology``); failover from IonQ requires another
trapped-ion provider (none registered in v1).
"""

from __future__ import annotations

from phoenix.trinity.orchestrate.provider_client import (
    OrchestrateProviderError,
    ProviderRawResult,
    ProviderSubmission,
)


class IonQStub:
    """IonQ stub adapter."""

    provider_id: str = "ionq"
    backend_name: str = "ionq_forte"
    quantum_technology: str = "trapped_ion"

    def __init__(self, *, available: bool = True) -> None:
        self._available = available

    def submit(self, submission: ProviderSubmission) -> ProviderRawResult:
        raise OrchestrateProviderError(
            (
                f"IonQStub.submit not yet wired (Phase 4 stub). Real "
                f"ionq SDK adapter lands in a focused later phase. "
                f"Tried to submit bundle_hash={submission.bundle_hash}."
            ),
            provider_id=self.provider_id,
            bundle_hash=submission.bundle_hash,
        )

    def is_available(self) -> bool:
        return self._available

    def estimated_latency_ms(self) -> float:
        """Placeholder: ~10 seconds, IonQ Forte's typical queue depth."""
        return 10000.0
