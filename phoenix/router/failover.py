"""Multi-provider failover protocol (Phase 4).

Per architecture v1 Section 4.5: when Orchestrate's
:meth:`BaseProviderClient.submit` fails on the primary provider, the
Router walks the :attr:`RoutingDecision.alternates` list, marking failed
providers degraded with exponential-backoff quarantine, until either an
alternate succeeds, all alternates exhaust (raise
:class:`AllAlternatesExhausted`), or the local-simulator fallback path
succeeds (when ``allow_simulator_fallback=True``).

Failover state lives on the :class:`ProviderRegistry` (DEGRADED health
+ ``degraded_until`` quarantine timestamp). The :class:`FailoverProtocol`
class owns the per-process failure-count history that drives exponential
backoff: each failure doubles the quarantine duration up to a cap. Phase
7's persistent state backend will move this state out of process memory.

The failover wrapper :func:`attempt_with_failover` is the integration
seam Phase 3's :func:`orchestrate.engine.orchestrate` does NOT know
about; the pipeline orchestrator (Step 9) calls it instead of orchestrate
directly when failover is desired.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from phoenix.providers.classical.local_simulator import LocalClassicalSimulator
from phoenix.router.data_model import ProviderSelection
from phoenix.router.errors import AllAlternatesExhausted
from phoenix.router.provider_registry import ProviderHealth, ProviderRegistry
from phoenix.trinity.orchestrate.provider_client import OrchestrateProviderError

if TYPE_CHECKING:
    from phoenix.router.data_model import RoutingDecision
    from phoenix.trinity.orchestrate.provider_client import (
        ProviderRawResult,
        ProviderSubmission,
    )

log = logging.getLogger(__name__)

# Section 4.5 exponential-backoff defaults.
_BASE_QUARANTINE_SECONDS = 300  # 5 minutes
_MAX_QUARANTINE_SECONDS = 3600  # 1 hour


class FailoverProtocol:
    """Stateful failover wrapper around a :class:`ProviderRegistry`.

    Tracks per-provider failure counts (in-process; Phase 7 persists)
    and exposes :meth:`attempt_with_failover` that walks a
    :class:`RoutingDecision`'s primary + alternates with retry semantics.

    Phase 4 ships a single shared instance per Phoenix daemon process;
    Phase 7 may shard or replicate state when the daemon scales beyond
    one process.
    """

    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        base_quarantine_seconds: int = _BASE_QUARANTINE_SECONDS,
        max_quarantine_seconds: int = _MAX_QUARANTINE_SECONDS,
    ) -> None:
        self._registry = registry
        self._failure_counts: dict[str, int] = {}
        self._base = base_quarantine_seconds
        self._max = max_quarantine_seconds

    def _next_quarantine_seconds(self, provider_id: str) -> int:
        """Compute the next quarantine duration via exponential backoff.

        :math:`t = \\min(\\text{base} \\times 2^{n-1}, \\text{max})` where
        :math:`n` is the failure count (1-indexed). First failure ->
        ``base`` (5 min); second -> 10 min; third -> 20 min; etc.; capped
        at :data:`_MAX_QUARANTINE_SECONDS` (1 hour).
        """
        count = self._failure_counts.get(provider_id, 0) + 1
        self._failure_counts[provider_id] = count
        seconds = self._base * (2 ** (count - 1))
        return min(int(seconds), self._max)

    def quarantine(self, provider_id: str) -> str:
        """Mark provider degraded with quarantine. Returns ISO 8601 UTC
        timestamp of when the provider auto-promotes back to HEALTHY.

        Public so the pipeline orchestrator (Step 9) can call it directly
        when it catches an :class:`OrchestrateProviderError` from the
        Phase 3 :func:`orchestrate` boundary.
        """
        seconds = self._next_quarantine_seconds(provider_id)
        until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        until_iso = until.isoformat()
        self._registry.mark_degraded(provider_id, degraded_until_utc=until_iso)
        log.warning(
            "Failover quarantine: %s degraded for %d s (until %s)",
            provider_id,
            seconds,
            until_iso,
        )
        return until_iso

    def reset_failures(self, provider_id: str) -> None:
        """Reset a provider's failure count after manual recovery.

        Phase 4 helper for ops + tests. The ``mark_healthy`` call on the
        registry restores the health flag; this resets the backoff counter
        so the next failure starts at the base quarantine again.
        """
        self._failure_counts.pop(provider_id, None)

    def attempt_with_failover(
        self,
        decision: RoutingDecision,
        submission: ProviderSubmission,
        *,
        allow_simulator_fallback: bool = True,
    ) -> tuple[ProviderRawResult, ProviderSelection]:
        """Attempt the primary; on failure, walk alternates.

        Returns ``(raw_result, used_selection)`` -- the
        :class:`ProviderRawResult` from the successful provider plus the
        :class:`ProviderSelection` that produced it. Raises
        :class:`AllAlternatesExhausted` when no candidate succeeds and
        the simulator fallback is disabled or also fails.

        Phase 4 fallback path: when ``allow_simulator_fallback=True`` AND
        all alternates fail, attempt one more submission against a fresh
        :class:`LocalClassicalSimulator`. Marks the failed providers
        degraded along the way; the fallback is a clean
        :class:`ProviderSelection` not in the registry's failure tracking.
        """
        attempts: list[dict[str, str]] = []
        candidates: list[ProviderSelection] = [decision.primary, *decision.alternates]

        for selection in candidates:
            entry = self._registry.get(selection.provider_id)
            if entry is None:
                attempts.append(
                    {
                        "provider_id": selection.provider_id,
                        "reason": "not_in_registry",
                    }
                )
                continue
            if entry.health is not ProviderHealth.HEALTHY:
                attempts.append(
                    {
                        "provider_id": selection.provider_id,
                        "reason": f"health={entry.health.value}",
                    }
                )
                continue
            try:
                raw = selection.client.submit(submission)
            except OrchestrateProviderError as exc:
                quarantine_until = self.quarantine(selection.provider_id)
                attempts.append(
                    {
                        "provider_id": selection.provider_id,
                        "reason": f"submit_failed: {exc}",
                        "quarantine_until_utc": quarantine_until,
                    }
                )
                continue
            return raw, selection

        # All RoutingDecision candidates exhausted.
        if allow_simulator_fallback:
            log.warning(
                "All %d alternates exhausted; falling back to "
                "LocalClassicalSimulator (allow_simulator_fallback=True).",
                len(candidates),
            )
            fallback_client = LocalClassicalSimulator()
            try:
                raw = fallback_client.submit(submission)
            except OrchestrateProviderError as exc:
                attempts.append(
                    {
                        "provider_id": fallback_client.provider_id,
                        "reason": f"simulator_fallback_failed: {exc}",
                    }
                )
                raise AllAlternatesExhausted(
                    (
                        f"All {len(candidates)} alternates failed AND simulator "
                        f"fallback also failed."
                    ),
                    attempts=attempts,
                ) from exc
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
            return raw, fallback_selection

        raise AllAlternatesExhausted(
            (f"All {len(candidates)} alternates failed and " f"allow_simulator_fallback=False."),
            attempts=attempts,
        )
