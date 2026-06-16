# Phase 13 Step 5c — Cognition Corpus & Training Plan

**Status:** scope (2026-06-12). The training/eval harness is built and merged-pending
(PR #31); this document scopes the **data work** that remains — the only thing between the
inert `AlwaysUnclassifiedClassifier` default and a real classifier that clears the gate.

## Context

Step 5b shipped the classifier substrate (Protocol, `GBMClassifier`, feature extractors,
eval framework). Step 5c (the training harness — `corpus.py`, `training.py`,
`acceptance.py`, synthetic fixture, CLIs) is built and tested. What is left is **the labeled
corpus itself** and the iterate-to-gate loop. Per the build guide and the
2026-05-20 roadmap, this was always "Adam-side research work, no deadline." This document
makes that work concrete: what to collect, how to label it, and the loop to clear the gate.

## Acceptance target (recap)

- **≥ 200 paired examples**, **~28+ per graded class** across all six graded classes
  (`FACTUAL_AGREEMENT`, `STYLISTIC_DIVERGENCE`, `FACTUAL_DISAGREEMENT`,
  `INTERPRETIVE_DIVERGENCE`, `REFUSAL_DIVERGENCE`, `TOOL_CHOICE_DIVERGENCE`), plus some
  gold-`UNCLASSIFIED` rows for abstention testing.
- **Gate: macro-F1 ≥ 0.70** across the six graded classes (UNCLASSIFIED not graded), on a
  **held-out** split (not in-sample).
- Each row carries `(prompt, response_a, response_b, gold_class, source_dataset,
  annotation_notes)` — exactly the `corpus.py` JSONL schema.

## The crux (read this first)

**This is a data-generation + annotation effort, not a download-and-convert.** The three
named source datasets are factuality/consistency benchmarks, none of which provide
pre-labeled *pairwise disagreement* examples in our taxonomy:

| Source | What it actually is | What we can extract | Effort |
|---|---|---|---|
| **SAC3** (intuit/sac3, arXiv 2311.01740) | Hallucination-*detection method*; ~100 HotpotQA-halu samples; generates multiple samples + cross-model responses | Cross-model/cross-question response **pairs** → `FACTUAL_AGREEMENT` (consistent) vs `FACTUAL_DISAGREEMENT` (inconsistent). Closest to pairwise, but small. | Medium (transform + label) |
| **FELM** (hkust-nlp/felm, arXiv 2310.00741) | 847 questions, **single** ChatGPT response each, span-labeled true/false + error type | Prompts + a factual response; pair a true response with a known-false variant on the same prompt → `FACTUAL_DISAGREEMENT`. Also a good source for the `factual_claim` feature. | Medium (synthesize the second response) |
| **FINCH-ZK** | Zero-shot fact-check (named in build guide); **not located** on HF/GitHub as of 2026-06-12 | TBD — **verify availability first**; may be replaced by an equivalent fact-check set. | Unknown |

**Consequence:** the named sources mostly yield the two *factual* classes — exactly the
~80/80/10/10/10/10 skew the roadmap predicted. The **four under-represented classes have no
source dataset** and must be **Phoenix-generated**. And **every pair needs a gold label.**
That is the real cost of Step 5c, and it is unavoidable.

## Source-by-source plan (the two factual classes)

1. **SAC3 →** clone `intuit/sac3`, take its sampled cross-model response sets on HotpotQA-halu,
   form pairs `(consistent, consistent)` → `FACTUAL_AGREEMENT` and `(answer, hallucinated)`
   → `FACTUAL_DISAGREEMENT`. Tag `source_dataset="SAC3"`.
2. **FELM →** clone `hkust-nlp/felm`. For prompts whose ChatGPT response is fully factual,
   pair it with a second factual paraphrase → `FACTUAL_AGREEMENT`. For prompts with a
   false-labeled span, pair the original with a corrected version → `FACTUAL_DISAGREEMENT`.
   Tag `source_dataset="FELM"`.
3. **FINCH-ZK →** verify it exists/accessible. If not, substitute an equivalent zero-shot
   fact-check set (e.g. a slice of TruthfulQA or HaluEval) — flag the substitution in
   `annotation_notes` and the changelog. Do **not** block the corpus on a missing source.

These three give us a strong `FACTUAL_AGREEMENT`/`FACTUAL_DISAGREEMENT` base (likely
40–80 each). They contribute little to the other four classes.

## The four under-represented classes — Phoenix-generation strategy

Generate pairs by running **≥ 2 cognition providers** (the existing Anthropic/OpenAI/Google
adapters) on purpose-built prompt sets, then label. Per the roadmap's targeted recipes:

- **REFUSAL_DIVERGENCE** — capability/policy gray-area prompts that trigger a refusal on one
  provider but a completion on another. Pair `(refusal, completion)`. Source prompts from
  borderline-but-benign safety topics; do **not** generate genuinely harmful content — the
  *refusal asymmetry* is the signal, not the payload.
- **TOOL_CHOICE_DIVERGENCE** — the same query run (a) with vs. without tool-use enabled, or
  (b) with different tool sets exposed. Pair `(tool_call, no_tool)` or
  `(tool_A, tool_B)`. Phoenix's MCP-client mode + the cognition adapters make this directly
  generable.
- **INTERPRETIVE_DIVERGENCE** — ambiguous-by-design prompts (polysemy: "best language",
  "handle this case"; context-free pronouns). Run two providers; when they answer different
  readings, pair them.
- **STYLISTIC_DIVERGENCE** — ask different providers (or the same provider with different
  system prompts) to render **the same fact** in different registers (casual vs. formal,
  terse vs. verbose). Pair the two renderings.

Target ~28–35 per class so each clears the ~28 floor with margin after the held-out split.

## Annotation process

Every generated/extracted pair needs a `gold_class`. Two viable paths:

- **(A) Hand-label** — Adam labels each pair against the [annotation guide](./STEP5C_ANNOTATION_GUIDE.md).
  Highest quality; ~250 pairs is a few focused hours.
- **(B) LLM-judge bootstrap, human-verify** — use a strong model (Claude) as an
  LLM-as-judge to propose `gold_class` per pair, then Adam spot-checks/corrects. Faster;
  quality depends on the verify pass. This mirrors the hybrid classifier's own
  LLM-as-judge escalation path, so the judge prompt is reusable.

**Recommendation:** (B) for a first pass to get to ~250 quickly, then hand-verify the four
under-represented classes (where boundaries are subtle) and a sample of the factual classes.
Either way, the [annotation guide](./STEP5C_ANNOTATION_GUIDE.md) is the source of truth for
class boundaries.

## Embedding model vendoring

`semantic_distance` currently falls back to **binary** (0/1) because the `all-MiniLM-L6-v2`
model (22 MB) isn't vendored under `vendor/cognition_wobble/embeddings/`. For real training
the continuous semantic feature matters (it's the primary separator for
`FACTUAL_AGREEMENT` ↔ `STYLISTIC_DIVERGENCE`). **Vendor the model before the first real
training run** (install `[ml-classifier]`, let `sentence-transformers` download it, commit
the local copy). Until then, training works but leans on the other five features.

## The iteration loop (assemble → audit → train → confusion → rebalance → retrain → gate)

1. **Assemble** the corpus JSONL (sources + generated pairs + labels).
2. **Audit balance** — `python scripts/corpus_stats.py --corpus <file>` reports per-class
   counts vs the ≥28 floor and per-class feature centroids. Fix gross imbalance *before*
   training (the roadmap is explicit: distribution first, features later).
3. **Train** — `python scripts/train_cognition_classifier.py --corpus <train.jsonl> --out
   models/gbm_classifier_v1.txt`.
4. **Read the confusion matrix** — `python scripts/evaluate_cognition_classifier.py --corpus
   <heldout.jsonl> --model <…> --confusion`. The roadmap's confusion→diagnosis map:
   - `FACTUAL_AGREEMENT ↔ STYLISTIC_DIVERGENCE` → need a factual-overlap feature distinct
     from text-overlap.
   - `FACTUAL_DISAGREEMENT ↔ INTERPRETIVE_DIVERGENCE` → need a topical-overlap feature.
   - High `REFUSAL_DIVERGENCE` precision, low recall → refusal patterns too narrow; expand
     `_REFUSAL_PATTERNS` in `features.py`.
5. **Rebalance / add features** (only now, after seeing the confusion matrix) — see below.
6. **Retrain + re-evaluate** on the held-out split until **macro-F1 ≥ 0.70**.
7. **Swap** — commit the artifact + register at startup (procedure in
   [the Step 5c harness plan](../superpowers/plans/2026-06-09-phase-13-step5c-classifier-training-harness.md)).

## Deferred feature additions (only after the rebalance)

Per the roadmap (do **not** add before fixing distribution): both append cleanly to
`extract_features()` + `FEATURE_NAMES` and require a retrain:

- **Named-entity overlap fraction** — fraction of named entities shared between responses
  (spaCy small English model). Separates `FACTUAL_DISAGREEMENT` from `INTERPRETIVE_DIVERGENCE`.
- **Numerical-agreement detector** — when both responses contain numbers, do they match
  within tolerance? Strong `FACTUAL_DISAGREEMENT` signal. *(Adam's measurement domain — the
  tolerance rule is a judgment call worth his input.)*

## Work breakdown

**Adam-side (the data work — gated on you):**
- Decide the annotation path (A hand-label vs B judge-bootstrap-then-verify).
- Generate/collect the pairs (SAC3 + FELM + FINCH-ZK-or-substitute + Phoenix-generated 4).
- Label them (per the annotation guide).
- Run the iterate-to-gate loop; commit the artifact when macro-F1 ≥ 0.70; do the swap.
- Provide API keys for the Phoenix-generation step (multi-provider calls).

**Claude-side (buildable now, NOT gated on the corpus):**
- ✅ Corpus audit/stats CLI (`scripts/corpus_stats.py`) — step 2 of the loop.
- ✅ Confusion-matrix reporting (`--confusion` on the eval CLI) — step 4 of the loop.
- ✅ Annotation guide (`STEP5C_ANNOTATION_GUIDE.md`) — the labeling source of truth.
- ✅ Pair-generation harness (`cognition_wobble/generation.py` + `scripts/generate_cognition_pairs.py`)
  — runs ≥2 providers on the per-class seed sets → unlabeled pairs JSONL.
- ✅ LLM-judge labeler (`cognition_wobble/annotation.py` + `scripts/label_cognition_pairs.py`)
  — reuses `LLMJudgeClassifier` to propose `gold_class`; flags hard/low-confidence rows.
- ✅ Embedding-model vendoring (`scripts/vendor_embedding_model.py` + loader prefers the
  vendored path) + provider factory (`cognition_wobble/provider_factory.py`) + seed prompt
  sets (`vendor/cognition_wobble/calibration/prompt_seeds/`).
- ✅ SAC3/FELM dataset adapters (`cognition_wobble/datasets.py` +
  `scripts/adapt_dataset.py`) — **FELM** verified-error records → deterministic,
  pre-labeled `FACTUAL_DISAGREEMENT` pairs (original vs. correction from FELM's
  `comment`, no model calls); **SAC3** method output → candidate factual-class
  pairs from the consistency votes (re-judge/verify). Run:
  `python scripts/adapt_dataset.py --dataset felm --in felm_*.jsonl --out felm_pairs.jsonl`.
- ⏳ (after rebalance) NER-overlap + numerical-agreement features.

### Generation + labeling workflow (delivered)

```bash
# 1. Vendor the embedding model once (decision #3):
pip install -e ".[ml-classifier]" && python scripts/vendor_embedding_model.py

# 2. Generate candidate pairs for an under-represented class (needs keys):
python scripts/generate_cognition_pairs.py --class refusal \
    --providers anthropic:claude-sonnet-4-7-20260418,openai:gpt-4o --out pairs_refusal.jsonl

# 3. Judge-label them (decision #1 path B), then human-verify the NEEDS-VERIFY rows:
python scripts/label_cognition_pairs.py --pairs pairs_refusal.jsonl \
    --judge-provider anthropic:claude-haiku-4-5-20251001 --out labeled_refusal.jsonl

# 4. Combine with the SAC3/FELM/substitute factual pairs, audit, train, iterate to the gate.
```

## Decisions locked (2026-06-12)

1. **Annotation path → (B)** LLM-judge bootstrap + human-verify (tooled above).
2. **FINCH-ZK → substitute OK** (TruthfulQA/HaluEval slice if it stays missing; flag in notes).
3. **Embedding model → vendor-first** (script + loader preference delivered; run before the
   first real training run).
4. **Pair-generation harness → build it** (delivered above).

## Risks

- **FINCH-ZK availability** — unverified; have a substitute ready.
- **Label consistency on the four hard classes** — mitigated by the annotation guide's
  boundary rules (and Adam's verify pass).
- **Synthetic ≠ real** — the synthetic fixture clears the gate trivially because it's
  separable by construction; real data will be harder and *should* drive at least one
  feature addition. Don't read the synthetic 1.0 as predictive.
- **Generation provider drift** — pairs generated today reflect today's model behavior;
  fine for v1.1's minimum-viable set (the build guide already frames expansion as v1.2).
