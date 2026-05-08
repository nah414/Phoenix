"""Router decision algorithm: the seven-stage routing algorithm.

Per architecture v1 Section 4.4: every Phoenix solve flows through the
Router's :class:`Router.decide` method, which executes seven filtering /
ranking stages over the registered providers and returns a
:class:`RoutingDecision` (primary + alternates + decision_provenance).

The seven stages:

1. **Modality eligibility** -- filter candidates by whether their
   ``quantum_technology`` can execute the dispatched Hamiltonian regime.
   Phase 4 routes any task to providers with ``quantum_technology in
   {"simulation", "superconducting", "trapped_ion"}`` (the v1 covered set).
2. **User policy filter** -- drop candidates violating ``cost_ceiling_usd``,
   ``latency_budget_ms``, ``fidelity_floor``, or appearing in
   ``excluded_providers``.
3. **Provider health filter** -- drop providers with health
   :class:`ProviderHealth.DEGRADED` or :class:`ProviderHealth.OFFLINE`.
4. **Frontier-physics gate** -- defense-in-depth re-check (Section 7's
   safety gate is the primary check, Phase 6). If
   ``task.tolerance.frontier_physics`` is False AND any candidate's
   regime is in the frontier set, drop ALL candidates and raise
   :class:`FrontierPhysicsRefused`.
5. **Reproducibility constraint** -- if ``reproducibility_mode == REPLAY``,
   pin the primary to the recorded :class:`ProviderSelection` from the
   ledger entry being replayed; raise
   :class:`ReplayProviderUnavailable` if the recorded provider is gone.
   Phase 4 surface; the actual ledger-backed replay flow lands at Phase 7.
6. **Ranking** -- score surviving candidates via the Section 4.4 weighted
   formula. Pick highest as primary, next two as alternates.
7. **Decision provenance** -- record full input snapshot (candidates
   considered, filters applied at each stage, weights used,
   pricing-staleness flag) into ``decision_provenance``; lands in ledger
   entry per Section 1 Decision 15.

Phase 4 ships all seven stages with the Phase 4 scope cuts:
- Stage 5 REPLAY mode: validates the recorded provider is still
  registered; the actual replay-data plumbing waits for Phase 7.
- Frontier-physics regime detection in Stage 4: Phase 4 inspects the
  ``task.metadata["regime_hint"]`` if present; full classifier-based
  detection lands when verification gate composes Axis 3 (Phase 5).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from phoenix.router.data_model import (
    ProviderSelection,
    ReproducibilityMode,
    RoutingDecision,
    RoutingRequest,
)
from phoenix.router.equivalence_registry import filter_equivalent_alternates
from phoenix.router.errors import (
    CostCeilingExceeded,
    NoEligibleProvidersError,
    ReplayProviderUnavailable,
)
from phoenix.router.intelligence import estimate_all
from phoenix.router.pricing import is_pricing_stale, load_pricing
from phoenix.router.provider_registry import (
    ProviderEntry,
    ProviderHealth,
    ProviderRegistry,
)
from phoenix.trinity.solver.engine import (
    FRONTIER_REGIME_NAMES,
    FrontierPhysicsRefused,
)

if TYPE_CHECKING:
    pass  # all imports above are runtime-resolved

log = logging.getLogger(__name__)


# Stage 6 weights per Section 4.4 (instrument-grade defaults).
_W_FIDELITY = 0.4
_W_COST = 0.2
_W_LATENCY = 0.2
_W_QUEUE = 0.1
_W_PREF = 0.1


# Phase 4 modality whitelist. Section 1 names "simulation",
# "superconducting", "trapped_ion", "photonic", "neutral_atom" as the v1
# covered set; Phase 4 ships the first three (the providers we have
# adapters for, even as stubs).
_PHASE4_MODALITY_WHITELIST: frozenset[str] = frozenset(
    {"simulation", "superconducting", "trapped_ion"}
)


class Router:
    """Trinity Core Router subsystem.

    Per architecture v1 Section 4: takes a :class:`RoutingRequest` (task
    + user policy + actor) and returns a :class:`RoutingDecision`
    (chosen provider + alternates + cost / latency / fidelity estimates
    + decision_provenance).

    Phase 4 ships the seven-stage algorithm with the locked scope cuts
    (stub-only cloud adapters, drift drain to Phase 7, Source A
    intelligence). Phase 5+ extends with adaptive rung-table routing
    requests from the verification gate's Axis 3.
    """

    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        pricing_data: dict[str, Any] | None = None,
    ) -> None:
        self._registry = registry
        # Pre-load pricing once at Router construction; tests can pass
        # synthetic pricing tables for filter / staleness exercises.
        self._pricing = pricing_data if pricing_data is not None else load_pricing()

    @property
    def registry(self) -> ProviderRegistry:
        """Read-only-by-convention access to the underlying registry.
        The failover protocol (Step 8) updates entries via the registry's
        ``mark_*`` methods; no Router consumer should mutate directly."""
        return self._registry

    def decide(self, request: RoutingRequest) -> RoutingDecision:
        """Execute the seven-stage routing algorithm.

        Returns a :class:`RoutingDecision`. Raises:
        - :class:`NoEligibleProvidersError` if no provider survives.
        - :class:`CostCeilingExceeded` if Stage 2's cost filter alone
          rejects every candidate.
        - :class:`ReplayProviderUnavailable` if Stage 5 needs to pin to
          a provider that's no longer registered.
        - :class:`FrontierPhysicsRefused` if Stage 4's defense-in-depth
          re-check fires.
        """
        per_stage_filters: list[dict[str, Any]] = []
        candidates = self._registry.all_entries()
        initial_count = len(candidates)

        # Stage 1: modality eligibility.
        candidates = self._stage_1_modality(candidates, request, per_stage_filters)

        # Stage 4 first to short-circuit frontier-physics refusal before
        # cost / health filters surface less-actionable errors. The spec
        # numbers them 1-7 but the earlier the frontier check fires, the
        # cleaner the error message.
        self._stage_4_frontier(candidates, request, per_stage_filters)

        # Stage 2: user policy.
        candidates = self._stage_2_policy(candidates, request, per_stage_filters)

        # Stage 3: provider health.
        candidates = self._stage_3_health(candidates, request, per_stage_filters)

        # Stage 5: reproducibility constraint (REPLAY pinning).
        if request.reproducibility_mode is ReproducibilityMode.REPLAY:
            candidates = self._stage_5_replay(candidates, request, per_stage_filters)

        if not candidates:
            raise NoEligibleProvidersError(
                (
                    f"All {initial_count} registered providers were filtered out. "
                    f"See per-stage filter rationale on the exception."
                ),
                stages_applied=[entry["stage"] for entry in per_stage_filters],
                candidates_considered=initial_count,
            )

        # Stage 6: ranking. Returns the full sorted list; primary = first,
        # alternates = next two.
        ranked = self._stage_6_rank(candidates, request, per_stage_filters)

        primary_entry, primary_estimates = ranked[0]
        all_alternates = ranked[1:]

        # Equivalence-filter alternates per Section 4.5 conservative
        # defaults: same quantum_technology + fidelity within 10%.
        alternate_fidelities = {entry.provider_id: est[0] for entry, est in all_alternates}
        equivalent_alternates_entries = filter_equivalent_alternates(
            primary_entry,
            [entry for entry, _ in all_alternates],
            primary_fidelity=primary_estimates[0],
            alternate_fidelities=alternate_fidelities,
        )
        # Phase 4 takes top 2 equivalents as the alternates list.
        equivalent_alternates_entries = equivalent_alternates_entries[:2]

        # Build ProviderSelections.
        primary_selection = self._build_selection(
            primary_entry,
            metadata={"phase": "phase_4_router_primary"},
        )
        alternate_selections = [
            self._build_selection(entry, metadata={"phase": "phase_4_router_alternate"})
            for entry in equivalent_alternates_entries
        ]

        # Stage 7: decision provenance snapshot.
        rationale = (
            f"Phase 4 Router selected {primary_entry.provider_id}/"
            f"{primary_entry.client.backend_name} via Stage 6 ranking "
            f"(fidelity={primary_estimates[0]:.4f}, "
            f"cost=${primary_estimates[2]:.4f}, "
            f"latency={primary_estimates[1]:.0f}ms). "
            f"Equivalent alternates: "
            f"{[a.provider_id for a in equivalent_alternates_entries] or 'none'}."
        )

        is_stale, days_stale = is_pricing_stale(self._pricing)
        decision_provenance: dict[str, Any] = {
            "stages_applied": per_stage_filters,
            "ranking_weights": {
                "fidelity": _W_FIDELITY,
                "cost": _W_COST,
                "latency": _W_LATENCY,
                "queue": _W_QUEUE,
                "pref": _W_PREF,
            },
            "candidates_considered": initial_count,
            "candidates_after_filters": len(ranked),
            "primary_score": _stage_6_score(
                primary_estimates,
                request,
                in_preferred=primary_entry.provider_id in request.preferred_providers,
            ),
            "pricing_stale": is_stale,
            "pricing_days_since_freshness": days_stale,
        }
        if is_stale:
            log.warning(
                "Router using pricing data %d days old (>90 day threshold); "
                "estimates inaccurate.",
                days_stale,
            )

        return RoutingDecision(
            primary=primary_selection,
            alternates=alternate_selections,
            rationale=rationale,
            estimated_cost_usd=primary_estimates[2],
            estimated_latency_ms=primary_estimates[1],
            estimated_fidelity=primary_estimates[0],
            decision_provenance=decision_provenance,
        )

    # ----- Per-stage filter implementations -----

    def _stage_1_modality(
        self,
        candidates: list[ProviderEntry],
        request: RoutingRequest,
        per_stage_filters: list[dict[str, Any]],
    ) -> list[ProviderEntry]:
        del request  # Phase 4 routes by modality whitelist; future phases inspect task.
        survivors = [
            c for c in candidates if c.client.quantum_technology in _PHASE4_MODALITY_WHITELIST
        ]
        per_stage_filters.append(
            {
                "stage": "modality_eligibility",
                "input": len(candidates),
                "output": len(survivors),
                "dropped": [
                    {
                        "provider_id": c.provider_id,
                        "reason": f"quantum_technology={c.client.quantum_technology!r} not in v1 whitelist",
                    }
                    for c in candidates
                    if c not in survivors
                ],
            }
        )
        return survivors

    def _stage_2_policy(
        self,
        candidates: list[ProviderEntry],
        request: RoutingRequest,
        per_stage_filters: list[dict[str, Any]],
    ) -> list[ProviderEntry]:
        survivors: list[ProviderEntry] = []
        dropped: list[dict[str, Any]] = []
        cheapest_violation: tuple[str, float] | None = None
        cost_filter_active = request.cost_ceiling_usd is not None

        for c in candidates:
            if c.provider_id in request.excluded_providers:
                dropped.append({"provider_id": c.provider_id, "reason": "in excluded_providers"})
                continue
            fid, lat, cost = estimate_all(c)
            if cost_filter_active and cost > request.cost_ceiling_usd:  # type: ignore[operator]
                if cheapest_violation is None or cost < cheapest_violation[1]:
                    cheapest_violation = (c.provider_id, cost)
                dropped.append(
                    {
                        "provider_id": c.provider_id,
                        "reason": f"estimated_cost ${cost:.4f} > ceiling ${request.cost_ceiling_usd:.4f}",
                    }
                )
                continue
            if request.latency_budget_ms is not None and lat > request.latency_budget_ms:
                dropped.append(
                    {
                        "provider_id": c.provider_id,
                        "reason": f"estimated_latency {lat:.0f}ms > budget {request.latency_budget_ms:.0f}ms",
                    }
                )
                continue
            if request.fidelity_floor is not None and fid < request.fidelity_floor:
                dropped.append(
                    {
                        "provider_id": c.provider_id,
                        "reason": f"estimated_fidelity {fid:.4f} < floor {request.fidelity_floor:.4f}",
                    }
                )
                continue
            survivors.append(c)

        per_stage_filters.append(
            {
                "stage": "user_policy",
                "input": len(candidates),
                "output": len(survivors),
                "dropped": dropped,
            }
        )

        # If no candidates survived AND at least one was cost-rejected, surface
        # the specific CostCeilingExceeded so Phase 9's HTTP map returns 402.
        # Other non-cost rejections (excluded_providers, latency, fidelity)
        # are still listed in the dropped log so the user sees them too,
        # but the actionable error names cost specifically because raising
        # the ceiling is the most common fix.
        if not survivors and cost_filter_active and cheapest_violation is not None:
            raise CostCeilingExceeded(
                (
                    f"No candidate survived Stage 2 filters; at least one was "
                    f"rejected by cost ceiling. Cheapest cost-rejected "
                    f"estimate was ${cheapest_violation[1]:.4f} from "
                    f"{cheapest_violation[0]!r} vs ceiling "
                    f"${request.cost_ceiling_usd:.4f}. Other filters may also "
                    f"have contributed; see Stage 2's dropped entries."
                ),
                ceiling_usd=request.cost_ceiling_usd or 0.0,
                cheapest_estimate_usd=cheapest_violation[1],
                provider_id=cheapest_violation[0],
            )
        return survivors

    def _stage_3_health(
        self,
        candidates: list[ProviderEntry],
        request: RoutingRequest,
        per_stage_filters: list[dict[str, Any]],
    ) -> list[ProviderEntry]:
        del request
        survivors = [c for c in candidates if c.health is ProviderHealth.HEALTHY]
        per_stage_filters.append(
            {
                "stage": "provider_health",
                "input": len(candidates),
                "output": len(survivors),
                "dropped": [
                    {
                        "provider_id": c.provider_id,
                        "reason": f"health={c.health.value!r}",
                    }
                    for c in candidates
                    if c not in survivors
                ],
            }
        )
        return survivors

    def _stage_4_frontier(
        self,
        candidates: list[ProviderEntry],
        request: RoutingRequest,
        per_stage_filters: list[dict[str, Any]],
    ) -> None:
        """Defense-in-depth re-check of frontier-physics permission.

        Section 7's safety gate is the primary check (Phase 6); this is
        the routing-boundary defensive re-check. If the task's
        ``regime_hint`` is in :data:`FRONTIER_REGIME_NAMES` AND the
        actor's tolerance lacks ``frontier_physics``, raise.
        """
        del candidates  # Stage 4 raises or no-ops; doesn't filter.
        regime_hint = request.task.metadata.get("regime_hint") if request.task.metadata else None
        if regime_hint is None:
            per_stage_filters.append(
                {
                    "stage": "frontier_physics_gate",
                    "result": "no_regime_hint_skip",
                }
            )
            return
        regime_str = str(regime_hint).upper()
        if regime_str in FRONTIER_REGIME_NAMES and not request.task.tolerance.frontier_physics:
            per_stage_filters.append(
                {
                    "stage": "frontier_physics_gate",
                    "result": "refused",
                    "regime": regime_str,
                }
            )
            raise FrontierPhysicsRefused(
                regime_name=regime_str,
                reason=(
                    f"Router defense-in-depth gate refused frontier-physics regime "
                    f"{regime_str} without frontier_physics permission. "
                    f"(Section 7's safety gate is the primary check; Phase 6 wires the "
                    f"upstream Actor verification.)"
                ),
            )
        per_stage_filters.append(
            {
                "stage": "frontier_physics_gate",
                "result": "passed",
                "regime": regime_str,
            }
        )

    def _stage_5_replay(
        self,
        candidates: list[ProviderEntry],
        request: RoutingRequest,
        per_stage_filters: list[dict[str, Any]],
    ) -> list[ProviderEntry]:
        """REPLAY mode: pin to the recorded provider.

        Phase 4 surface: validates the recorded provider is still
        registered. The actual ledger-entry retrieval lands at Phase 7.
        Phase 4 reads the recorded provider_id from
        ``task.metadata["replay_recorded_provider_id"]`` if present;
        else REPLAY mode is a no-op (Phase 7 will refuse instead).
        """
        recorded_id = (
            request.task.metadata.get("replay_recorded_provider_id")
            if request.task.metadata
            else None
        )
        if recorded_id is None:
            per_stage_filters.append(
                {
                    "stage": "reproducibility_replay",
                    "result": "no_recorded_provider_phase_4_noop",
                }
            )
            return candidates
        recorded_id = str(recorded_id)
        survivors = [c for c in candidates if c.provider_id == recorded_id]
        if not survivors:
            recorded_backend = (
                str(request.task.metadata.get("replay_recorded_backend_name", "<unknown>"))
                if request.task.metadata
                else "<unknown>"
            )
            per_stage_filters.append(
                {
                    "stage": "reproducibility_replay",
                    "result": "recorded_provider_unavailable",
                    "recorded_provider_id": recorded_id,
                }
            )
            raise ReplayProviderUnavailable(
                (
                    f"Replay mode requires provider {recorded_id!r} (recorded in "
                    f"the ledger entry being replayed) but it is no longer "
                    f"registered in this Phoenix install."
                ),
                recorded_provider_id=recorded_id,
                recorded_backend_name=recorded_backend,
            )
        per_stage_filters.append(
            {
                "stage": "reproducibility_replay",
                "result": "pinned",
                "recorded_provider_id": recorded_id,
            }
        )
        return survivors

    def _stage_6_rank(
        self,
        candidates: list[ProviderEntry],
        request: RoutingRequest,
        per_stage_filters: list[dict[str, Any]],
    ) -> list[tuple[ProviderEntry, tuple[float, float, float]]]:
        """Score each surviving candidate; return sorted by descending score.

        Returns ``(entry, (fidelity, latency_ms, cost_usd))`` tuples so
        the caller can extract estimates without re-computing.
        """
        scored: list[
            tuple[
                float,  # score
                ProviderEntry,
                tuple[float, float, float],  # (fidelity, latency_ms, cost_usd)
            ]
        ] = []
        for c in candidates:
            estimates = estimate_all(c)
            in_preferred = c.provider_id in request.preferred_providers
            score = _stage_6_score(estimates, request, in_preferred=in_preferred)
            scored.append((score, c, estimates))
        # Descending score; ties broken by provider_id alphabetical for
        # determinism.
        scored.sort(key=lambda triple: (-triple[0], triple[1].provider_id))
        per_stage_filters.append(
            {
                "stage": "ranking",
                "input": len(candidates),
                "output": len(scored),
                "ranked": [
                    {
                        "provider_id": entry.provider_id,
                        "score": round(score, 6),
                        "fidelity": round(est[0], 6),
                        "latency_ms": round(est[1], 1),
                        "cost_usd": round(est[2], 6),
                    }
                    for score, entry, est in scored
                ],
            }
        )
        return [(entry, est) for _score, entry, est in scored]

    def _build_selection(
        self,
        entry: ProviderEntry,
        *,
        metadata: dict[str, Any],
    ) -> ProviderSelection:
        return ProviderSelection(
            provider_id=entry.client.provider_id,
            backend_name=entry.client.backend_name,
            quantum_technology=entry.client.quantum_technology,
            client=entry.client,
            selection_metadata=metadata,
        )


def _stage_6_score(
    estimates: tuple[float, float, float],
    request: RoutingRequest,
    *,
    in_preferred: bool,
) -> float:
    """Section 4.4 weighted score:

    score = w_fid * fidelity_normalized
          - w_cost * (cost / cost_ceiling)
          - w_latency * (latency / budget)
          - w_queue * queue_depth_normalized
          + w_pref * (1 if preferred else 0)

    Phase 4 simplifications:
    - ``fidelity_normalized = fidelity`` (already in [0,1]).
    - ``cost / cost_ceiling`` clamped to [0, infinity); when ceiling is
      None, the cost penalty is zero (no normalization possible).
    - ``latency / budget`` similar; budget None -> zero penalty.
    - ``queue_depth_normalized`` = 0 (Source B not yet wired).
    """
    fidelity, latency_ms, cost_usd = estimates
    score = _W_FIDELITY * fidelity
    if request.cost_ceiling_usd is not None and request.cost_ceiling_usd > 0:
        score -= _W_COST * (cost_usd / request.cost_ceiling_usd)
    if request.latency_budget_ms is not None and request.latency_budget_ms > 0:
        score -= _W_LATENCY * (latency_ms / request.latency_budget_ms)
    # Queue-depth penalty: Phase 4 zero (Source B deferred).
    if in_preferred:
        score += _W_PREF
    return score
