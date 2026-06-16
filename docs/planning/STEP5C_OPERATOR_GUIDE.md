# Step 5c — Operator Guide (`phoenix cognition`)

How to drive the cognition-corpus harness end-to-end from one console. Every
capability is a `phoenix cognition <verb>` subcommand (the standalone
`scripts/*.py` remain as equivalents).

```text
phoenix cognition adapt|generate|label|audit|train|evaluate|vendor-embeddings
```

All verbs are **offline + file-based** (no daemon needed). Output honours the
global `--format {auto|json|text|table}` flag.

## Prerequisites

```bash
pip install -e .                    # core
pip install -e ".[ml-classifier]"   # lightgbm + sentence-transformers (train + embeddings)
# Provider keys for generate/label: ANTHROPIC_API_KEY / OPENAI_API_KEY / GOOGLE_API_KEY
```

## 60-second quickstart (no keys)

Sample data lives in [`samples/step5c/`](../../samples/step5c/README.md):

```bash
phoenix cognition adapt --dataset felm --in samples/step5c/felm_sample.jsonl --out felm.jsonl
phoenix cognition audit --corpus felm.jsonl
phoenix cognition train --corpus tests/cognition/fixtures/synthetic_corpus.jsonl --out gbm.txt
phoenix cognition evaluate --corpus tests/cognition/fixtures/synthetic_corpus.jsonl --model gbm.txt --confusion
```

## The full pipeline → a real classifier

### 1. Factual classes — adapt the source datasets (no keys)

```bash
# FELM (verified errors -> FACTUAL_DISAGREEMENT pairs, deterministic):
phoenix cognition adapt --dataset felm --in path/to/felm_*.jsonl --out felm_pairs.jsonl
# SAC3 (method output -> candidate pairs from the consistency votes):
phoenix cognition adapt --dataset sac3 --in path/to/sac3_out.jsonl --out sac3_pairs.jsonl
```

### 2. The four under-represented classes — generate (needs keys)

```bash
phoenix cognition generate --class refusal \
    --providers anthropic:claude-sonnet-4-7-20260418,openai:gpt-4o --out refusal_pairs.jsonl
# repeat for: tool_choice, interpretive, stylistic
```

### 3. Label the generated pairs (needs a judge key), then verify

```bash
phoenix cognition label --pairs refusal_pairs.jsonl \
    --judge-provider anthropic:claude-haiku-4-5-20251001 --out refusal_labeled.jsonl
```

Hand-check the rows the report marks `NEEDS-VERIFY` (the four hard classes,
low-confidence, and abstentions) per the
[annotation guide](./STEP5C_ANNOTATION_GUIDE.md).

### 4. Assemble → audit balance

Concatenate the labeled/adapted JSONL files into one corpus, then:

```bash
phoenix cognition audit --corpus corpus.jsonl --strict
```

Fix gross imbalance **before** training — aim for ≥ 28 per graded class (the
roadmap is explicit: distribution first, features later).

### 5. Vendor the embedding model (once)

```bash
phoenix cognition vendor-embeddings   # commit vendor/cognition_wobble/embeddings/all-MiniLM-L6-v2/
```

### 6. Train → read the confusion matrix → iterate

```bash
phoenix cognition train --corpus train.jsonl --out models/gbm_classifier_v1.txt --version gbm-v1.0.0
phoenix cognition evaluate --corpus heldout.jsonl --model models/gbm_classifier_v1.txt --confusion
```

`evaluate` exits **0** when macro-F1 ≥ 0.70, **1** otherwise. Read the confusion
matrix to decide what to rebalance / which feature to add (see
[`STEP5C_CORPUS_PLAN.md`](./STEP5C_CORPUS_PLAN.md)), then repeat until the gate
passes.

### 7. Swap in (only after the gate passes)

Commit `vendor/cognition_wobble/models/gbm_classifier_v1.txt`, bump
`GBMClassifier.version`, and register at daemon startup:

```python
from phoenix.ledger.cognition_classifier import set_cognition_classifier
from cognition_wobble.classifier_gbm import GBMClassifier
set_cognition_classifier(GBMClassifier())
```

Full procedure: [`2026-06-09-phase-13-step5c-classifier-training-harness.md`](../superpowers/plans/2026-06-09-phase-13-step5c-classifier-training-harness.md).

## Exit codes

`0` ok / gate pass · `1` gate fail (`evaluate`) · `2` usage/load error · `4`
missing optional extra (`[ml-classifier]`).
