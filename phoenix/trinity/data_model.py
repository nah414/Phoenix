"""Trinity Core typed data model.

Per architecture v1.1 Section 2.2: the four dataclasses that flow through
Trinity Core's pipeline (Solver -> Control -> Orchestrate) plus their
supporting types.

- :class:`PhysicsTask` -- input from the front door (REST/WS/CLI/MCP).
- :class:`CandidateAnswer` -- what Solver produces.
- :class:`VerifiedAnswer` -- what Control produces (Phase 3 wires the producer).
- :class:`Result` -- final output from Orchestrate (Phase 3 wires the producer).

Phase 2 ships all four dataclasses now (rather than just the two Phase 2
exercises) so Phase 3's Control + Orchestrate work plugs in without churning
the data model. Forward-compat fields use ``Any`` where a Phase 3 type isn't
yet importable; those fields tighten in Phase 3.

Spec-aligned class names per the 2026-05-08 v1.1 follow-up: ``DisagreementType``
(vendored), not ``AgreementType`` (the v1.0 spec drift). The field name
``agreement_type`` describes the semantic concept (the agreement classification);
the type ``DisagreementType`` matches the vendored class name from
``wobble/disagreement_types.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from phoenix._internal.latency import LatencyTier

if TYPE_CHECKING:
    # Imported under TYPE_CHECKING so we don't pay the import-cost at module load
    # AND so the dependency on the vendored substrate is explicit for type checkers
    # but doesn't fail at import time when vendor/ is absent (e.g. fresh clone
    # before Phase 1 vendor sync runs).
    from actor.actor import Actor
    from wobble.disagreement_types import DisagreementType

    from phoenix.verification.wobble_axis import AxisResult


# ---------------------------------------------------------------------------
# Tolerance + reproducibility


@dataclass(frozen=True)
class ToleranceSpec:
    """User-provided tolerance + execution-mode envelope on every PhysicsTask.

    Fields:
        max_error_bar: Required combined-error-bar tolerance. Drives the
            verification gate's rung selection (Section 6.4). Default 1e-3
            matches the v1 batch-real-time target.
        reproducibility_mode: One of ``"default"``, ``"strict"``, ``"replay"``
            per Section 1 Decision 19. ``"default"`` writes provenance but
            makes no replay guarantee; ``"strict"`` adds bit-exact local
            replay; ``"replay"`` re-executes and verifies before returning.
            Phase 2 only honors ``"default"``; Phase 7 wires strict + replay.
        latency_tier: Required :class:`LatencyTier` enum value. v1 routes only
            ``BATCH_REALTIME``; the other two values raise
            :class:`LatencyTierNotImplemented` from the pipeline orchestrator.
        frontier_physics: Whether the task is permitted to dispatch to
            Wheeler-DeWitt, Gravitational Decoherence, or Semiclassical Gravity
            solvers. Per Section 1 Decision 7: default False; users opt in
            explicitly; Phase 6 wires the Actor-based capability check.
    """

    max_error_bar: float = 1e-3
    reproducibility_mode: str = "default"
    latency_tier: LatencyTier = LatencyTier.BATCH_REALTIME
    frontier_physics: bool = False


# ---------------------------------------------------------------------------
# Provenance


@dataclass(frozen=True)
class SolverProvenance:
    """Solver-side provenance trace.

    Subset of the full :class:`ProvenanceTrace` (Section 1 Decision 15)
    populated by the Solver subsystem. Phase 2 captures dispatch metadata,
    grid resolutions used by cross-precision wobble (Axis 1), and the
    explicit ``phase`` marker noting Phase 2's incomplete state (Solver-only;
    Phase 3 extends with Control + Orchestrate).
    """

    request_id: str
    dispatched_solver: str  # e.g. ``"NON_RELATIVISTIC_TI/TISESolver"``
    n_grid_low: int
    n_grid_high: int
    wall_clock_ms_total: float
    cross_precision_axis_result: AxisResult | None = None
    phase: str = "phase_2_solver_only"


@dataclass(frozen=True)
class ProvenanceTrace:
    """Full per-solve audit trail.

    Per Section 1 Decision 15: every Phoenix solve produces a hashchained
    ledger entry. Phase 2 ships a minimal :class:`ProvenanceTrace` carrying
    only the Solver-side provenance (``solver`` populated; ``control`` and
    ``orchestrate`` placeholder ``None``). Phase 3 fills in the other two
    fields. Phase 7 wires the hashchain through ``phoenix/ledger/``.

    The ``cloud_shots_recorded`` flag per Section 1 Decision 20 lives here
    rather than on Result so the asterisk surfaces whenever the trace is
    inspected, not only on the final Result envelope.
    """

    request_id: str
    solver: SolverProvenance | None = None
    control: Any = None  # Phase 3 defines ControlProvenance
    orchestrate: Any = None  # Phase 3 defines OrchestrateProvenance
    cloud_shots_recorded: bool = False


# ---------------------------------------------------------------------------
# Trinity Core pipeline data classes


@dataclass(frozen=True)
class PhysicsTask:
    """The input every Phoenix solve receives.

    Constructed at the front door (Section 5) once the request has been
    schema-validated and the actor authenticated. Flows through the pipeline:
    Solver consumes :attr:`physics_context`; Control consumes the
    :class:`CandidateAnswer` Solver produced; Orchestrate consumes the
    :class:`VerifiedAnswer` Control produced.
    """

    physics_context: Any  # synthesis.equations.base.PhysicsContext (vendored)
    tolerance: ToleranceSpec
    actor: Actor | None  # vendored Actor; Phase 6 enforces verification
    request_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateAnswer:
    """What Solver produces.

    Phase 2's pipeline returns a :class:`CandidateAnswer` directly from the
    front door (with a ``phase: phase_2_solver_only`` provenance marker)
    because Phase 3's Control + Orchestrate aren't wired yet. Phase 3
    promotes the endpoint to return a full :class:`Result` envelope.
    """

    solver_id: str  # ``"<regime>/<solver_class_name>"`` per Section 2.3
    value: Any  # the requested observable (energy, state, distribution, ...)
    error_bar_solver: float  # cross-precision wobble result (Axis 1)
    sigma_solver: float  # standard-deviation form of error_bar_solver
    solver_kpi_bundle: dict[str, Any] = field(default_factory=dict)
    provenance_solver: SolverProvenance | None = None


@dataclass(frozen=True)
class VerifiedAnswer:
    """What Control produces. Phase 3 wires the producer.

    Defined in Phase 2 for forward-compat so Phase 3's Control wiring plugs in
    without churning the data model. Forward-compat fields use ``Any`` where
    Phase 3 hasn't tightened the type yet.
    """

    rho_verified: np.ndarray  # post-DPD density matrix
    dpd_result: Any  # synthesis.core.dpd_engine.DPDResult (vendored, Phase 3)
    kpi_bundle_control: dict[str, Any] = field(default_factory=dict)
    error_bar_control: float = 0.0  # cross-control wobble result (Axis 2)
    probe_strengths_used: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class Result:
    """Final output of any Phoenix solve. Produced by Orchestrate.

    Phase 3 wires the producer. Phase 2's pipeline returns
    :class:`CandidateAnswer` directly (Solver-only) and surfaces the
    incomplete state via the ``phase: phase_2_solver_only`` provenance marker.

    Per Section 2.2: ``error_bar`` is the quadrature sum of the three layer
    error bars; ``sigma`` is the standard wobble disagreement metric across
    all three axes; ``agreement_type`` is the typed disagreement classification
    (vendored ``DisagreementType`` from ``wobble/disagreement_types.py``).
    """

    value: Any
    error_bar: float
    sigma: float
    agreement_type: DisagreementType
    kpi_bundle_orchestrate: dict[str, Any] = field(default_factory=dict)
    provenance: ProvenanceTrace | None = None
