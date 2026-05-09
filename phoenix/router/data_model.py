"""Router subsystem typed data model.

Per architecture v1 Section 4: every Phoenix solve produces a
:class:`RoutingDecision` selecting which provider Orchestrate dispatches
against. Phase 3 shipped :class:`ProviderSelection` and
:class:`RoutingDecision` as forward-compat dataclasses with no producer.
Phase 4 adds:

- :class:`RoutingRequest` -- the input shape :func:`Router.decide` consumes.
  Carries the :class:`PhysicsTask` plus user-policy filters
  (cost_ceiling_usd, latency_budget_ms, fidelity_floor) and the
  reproducibility-mode constraint.
- :class:`ReproducibilityMode` -- one of ``default`` / ``strict`` /
  ``replay`` per Section 1 Decision 19. Phase 4 honors ``replay`` (Stage 5
  pins to recorded provider) and ``default`` (no replay constraint);
  ``strict`` semantics land at Phase 7 with the ledger.

The seven-stage routing algorithm lives in
``phoenix/router/decision.py`` (Phase 4 Step 7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # TYPE_CHECKING import keeps the runtime cycle broken (provider_client
    # imports nothing from phoenix.router); mypy resolves the type via the
    # forward reference.
    from phoenix.trinity.data_model import PhysicsTask
    from phoenix.trinity.orchestrate.provider_client import BaseProviderClient


class ReproducibilityMode(Enum):
    """Reproducibility tier per architecture v1 Section 1 Decision 19.

    Three tiers; Phase 4's Router honors the first two (Stage 5 pins to
    the recorded provider on ``REPLAY``); ``STRICT`` semantics (bit-exact
    local replay) land at Phase 7 with the Omega Ledger.
    """

    DEFAULT = "default"
    """Provenance written; no replay guarantee."""

    STRICT = "strict"
    """Bit-exact local replay (Phase 7 backed)."""

    REPLAY = "replay"
    """Re-execute and verify before returning. Stage 5 pins the provider
    to the recorded :class:`ProviderSelection` from the ledger entry being
    replayed; raises :class:`ReplayProviderUnavailable` if it's gone."""


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


@dataclass(frozen=True)
class RoutingRequest:
    """The input :func:`Router.decide` consumes (Phase 4).

    Per architecture v1 Section 4.3: the Router takes a ``PhysicsTask``
    plus the user-policy envelope (cost ceiling, latency budget, fidelity
    floor, preferred / excluded providers, reproducibility mode) and
    returns a :class:`RoutingDecision`.

    Fields:
        task: The :class:`PhysicsTask` to route. The Router parses its
            ``physics_context`` to determine the dispatched Hamiltonian
            regime + qubit count for Stage 1's modality eligibility filter.
        cost_ceiling_usd: Per-solve dollar cap. ``None`` means no ceiling.
            Section 4.7 v1 defaults: $5.00 (default), $25.00 (strict),
            $50.00 (replay). Phase 4's Stage 2 refuses candidates whose
            ``estimated_cost_usd`` exceeds the ceiling.
        latency_budget_ms: Soft latency cap in milliseconds. ``None``
            means no budget. Stage 2 drops candidates whose
            ``estimated_latency_ms`` exceeds; Stage 6 ranking penalizes
            higher latency.
        fidelity_floor: Soft minimum fidelity. ``None`` means no floor.
            Stage 2 drops candidates whose ``estimated_fidelity`` falls
            below.
        reproducibility_mode: Phase 4 honors ``DEFAULT`` and ``REPLAY``;
            ``STRICT`` Phase 7.
        preferred_providers: Stage 6 ranking adds a small bonus to
            candidates whose ``provider_id`` appears here. Empty list =
            no preference.
        excluded_providers: Stage 2 drops candidates whose ``provider_id``
            appears here. Used by the verification gate's Axis 3 routing
            (Phase 5) to force a different provider for the cross-provider
            comparison run.
        allow_failover: When ``False``, the Router's failover protocol
            (Section 4.5) refuses to retry on alternates; the original
            failure surfaces directly. ``True`` is the default.
        allow_simulator_fallback: When ``True`` and all alternates exhaust,
            the Router falls back to :class:`LocalClassicalSimulator` with
            a ``degraded`` flag on the eventual :class:`Result`. ``False``
            raises :class:`AllAlternatesExhausted`.
    """

    task: PhysicsTask
    cost_ceiling_usd: float | None = None
    latency_budget_ms: float | None = None
    fidelity_floor: float | None = None
    reproducibility_mode: ReproducibilityMode = ReproducibilityMode.DEFAULT
    preferred_providers: list[str] = field(default_factory=list)
    excluded_providers: list[str] = field(default_factory=list)
    allow_failover: bool = True
    allow_simulator_fallback: bool = True
