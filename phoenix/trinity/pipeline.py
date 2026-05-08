"""Trinity Core pipeline orchestrator.

Per architecture v1 Section 2.3: every Phoenix solve flows through Trinity
Core's three peer engines (Solver, Control, Orchestrate). Phase 2 shipped
the Solver-only path returning a :class:`CandidateAnswer`. Phase 3 wires
Control + Orchestrate end-to-end and promotes the return type to a full
:class:`Result` envelope. The ``phase_2_solver_only`` honesty marker is
retired in Phase 3.

Phase 3 responsibilities:

1. **Latency-tier gate** -- unchanged from Phase 2. ``BATCH_REALTIME`` is
   the only tier v1 routes; the others raise
   :class:`LatencyTierNotImplemented`.
2. **Frontier-physics gate** -- enforced one layer down by
   :func:`engine.pick_solver` (architecture Section 1 Decision 7). The
   pipeline does not re-check; the engine-boundary check is authoritative.
3. **Solver + Axis 1** -- runs :class:`CrossPrecisionAxis` at the default
   depth :attr:`RungDepth.R3_TWO_AXES`. Phase 2 shipped R2; Phase 3
   promotes to R3 since Axis 2 now exists. Phase 5's verification gate
   composes adaptive rung selection driven by ``max_error_bar``.
4. **Control + Axis 2** -- runs :class:`CrossControlAxis` injected with
   the high-grid :class:`SolverRunResult` from Axis 1's metadata
   (avoiding a redundant solver invocation). The weak-probe leg of Axis 2
   doubles as the canonical run for :class:`VerifiedAnswer.rho_verified`,
   saving one DPD invocation per solve (~1-2 s on QHO).
5. **Orchestrate** -- builds a default :class:`ProviderSelection` pointing
   at :class:`LocalClassicalSimulator` (Phase 4's Router replaces this),
   then sequences the six Orchestrate modules via
   :func:`orchestrate.engine.orchestrate`.
6. **Provenance composition** -- stitches Solver + Control + Orchestrate
   provenances into a :class:`ProvenanceTrace` and attaches it to the
   returned :class:`Result`. The ``cloud_shots_recorded`` flag mirrors
   onto the trace per Section 1 Decision 20.

PERF: R3 default doubles solver runs (Axis 1: N + 2N, ~10-30 ms each) and
adds two DPD runs (eps=0.1 weak + eps=0.5 strong, ~1-2 s each on the
default 10 ns drive at dt=1e-12 RK4 step). Local simulator adds <1 ms.
Total wall-clock is ~2-4 s per solve at R3 vs Phase 2's ~50 ms at R2.
Phase 5's adaptive rung selection lets loose-tolerance tasks demote to R2.
"""

from __future__ import annotations

import dataclasses
import time

from phoenix._internal.latency import LatencyTier, LatencyTierNotImplemented
from phoenix.router.data_model import (
    ProviderSelection,
    ReproducibilityMode,
    RoutingDecision,
    RoutingRequest,
)
from phoenix.router.decision import Router
from phoenix.router.errors import AllAlternatesExhausted
from phoenix.router.failover import FailoverProtocol
from phoenix.router.provider_registry import build_default_registry
from phoenix.trinity.control.engine import (
    ControlVerificationError as ControlVerificationError,  # re-export for docstring + tests
)
from phoenix.trinity.data_model import (
    ControlProvenance,
    OrchestrateProvenance,
    PhysicsTask,
    ProvenanceTrace,
    Result,
    SolverProvenance,
    VerifiedAnswer,
)
from phoenix.trinity.orchestrate.engine import orchestrate
from phoenix.trinity.orchestrate.provider_client import OrchestrateProviderError
from phoenix.trinity.solver.engine import SolverRunResult
from phoenix.verification.wobble_axis import (
    AxisResult,
    CrossControlAxis,
    CrossPrecisionAxis,
    RungDepth,
)

# Phase 3 default depth promotes from Phase 2's R2 (Solver-only) to R3
# (Solver + Control). Phase 5's verification gate ships adaptive selection.
_DEFAULT_DEPTH = RungDepth.R3_TWO_AXES


def _enforce_latency_tier(task: PhysicsTask) -> None:
    """Refuse non-routable latency tiers (unchanged from Phase 2)."""
    tier = task.tolerance.latency_tier
    if tier is LatencyTier.BATCH_REALTIME:
        return
    if tier is LatencyTier.STREAMING_REALTIME:
        raise LatencyTierNotImplemented(
            tier=tier,
            reason=(
                f"Latency tier {tier.value!r} is defined-but-not-routable in "
                f"Phoenix v1. Streaming-realtime (sub-millisecond loops, "
                f"standing-computation API) lands in v2 per architecture "
                f"Section 1 Decision 28. Submit this task with "
                f"latency_tier=BATCH_REALTIME or wait for v2."
            ),
        )
    if tier is LatencyTier.PERCEPTION_REALTIME:
        raise LatencyTierNotImplemented(
            tier=tier,
            reason=(
                f"Latency tier {tier.value!r} is routed only by the perception "
                f"harness extension (Phase 12+ per architecture Section "
                f"11.14.7, locked 2026-05-08). Phoenix v1's quantum-solve "
                f"pipeline does not accept this tier. Submit with "
                f"latency_tier=BATCH_REALTIME for v1 quantum work."
            ),
        )
    raise LatencyTierNotImplemented(
        tier=tier,
        reason=(
            f"Latency tier {tier!r} is not routable in this Phoenix release. "
            f"Submit with latency_tier=BATCH_REALTIME."
        ),
    )


def _extract_value(high_grid_result: SolverRunResult) -> float:
    """Pull the canonical observable from the high-grid solver result.

    Phase 3 keeps Phase 2's logic: ground-state eigenvalue (or .energy
    fallback). Phase 5 extends to richer observables once the verified
    surface lands.
    """
    if high_grid_result.eigenvalues:
        return float(high_grid_result.eigenvalues[0])
    if high_grid_result.energy is not None:
        return float(high_grid_result.energy)
    return 0.0


# Module-level Router + FailoverProtocol singletons. Phase 4 ships
# in-process state; Phase 6+ wires the persistent state backend so registry
# health survives daemon restarts. The Router constructor pre-loads pricing,
# so deferring construction to first call also defers the JSON disk read.
_ROUTER: Router | None = None
_FAILOVER: FailoverProtocol | None = None


def _get_router() -> Router:
    """Lazy module-level Router singleton.

    Tests can override by monkey-patching ``phoenix.trinity.pipeline._ROUTER``
    to a custom :class:`Router` instance pointing at a fixture
    :class:`ProviderRegistry`.
    """
    global _ROUTER
    if _ROUTER is None:
        _ROUTER = Router(build_default_registry())
    return _ROUTER


def _get_failover() -> FailoverProtocol:
    """Lazy module-level FailoverProtocol singleton."""
    global _FAILOVER
    if _FAILOVER is None:
        _FAILOVER = FailoverProtocol(_get_router().registry)
    return _FAILOVER


def _build_routing_request(task: PhysicsTask) -> RoutingRequest:
    """Translate a :class:`PhysicsTask` into a :class:`RoutingRequest`.

    Phase 4 maps directly: latency tier already validated upstream,
    cost / latency / fidelity policy fields default to None (no caps),
    reproducibility_mode mapped from ``task.tolerance.reproducibility_mode``
    string. Future phases (especially Phase 8 admin policy) may inject
    per-actor cost ceilings here from the actor's tier.
    """
    repro_str = task.tolerance.reproducibility_mode
    try:
        repro_mode = ReproducibilityMode(repro_str)
    except ValueError:
        # Unknown reproducibility string -> default per the spec.
        repro_mode = ReproducibilityMode.DEFAULT
    return RoutingRequest(
        task=task,
        cost_ceiling_usd=None,
        latency_budget_ms=None,
        fidelity_floor=None,
        reproducibility_mode=repro_mode,
        preferred_providers=[],
        excluded_providers=[],
        allow_failover=True,
        allow_simulator_fallback=True,
    )


def _orchestrate_with_failover(
    verified: VerifiedAnswer,
    decision: RoutingDecision,
    *,
    request_id: str,
    error_bar_solver: float,
    error_bar_control: float,
    solver_id: str,
    tolerance_max_error_bar: float,
    allow_simulator_fallback: bool,
) -> tuple[Result, OrchestrateProvenance, ProviderSelection]:
    """Call orchestrate() with failover walking primary -> alternates.

    Returns ``(result, orchestrate_provenance, used_selection)`` -- the
    ``used_selection`` is the ProviderSelection that finally succeeded
    (primary, an alternate, or the simulator fallback). The pipeline
    composes the final ProvenanceTrace from these three.

    On every :class:`OrchestrateProviderError` the failover protocol
    quarantines the failed provider in the shared registry and walks to
    the next candidate. When all RoutingDecision candidates fail and
    ``allow_simulator_fallback`` is True, the failover wrapper falls
    back to a :class:`LocalClassicalSimulator` and continues; when
    False, raises :class:`AllAlternatesExhausted`.
    """
    failover = _get_failover()
    candidates: list[ProviderSelection] = [decision.primary, *decision.alternates]
    attempts: list[dict[str, str]] = []

    for selection in candidates:
        try:
            result, orch_prov = orchestrate(
                verified,
                selection,
                request_id=request_id,
                error_bar_solver=error_bar_solver,
                error_bar_control=error_bar_control,
                solver_id=solver_id,
                tolerance_max_error_bar=tolerance_max_error_bar,
            )
        except OrchestrateProviderError as exc:
            quarantine_until = failover.quarantine(selection.provider_id)
            attempts.append(
                {
                    "provider_id": selection.provider_id,
                    "reason": f"orchestrate_failed: {exc}",
                    "quarantine_until_utc": quarantine_until,
                }
            )
            continue
        return result, orch_prov, selection

    # All RoutingDecision candidates exhausted.
    if allow_simulator_fallback:
        # Fall back to a fresh LocalClassicalSimulator (not in the
        # registry's failure tracking; clean fallback path).
        from phoenix.providers.classical.local_simulator import LocalClassicalSimulator

        fallback_client = LocalClassicalSimulator()
        fallback_selection = ProviderSelection(
            provider_id=fallback_client.provider_id,
            backend_name=fallback_client.backend_name,
            quantum_technology=fallback_client.quantum_technology,
            client=fallback_client,
            selection_metadata={
                "phase": "phase_4_simulator_fallback",
                "fallback_reason": "all_alternates_exhausted",
                "attempts_before_fallback": len(attempts),
            },
        )
        try:
            result, orch_prov = orchestrate(
                verified,
                fallback_selection,
                request_id=request_id,
                error_bar_solver=error_bar_solver,
                error_bar_control=error_bar_control,
                solver_id=solver_id,
                tolerance_max_error_bar=tolerance_max_error_bar,
            )
        except OrchestrateProviderError as exc:
            attempts.append(
                {
                    "provider_id": fallback_client.provider_id,
                    "reason": f"simulator_fallback_failed: {exc}",
                }
            )
            raise AllAlternatesExhausted(
                "All alternates failed AND simulator fallback also failed.",
                attempts=attempts,
            ) from exc
        return result, orch_prov, fallback_selection

    raise AllAlternatesExhausted(
        "All alternates failed and allow_simulator_fallback=False.",
        attempts=attempts,
    )


def solve(task: PhysicsTask) -> Result:
    """Phase 3 three-layer pipeline entry point.

    Returns a :class:`Result` envelope carrying the dispatched solver's
    canonical value, the quadrature-combined error bar, the typed
    :class:`KPIBundle` from Orchestrate, and a full :class:`ProvenanceTrace`
    (Solver + Control + Orchestrate) with the
    ``phase_3_solver_control_orchestrate`` honesty marker.

    Raises:
        LatencyTierNotImplemented: ``task.tolerance.latency_tier`` is not
            ``BATCH_REALTIME``.
        FrontierPhysicsRefused: dispatched solver is in the frontier-physics
            set and ``task.tolerance.frontier_physics`` is False (raised by
            :func:`engine.pick_solver` one layer down).
        ControlVerificationError: DPD propagator violated trace preservation
            or positivity (raised by Control's run_dpd one layer down).
        OrchestrateProviderError: provider client submission failed (raised
            by Orchestrate's engine one layer down).
        ValueError: cross-precision logic could not compute disagreement
            (both eigenvalue lists empty AND energy is None on both grids).
    """
    _enforce_latency_tier(task)

    t_start = time.perf_counter()

    # ---- Layer 1: Solver + Axis 1 (cross-precision) -------------------------
    axis_1 = CrossPrecisionAxis()
    axis_1_result: AxisResult = axis_1.run(task, _DEFAULT_DEPTH)
    high_grid_result: SolverRunResult = axis_1_result.metadata["high_grid_result"]

    solver_id = f"{high_grid_result.regime.name}/{high_grid_result.solver_class_name}"
    error_bar_solver = axis_1_result.error_bar_contribution

    # Note: Phase 2 extracted the solver's ground-state value here and wrapped
    # it as a CandidateAnswer. Phase 3's Orchestrate engine produces the
    # canonical Result.value via the local-simulator's expectation
    # measurement (currently a placeholder trace ~1.0; Phase 5 wires real
    # observable extraction). The solver's ground-state value still
    # survives indirectly via :func:`_extract_value` on the high-grid result
    # when CrossControlAxis builds its internal CandidateAnswer; the
    # function is exported for that consumer and for Phase 5's expansion.

    # ---- Layer 2: Control + Axis 2 (cross-control) --------------------------
    # Inject Axis 1's high-grid result so Axis 2 doesn't re-run the solver.
    # PERF win: ~10-30 ms saved per solve (matches Phase 2's high_grid_result
    # caching pattern but applied to the Axis 2 setup path).
    axis_2 = CrossControlAxis(prior_high_grid_result=high_grid_result)
    axis_2_result: AxisResult = axis_2.run(task, _DEFAULT_DEPTH)

    # The weak-probe ControlRunResult was preserved in metadata by Step 4
    # specifically so this orchestrator can use it as the canonical
    # rho_verified without a third DPD invocation. PERF win: ~1-2 s saved
    # per solve at the default 10 ns drive.
    weak_control = axis_2_result.metadata["weak_control_result"]

    error_bar_control = axis_2_result.error_bar_contribution
    verified = VerifiedAnswer(
        rho_verified=weak_control.rho_verified,
        dpd_result=weak_control.dpd_result,
        kpi_bundle_control=weak_control.kpi_bundle_control,
        error_bar_control=error_bar_control,
        probe_strengths_used=[
            axis_2_result.metadata["epsilon_weak"],
            axis_2_result.metadata["epsilon_strong"],
        ],
    )

    # ---- Layer 3: Router decision + Orchestrate (with failover) ------------
    # Phase 4 replaced Phase 3's _build_default_provider_selection helper
    # with a real Router.decide call. The decision carries primary +
    # alternates ranked by Section 4.4 weighted score; the failover
    # wrapper walks them on OrchestrateProviderError, falling back to a
    # LocalClassicalSimulator when allow_simulator_fallback is True
    # (default). _used_selection records which provider actually
    # produced the Result -- lands in OrchestrateProvenance via the
    # provider_id field on the dispatched selection.
    routing_request = _build_routing_request(task)
    decision = _get_router().decide(routing_request)
    result, orchestrate_provenance, _used_selection = _orchestrate_with_failover(
        verified,
        decision,
        request_id=task.request_id,
        error_bar_solver=error_bar_solver,
        error_bar_control=error_bar_control,
        solver_id=solver_id,
        tolerance_max_error_bar=task.tolerance.max_error_bar,
        allow_simulator_fallback=routing_request.allow_simulator_fallback,
    )

    # ---- Provenance composition ---------------------------------------------
    wall_clock_ms_total = (time.perf_counter() - t_start) * 1000.0

    solver_provenance = SolverProvenance(
        request_id=task.request_id,
        dispatched_solver=solver_id,
        n_grid_low=axis_1_result.metadata["n_grid_low"],
        n_grid_high=axis_1_result.metadata["n_grid_high"],
        wall_clock_ms_total=wall_clock_ms_total,
        cross_precision_axis_result=axis_1_result,
        phase="phase_3_solver_control_orchestrate",
    )

    control_provenance = ControlProvenance(
        request_id=task.request_id,
        dpd_n_blocks=int(weak_control.dpd_result.n_blocks),
        probe_strengths_used=[
            axis_2_result.metadata["epsilon_weak"],
            axis_2_result.metadata["epsilon_strong"],
        ],
        total_backaction=float(weak_control.dpd_result.total_backaction),
        trace_preservation=float(weak_control.dpd_result.trace_preservation),
        positivity_check=bool(weak_control.dpd_result.positivity_check),
        wall_clock_ms=weak_control.wall_clock_ms,
        cross_control_axis_result=axis_2_result,
        phase="phase_3_solver_control_orchestrate",
    )

    trace = ProvenanceTrace(
        request_id=task.request_id,
        solver=solver_provenance,
        control=control_provenance,
        orchestrate=orchestrate_provenance,
        cloud_shots_recorded=orchestrate_provenance.cloud_shots_recorded,
    )

    # The Result returned by orchestrate() has provenance=None; attach the
    # composed trace via dataclasses.replace (Result is frozen).
    return dataclasses.replace(result, provenance=trace)
