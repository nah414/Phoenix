"""``BaseProviderClient`` Protocol + submission/raw-result dataclasses.

Per architecture v1 Section 2.5: every Phoenix solve dispatches through a
:class:`BaseProviderClient` (a Phoenix-native Protocol; nothing vendored
from SynQc TDS Core per Decision 37). Phase 3 ships the Protocol plus the
two payload dataclasses (:class:`ProviderSubmission`,
:class:`ProviderRawResult`) and one concrete adapter
(:class:`LocalClassicalSimulator` at ``phoenix/providers/classical/``).
Phase 4 adds cloud quantum adapters (IBM Eagle, Braket, IonQ) under
``phoenix/providers/{quantum,cognition,cloud_gpu}/`` implementing this
Protocol.

The Protocol is structural (PEP 544): any class exposing the three
attributes and the three methods satisfies it; concrete adapters need not
inherit. Phase 4's Router maintains a per-provider registry keyed by
``provider_id`` and resolves a :class:`ProviderSelection` from a
:class:`RoutingDecision`.

The submission payload is intentionally a free-form ``dict[str, Any]`` so
Phase 4 can ship per-provider shapes (Qiskit circuit dict, Braket task
spec, IonQ shot batch) without churning this Protocol. Phase 3 only ships
the ``"classical_density_matrix"`` shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


# ---------------------------------------------------------------------------
# Typed errors


class ProviderError(Exception):
    """Base for provider-side failures."""


class OrchestrateProviderError(ProviderError):
    """A provider's :meth:`BaseProviderClient.submit` failed.

    Carries the :attr:`provider_id` and :attr:`bundle_hash` so failover
    decisions and ledger entries have full context. Phase 3 surfaces this
    as HTTP 502 at the front door (Step 9). Phase 4's Router failover
    protocol (Section 4.5) catches it for retry on alternate providers.
    """

    def __init__(self, msg: str, *, provider_id: str, bundle_hash: str) -> None:
        super().__init__(msg)
        self.provider_id = provider_id
        self.bundle_hash = bundle_hash


# ---------------------------------------------------------------------------
# Submission + raw-result dataclasses


@dataclass(frozen=True)
class ProviderSubmission:
    """One bundle prepared for a specific provider.

    Built by :func:`bundle_builder.build_submission` from a
    :class:`VerifiedAnswer` plus a :class:`ProviderSelection`. Pure
    translation, no I/O.

    Fields:
        bundle_kind: Discriminator for the payload shape. Phase 3 ships
            ``"classical_density_matrix"``; Phase 4 adds
            ``"qiskit_circuit"``, ``"braket_task"``, ``"ionq_shot_batch"``.
        payload: Provider-specific submission body. Free-form by design;
            shape is owned by the bundle_builder and consumed by the
            concrete BaseProviderClient impl. Phase 3 carries
            ``{"rho": list[list[complex]], "dpd_n_blocks": int}``.
        shots: Requested shot budget (1 for the local-simulator path;
            cloud adapters in Phase 4 honor user-specified counts).
        bundle_hash: Stable SHA-256-prefix fingerprint of the canonical
            JSON of ``payload`` (16 hex chars). Phase 7's ledger uses this
            as the bundle identity for replay; Phase 4's Router uses it
            for cache-on-failover.
        metadata: Free-form audit dict. Phase 3 carries the dispatching
            context (provider_id of the resolved selection, request_id).
    """

    bundle_kind: str
    payload: dict[str, Any]
    shots: int
    bundle_hash: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderRawResult:
    """Raw output from a :meth:`BaseProviderClient.submit` invocation.

    Fed to :func:`result_extractor.extract` along with the originating
    :class:`VerifiedAnswer` to produce an :class:`ExtractedResult` (the
    bridge to :class:`Result.value` + :class:`KPIBundle`).

    Fields:
        raw_data: Provider-specific raw output. Free-form. Phase 3's
            local-simulator returns ``{"expectation_value": float,
            "rho_dim": int}``; cloud adapters in Phase 4 return
            shot-count histograms or expectation-value tables.
        shots_used: Actual shot count consumed. May differ from
            ``ProviderSubmission.shots`` for cloud providers (queue
            truncation, partial results).
        latency_us: Wall-clock latency reported by the provider, in
            microseconds.
        provider_id / backend_name: Echoed from the dispatching
            :class:`ProviderSelection` for ledger join.
        cloud_shots_recorded: ``True`` when any cloud provider was
            invoked. Phoenix's reproducibility-asterisk machinery (Section
            1 Decision 20) reads this flag to mark the
            :class:`ProvenanceTrace`. ``False`` for the local-simulator
            path; cloud adapters in Phase 4 set ``True``.
    """

    raw_data: dict[str, Any]
    shots_used: int
    latency_us: float
    provider_id: str
    backend_name: str
    cloud_shots_recorded: bool = False


# ---------------------------------------------------------------------------
# BaseProviderClient Protocol


class BaseProviderClient(Protocol):
    """The structural contract every provider adapter satisfies.

    Phase 3 ships :class:`LocalClassicalSimulator` as the only concrete
    impl; Phase 4 adds cloud quantum adapters. The Protocol is structural
    (PEP 544): any class with the three attributes and three methods
    satisfies it; no inheritance required.

    Phase 3's Protocol is synchronous (``submit`` returns a
    :class:`ProviderRawResult` directly). Phase 4 may extend with async
    polling for cloud providers that queue jobs (Braket, IBM); the
    extension can land as a sibling Protocol or as optional methods, both
    are backward-compatible with this Phase 3 contract.
    """

    provider_id: str
    """Stable provider identifier (e.g. ``"phoenix.local_simulator"``,
    ``"ibm.eagle_r3"``, ``"braket.rigetti_aspen_m_3"``)."""

    backend_name: str
    """Provider-specific backend within the provider's catalog."""

    quantum_technology: str
    """One of ``"simulation"``, ``"superconducting"``, ``"trapped_ion"``,
    ``"photonic"``, ``"neutral_atom"``, etc. Phase 3 ships
    ``"simulation"`` only."""

    def submit(self, submission: ProviderSubmission) -> ProviderRawResult:
        """Execute the bundle on this provider's backend.

        Phase 3 contract: synchronous; raises :class:`OrchestrateProviderError`
        on failure. Phase 4's cloud adapters may queue and return only when
        the provider yields a result.
        """
        ...

    def is_available(self) -> bool:
        """Cheap health probe. ``True`` when the provider can accept a
        submission *now*; ``False`` when degraded (queue full, network
        partition, credentials expired). Phase 4's Router consults this
        before every dispatch."""
        ...

    def estimated_latency_ms(self) -> float:
        """Predicted wall-clock latency in milliseconds for an average
        submission. Phase 4's Router intelligence layer (Section 4.6)
        uses this to bias selection."""
        ...
