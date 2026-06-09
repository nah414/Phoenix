# Phase 13 Step 5c — cognition-classifier training + evaluation harness

**Date:** 2026-06-09
**Status:** harness landed; real training **blocked on Adam's labeled corpus.**
**Branch:** `phase-13-step5c-cognition-training-harness`

## What this is

Step 5b shipped the cognition-classifier *scaffolding* — the
`CognitionClassifier` Protocol, the `GBMClassifier` wrapper, the feature
extractors, the calibration eval framework, and a 14-row bootstrap set —
but no trained model artifact, and the **macro-F1 ≥ 0.70** acceptance
gate has never been met. The shipped default is therefore still
`AlwaysUnclassifiedClassifier` (every prediction `UNCLASSIFIED`,
confidence 0.0).

Step 5c is "train the real model and clear the gate." That is gated on a
real labeled corpus that does not yet exist. **This work builds
everything around that corpus** so that the moment the corpus lands,
training and swap-in are mechanical:

1. a documented on-disk **corpus schema + loader**,
2. a **GBM training pipeline** that emits the exact artifact the shipped
   `GBMClassifier` loads,
3. an **evaluation harness** that computes macro-F1 and reports
   pass/fail against the 0.70 gate,
4. a **synthetic fixture** so the whole `load → train → eval` pipeline
   runs end-to-end without the real corpus, and
5. **tests** + these wiring notes.

Nothing here changes production behavior. No model artifact is committed
at `vendor/cognition_wobble/models/`, the registry default is untouched,
and the gate stays unmet until a *real* model passes it.

## Files added

| File | Role |
|---|---|
| `vendor/cognition_wobble/corpus.py` | JSONL corpus schema + `load_corpus()` → `list[CalibrationExample]`, fail-closed validation. |
| `vendor/cognition_wobble/training.py` | `train_gbm()` → lightgbm native-text artifact + `*.meta.json` sidecar; dependency-free `build_training_matrix()`. |
| `vendor/cognition_wobble/acceptance.py` | `ACCEPTANCE_MACRO_F1 = 0.70`, `check_gate()`, `format_report()` (wraps the existing `eval.evaluate()`). |
| `vendor/cognition_wobble/calibration/synthetic.py` | Deterministic **synthetic** corpus generator (NOT real data). |
| `scripts/train_cognition_classifier.py` | Training CLI. |
| `scripts/evaluate_cognition_classifier.py` | Evaluation CLI (exit 0 = gate PASS, 1 = FAIL). |
| `scripts/gen_synthetic_cognition_corpus.py` | Regenerates the fixture JSONL. |
| `tests/cognition/fixtures/synthetic_corpus.jsonl` | Committed synthetic fixture (252 rows). |
| `tests/cognition/test_corpus_loader.py` | Loader + validation tests (always run). |
| `tests/cognition/test_classifier_acceptance.py` | Gate + report + training-matrix tests (always run). |
| `tests/cognition/test_train_cognition_classifier.py` | Training + e2e tests (`importorskip lightgbm`). |

## Corpus schema (the contract for Adam's labeled data)

JSONL — one JSON object per line; blank lines and `#`-comment lines are
skipped. Full reference lives in the `cognition_wobble.corpus` module
docstring. Minimal row:

```json
{"prompt": "When did World War II end?",
 "response_a": "It ended in 1944.",
 "response_b": "It ended in 1945.",
 "gold_class": "factual_disagreement",
 "source_dataset": "phoenix-generated"}
```

- `prompt` / `response_a` / `response_b` accept either a **bare string**
  or the full object form (`prompt` → `{system, messages, metadata}`;
  response → `{text, tool_calls:[{call_id,name,arguments}]}`). Only
  `text` and `tool_calls` feed the feature extractors; usage/latency/
  fingerprint are filled with neutral placeholders by the loader.
- `gold_class` ∈ the seven `CognitionDisagreementType` string values.
  `unclassified` rows are allowed (they test abstention) but are dropped
  from training and excluded from the graded macro-F1.
- Validation is **fail-closed**: an unknown field, missing required
  field, malformed tool-call, or unrecognized `gold_class` stops the
  load with the offending **line number**.

Target size (per the build guide / Step 5b notes): **≥ 200 paired
examples, ~28+ per graded class.** The six graded classes must all be
represented; the training CLI warns on classes with < 20 rows.

## Pipeline

```text
labeled corpus (.jsonl)
        │  cognition_wobble.corpus.load_corpus
        ▼
 list[CalibrationExample]
        │  cognition_wobble.training.train_gbm  (lightgbm multiclass, 6 graded classes)
        ▼
 gbm_classifier_v1.txt  +  gbm_classifier_v1.txt.meta.json
        │  cognition_wobble.classifier_gbm.GBMClassifier(model_path=...)
        ▼
 cognition_wobble.eval.evaluate  →  CalibrationReport
        │  cognition_wobble.acceptance.check_gate
        ▼
 PASS (macro-F1 ≥ 0.70)  /  FAIL
```

Train:

```bash
python scripts/train_cognition_classifier.py \
    --corpus path/to/real_corpus.jsonl \
    --out vendor/cognition_wobble/models/gbm_classifier_v1.txt \
    --version gbm-v1.0.0
# add --class-weight balanced if the corpus is class-skewed
```

Evaluate against a held-out split for the real acceptance decision:

```bash
python scripts/evaluate_cognition_classifier.py \
    --corpus path/to/heldout_corpus.jsonl \
    --model vendor/cognition_wobble/models/gbm_classifier_v1.txt
echo $?   # 0 = gate PASS, 1 = FAIL
```

### Artifact format note (why `.txt`, not `.joblib`)

The shipped `GBMClassifier` loads via `lgb.Booster(model_file=...)`, i.e.
lightgbm's **native text** format. `train_gbm()` writes exactly that with
`booster.save_model()`, so the trained artifact drops straight into the
loader with zero glue. The sidecar `*.meta.json` records the class order,
feature names, version, class balance, and lightgbm version for
provenance / replay.

### `UNCLASSIFIED` is never a trained class

The GBM is trained on the **six graded classes only**. `UNCLASSIFIED` is
emitted purely as a *threshold* decision at inference (argmax probability
< `confidence_threshold`). `train_gbm()` drops gold-`UNCLASSIFIED` rows,
and `num_class` is locked to `len(_GBM_CLASS_ORDER)` so the predicted
probability vector index-aligns with `_GBM_CLASS_ORDER`.

## The swap — replacing `AlwaysUnclassifiedClassifier` (only after the gate passes)

This is the deliberate, separate step. Do **not** do it until a real
model clears the gate on a held-out corpus.

1. **Train** the real model and confirm the held-out gate PASS (above).
2. **Commit the artifact** at
   `vendor/cognition_wobble/models/gbm_classifier_v1.txt` (and its
   `.meta.json`). This is the path `GBMClassifier._DEFAULT_MODEL_PATH`
   already points at, so it ships inside the wheel.
3. **Bump the version string.** `GBMClassifier.version` is currently
   `"gbm-v1-step5b-scaffold"`; change it to match the trained artifact
   (e.g. `"gbm-v1.0.0"`, matching `--version` / the meta sidecar) so
   Step 8 ledger provenance + replay record the real model, not the
   scaffold tag. (One-line edit in `classifier_gbm.py`.)
4. **Register at daemon startup**, mirroring how ops swap the prompt
   encryptor:

   ```python
   from phoenix.ledger.cognition_classifier import set_cognition_classifier
   from cognition_wobble.classifier_gbm import GBMClassifier

   set_cognition_classifier(GBMClassifier())  # loads the committed artifact
   ```

   The default stays `AlwaysUnclassifiedClassifier` for any deployment
   that does not call this (e.g. installs without the `[ml-classifier]`
   extra) — fail-soft, not fail-closed.
5. **(Later, optional)** wrap with the hybrid LLM-judge escalation
   (`classifier_hybrid` / `classifier_llm_judge`) for the
   GBM-low-confidence path, per the Step 5b hybrid design.

## What is still blocked on Adam's real corpus

- **The labeled corpus itself** — SAC3 / FELM / FINCH-ZK ingest +
  Phoenix-generated rows, ≥ 200 examples, ~28+ per graded class, in the
  JSONL schema above. The synthetic fixture is a pipeline exerciser, not
  training data.
- **Meeting the gate for real** — the 0.70 macro-F1 number can only be
  earned on real held-out data. The synthetic fixture clears it
  trivially (separable by construction) and proves nothing about
  real-world accuracy.
- **Committing `models/gbm_classifier_v1.txt`** and the registry swap
  (steps 2–4 above) — intentionally not done here.
- **The vendored `all-MiniLM-L6-v2` embedding model** — without it,
  `semantic_distance` falls back to binary exact-match. Real training
  should install the `[ml-classifier]` extra so the semantic feature is
  continuous; the harness works either way.

## Decision flagged for Adam

`cognition_wobble/training.py :: DEFAULT_GBM_PARAMS` carries conservative
small-corpus hyperparameters (marked `[ADAM-TUNE]`). The real corpus's
size and class balance are what should ultimately drive `num_leaves`,
`min_data_in_leaf`, and whether to pass `--class-weight balanced`. The
defaults are a safe starting point, not a tuned answer.
