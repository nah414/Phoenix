# `phoenix/verification/axes/`

Cognition wobble axes — Phase 13 Step 4.

Three axis classes that measure disagreement across cognition (LLM) outputs:

| Axis | Probes | Use case |
|---|---|---|
| [`CrossModelAxis`](cross_model.py) | Same prompt → N providers | Cross-vendor verification: Claude vs GPT vs Gemini |
| [`SelfConsistencyAxis`](self_consistency.py) | Same prompt → same provider, varied temperatures | Stability under sampling |
| [`PromptPerturbationAxis`](prompt_perturbation.py) | Paraphrased prompts → same provider | Robustness to surface phrasing |

All three return a [`CognitionDisagreementMetric`](_result.py) carrying:

- `distance` — aggregate disagreement scalar in `[0, 1]`.
- `pairwise_distance_matrix` — full N×N matrix (preserved per the `DO NOT COLLAPSE` invariant).
- `provenance` — per-call `CognitionAxisProvenance` (provider, model, latency, usage, temperature, fingerprint).
- `responses` — the raw `CognitionResult` objects.
- `disagreement_type` — always `PhoenixDisagreementType.COGNITION_UNCLASSIFIED` at Step 4; the [Step 5 classifier](../../../../BUILDGUIDE_phoenix_v1_phase13_cognition_mcp_client.md) replaces it with a real class.

## Distance metric

[`_distance.py`](_distance.py) holds the metric. Step 4 ships **exact-string match** (`0.0` if texts match after whitespace strip, `1.0` otherwise) per `[OPEN: P13-3]` default. Step 5 swaps in sentence-embedding cosine similarity from the vendored `all-MiniLM-L6-v2` model (shipped at `vendor/cognition_wobble/embeddings/`).

The swap surface is the single `_text_distance` private function. The axis classes call through `text_distance` so the metric upgrade is one line.

## Relationship to the existing `WobbleAxis` Protocol

The physics-side [`WobbleAxis` Protocol](../wobble_axis.py) takes `run(task: PhysicsTask, depth: RungDepth) -> AxisResult`. Cognition axes take `run(prompt: Prompt, *, max_tokens, temperature) -> CognitionDisagreementMetric` — a structural mismatch.

**Step 4 ships these axes as their own classes** rather than forcing Protocol satisfaction. The verification gate's cognition branch (Step 9+ when the gate gains a `task.kind == "cognition"` path) can either widen the Protocol or carry both axis-result types.

## Open items carried forward

- **`[OPEN: P13-3]`** — Step 4 distance metric ships as exact-string match; Step 5 swaps in semantic distance.
- **`[OPEN]`** — Default `CrossModelAxis` "primary + cheapest secondary" provider selection: Step 4 takes providers explicitly; the selection heuristic lands when cognition routing concretizes.

See [`BUILDGUIDE_phoenix_v1_phase13_cognition_mcp_client.md`](../../../BUILDGUIDE_phoenix_v1_phase13_cognition_mcp_client.md) §4.4 for full Step 4 spec.
