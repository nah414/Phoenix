"""Pricing module: load + query per-provider cost estimates.

Per architecture v1 Section 4.7: the Router consumes per-provider cost
estimates at Stage 2 (cost-ceiling filter) and Stage 6 (ranking). Phase 4
ships a JSON-backed pricing table at
``phoenix/router/pricing/pricing_v1.json``; ops refresh out-of-band via
``phoenix admin pricing-update`` (Phase 8 endpoint).

Per Section 11.2.2 (RESOLVED 2026-05-08): pricing staleness > 90 days
emits a soft warn (logged, surfaced in routing provenance) but never
hard-errors. Stale pricing never blocks a solve; it just makes cost
estimates inaccurate.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Path resolution from the package install root.
_PRICING_FILE = Path(__file__).parent / "pricing" / "pricing_v1.json"


class PricingError(Exception):
    """Pricing data couldn't be loaded or parsed.

    Distinct from a stale-data soft warn: this raises only when the
    JSON file is malformed or missing entirely. Stale data never raises.
    """


def load_pricing(path: Path | None = None) -> dict[str, Any]:
    """Load the pricing JSON.

    Returns the parsed dict (with ``_metadata`` and ``providers`` keys).
    Raises :class:`PricingError` if the file is missing or unparseable.

    Phase 4 ops can override the path by passing ``path``; the default
    resolves to ``phoenix/router/pricing/pricing_v1.json`` shipped with
    the package.
    """
    target = path if path is not None else _PRICING_FILE
    if not target.exists():
        raise PricingError(
            f"Pricing data file not found at {target}. The package install "
            f"is broken; reinstall phoenix-middleware or restore the file."
        )
    try:
        with target.open(encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
    except json.JSONDecodeError as exc:
        raise PricingError(f"Pricing data at {target} is not valid JSON: {exc}") from exc
    if "providers" not in data:
        raise PricingError(f"Pricing data at {target} is missing the 'providers' key.")
    return data


def estimate_cost_usd(
    provider_id: str,
    *,
    pricing: dict[str, Any] | None = None,
) -> float:
    """Look up the per-solve cost estimate for ``provider_id``.

    Returns ``0.0`` when the provider is not in the pricing table (Phase 4
    surface; future phases may raise instead so unknown providers don't
    silently route as free). Returns the ``estimate_per_solve_usd`` field
    when present; otherwise ``0.0``.

    Phase 4 doesn't pass the actual solve's shot count or gate count
    through here -- those land in Phase 7+ when the Router's Stage 6
    ranking gets shot-aware. The Phase 4 estimate is a per-provider
    static placeholder sufficient for cost-ceiling filtering.
    """
    table = pricing if pricing is not None else load_pricing()
    providers = table.get("providers", {})
    entry = providers.get(provider_id)
    if entry is None:
        return 0.0
    estimate = entry.get("estimate_per_solve_usd", 0.0)
    return float(estimate)


def is_pricing_stale(
    pricing: dict[str, Any] | None = None,
    *,
    now_utc: datetime | None = None,
) -> tuple[bool, int]:
    """Return ``(is_stale, days_since_freshness)``.

    Per Section 11.2.2: stale > 90 days surfaces a soft warn; never
    hard-error. This helper exposes the staleness for the Router's
    decision_provenance to record.
    """
    table = pricing if pricing is not None else load_pricing()
    metadata = table.get("_metadata", {})
    freshness_str = metadata.get("data_freshness_utc")
    stale_after_days = int(metadata.get("stale_after_days", 90))
    if not freshness_str:
        return False, 0
    try:
        freshness = datetime.fromisoformat(freshness_str.replace("Z", "+00:00"))
    except ValueError:
        log.warning("Pricing _metadata.data_freshness_utc unparseable: %s", freshness_str)
        return False, 0
    now = now_utc if now_utc is not None else datetime.now(timezone.utc)
    delta_days = (now - freshness).days
    return delta_days > stale_after_days, delta_days
