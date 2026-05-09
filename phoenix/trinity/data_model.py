"""Trinity Core typed data model.

Per architecture v1.1 Section 2.2: the four dataclasses that flow through
Trinity Core's pipeline (Solver -> Control -> Orchestrate) plus their
supporting provenance types.

- :class:`PhysicsTask` -- input from the front door (REST/WS/CLI/MCP).
- :class:`CandidateAnswer` -- what Solver produces.
- :class:`VerifiedAnswer` -- what Control produces (Phase 3 wires the producer).
- :class:`Result` -- final output from Orchestrate (Phase 3 wires the producer).

Phase 2 shipped all four dataclasses with ``Any``/``dict[str, Any]``
forward-compat placeholders so Phase 3's Control + Orchestrate work plugs in
without churning call sites. Phase 3 tightens those fields:

- :attr:`VerifiedAnswer.dpd_result` -- ``Any`` -> :class:`DPDResult`
  (vendored from :mod:`synthesis.core.dpd_engine`).
- :attr:`VerifiedAnswer.kpi_bundle_control` -- ``dict[str, Any]`` ->
  :class:`KPIBundle` (Phoenix-native typed dataclass per Section 2.5).
- :attr:`Result.kpi_bundle_orchestrate` -- ``dict[str, Any]`` ->
  :class:`KPIBundle`.
- :attr:`ProvenanceTrace.control` -- ``Any`` -> :class:`ControlProvenance` | None.
- :attr:`ProvenanceTrace.orchestrate` -- ``Any`` -> :class:`OrchestrateProvenance` | None.

Phase 3 also adds two new provenance dataclasses (:class:`ControlProvenance`,
:class:`OrchestrateProvenance`) populated by Trinity Core's two newly-wired
subsystems.

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
    from synthesis.core.dpd_engine import DPDResult
    from wobble.disagreement_types import DisagreementType

    from phoenix.trinity.orchestrate.kpi_bundle import KPIBundle
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
class ControlProvenance:
    """Control-side provenance trace (Phase 3).

    Captures DPD execution metadata for the canonical (weak-probe) run. The
    cross-control axis result lands separately in
    :attr:`cross_control_axis_result` so the verification gate's distance
    matrix can compose Axis 2 alongside Axes 1 and 3 without re-extracting
    from the trace.

    Fields:
        request_id: Echoes :attr:`PhysicsTask.request_id` for ledger join.
        dpd_n_blocks: Number of :class:`DPDBlock` instances the scheduler
            executed. Phase 3 ships a single block per run; Phase 5+ may
            ship adaptive multi-block sequences.
        probe_strengths_used: ``[ε_canonical]`` for Phase 3's R3 path; Axis
            2's strong-probe leg is captured in
            :attr:`cross_control_axis_result.metadata`.
        total_backaction: From :attr:`DPDResult.total_backaction` for the
            canonical run. SAFETY-relevant; flowed into the KPI bundle.
        trace_preservation: Should be ``≈ 1.0``. Drift > 1e-3 raises
            :class:`ControlVerificationError` at the engine boundary.
        positivity_check: From :attr:`DPDResult.positivity_check`. Must be
            ``True``; ``False`` raises :class:`ControlVerificationError`.
        wall_clock_ms: Wall-clock time spent in ``DPDScheduler``.
        cross_control_axis_result: The Axis 2 :class:`AxisResult` (or
            ``None`` if Axis 2 was skipped at this rung depth).
        phase: ``"phase_3_solver_control_orchestrate"`` for Phase 3 honesty.
    """

    request_id: str
    dpd_n_blocks: int
    probe_strengths_used: list[float]
    total_backaction: float
    trace_preservation: float
    positivity_check: bool
    wall_clock_ms: float
    cross_control_axis_result: AxisResult | None = None
    phase: str = "phase_3_solver_control_orchestrate"


@dataclass(frozen=True)
class OrchestrateProvenance:
    """Orchestrate-side provenance trace (Phase 3).

    Captures the dispatched provider, backend, bundle hash, and shot
    accounting per architecture v1 Section 2.5. The
    :attr:`cloud_shots_recorded` flag per Section 1 Decision 20 originates
    here (sourced from :attr:`ProviderRawResult.cloud_shots_recorded` by the
    provider client) and is mirrored on :class:`ProvenanceTrace` so the
    asterisk surfaces wherever the trace is inspected.

    Fields:
        request_id: Echoes :attr:`PhysicsTask.request_id`.
        provider_id: Stable provider identifier (e.g.
            ``"phoenix.local_simulator"`` for Phase 3's only adapter).
        backend_name: Provider-specific backend name.
        quantum_technology: ``"simulation"`` / ``"superconducting"`` / etc.
        shots_used: Actual shot count consumed (may differ from budget for
            cloud providers; identical for local sim).
        latency_us: Wall-clock latency reported by the provider client.
        bundle_hash: Stable 16-char SHA-256 prefix of the canonical-JSON
            submission payload. Phase 7's ledger uses this as the bundle
            fingerprint for replay.
        cloud_shots_recorded: ``True`` when any cloud provider was invoked
            (Phase 4+); always ``False`` for Phase 3's local-simulator path.
        wall_clock_ms: Total Orchestrate-side wall-clock (bundle build +
            submit + extract + drift emit).
        phase: ``"phase_3_solver_control_orchestrate"`` for Phase 3 honesty.
    """

    request_id: str
    provider_id: str
    backend_name: str
    quantum_technology: str
    shots_used: int
    latency_us: float
    bundle_hash: str
    cloud_shots_recorded: bool = False
    wall_clock_ms: float = 0.0
    phase: str = "phase_3_solver_control_orchestrate"


@dataclass(frozen=True)
class VerificationProvenance:
    """Verification-gate provenance trace (Phase 5).

    Captures the gate's per-task decisions: initial rung, final rung,
    promotion/demotion history, drift state at solve time, and the
    full distance-matrix-derived sigma.

    Fields:
        request_id: Echoes :attr:`PhysicsTask.request_id`.
        initial_rung: :class:`RungDepth` selected from
            ``task.tolerance.max_error_bar`` per Section 6.4 indicative
            thresholds.
        final_rung: :class:`RungDepth` actually exercised (after
            promotions / demotions). Same as ``initial_rung`` for static
            paths.
        promotions: Number of reactive promotions per Section 6.4 (max 2
            per task).
        demotions: Number of reactive demotions (max 1 per task; Phase
            5 records as telemetry without a functional change since
            axes already ran).
        drift_state: Drift-state classification at solve time (``healthy``
            / ``warning`` / ``unavailable``). Phase 5 reads from
            :func:`phoenix.verification.drift_state.read_drift_state`
            stub; Phase 7 wires real telemetry.
        distance_matrix: Full per-axis distance rows, preserved per
            Section 6.2 DO-NOT-COLLAPSE invariant.
        wobble_score_sigma: ``sqrt(Var(distance_matrix_upper_triangle))``
            -- standard wobble disagreement metric across all axes.
        budget_bound: ``True`` when a promotion was refused because the
            cost ceiling can't accommodate the next rung. Drives the
            DEGRADED_BUDGET_BOUND classification.
        phase: ``"phase_5_verification_gate"`` for Phase 5 honesty.
    """

    request_id: str
    initial_rung: str
    final_rung: str
    promotions: int = 0
    demotions: int = 0
    drift_state: str = "healthy"
    distance_matrix: list[list[float]] = field(default_factory=list)
    wobble_score_sigma: float = 0.0
    budget_bound: bool = False
    phase: str = "phase_5_verification_gate"


@dataclass(frozen=True)
class ProvenanceTrace:
    """Full per-solve audit trail.

    Per Section 1 Decision 15: every Phoenix solve produces a hashchained
    ledger entry. Phase 2 shipped only Solver-side provenance with the other
    two fields placeholder ``None``; Phase 3 tightens the field types and
    populates ``control`` + ``orchestrate``. Phase 7 wires the hashchain
    through ``phoenix/ledger/``.

    The ``cloud_shots_recorded`` flag per Section 1 Decision 20 lives here
    rather than on :class:`Result` so the asterisk surfaces whenever the
    trace is inspected, not only on the final :class:`Result` envelope.
    Phase 3's Orchestrate engine mirrors :attr:`OrchestrateProvenance.cloud_shots_recorded`
    onto this field.
    """

    request_id: str
    solver: SolverProvenance | None = None
    control: ControlProvenance | None = None
    orchestrate: OrchestrateProvenance | None = None
    verification: VerificationProvenance | None = None
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
    """What Control produces.

    Phase 2 defined the dataclass with ``Any`` placeholders for forward-compat;
    Phase 3 tightens those to typed shapes. ``dpd_result`` now references the
    vendored :class:`synthesis.core.dpd_engine.DPDResult`; ``kpi_bundle_control``
    is the Phoenix-native :class:`KPIBundle` per architecture Section 2.5.
    """

    rho_verified: np.ndarray  # post-DPD density matrix
    dpd_result: DPDResult  # vendored from synthesis.core.dpd_engine
    kpi_bundle_control: KPIBundle  # Phoenix-native typed KPI aggregate
    error_bar_control: float = 0.0  # cross-control wobble result (Axis 2)
    probe_strengths_used: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class Result:
    """Final output of any Phoenix solve. Produced by Orchestrate.

    Phase 3 wires the producer end-to-end: Orchestrate's :func:`engine.orchestrate`
    builds the envelope from a :class:`VerifiedAnswer` plus a
    :class:`ProviderSelection`. The Phase 2 ``phase: phase_2_solver_only``
    honesty marker is retired in Phase 3.

    Per Section 2.2: ``error_bar`` is the quadrature sum of the three layer
    error bars (the combiner formula is OPEN per Section 11.1.1, refined at
    v1.1 with empirical covariance data); ``sigma`` is the standard wobble
    disagreement metric across all three axes; ``agreement_type`` is the
    typed disagreement classification (vendored :class:`DisagreementType`
    from ``wobble/disagreement_types.py``); ``kpi_bundle_orchestrate``
    tightened from ``dict[str, Any]`` to :class:`KPIBundle` in Phase 3 per
    architecture Section 2.5.
    """

    value: Any
    error_bar: float
    sigma: float
    agreement_type: DisagreementType
    kpi_bundle_orchestrate: KPIBundle  # Phoenix-native typed KPI aggregate
    provenance: ProvenanceTrace | None = None
