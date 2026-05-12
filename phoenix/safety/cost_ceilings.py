"""Cost-ceiling defaults + resolver (Phase 10 Step 3, Section 4.7).

Per architecture v1 Section 4.7 ("Default ceilings"):

- **Per-solve** ceilings depend on the reproducibility mode the user
  requested: ``default`` = $5, ``strict`` = $25, ``replay`` = $50.
  Stricter reproducibility tolerates higher per-solve cost because
  the user explicitly opted into deeper verification.
- **Per-actor-24h** cumulative ceilings depend on the actor's
  rate-limit tier (mirrors Section 7.5's rate-limit ladder):
  ``default`` = $50/day, ``elevated`` = $500/day, ``admin`` =
  no ceiling.
- **Per-org-24h** cumulative ceiling is $2000/day by default;
  org admins can raise via the Step 8 admin override endpoint.

**Env-var overrides** (per-install, NOT per-mode/per-tier):

- ``$PHOENIX_PER_SOLVE_CEILING_USD`` -- flat per-solve cap that
  overrides ALL three reproducibility-mode entries.
- ``$PHOENIX_PER_ACTOR_24H_CEILING_USD`` -- flat per-actor-24h cap.
- ``$PHOENIX_PER_ORG_24H_CEILING_USD`` -- flat per-org-24h cap.

The env-var overrides are blunt instruments useful for ops who want
to set a single ceiling and forget about per-mode/per-tier nuance
(e.g., a hosted CI install that runs only ``default``-mode solves
on a budget). Per-actor and admin-override paths apply ON TOP of
the resolved ceiling via the :class:`LocalJobBudgetController`
(Step 4).

Invalid env-var values fall back to the defaults with a logged
warning -- the cost-ceiling path is on the hot path and must not
fail closed on a typo.

**Edge case:** when a tier or mode lookup misses (an actor with
an unknown tier, or a reproducibility mode the registry doesn't
recognize), the resolver returns the strictest applicable ceiling
($5 per-solve, $50 per-actor-24h, $2000 per-org-24h). Phoenix's
existing tier validation (Section 7.5 + the rate limiter) would
already have rejected unknown tiers before the cost path runs, so
this is a defense-in-depth check rather than a routine code path.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# Section 4.7 defaults.
_DEFAULT_PER_SOLVE_CEILINGS: dict[str, float] = {
    "default": 5.0,
    "strict": 25.0,
    "replay": 50.0,
}

_DEFAULT_PER_ACTOR_24H_CEILINGS: dict[str, float | None] = {
    "default": 50.0,
    "elevated": 500.0,
    "admin": None,  # No ceiling for admin-tier actors.
}

_DEFAULT_PER_ORG_24H_CEILING_USD: float = 2000.0


# Env-var override hooks.
_ENV_PER_SOLVE = "PHOENIX_PER_SOLVE_CEILING_USD"
_ENV_PER_ACTOR_24H = "PHOENIX_PER_ACTOR_24H_CEILING_USD"
_ENV_PER_ORG_24H = "PHOENIX_PER_ORG_24H_CEILING_USD"


# Strictest-of-default fallbacks for unknown modes/tiers (defense-in-depth).
_FALLBACK_PER_SOLVE = 5.0
_FALLBACK_PER_ACTOR_24H = 50.0
_FALLBACK_PER_ORG_24H = 2000.0


@dataclass(frozen=True)
class CeilingTriple:
    """Resolved per-solve / per-actor-24h / per-org-24h ceilings.

    Fields:
      - ``per_solve_usd`` (float) -- max cost for any single solve.
      - ``per_actor_24h_usd`` (float | None) -- rolling-24h cumulative
        cap. ``None`` means "no cap" (admin-tier actors).
      - ``per_org_24h_usd`` (float) -- rolling-24h cumulative cap for
        the actor's org (or for the actor itself when no org).
    """

    per_solve_usd: float
    per_actor_24h_usd: float | None
    per_org_24h_usd: float


def resolve_ceilings(
    *,
    reproducibility_mode: str,
    actor_tier: str,
    env: dict[str, str] | None = None,
) -> CeilingTriple:
    """Return the active :class:`CeilingTriple` for the given mode + tier.

    Resolution order (later wins, except None doesn't override):

    1. Section 4.7 defaults indexed by ``reproducibility_mode`` and
       ``actor_tier``.
    2. Env-var overrides (``$PHOENIX_PER_SOLVE_CEILING_USD`` etc.),
       which are install-wide and override the mode/tier lookup.

    Step 8's admin override layer adds a third level (per-actor
    temporary bumps) on top of this resolver in
    :class:`LocalJobBudgetController`.

    Parameters:
      - ``reproducibility_mode``: ``"default"`` | ``"strict"`` |
        ``"replay"``. Unknown values fall back to ``"default"``.
      - ``actor_tier``: ``"default"`` | ``"elevated"`` | ``"admin"``.
        Unknown values fall back to ``"default"``.
      - ``env``: explicit env dict (tests inject); defaults to
        :data:`os.environ`.
    """
    if env is None:
        env = dict(os.environ)

    per_solve = _DEFAULT_PER_SOLVE_CEILINGS.get(reproducibility_mode, _FALLBACK_PER_SOLVE)
    per_actor_24h: float | None
    if actor_tier in _DEFAULT_PER_ACTOR_24H_CEILINGS:
        per_actor_24h = _DEFAULT_PER_ACTOR_24H_CEILINGS[actor_tier]
    else:
        per_actor_24h = _FALLBACK_PER_ACTOR_24H
    per_org_24h = _DEFAULT_PER_ORG_24H_CEILING_USD

    # Env-var overrides (parsed defensively; invalid -> warn + fallback).
    per_solve_override = _parse_positive_float(env.get(_ENV_PER_SOLVE), _ENV_PER_SOLVE)
    if per_solve_override is not None:
        per_solve = per_solve_override

    per_actor_override = _parse_positive_float(env.get(_ENV_PER_ACTOR_24H), _ENV_PER_ACTOR_24H)
    if per_actor_override is not None:
        per_actor_24h = per_actor_override

    per_org_override = _parse_positive_float(env.get(_ENV_PER_ORG_24H), _ENV_PER_ORG_24H)
    if per_org_override is not None:
        per_org_24h = per_org_override

    return CeilingTriple(
        per_solve_usd=per_solve,
        per_actor_24h_usd=per_actor_24h,
        per_org_24h_usd=per_org_24h,
    )


def _parse_positive_float(raw: str | None, env_name: str) -> float | None:
    """Parse a positive-float env-var; return None on missing/invalid.

    Logs a warning on parse failure so ops can see the bad value
    without the cost path failing closed.
    """
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "Ignoring invalid %s=%r (must be a positive float)",
            env_name,
            raw,
        )
        return None
    if value <= 0:
        logger.warning("Ignoring non-positive %s=%r (must be > 0)", env_name, raw)
        return None
    return value


__all__ = [
    "CeilingTriple",
    "resolve_ceilings",
]
