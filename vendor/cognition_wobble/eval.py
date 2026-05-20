"""Calibration eval framework for cognition classifiers.

Per Phase 13 build guide Step 5: the evaluator runs a
:class:`CognitionClassifier` against a calibration eval set and
reports per-class precision/recall/F1 + macro-F1 + confusion matrix.
Step 5b's acceptance gate is **macro-F1 ≥ 0.70** across the six
graded classes (UNCLASSIFIED is the escape valve, not graded).

The Step 5a evaluator is fully functional but operates on a small
bootstrap eval set (~14 examples in
:mod:`cognition_wobble.calibration.bootstrap`); Step 5b expands this
to ≥ 200 examples (~28+ per class).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from cognition_wobble.classifier import DEFAULT_CONFIDENCE_THRESHOLD, CognitionClassifier
from cognition_wobble.disagreement_types import GRADED_CLASSES, CognitionDisagreementType

if TYPE_CHECKING:
    from phoenix.providers.cognition.types import CognitionResult, Prompt


@dataclass(frozen=True)
class CalibrationExample:
    """One ground-truth example in the calibration eval set.

    Step 5a's bootstrap ships hand-crafted examples; Step 5b's full
    eval set draws from SAC3, FELM, FINCH-ZK datasets plus
    Phoenix-generated examples covering Phoenix-specific cases
    (physics-prompt LLM responses, tool-use scenarios).

    Fields:
        prompt: The original prompt the responses were generated from.
        response_a: First :class:`CognitionResult` in the pair.
        response_b: Second :class:`CognitionResult` in the pair.
        gold_class: Ground-truth :class:`CognitionDisagreementType`.
            May be :attr:`CognitionDisagreementType.UNCLASSIFIED` for
            genuinely-unclassifiable examples (gold-UNCLASSIFIED rows
            are filtered out of the graded F1 computation; they
            exist to test that the classifier abstains correctly).
        source_dataset: Provenance tag (``"SAC3"``, ``"FELM"``,
            ``"FINCH-ZK"``, ``"phoenix-bootstrap"``,
            ``"phoenix-generated"``).
        annotation_notes: Free-form annotator notes; useful for
            debugging why a particular example was labeled with a
            specific class.
    """

    prompt: Prompt
    response_a: CognitionResult
    response_b: CognitionResult
    gold_class: CognitionDisagreementType
    source_dataset: str = "phoenix-bootstrap"
    annotation_notes: str = ""


@dataclass(frozen=True)
class ClassMetrics:
    """Per-class precision / recall / F1 / support.

    Fields:
        precision: ``TP / (TP + FP)`` for this class. ``0.0`` when
            ``TP + FP == 0`` (no predictions of this class made).
        recall: ``TP / (TP + FN)`` for this class. ``0.0`` when
            ``TP + FN == 0`` (no gold examples of this class).
        f1: ``2 * P * R / (P + R)`` for this class. ``0.0`` when
            ``P + R == 0``.
        support: Number of gold examples of this class in the eval set.
        true_positives: Examples correctly predicted as this class.
        false_positives: Examples predicted as this class but with a
            different gold class.
        false_negatives: Examples with this gold class but predicted
            as a different class (including UNCLASSIFIED).
    """

    precision: float
    recall: float
    f1: float
    support: int
    true_positives: int
    false_positives: int
    false_negatives: int


@dataclass(frozen=True)
class CalibrationReport:
    """Full classifier evaluation report.

    Fields:
        macro_f1: Unweighted mean of per-class F1 across the six
            graded classes. Step 5b acceptance gate: ``>= 0.70``.
        macro_precision: Unweighted mean of per-class precision.
        macro_recall: Unweighted mean of per-class recall.
        per_class: Per-class :class:`ClassMetrics`, keyed by the
            graded :class:`CognitionDisagreementType` values
            (UNCLASSIFIED is not graded and not present in this dict).
        confusion_matrix: Sparse confusion matrix keyed by
            ``(gold, predicted)``. Values are example counts.
        n_examples: Total examples evaluated (all gold classes).
        n_graded_examples: Examples whose gold class is in
            :data:`GRADED_CLASSES` (excludes gold-UNCLASSIFIED rows).
        n_unclassified_predictions: How many predictions came back as
            UNCLASSIFIED. Useful for tuning the confidence threshold.
        classifier_version: The :attr:`CognitionClassifier.version`
            at the time the report was generated.
    """

    macro_f1: float
    macro_precision: float
    macro_recall: float
    per_class: dict[CognitionDisagreementType, ClassMetrics] = field(default_factory=dict)
    confusion_matrix: dict[tuple[CognitionDisagreementType, CognitionDisagreementType], int] = (
        field(default_factory=dict)
    )
    n_examples: int = 0
    n_graded_examples: int = 0
    n_unclassified_predictions: int = 0
    classifier_version: str = ""


def evaluate(
    classifier: CognitionClassifier,
    examples: list[CalibrationExample],
    *,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> CalibrationReport:
    """Evaluate ``classifier`` against ``examples``.

    Runs the classifier on each example, collects predictions, and
    computes per-class precision/recall/F1 plus macro metrics across
    the six graded classes (UNCLASSIFIED excluded per Step 5 spec).

    The Step 5b acceptance gate is ``macro_f1 >= 0.70``; the gate
    check itself lives in the build-guide harness (Step 10's
    acceptance battery), not in the evaluator.
    """
    confusion: dict[tuple[CognitionDisagreementType, CognitionDisagreementType], int] = {}
    n_unclassified_predictions = 0

    for example in examples:
        result = classifier.classify(
            example.prompt,
            [example.response_a, example.response_b],
            confidence_threshold=confidence_threshold,
        )
        key = (example.gold_class, result.disagreement_type)
        confusion[key] = confusion.get(key, 0) + 1
        if result.disagreement_type is CognitionDisagreementType.UNCLASSIFIED:
            n_unclassified_predictions += 1

    per_class: dict[CognitionDisagreementType, ClassMetrics] = {}
    for cls in GRADED_CLASSES:
        # True positives: gold == cls AND predicted == cls.
        tp = confusion.get((cls, cls), 0)
        # False positives: predicted == cls AND gold != cls.
        fp = sum(
            count
            for (gold, pred), count in confusion.items()
            if pred is cls and gold is not cls
        )
        # False negatives: gold == cls AND predicted != cls (includes
        # UNCLASSIFIED predictions on a graded gold).
        fn = sum(
            count
            for (gold, pred), count in confusion.items()
            if gold is cls and pred is not cls
        )
        support = tp + fn

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

        per_class[cls] = ClassMetrics(
            precision=precision,
            recall=recall,
            f1=f1,
            support=support,
            true_positives=tp,
            false_positives=fp,
            false_negatives=fn,
        )

    # Macro: unweighted mean across the six graded classes.
    n_classes = len(GRADED_CLASSES)
    macro_precision = sum(m.precision for m in per_class.values()) / n_classes
    macro_recall = sum(m.recall for m in per_class.values()) / n_classes
    macro_f1 = sum(m.f1 for m in per_class.values()) / n_classes

    n_graded = sum(1 for e in examples if e.gold_class in GRADED_CLASSES)

    return CalibrationReport(
        macro_f1=macro_f1,
        macro_precision=macro_precision,
        macro_recall=macro_recall,
        per_class=per_class,
        confusion_matrix=confusion,
        n_examples=len(examples),
        n_graded_examples=n_graded,
        n_unclassified_predictions=n_unclassified_predictions,
        classifier_version=classifier.version,
    )
