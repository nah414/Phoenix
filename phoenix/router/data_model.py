"""Router subsystem typed data model (Phase 3 forward-compat).

Per architecture v1 Section 4: every Phoenix solve produces a
:class:`RoutingDecision` selecting which provider Orchestrate dispatches
against. Phase 4 ships the producer (the seven-stage routing algorithm at
``phoenix/router/decision.py``); Phase 3 ships the typed dataclasses so
Orchestrate's :func:`engine.orchestrate` can accept a ``ProviderSelection``
argument from day one without churning the call site when Phase 4 lands.

This is the same forward-compat pattern Phase 2 used for
:class:`VerifiedAnswer` / :class:`Result`: define the data classes now,
defer the producer to the phase that owns it.

Phase 3's pipeline orchestrator constructs a default
:class:`ProviderSelection` pointing at
:class:`LocalClassicalSimulator` directly (no real routing yet). Phase 4
replaces that path with ``Router.decide(routing_request) -> RoutingDecision``
and the seven-stage algorithm.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # TYPE_CHECKING import keeps the runtime cycle broken (provider_client
    # imports nothing from phoenix.router); mypy resolves the type via the
    # forward reference.
    from phoenix.trinity.orchestrate.provider_client import BaseProviderClient


@dataclass(frozen=True)
class ProviderSelection:
    """One concrete provider chosen for an Orchestrate dispatch.

    Carries the resolved :class:`BaseProviderClient` instance plus the
    metadata Orchestrate's bundle_builder needs to translate a
    :class:`VerifiedAnswer` into a provider-specific submission.

    Fields:
        provider_id: Stable provider identifier (e.g.
            ``"phoenix.local_simulator"``, ``"ibm.eagle"``,
            ``"braket.rigetti_aspen_m_3"``). Phase 3 ships only the local
            simulator; Phase 4 adds cloud quantum providers as additional
            ids.
        backend_name: Provider-specific backend name (e.g.
            ``"local_density_matrix"`` for the local sim,
            ``"ibmq_eagle_r3"`` for IBM Eagle). The same provider may
            expose multiple backends.
        quantum_technology: One of ``"simulation"``,
            ``"superconducting"``, ``"trapped_ion"``, ``"photonic"``,
            ``"neutral_atom"``, etc. Phase 3 ships ``"simulation"`` only;
            bundle_builder dispatches submission shape off this field.
        client: The resolved :class:`BaseProviderClient` instance the
            Orchestrate engine calls ``submit()`` on. Phase 4's Router
            populates this from a per-provider registry.
        selection_metadata: Free-form dict carrying routing-decision
            rationale (e.g. predicted fidelity, queue depth). Phase 3
            populates this with ``{"phase": "phase_3_default_local_sim"}``
            so consumers can tell a Phase-3 default selection apart from
            a Phase-4 routed selection without inspecting commit history.
    """

    provider_id: str
    backend_name: str
    quantum_technology: str
    client: BaseProviderClient
    selection_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RoutingDecision:
    """The Router's verdict for one solve, per architecture v1 Section 4.10.

    Phase 4 ships the producer. Phase 3 defines the type only.

    Fields:
        primary: The chosen :class:`ProviderSelection` Orchestrate dispatches
            against first.
        alternates: Ranked failover candidates. Phase 4's failover protocol
            (Section 4.5) walks this list when the primary fails; Phase 3's
            local-simulator path produces an empty list.
        rationale: One-sentence human-readable reason the Router chose
            ``primary``. Lands in :class:`ProvenanceTrace` so audit-log
            readers can see why a particular provider ran a particular solve.
        estimated_cost_usd: Predicted dollar cost. Section 4.7's cost-ceiling
            enforcement gates the routing decision against this estimate;
            Phase 3's local-simulator path always reports ``0.0``.
        estimated_latency_ms: Predicted wall-clock latency in milliseconds.
            Used by the Router intelligence layer (Section 4.6) to compose
            multi-stage routing decisions; Phase 3's local-simulator path
            reports a sub-millisecond estimate.
        estimated_fidelity: Predicted state fidelity in :math:`[0, 1]`.
            Section 4.6 uses this to bias provider selection toward
            high-fidelity backends when ``max_error_bar`` is tight.
        decision_provenance: Free-form dict carrying the seven-stage routing
            algorithm's per-stage decisions (Section 4.4). Phase 4 populates;
            Phase 3 ships an empty dict.
    """

    primary: ProviderSelection
    alternates: list[ProviderSelection] = field(default_factory=list)
    rationale: str = ""
    estimated_cost_usd: float = 0.0
    estimated_latency_ms: float = 0.0
    estimated_fidelity: float = 0.0
    decision_provenance: dict[str, Any] = field(default_factory=dict)
