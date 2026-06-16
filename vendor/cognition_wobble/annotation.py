"""LLM-judge labeling for the Step 5c corpus (annotation path B).

Adam's chosen annotation path: bootstrap gold labels with an LLM-as-judge, then
human-verify (especially the four subtle classes). This module is the bootstrap
half — it runs unlabeled pairs through a :class:`CognitionClassifier` (in
production, :class:`cognition_wobble.classifier_llm_judge.LLMJudgeClassifier`)
and writes back the proposed ``gold_class`` plus a verify flag.

It reuses the existing judge **by Protocol**: the core :func:`label_pairs` takes
any classifier exposing ``classify``, so the CLI injects the real LLM judge
while tests inject a scripted classifier — no API keys in the test path. The
judge prompt + JSON parser are NOT re-implemented (they're locked in 13-D3).

A pair is flagged ``needs_verify`` when the judge picks one of the four
hard-to-bootstrap classes, abstains (``UNCLASSIFIED``), or reports confidence
below ``min_confidence`` — those are exactly the rows a human should re-check.

**Security note:** the pair text fed here is provider-generated (or
dataset-sourced) and therefore not fully trusted; an adversarial prompt could
attempt to inject instructions into the judge. The judge
(:mod:`cognition_wobble.classifier_llm_judge`) mitigates this with a fixed system
prompt, ``temperature=0`` and strict enum/JSON validation of its output, so a
successful injection cannot produce an out-of-taxonomy label. Defense-in-depth
input sanitisation belongs in the judge's meta-prompt builder (tracked
separately, not changed by this labeler). The ``needs_verify`` human pass is the
backstop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from cognition_wobble.classifier import DEFAULT_CONFIDENCE_THRESHOLD
from cognition_wobble.disagreement_types import CognitionDisagreementType
from cognition_wobble.eval import CalibrationExample

if TYPE_CHECKING:
    from cognition_wobble.classifier import CognitionClassifier

__all__ = [
    "HARD_VERIFY_CLASSES",
    "LabeledPair",
    "label_pairs",
    "labeled_examples",
    "verify_summary",
]

# The four under-represented classes are the ones an LLM-judge bootstrap is
# least reliable on (subtle boundaries per the annotation guide), so they always
# get a human-verify flag regardless of judge confidence.
HARD_VERIFY_CLASSES: frozenset[CognitionDisagreementType] = frozenset(
    {
        CognitionDisagreementType.STYLISTIC_DIVERGENCE,
        CognitionDisagreementType.INTERPRETIVE_DIVERGENCE,
        CognitionDisagreementType.REFUSAL_DIVERGENCE,
        CognitionDisagreementType.TOOL_CHOICE_DIVERGENCE,
    }
)


@dataclass(frozen=True)
class LabeledPair:
    """One judge-labeled pair.

    Fields:
        example: The pair with ``gold_class`` set to the judge's prediction and
            ``annotation_notes`` updated to record the judge call.
        judge_class: The class the judge predicted (== ``example.gold_class``).
        confidence: The judge's reported confidence in ``[0, 1]``.
        needs_verify: ``True`` when a human should re-check this row (hard class,
            abstention, or low confidence).
    """

    example: CalibrationExample
    judge_class: CognitionDisagreementType
    confidence: float
    needs_verify: bool


def label_pairs(
    examples: list[CalibrationExample],
    classifier: CognitionClassifier,
    *,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    min_confidence: float = 0.5,
) -> list[LabeledPair]:
    """Run each pair through the judge ``classifier`` and assign a proposed label.

    Args:
        examples: Unlabeled pairs (e.g. from
            :func:`cognition_wobble.generation.generate_pairs`, loaded via
            :func:`cognition_wobble.corpus.load_corpus`). Their existing
            ``gold_class`` (a placeholder) is overwritten.
        classifier: Any object satisfying the ``CognitionClassifier`` Protocol.
            The CLI passes :class:`LLMJudgeClassifier`; tests pass a stub.
        confidence_threshold: Forwarded to ``classify`` (the judge downgrades to
            UNCLASSIFIED below it).
        min_confidence: Rows with judge confidence below this are flagged
            ``needs_verify`` (in addition to the hard-class + abstention flags).

    Returns:
        One :class:`LabeledPair` per input example, in order.
    """
    out: list[LabeledPair] = []
    for ex in examples:
        result = classifier.classify(
            ex.prompt,
            [ex.response_a, ex.response_b],
            confidence_threshold=confidence_threshold,
        )
        judge_class = result.disagreement_type
        needs_verify = (
            judge_class in HARD_VERIFY_CLASSES
            or judge_class is CognitionDisagreementType.UNCLASSIFIED
            or result.confidence < min_confidence
        )
        flag = " NEEDS-VERIFY" if needs_verify else ""
        # Preserve the generation note; append the judge provenance.
        base_note = ex.annotation_notes.split(" | judge=")[0].strip()
        note = (
            f"{base_note} | judge={judge_class.value} conf={result.confidence:.2f}"
            f" ver={classifier.version}{flag}"
        ).strip()
        labeled = CalibrationExample(
            prompt=ex.prompt,
            response_a=ex.response_a,
            response_b=ex.response_b,
            gold_class=judge_class,
            source_dataset=ex.source_dataset,
            annotation_notes=note,
        )
        out.append(
            LabeledPair(
                example=labeled,
                judge_class=judge_class,
                confidence=result.confidence,
                needs_verify=needs_verify,
            )
        )
    return out


def labeled_examples(labeled: list[LabeledPair]) -> list[CalibrationExample]:
    """Extract the :class:`CalibrationExample` list (for writing back to JSONL)."""
    return [lp.example for lp in labeled]


def verify_summary(labeled: list[LabeledPair]) -> dict[str, int]:
    """Tally results for a post-labeling report: per-class counts + verify count.

    All seven taxonomy classes are always present (0 when absent), so callers can
    index any class key without a ``KeyError`` (mirrors
    :func:`cognition_wobble.corpus.count_by_class`).
    """
    summary: dict[str, int] = {"total": len(labeled), "needs_verify": 0}
    for cls in CognitionDisagreementType:
        summary[cls.value] = 0
    for lp in labeled:
        summary[lp.judge_class.value] += 1
        if lp.needs_verify:
            summary["needs_verify"] += 1
    return summary
