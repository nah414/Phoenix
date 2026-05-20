# `vendor/cognition_wobble/`

Cognition disagreement classifier substrate — Phase 13 Step 5.

Lives alongside [`vendor/wobble/`](../wobble/) (the physics disagreement classifier substrate) but is **entirely independent** per [13-D3](../../DESIGN_DECISIONS_PHASE13_LOCKED_2026-05-18.md): cognition disagreements are semantic and pragmatic, not numerical. A classifier trained on physics performs poorly on cognition.

## Status

**Step 5a (current):** scaffolding, taxonomy, eval framework, bootstrap eval set, stub classifier, classifier provenance.

**Step 5b (next):**
- Real GBM-based classifier (xgboost or lightgbm pinned) with feature engineering
- 200+ calibration examples spanning all seven classes
- Pinned `all-MiniLM-L6-v2` embedding model under [`embeddings/`](./)
- LLM-as-judge escalation (hybrid per [`[OPEN: P13-4]`](../../BUILDGUIDE_phoenix_v1_phase13_cognition_mcp_client.md))
- Axis integration: [`phoenix.verification.axes`](../../phoenix/verification/axes/) wired to call this classifier
- Distance-metric upgrade (P13-3 swap): exact-string match → sentence-embedding cosine similarity
- Step 5b acceptance gate: **macro-F1 ≥ 0.70** across the six graded classes

## Files

- [`__init__.py`](__init__.py) — package marker + module-level re-exports.
- [`disagreement_types.py`](disagreement_types.py) — `CognitionDisagreementType` enum (7 classes) + `GRADED_CLASSES` set.
- [`classifier.py`](classifier.py) — `CognitionClassifier` Protocol + `ClassificationResult` + `AlwaysUnclassifiedClassifier` stub.
- [`eval.py`](eval.py) — `CalibrationExample`, `ClassMetrics`, `CalibrationReport`, `evaluate()`.
- [`calibration/bootstrap.py`](calibration/bootstrap.py) — ~14 hand-crafted bootstrap examples.

Step 5b adds:
- `classifier_gbm.py` — real GBM impl with feature engineering.
- `classifier_llm_judge.py` — LLM-as-judge escalation.
- `classifier_hybrid.py` — hybrid orchestrator (GBM primary, LLM-judge for UNCLASSIFIED).
- `embeddings/` — pinned sentence-transformers model (~22 MB).
- `calibration/full.py` (or JSON) — 200+ examples (SAC3 + FELM + FINCH-ZK + Phoenix-generated).

## Taxonomy

Seven classes total — six graded + one escape valve:

| Class | Meaning |
|---|---|
| `FACTUAL_AGREEMENT` | Models agree on load-bearing claims. |
| `STYLISTIC_DIVERGENCE` | Agree on facts; differ in presentation. |
| `FACTUAL_DISAGREEMENT` | Disagree on a verifiable claim. |
| `INTERPRETIVE_DIVERGENCE` | Read the prompt differently — one answers A, another answers B. |
| `REFUSAL_DIVERGENCE` | One model answered; another refused. |
| `TOOL_CHOICE_DIVERGENCE` | Different tools chosen (or one chose no tool when another did). |
| `UNCLASSIFIED` | Escape valve. Confidence below threshold → abstain. **Not graded.** |

The locked taxonomy is v1.1; v1.2 may add classes (`CITATION_DIVERGENCE`, `NUMERIC_PRECISION_DIVERGENCE`) as production traffic surfaces under-represented patterns.

## Eval framework

```python
from cognition_wobble import AlwaysUnclassifiedClassifier
from cognition_wobble.calibration import BOOTSTRAP_EXAMPLES
from cognition_wobble.eval import evaluate

report = evaluate(AlwaysUnclassifiedClassifier(), BOOTSTRAP_EXAMPLES)
print(report.macro_f1)  # 0.0 — the stub always abstains
print(report.per_class[CognitionDisagreementType.FACTUAL_AGREEMENT])
```

Macro-F1 is computed across the six graded classes only; `UNCLASSIFIED` is the calibrated escape valve and not graded (per build guide §4.5).

## Independence from `vendor/wobble/`

The two vendored disagreement substrates share zero code. They model different problems:

| | `vendor/wobble/` (physics) | `vendor/cognition_wobble/` (this) |
|---|---|---|
| Inputs | Two solver outputs (numerical) | Two LLM responses (text + tool calls) |
| Distance | Cross-precision floating-point diff | Semantic (Step 5b) or exact-string (Step 5a) |
| Output classes | `NUMERICAL_DRIFT`, `BACKACTION_SENSITIVE`, etc. | `FACTUAL_AGREEMENT`, `STYLISTIC_DIVERGENCE`, etc. |
| Training data | Physics regression suite | SAC3 + FELM + FINCH-ZK + Phoenix-generated |

The independence is the load-bearing claim of 13-D3.
