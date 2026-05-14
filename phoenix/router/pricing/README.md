# `phoenix/router/pricing/`

Pricing data for the Router's cost-estimation layer (Section 4.7).
`pricing_v1.json` ships as a versioned JSON file with per-provider
pricing models and per-solve estimates; the Router consumes it via
`phoenix.router.pricing.estimate_cost_usd`.

Per Section 4.7's staleness policy: Phoenix never hard-errors on
stale pricing -- it surfaces a `pricing_data_staleness_days` field
in every routing decision's provenance, and warns when stale > 90
days. Operators refresh out-of-band; future Phase 12+ ships a
`phoenix admin pricing-update` command (Section 11.2.2 dispo).

Architectural reference: `PHOENIX_ARCHITECTURE_v1.md` Section 4.7
(cost estimation), Section 11.2.2 (pricing-data staleness
disposition).
