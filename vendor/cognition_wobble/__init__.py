"""``cognition_wobble`` — Phoenix's cognition disagreement classifier substrate.

Phase 13 Step 5 vendor package. Lives alongside the existing
``vendor/wobble/`` (physics disagreement classifier) but is entirely
independent per 13-D3: cognition disagreements are semantic and
pragmatic (FACTUAL_AGREEMENT, STYLISTIC_DIVERGENCE,
FACTUAL_DISAGREEMENT, ...), not numerical (cross-precision distance).
A classifier trained on physics performs poorly on cognition.

Step 5a (current) ships:

- :mod:`cognition_wobble.disagreement_types` — the
  :class:`CognitionDisagreementType` enum (seven classes).
- :mod:`cognition_wobble.classifier` — the
  :class:`CognitionClassifier` Protocol + result types + the
  ``AlwaysUnclassifiedClassifier`` stub (always emits UNCLASSIFIED;
  used for harness testing).
- :mod:`cognition_wobble.eval` — :class:`CalibrationExample`,
  :class:`CalibrationReport`, and :func:`evaluate` with macro-F1
  + per-class precision/recall + confusion matrix.
- :mod:`cognition_wobble.calibration.bootstrap` — a small (~14
  examples) hand-crafted bootstrap eval set sufficient to exercise
  the eval framework. The 200+-example calibration set lands in
  Step 5b.

Step 5b (next) ships:

- The real GBM-based classifier (xgboost or lightgbm pinned) with
  feature engineering: semantic-distance score, length ratio,
  refusal-pattern match, tool-call equality, factual-claim NER.
- 200+ calibration examples spanning all seven classes (~28/class).
- The pinned ``all-MiniLM-L6-v2`` embedding model
  (``vendor/cognition_wobble/embeddings/``, 22 MB).
- LLM-as-judge escalation for UNCLASSIFIED cases (hybrid per
  ``[OPEN: P13-4]`` default).
- Axis integration:
  :mod:`phoenix.verification.axes` updated to call the classifier
  instead of emitting raw UNCLASSIFIED.
- Distance-metric upgrade (P13-3 swap): exact-string match →
  sentence-embedding cosine similarity.
- Step 5b acceptance gate: macro-F1 ≥ 0.70 on the calibration set.
"""

from cognition_wobble.classifier import (
    AlwaysUnclassifiedClassifier,
    ClassificationResult,
    CognitionClassifier,
)
from cognition_wobble.disagreement_types import CognitionDisagreementType

__all__ = [
    "AlwaysUnclassifiedClassifier",
    "ClassificationResult",
    "CognitionClassifier",
    "CognitionDisagreementType",
]
