"""Acceptance gate + human-readable report for the cognition classifier.

The numeric work (per-class precision/recall/F1, macro-F1, confusion
matrix) already lives in :func:`cognition_wobble.eval.evaluate`. This
module is the thin layer on top that the Step 5c harness needs:

- :data:`ACCEPTANCE_MACRO_F1` — the build-guide gate (``>= 0.70`` across
  the six graded classes; UNCLASSIFIED is the escape valve, not graded).
- :func:`check_gate` — turn a :class:`CalibrationReport` into a
  pass/fail :class:`GateResult`.
- :func:`format_report` — render a report (and gate verdict) as a
  fixed-width text block for the CLI and CI logs.

Nothing here registers or mutates the production classifier; it only
reports. The gate is advisory until a *trained* artifact clears it — at
which point the operator performs the swap described in
``docs/.../STEP5C_classifier_training_harness.md``.
"""

from __future__ import annotations

from dataclasses import dataclass

from cognition_wobble.disagreement_types import CognitionDisagreementType
from cognition_wobble.eval import CalibrationReport

__all__ = [
    "ACCEPTANCE_MACRO_F1",
    "GateResult",
    "check_gate",
    "format_report",
    "format_confusion_matrix",
]


# Phase 13 build guide Step 5b/5c acceptance gate: macro-F1 across the
# six graded classes must reach this for the trained classifier to ship.
ACCEPTANCE_MACRO_F1: float = 0.70


@dataclass(frozen=True)
class GateResult:
    """Pass/fail verdict against the macro-F1 acceptance gate.

    Fields:
        passed: ``True`` iff ``macro_f1 >= threshold``.
        macro_f1: The evaluated macro-F1 (echoed from the report).
        threshold: The gate threshold checked against.
        classifier_version: Version of the classifier evaluated.
        n_graded_examples: Graded examples the F1 was computed over
            (gate confidence is low when this is small — the bootstrap
            set, for instance, has only 12 graded rows).
    """

    passed: bool
    macro_f1: float
    threshold: float
    classifier_version: str
    n_graded_examples: int


def check_gate(
    report: CalibrationReport,
    *,
    threshold: float = ACCEPTANCE_MACRO_F1,
) -> GateResult:
    """Evaluate ``report`` against the macro-F1 acceptance gate."""
    return GateResult(
        passed=report.macro_f1 >= threshold,
        macro_f1=report.macro_f1,
        threshold=threshold,
        classifier_version=report.classifier_version,
        n_graded_examples=report.n_graded_examples,
    )


# Stable display order for the per-class table (matches the GBM class
# order; deterministic output for diffs/CI).
_DISPLAY_ORDER: tuple[CognitionDisagreementType, ...] = (
    CognitionDisagreementType.FACTUAL_AGREEMENT,
    CognitionDisagreementType.STYLISTIC_DIVERGENCE,
    CognitionDisagreementType.FACTUAL_DISAGREEMENT,
    CognitionDisagreementType.INTERPRETIVE_DIVERGENCE,
    CognitionDisagreementType.REFUSAL_DIVERGENCE,
    CognitionDisagreementType.TOOL_CHOICE_DIVERGENCE,
)


def format_report(
    report: CalibrationReport,
    *,
    threshold: float = ACCEPTANCE_MACRO_F1,
) -> str:
    """Render a :class:`CalibrationReport` as a fixed-width text block.

    Includes the per-class precision/recall/F1/support table, the macro
    line, the abstention count, and the gate verdict. Pure formatting;
    no side effects.
    """
    gate = check_gate(report, threshold=threshold)
    lines: list[str] = []
    lines.append("=" * 64)
    lines.append("Cognition classifier calibration report")
    lines.append("=" * 64)
    lines.append(f"classifier version : {report.classifier_version or '(unset)'}")
    lines.append(f"examples evaluated : {report.n_examples} "
                 f"({report.n_graded_examples} graded)")
    lines.append(f"UNCLASSIFIED preds : {report.n_unclassified_predictions}")
    lines.append("")

    header = f"{'class':<26}{'prec':>7}{'recall':>8}{'f1':>7}{'support':>9}"
    lines.append(header)
    lines.append("-" * len(header))
    for cls in _DISPLAY_ORDER:
        m = report.per_class.get(cls)
        if m is None:
            continue
        lines.append(
            f"{cls.value:<26}"
            f"{m.precision:>7.3f}"
            f"{m.recall:>8.3f}"
            f"{m.f1:>7.3f}"
            f"{m.support:>9d}"
        )
    lines.append("-" * len(header))
    lines.append(
        f"{'MACRO':<26}"
        f"{report.macro_precision:>7.3f}"
        f"{report.macro_recall:>8.3f}"
        f"{report.macro_f1:>7.3f}"
        f"{report.n_graded_examples:>9d}"
    )
    lines.append("")
    verdict = "PASS" if gate.passed else "FAIL"
    lines.append(
        f"GATE: macro-F1 {report.macro_f1:.4f} "
        f"{'>=' if gate.passed else '<'} {threshold:.2f}  ->  {verdict}"
    )
    if not gate.passed:
        lines.append(
            "  (classifier does NOT clear the acceptance gate; "
            "the shipped default stays AlwaysUnclassifiedClassifier.)"
        )
    lines.append("=" * 64)
    return "\n".join(lines)


# Two-letter abbreviations for the confusion-matrix grid (ASCII-only so the
# table renders on a Windows cp1252 console).
_ABBREV: dict[CognitionDisagreementType, str] = {
    CognitionDisagreementType.FACTUAL_AGREEMENT: "FA",
    CognitionDisagreementType.STYLISTIC_DIVERGENCE: "SD",
    CognitionDisagreementType.FACTUAL_DISAGREEMENT: "FD",
    CognitionDisagreementType.INTERPRETIVE_DIVERGENCE: "ID",
    CognitionDisagreementType.REFUSAL_DIVERGENCE: "RD",
    CognitionDisagreementType.TOOL_CHOICE_DIVERGENCE: "TC",
    CognitionDisagreementType.UNCLASSIFIED: "UNCL",
}


def format_confusion_matrix(report: CalibrationReport) -> str:
    """Render the report's confusion matrix as a fixed-width grid.

    Rows are the gold class, columns the predicted class; both span the six
    graded classes plus ``UNCLASSIFIED`` (predictions can be UNCLASSIFIED via
    the threshold escape valve, and gold-UNCLASSIFIED rows can exist). This is
    step 4 of the Step 5c iterate-to-gate loop: read the confusion matrix
    *before* retraining to see which class pairs the classifier confuses
    (e.g. FA<->SD confusion -> need a factual-overlap feature; FD<->ID -> need a
    topical-overlap feature; high RD precision + low recall -> refusal patterns
    too narrow).

    Off-diagonal cells are where the misclassifications live; the diagonal is
    correct predictions.
    """
    order: tuple[CognitionDisagreementType, ...] = (*_DISPLAY_ORDER, CognitionDisagreementType.UNCLASSIFIED)
    cm = report.confusion_matrix

    lines: list[str] = []
    lines.append("confusion matrix (rows=gold, cols=predicted):")
    legend = ", ".join(f"{_ABBREV[c]}={c.value}" for c in order)
    lines.append(f"  legend: {legend}")
    lines.append("")

    # Header row of predicted-class abbreviations.
    header = f"{'gold \\ pred':<26}" + "".join(f"{_ABBREV[c]:>6}" for c in order)
    lines.append(header)
    lines.append("-" * len(header))
    for gold in order:
        row_total = sum(cm.get((gold, pred), 0) for pred in order)
        if row_total == 0:
            continue  # no gold examples of this class; skip the empty row
        cells = "".join(f"{cm.get((gold, pred), 0):>6d}" for pred in order)
        lines.append(f"{gold.value:<26}{cells}")
    return "\n".join(lines)
