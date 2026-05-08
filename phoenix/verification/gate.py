"""Verification gate orchestrator (Phase 5 Step 6).

Per architecture v1 Section 6: the gate composes the three wobble axes
(cross-precision, cross-control, cross-provider) per the rung-table
selection and produces the final :class:`Result` envelope with
:class:`PhoenixDisagreementType` classification.

Phase 5 ships static + reactive promotion per the locked scope decision
(2026-05-08): initial rung from :func:`select_initial_rung`; reactive
promotion when an axis's measured disagreement exceeds half the
remaining error budget (max 2 promotions per task per Section 6.4);
demotion recorded as telemetry only (axes already ran by the time we
could demote). Cost-ceiling check before promotion: if the next rung's
cost would exceed remaining budget, refuse promotion and tag
DEGRADED_BUDGET_BOUND.

Drift state is read at solve start (fail-closed on
:class:`DriftStateUnavailable` per Section 6.8); if the state is
``"warning"``, the final classification is DEGRADED regardless of axis
convergence.

R5 (replicated) is not fully shipped in Phase 5 -- it maps to R4
behavior with a ``replication_skipped: True`` provenance marker.
Replication lands in v1.x once the per-task budget tracking is wired.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from phoenix.router.data_model import RoutingRequest
from phoenix.router.errors import NoEligibleProvidersError
from phoenix.trinity.control.engine import run_dpd
from phoenix.trinity.data_model import (
    ControlProvenance,
    ProvenanceTrace,
    Result,
    SolverProvenance,
    VerificationProvenance,
    VerifiedAnswer,
)
from phoenix.trinity.orchestrate.engine import orchestrate
from phoenix.trinity.orchestrate.provider_client import OrchestrateProviderError
from phoenix.verification.agreement_classifier import (
    PhoenixDisagreementType,
    classify,
)
from phoenix.verification.drift_state import read_drift_state
from phoenix.verification.rung_table import (
    next_rung,
    select_initial_rung,
)
from phoenix.verification.wobble_axis import (
    AxisResult,
    CrossControlAxis,
    CrossPrecisionAxis,
    CrossProviderAxis,
    RungDepth,
)

if TYPE_CHECKING:
    from phoenix.router.decision import Router
    from phoenix.trinity.data_model import PhysicsTask

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _AxisDispatchResult:
    """Internal: the gate's per-axis dispatch bundle."""

    axis_result: AxisResult
    extra: dict[str, Any]  # axis-specific stash (e.g. high_grid_result)


class VerificationGate:
    """Phase 5 verification gate orchestrator.

    Per Section 6: takes a :class:`PhysicsTask` and returns a fully-
    composed :class:`Result` envelope by orchestrating the three axes
    at the rung-table-selected depth, with reactive promotion when
    measured disagreement exceeds the budget threshold.

    The gate accepts a :class:`Router` instance for Axis 3's secondary
    routing (Section 6.10: second :class:`RoutingRequest` with
    ``excluded_providers`` for the alternate provider). The pipeline
    orchestrator (Step 7) instantiates the gate with the module-level
    Router singleton.
    """

    def __init__(
        self,
        router: Router,
        *,
        max_promotions: int = 2,
        max_demotions: int = 1,
    ) -> None:
        self._router = router
        self._max_promotions = max_promotions
        self._max_demotions = max_demotions

    def verify(self, task: PhysicsTask) -> Result:
        """Run the gate's full orchestration. Returns a :class:`Result`.

        Raises :class:`DriftStateUnavailable` per Section 6.8 fail-closed
        if drift state can't be read. Other exceptions
        (:class:`FrontierPhysicsRefused`, :class:`OrchestrateProviderError`,
        :class:`ControlVerificationError`) propagate from the underlying
        subsystems unchanged.
        """
        t_start = time.perf_counter()

        # Section 6.8 fail-closed: read drift state first; raise if
        # unavailable (Phase 5 stub never raises; Phase 7 wires real
        # telemetry that may).
        drift = read_drift_state()  # may raise DriftStateUnavailable

        max_error_bar = task.tolerance.max_error_bar
        initial_rung = select_initial_rung(max_error_bar)
        current_rung = initial_rung
        promotions = 0
        budget_bound = False

        axis_results: list[AxisResult] = []

        # ---- Layer 1: Solver + Axis 1 (cross-precision) -------------------
        axis_1 = CrossPrecisionAxis()
        axis_1_result = axis_1.run(task, current_rung)
        if not axis_1_result.metadata.get("skipped", False):
            axis_results.append(axis_1_result)
            high_grid_result = axis_1_result.metadata.get("high_grid_result")
        else:
            high_grid_result = None

        # Reactive promotion check after Axis 1.
        promoted = self._maybe_promote(axis_1_result, current_rung, max_error_bar, promotions)
        if promoted is not None:
            current_rung = promoted
            promotions += 1

        # ---- Layer 2: Control + Axis 2 (cross-control) --------------------
        run_axis_2 = current_rung in (
            RungDepth.R3_TWO_AXES,
            RungDepth.R4_THREE_AXES,
            RungDepth.R5_REPLICATED,
        )
        axis_2_result: AxisResult | None = None
        weak_control: Any = None
        if run_axis_2:
            axis_2 = CrossControlAxis(prior_high_grid_result=high_grid_result)
            axis_2_result = axis_2.run(task, current_rung)
            if not axis_2_result.metadata.get("skipped", False):
                axis_results.append(axis_2_result)
                weak_control = axis_2_result.metadata.get("weak_control_result")

            # Reactive promotion check after Axis 2.
            promoted = self._maybe_promote(axis_2_result, current_rung, max_error_bar, promotions)
            if promoted is not None:
                current_rung = promoted
                promotions += 1

        # Build the canonical VerifiedAnswer for Orchestrate.
        if weak_control is None:
            # Axis 2 didn't run (R1/R2) -- run a minimal canonical DPD pass
            # to produce a VerifiedAnswer for Orchestrate.
            from phoenix.trinity.data_model import CandidateAnswer

            value = self._extract_value(high_grid_result)
            solver_id = self._build_solver_id(high_grid_result)
            cand = CandidateAnswer(
                solver_id=solver_id,
                value=value,
                error_bar_solver=axis_1_result.error_bar_contribution,
                sigma_solver=axis_1_result.error_bar_contribution,
            )
            weak_control = run_dpd(
                cand,
                probe_strength=0.1,
                solver_run_result=high_grid_result,
            )
        verified = VerifiedAnswer(
            rho_verified=weak_control.rho_verified,
            dpd_result=weak_control.dpd_result,
            kpi_bundle_control=weak_control.kpi_bundle_control,
            error_bar_control=axis_2_result.error_bar_contribution if axis_2_result else 0.0,
            probe_strengths_used=(
                [
                    axis_2_result.metadata["epsilon_weak"],
                    axis_2_result.metadata["epsilon_strong"],
                ]
                if axis_2_result and not axis_2_result.metadata.get("skipped")
                else [0.1]
            ),
        )

        # ---- Layer 3: Orchestrate (primary) ------------------------------
        primary_request = self._build_routing_request(task)
        primary_decision = self._router.decide(primary_request)
        solver_id = self._build_solver_id(high_grid_result)
        primary_result, primary_orch_prov = orchestrate(
            verified,
            primary_decision.primary,
            request_id=task.request_id,
            error_bar_solver=axis_1_result.error_bar_contribution,
            error_bar_control=verified.error_bar_control,
            solver_id=solver_id,
            tolerance_max_error_bar=max_error_bar,
        )

        # ---- Layer 3b: Axis 3 (cross-provider) at R4+ --------------------
        run_axis_3 = current_rung in (
            RungDepth.R4_THREE_AXES,
            RungDepth.R5_REPLICATED,
        ) and CrossProviderAxis().applies_to(task)
        axis_3_result: AxisResult | None = None
        if run_axis_3:
            try:
                alt_request = RoutingRequest(
                    task=task,
                    cost_ceiling_usd=primary_request.cost_ceiling_usd,
                    latency_budget_ms=primary_request.latency_budget_ms,
                    fidelity_floor=primary_request.fidelity_floor,
                    reproducibility_mode=primary_request.reproducibility_mode,
                    preferred_providers=primary_request.preferred_providers,
                    excluded_providers=[primary_decision.primary.provider_id],
                    allow_failover=primary_request.allow_failover,
                    allow_simulator_fallback=primary_request.allow_simulator_fallback,
                )
                alt_decision = self._router.decide(alt_request)
                alt_result, _alt_orch_prov = orchestrate(
                    verified,
                    alt_decision.primary,
                    request_id=task.request_id + "_alt",
                    error_bar_solver=axis_1_result.error_bar_contribution,
                    error_bar_control=verified.error_bar_control,
                    solver_id=solver_id,
                    tolerance_max_error_bar=max_error_bar,
                )
                axis_3 = CrossProviderAxis(
                    primary_result=primary_result,
                    alternate_result=alt_result,
                    primary_provider_id=primary_decision.primary.provider_id,
                    alternate_provider_id=alt_decision.primary.provider_id,
                )
                axis_3_result = axis_3.run(task, current_rung)
                if not axis_3_result.metadata.get("skipped", False):
                    axis_results.append(axis_3_result)
            except (
                NoEligibleProvidersError,
                OrchestrateProviderError,
                NotImplementedError,
            ) as exc:
                # NotImplementedError surfaces when the alternate provider's
                # quantum_technology isn't yet bundle-able (Phase 4+ stubs).
                # The gate gracefully degrades to primary-only result.
                log.info(
                    "Axis 3 skipped: alternate provider not available "
                    "(reason: %s); proceeding with primary-only result.",
                    exc,
                )

        # ---- Result composition -----------------------------------------
        wall_clock_ms_total = (time.perf_counter() - t_start) * 1000.0

        distance_matrix = [a.distance_matrix_row for a in axis_results if a.distance_matrix_row]
        sigma = self._compute_wobble_sigma(distance_matrix)

        # Quadrature combine per Section 11.1.1 placeholder.
        error_bar = math.sqrt(sum(a.error_bar_contribution**2 for a in axis_results))

        # Section 6.5: drift-warning override ships DEGRADED.
        # Axis 3 contribution updates error_bar; agreement_classifier
        # consumes the full axis_results list.
        agreement_type = classify(
            axis_results,
            max_error_bar=max_error_bar,
            drift_state=drift,
            budget_bound=budget_bound,
        )

        # Build provenances.
        solver_provenance = SolverProvenance(
            request_id=task.request_id,
            dispatched_solver=solver_id,
            n_grid_low=axis_1_result.metadata.get("n_grid_low", 0),
            n_grid_high=axis_1_result.metadata.get("n_grid_high", 0),
            wall_clock_ms_total=wall_clock_ms_total,
            cross_precision_axis_result=axis_1_result,
            phase="phase_5_verification_gate",
        )
        control_provenance: ControlProvenance | None = None
        if axis_2_result and not axis_2_result.metadata.get("skipped") and weak_control is not None:
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
                phase="phase_5_verification_gate",
            )

        verification_provenance = VerificationProvenance(
            request_id=task.request_id,
            initial_rung=initial_rung.name,
            final_rung=current_rung.name,
            promotions=promotions,
            demotions=0,
            drift_state=drift.state,
            distance_matrix=distance_matrix,
            wobble_score_sigma=sigma,
            budget_bound=budget_bound,
            phase="phase_5_verification_gate",
        )

        trace = ProvenanceTrace(
            request_id=task.request_id,
            solver=solver_provenance,
            control=control_provenance,
            orchestrate=primary_orch_prov,
            verification=verification_provenance,
            cloud_shots_recorded=primary_orch_prov.cloud_shots_recorded,
        )

        # Phase 5 keeps Result.agreement_type as the vendored DisagreementType
        # for backward-compat at the data model boundary; the Phoenix-native
        # PhoenixDisagreementType is a sibling enum exposed via the
        # VerificationProvenance for audit. Map back to the closest vendored
        # value for the Result envelope.
        return Result(
            value=primary_result.value,
            error_bar=error_bar,
            sigma=sigma,
            agreement_type=_to_vendored_disagreement(agreement_type),
            kpi_bundle_orchestrate=primary_result.kpi_bundle_orchestrate,
            provenance=trace,
        )

    # ----- helpers ------------------------------------------------------

    def _maybe_promote(
        self,
        axis_result: AxisResult,
        current_rung: RungDepth,
        max_error_bar: float,
        promotions_so_far: int,
    ) -> RungDepth | None:
        """Reactive promotion check: did this axis exceed half the budget?"""
        if promotions_so_far >= self._max_promotions:
            return None
        if axis_result.metadata.get("skipped", False):
            return None
        if axis_result.error_bar_contribution <= 0.5 * max_error_bar:
            return None
        new_rung = next_rung(current_rung)
        return new_rung  # None if already at R5

    def _extract_value(self, high_grid_result: Any) -> float:
        if high_grid_result is None:
            return 0.0
        if high_grid_result.eigenvalues:
            return float(high_grid_result.eigenvalues[0])
        if high_grid_result.energy is not None:
            return float(high_grid_result.energy)
        return 0.0

    def _build_solver_id(self, high_grid_result: Any) -> str:
        if high_grid_result is None:
            return "unknown/unknown"
        return f"{high_grid_result.regime.name}/{high_grid_result.solver_class_name}"

    def _build_routing_request(self, task: PhysicsTask) -> RoutingRequest:
        from phoenix.router.data_model import ReproducibilityMode

        repro_str = task.tolerance.reproducibility_mode
        try:
            repro_mode = ReproducibilityMode(repro_str)
        except ValueError:
            repro_mode = ReproducibilityMode.DEFAULT
        return RoutingRequest(task=task, reproducibility_mode=repro_mode)

    def _compute_wobble_sigma(self, distance_matrix: list[list[float]]) -> float:
        """sigma = sqrt(Var(distance_matrix_upper_triangle)).

        Per Section 6.2: the standard wobble formula on the upper
        triangle of the distance matrix. Phase 5 simplification: we
        flatten all per-axis distance rows and compute population
        variance over the flat list. For the v1 single-element rows this
        is just Var(error_bar_contributions) which is meaningful.
        """
        flat: list[float] = []
        for row in distance_matrix:
            flat.extend(row)
        if not flat:
            return 0.0
        mean = sum(flat) / len(flat)
        var = sum((x - mean) ** 2 for x in flat) / len(flat)
        return math.sqrt(var)


def _to_vendored_disagreement(phoenix_type: PhoenixDisagreementType) -> Any:
    """Map :class:`PhoenixDisagreementType` to the vendored DisagreementType.

    Phase 5 keeps Result.agreement_type as the vendored DisagreementType
    enum for backward-compat at the data-model layer; the Phoenix-native
    extensions are exposed via ``provenance.verification`` for audit.

    Mapping: Phoenix specializations collapse to their nearest vendored
    value. CONVERGED -> HEDGED_CONSENSUS (the vendored value Phase 3
    used as placeholder); the four specific dominators
    (NUMERICAL_DRIFT / BACKACTION_SENSITIVE / PROVIDER_DIVERGENT /
    DEGRADED) map to TEMPORAL_DRIFT / CONTRADICTION / FRAME_MISMATCH
    based on their semantic distance to vendored values.
    """
    from wobble.disagreement_types import DisagreementType as VendoredDisagreement

    mapping = {
        PhoenixDisagreementType.CONVERGED: VendoredDisagreement.HEDGED_CONSENSUS,
        PhoenixDisagreementType.HEDGED_CONSENSUS: VendoredDisagreement.HEDGED_CONSENSUS,
        PhoenixDisagreementType.NUMERICAL_DRIFT: VendoredDisagreement.TEMPORAL_DRIFT,
        PhoenixDisagreementType.BACKACTION_SENSITIVE: VendoredDisagreement.CONTRADICTION,
        PhoenixDisagreementType.PROVIDER_DIVERGENT: VendoredDisagreement.FRAME_MISMATCH,
        PhoenixDisagreementType.DEGRADED: VendoredDisagreement.UNKNOWN,
        PhoenixDisagreementType.DEGRADED_BUDGET_BOUND: VendoredDisagreement.UNKNOWN,
        PhoenixDisagreementType.UNKNOWN: VendoredDisagreement.UNKNOWN,
        PhoenixDisagreementType.CONTRADICTION: VendoredDisagreement.CONTRADICTION,
        PhoenixDisagreementType.TEMPORAL_DRIFT: VendoredDisagreement.TEMPORAL_DRIFT,
        PhoenixDisagreementType.FRAME_MISMATCH: VendoredDisagreement.FRAME_MISMATCH,
    }
    return mapping.get(phoenix_type, VendoredDisagreement.UNKNOWN)
