"""Phoenix quantum provider adapters.

Phase 4 ships three stub adapters (IBM Quantum, AWS Braket, IonQ) that
implement the :class:`BaseProviderClient` Protocol structurally but raise
:class:`OrchestrateProviderError` on :meth:`submit`. The stubs let the
Phase 4 Router register them as candidates, exercise modality eligibility
+ cost-ceiling filters + failover protocol against them in tests, and
produce honest 'not yet wired' surfaces for users who try to route to
them in real solves.

Real cloud SDK wiring (qiskit-ibm-runtime, amazon-braket-sdk, ionq) lands
in a focused later phase (likely Phase 9 with adapters / MCP) when SDK +
credential management gets tackled deliberately. Phase 4's locked
decision (2026-05-08): keep Phase 4 about the Router, not the SDK
landscape.
"""

from __future__ import annotations

from phoenix.providers.quantum.braket_stub import BraketStub
from phoenix.providers.quantum.ibm_stub import IBMQuantumStub
from phoenix.providers.quantum.ionq_stub import IonQStub

__all__ = ["IBMQuantumStub", "BraketStub", "IonQStub"]
