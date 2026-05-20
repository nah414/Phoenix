# `phoenix/pricing/`

Phoenix pricing subsystem (introduced in Phase 13).

## Layout

- [`v2/`](v2/) — cognition (LLM) pricing v2 schema + loader + initial pricing table. New in Phase 13 Step 1.

Pricing v1 (physics) remains at [`phoenix/router/pricing.py`](../router/pricing.py) and is unchanged. The split reflects different consumer surfaces:

| Pricing | Path | Consumers |
|---|---|---|
| v1 (physics) | `phoenix.router.pricing` | Router Stage 2 (cost-ceiling filter) + Stage 6 (ranking) |
| v2 (cognition) | `phoenix.pricing.v2` | Phase 10 cost-ceiling engine + Phase 13 streaming-token cost tracking |

A future consolidation could move v1 under `phoenix.pricing.v1`, but Phase 13 deliberately does not touch v1's import path to avoid churn outside the cognition scope.
