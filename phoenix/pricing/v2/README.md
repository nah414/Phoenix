# `phoenix/pricing/v2/`

Cognition pricing v2 — per-token LLM cost estimates with prompt-cache and batch-API discount factors.

## Phase 13 status

**Step 1 (current):** schema, loader, initial pricing table for Anthropic + OpenAI + Google.

**Step 3:** the optional `LiteLLM` passthrough provider delegates to LiteLLM's built-in cost catalogue when available, falls back to a v2 entry if registered, otherwise raises `PricingUnavailable` and the router skips the candidate.

## Files

- [`schema.py`](schema.py) — `CognitionPricingRecord` frozen dataclass.
- [`loader.py`](loader.py) — `load_cognition_pricing()` returns a `dict[(provider, model), CognitionPricingRecord]`.
- [`cognition_pricing.json`](cognition_pricing.json) — initial pricing table.

## Cost formula

```text
input_cost  = (input_tokens - cached_input_tokens) / 1e6 * usd_per_1m_input_tokens
cached_cost = cached_input_tokens                  / 1e6 * usd_per_1m_input_tokens * prompt_cache_discount_factor
output_cost = output_tokens                        / 1e6 * usd_per_1m_output_tokens
vision_cost = vision_tokens                        / 1e6 * usd_per_1m_input_tokens * vision_token_multiplier  # subset of input
total = (input_cost + cached_cost + output_cost + vision_cost) * (batch_discount_factor if batched else 1.0)
```

## Updating rates

Provider rate cards change over time. Per architecture v1 Section 11.2.2 (RESOLVED), stale pricing emits a soft warn but never hard-errors — a solve never fails because pricing is stale; cost estimates just become inaccurate.

Ops refresh out-of-band via the `pricing-update` admin surface. The build guide's Step 1 reference to extending `pricing_update.py (existing)` is carried forward as an open item — the actual refresh surface is the admin endpoint (Phase 8/9 surface) rather than a standalone script. Resolution lands when v2 needs an explicit ops-refresh hook.
