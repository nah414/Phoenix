"""Loader for the cognition pricing v2 JSON table.

Parallels :func:`phoenix.router.pricing.load_pricing` but returns
:class:`CognitionPricingRecord` instances keyed by
``(provider, model)``. The table ships with the package install at
``phoenix/pricing/v2/cognition_pricing.json``; ops refresh
out-of-band via the ``pricing-update`` admin surface added in
Phase 9 (extended in Phase 13 Step 1 to also refresh v2).

Per architecture v1 Section 11.2.2 (RESOLVED 2026-05-08): stale
pricing emits a soft warn (logged, surfaced in routing provenance)
but never hard-errors. Stale data never blocks a solve; it just makes
cost estimates inaccurate.
"""

from __future__ import annotations

import json
from pathlib import Path

from phoenix.pricing.v2.schema import CognitionPricingRecord

_PRICING_FILE = Path(__file__).parent / "cognition_pricing.json"


class CognitionPricingError(Exception):
    """Pricing v2 data couldn't be loaded or parsed.

    Parallels :class:`phoenix.router.pricing.PricingError`. Raised
    only when the JSON file is malformed or missing; stale data never
    raises.
    """


def load_cognition_pricing(
    path: Path | None = None,
) -> dict[tuple[str, str], CognitionPricingRecord]:
    """Load the cognition pricing v2 JSON.

    Returns a dict keyed by ``(provider, model)`` so callers can look
    up a specific provider/model row in O(1). Raises
    :class:`CognitionPricingError` if the file is missing or
    unparseable.

    Phase 13 Step 7+ ops can override the path via the
    ``pricing-update`` admin endpoint (Phase 9 surface, extended in
    Step 1 to refresh v2 alongside v1).
    """
    target = path if path is not None else _PRICING_FILE
    if not target.exists():
        raise CognitionPricingError(
            f"Cognition pricing v2 data file not found at {target}. The "
            f"package install is broken; reinstall phoenix-middleware or "
            f"restore the file."
        )
    try:
        with target.open(encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise CognitionPricingError(
            f"Cognition pricing v2 data at {target} is not valid JSON: {exc}"
        ) from exc
    if "providers" not in data:
        raise CognitionPricingError(
            f"Cognition pricing v2 data at {target} is missing the 'providers' key."
        )

    out: dict[tuple[str, str], CognitionPricingRecord] = {}
    for row in data["providers"]:
        rec = CognitionPricingRecord(
            provider=row["provider"],
            model=row["model"],
            usd_per_1m_input_tokens=float(row["usd_per_1m_input_tokens"]),
            usd_per_1m_output_tokens=float(row["usd_per_1m_output_tokens"]),
            prompt_cache_discount_factor=float(row.get("prompt_cache_discount_factor", 1.0)),
            batch_discount_factor=float(row.get("batch_discount_factor", 1.0)),
            vision_token_multiplier=float(row.get("vision_token_multiplier", 1.0)),
        )
        out[(rec.provider, rec.model)] = rec
    return out
