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
    OrchestrateProvenance,
    PhysicsTask,
    Result,
    VerifiedAnswer,
)
from phoenix.trinity.orchestrate.engine import orchestrate
from phoenix.trinity.orchestrate.provider_client import OrchestrateProviderError
from phoenix.trinity.solver.engine import SolverRunResult
from phoenix.verification.gate import VerificationGate
from phoenix.verification.wobble_axis import (
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
_GATE: VerificationGate | None = None


def _get_gate() -> VerificationGate:
    """Lazy module-level VerificationGate singleton.

    Tests can override by monkey-patching ``phoenix.trinity.pipeline._GATE``
    with a custom :class:`VerificationGate` instance pointing at a
    fixture :class:`Router`.
    """
    global _GATE
    if _GATE is None:
        _GATE = VerificationGate(_get_router())
    return _GATE


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

    # Phase 5: delegate the entire three-layer flow to the verification
    # gate. The gate handles rung selection (replaces Phase 3's hardcoded
    # _DEFAULT_DEPTH), reactive promotion, drift fail-closed wiring,
    # Axis 1 + Axis 2 + Axis 3 dispatch, distance matrix composition,
    # and agreement classification. The gate's verify() returns a fully-
    # composed Result with VerificationProvenance attached.
    return _get_gate().verify(task)
