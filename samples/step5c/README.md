# Step 5c — runnable sample data

Small, **hand-crafted** sample records in the real FELM / SAC3 schemas so you can
drive the cognition-corpus harness end-to-end **locally, with no API keys**.

> These are *not* the real FELM/SAC3 datasets — they're tiny stand-ins (same
> field shapes) to prove the pipeline runs and to show what each step produces.
> The real datasets go in the same place via the same commands.

## What's here

| File | Format | Use |
|---|---|---|
| `felm_sample.jsonl` | FELM (`hkust-nlp/felm`) | 6 error records → `FACTUAL_DISAGREEMENT` pairs; 2 fully-factual → skipped |
| `sac3_sample.jsonl` | SAC3 (`intuit/sac3`) output | candidate `FACTUAL_AGREEMENT` / `FACTUAL_DISAGREEMENT` pairs from the votes |

## Run it (no keys needed)

```bash
# 1. Adapt the factual-class sources into corpus JSONL:
python scripts/adapt_dataset.py --dataset felm --in samples/step5c/felm_sample.jsonl --out felm_pairs.jsonl
python scripts/adapt_dataset.py --dataset sac3 --in samples/step5c/sac3_sample.jsonl --out sac3_pairs.jsonl

# 2. Audit class balance (per-class counts vs the ~28 floor, dup + feature centroids):
python scripts/corpus_stats.py --corpus felm_pairs.jsonl

# 3. Train + evaluate the GBM end-to-end on the bundled synthetic fixture
#    (separable by construction → clears the 0.70 gate; proves train→save→eval wiring):
python scripts/train_cognition_classifier.py --corpus tests/cognition/fixtures/synthetic_corpus.jsonl --out gbm.txt
python scripts/evaluate_cognition_classifier.py --corpus tests/cognition/fixtures/synthetic_corpus.jsonl --model gbm.txt --confusion
```

## The steps that DO need API keys

The four under-represented classes are model-generated, so they need provider
keys (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY`):

```bash
python scripts/generate_cognition_pairs.py --class refusal \
    --providers anthropic:claude-sonnet-4-7-20260418,openai:gpt-4o --out refusal_pairs.jsonl
python scripts/label_cognition_pairs.py --pairs refusal_pairs.jsonl \
    --judge-provider anthropic:claude-haiku-4-5-20251001 --out refusal_labeled.jsonl
```

Full pipeline + the path to a real classifier: see
[`docs/planning/STEP5C_CORPUS_PLAN.md`](../../docs/planning/STEP5C_CORPUS_PLAN.md).
