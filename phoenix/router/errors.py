"""Typed errors raised by Phoenix's Router subsystem (Phase 4).

Per architecture v1 Section 3.7 typed-error discipline: every Router
failure surfaces as a specific subclass of :class:`RouterError`. Phase 9
maps these to HTTP status codes at the front door (the Router runs above
``POST /v1/tasks``):

- :class:`NoEligibleProvidersError` -> 422 (no provider satisfies the
  filters; user can relax cost/latency/fidelity).
- :class:`CostCeilingExceeded` -> 402 (Payment Required; cost ceiling
  filter rejected all candidates).
- :class:`FrontierPhysicsRefused` is re-raised from the Trinity Core
  Solver layer through the Router's defense-in-depth Stage 4; the front
  door already maps it to 403.
- :class:`ReplayProviderUnavailable` -> 410 Gone (replay target provider
  no longer registered).
- :class:`AllAlternatesExhausted` -> 503 Service Unavailable (all
  failover candidates failed; no simulator fallback or it failed too).

Phase 4 ships the typed exceptions; the routes.py mapping lands when
Phase 4's Router replaces the default-provider helper at Step 9.
"""

from __future__ import annotations


class RouterError(Exception):
    """Base for Trinity Core Router failures."""


class NoEligibleProvidersError(RouterError):
    """No provider satisfied the routing filters.

    Carries the per-stage filter rationale so audit logs can show *why*
    no candidate survived (which filter dropped it).
    """

    def __init__(
        self,
        message: str,
        *,
        stages_applied: list[str] | None = None,
        candidates_considered: int = 0,
    ) -> None:
        super().__init__(message)
        self.stages_applied = list(stages_applied) if stages_applied else []
        self.candidates_considered = candidates_considered


class CostCeilingExceeded(RouterError):
    """Stage 2 dropped all candidates because every estimated cost exceeded
    the user's cost_ceiling_usd OR the actor's remaining 24-hour budget.

    Distinct from :class:`NoEligibleProvidersError` so the front door can
    map it to HTTP 402 ('Payment Required') and the user knows the fix is
    raising the ceiling, not changing tolerance.
    """

    def __init__(
        self,
        message: str,
        *,
        ceiling_usd: float,
        cheapest_estimate_usd: float,
        provider_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.ceiling_usd = ceiling_usd
        self.cheapest_estimate_usd = cheapest_estimate_usd
        self.provider_id = provider_id


class ReplayProviderUnavailable(RouterError):
    """Stage 5 (reproducibility constraint, replay mode) couldn't pin to
    the recorded provider because it's no longer registered.

    The recorded :class:`ProviderSelection` from the ledger entry being
    replayed names a provider that no longer exists in this Phoenix
    install (deregistered, renamed, removed). v1 surface; v1.x may add a
    "best-effort similar provider" downgrade path.
    """

    def __init__(
        self,
        message: str,
        *,
        recorded_provider_id: str,
        recorded_backend_name: str,
    ) -> None:
        super().__init__(message)
        self.recorded_provider_id = recorded_provider_id
        self.recorded_backend_name = recorded_backend_name


class AllAlternatesExhausted(RouterError):
    """The failover protocol walked the full :class:`RoutingDecision.alternates`
    list and every candidate failed, AND ``allow_simulator_fallback=False``
    or the simulator fallback also failed.

    Carries the per-attempt failure list so audit logs can reconstruct the
    failover sequence.
    """

    def __init__(
        self,
        message: str,
        *,
        attempts: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.attempts = list(attempts) if attempts else []
