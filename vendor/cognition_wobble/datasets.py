"""Adapters: FELM / SAC3 source datasets -> Step 5c corpus pairs.

The two named factual-class source datasets have very different shapes, so the
adapters are not symmetric (see ``docs/planning/STEP5C_CORPUS_PLAN.md``):

**FELM** (``hkust-nlp/felm``) is *single-response* factuality: each record is
``{prompt, response, segmented_response[], labels[] (bool), comment[], ...}``,
where ``comment`` holds the **ground-truth correction** for each false-labeled
segment. :func:`from_felm` turns every record that contains a verified error
into a deterministic **FACTUAL_DISAGREEMENT** pair — the original (erroneous)
response vs. a corrected response built by swapping the false segment(s) for
their corrections. These are pre-labeled with high confidence (FELM annotators
verified the error); no model calls needed.

**SAC3** (``intuit/sac3``) is a *method*, not a static dataset: run on
HotpotQA-halu it emits, per question, ``{question, target_answer,
self_responses[], sc2_vote[], ...}`` — multiple sampled responses plus a
per-response consistency vote. :func:`from_sac3` pairs the first response with
each subsequent one; a consistent vote → **FACTUAL_AGREEMENT** candidate, an
inconsistent vote → **FACTUAL_DISAGREEMENT** candidate. SAC3 votes are weaker
than FELM's human labels, so re-judge / human-verify before committing.

Both adapters consume already-parsed records (dicts) and return
:class:`CalibrationExample` lists; the CLI (``scripts/adapt_dataset.py``) reads
JSONL and writes the corpus via :func:`cognition_wobble.corpus.write_corpus`.
Field names follow each dataset's documented schema — **verify against your
actual download**; malformed records are skipped (not fatal) with a reason.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from cognition_wobble.disagreement_types import CognitionDisagreementType
from cognition_wobble.eval import CalibrationExample
from phoenix.providers.cognition.types import CognitionResult, Prompt, TokenUsage

__all__ = [
    "AdapterReport",
    "from_felm",
    "from_sac3",
]

_ZERO_USAGE = TokenUsage(input_tokens=0, output_tokens=0)


@dataclass(frozen=True)
class AdapterReport:
    """Outcome of an adapter run."""

    examples: list[CalibrationExample]
    n_input: int
    skip_reasons: list[str] = field(default_factory=list)

    @property
    def n_emitted(self) -> int:
        return len(self.examples)

    @property
    def n_skipped(self) -> int:
        return len(self.skip_reasons)


def _result(text: str, source_tag: str) -> CognitionResult:
    return CognitionResult(
        text=text,
        tool_calls=[],
        usage=_ZERO_USAGE,
        latency_ms=0.0,
        provider_fingerprint=f"corpus|{source_tag}",
    )


def _prompt(text: str) -> Prompt:
    return Prompt(system=None, messages=[{"role": "user", "content": text}])


# ---------------------------------------------------------------------------
# FELM -> FACTUAL_DISAGREEMENT pairs


def _felm_corrected_response(
    segments: list[str], labels: list[bool], comments: list[str]
) -> str | None:
    """Rebuild the response with each false segment swapped for its correction.

    Returns ``None`` when a false segment has no usable correction (so the
    record can't form a clean disagreement pair and is skipped).
    """
    parts: list[str] = []
    corrected_any = False
    for i, seg in enumerate(segments):
        is_true = labels[i] if i < len(labels) else True
        if is_true:
            parts.append(seg)
            continue
        correction = (comments[i] if i < len(comments) else "").strip()
        if not correction:
            return None  # false segment with no correction -> can't build a clean pair
        parts.append(correction)
        corrected_any = True
    if not corrected_any:
        return None
    return " ".join(p for p in parts if p).strip()


def from_felm(
    records: Iterable[dict[str, Any]],
    *,
    source_tag: str = "FELM",
) -> AdapterReport:
    """Adapt FELM records into FACTUAL_DISAGREEMENT pairs.

    A record yields a pair when it has at least one false-labeled segment with a
    correction: ``response_a`` is the original (erroneous) ``response``,
    ``response_b`` is the corrected reconstruction. Records that are fully
    factual (no false segment) or have an uncorrectable error are skipped —
    fully-factual FELM responses are better used as generation prompt-seeds, not
    as one-sided pairs.

    Expected record keys: ``prompt``, ``response``, ``segmented_response``,
    ``labels``, ``comment`` (and optionally ``source``).
    """
    examples: list[CalibrationExample] = []
    skips: list[str] = []
    n_input = 0
    for i, rec in enumerate(records):
        n_input += 1
        prompt = rec.get("prompt")
        response = rec.get("response")
        segments = rec.get("segmented_response")
        labels = rec.get("labels")
        comments = rec.get("comment", [])
        if not isinstance(prompt, str) or not isinstance(response, str):
            skips.append(f"felm[{i}]: missing 'prompt'/'response' string")
            continue
        if not isinstance(segments, list) or not isinstance(labels, list):
            skips.append(f"felm[{i}]: missing 'segmented_response'/'labels' list")
            continue
        if all(bool(v) for v in labels):
            skips.append(f"felm[{i}]: fully factual (no error to contrast) -> use as prompt-seed")
            continue
        comments_list = comments if isinstance(comments, list) else []
        corrected = _felm_corrected_response(
            [str(s) for s in segments], [bool(v) for v in labels], [str(c) for c in comments_list]
        )
        if corrected is None:
            skips.append(f"felm[{i}]: false segment without a usable correction")
            continue
        src = str(rec.get("source", source_tag)) or source_tag
        examples.append(
            CalibrationExample(
                prompt=_prompt(prompt),
                response_a=_result(response, source_tag),
                response_b=_result(corrected, source_tag),
                gold_class=CognitionDisagreementType.FACTUAL_DISAGREEMENT,
                source_dataset=source_tag,
                annotation_notes=f"FELM verified error vs correction (felm-source={src})",
            )
        )
    return AdapterReport(examples=examples, n_input=n_input, skip_reasons=skips)


# ---------------------------------------------------------------------------
# SAC3 method output -> candidate pairs


def from_sac3(
    records: Iterable[dict[str, Any]],
    *,
    source_tag: str = "SAC3",
    prelabel_from_votes: bool = True,
) -> AdapterReport:
    """Adapt SAC3 method output into candidate factual-class pairs.

    Each record is expected to carry ``question`` and ``self_responses`` (a list
    of >= 2 sampled responses), and optionally ``sc2_vote`` (a per-response
    consistency vote, 1 = consistent with the first/reference response). The
    first response is paired with each subsequent one:

    - ``prelabel_from_votes=True`` (default): vote 1 -> FACTUAL_AGREEMENT, vote 0
      -> FACTUAL_DISAGREEMENT. SAC3 votes are model-derived (weaker than FELM's
      human labels), so treat these as candidates and re-judge / human-verify.
    - ``prelabel_from_votes=False`` or no votes present: emit UNLABELED
      (placeholder) pairs for the LLM-judge labeler.
    """
    examples: list[CalibrationExample] = []
    skips: list[str] = []
    n_input = 0
    for i, rec in enumerate(records):
        n_input += 1
        question = rec.get("question")
        responses = rec.get("self_responses")
        if not isinstance(question, str) or not isinstance(responses, list) or len(responses) < 2:
            skips.append(f"sac3[{i}]: needs 'question' + 'self_responses' (>=2)")
            continue
        votes = rec.get("sc2_vote") if isinstance(rec.get("sc2_vote"), list) else None
        ref = str(responses[0])
        for j in range(1, len(responses)):
            cand = str(responses[j])
            if prelabel_from_votes and votes is not None and j < len(votes):
                consistent = bool(votes[j])
                gold = (
                    CognitionDisagreementType.FACTUAL_AGREEMENT
                    if consistent
                    else CognitionDisagreementType.FACTUAL_DISAGREEMENT
                )
                note = f"SAC3 sc2_vote={int(bool(votes[j]))} (candidate; verify)"
            else:
                gold = CognitionDisagreementType.UNCLASSIFIED  # placeholder for the judge
                note = "SAC3 pair UNLABELED (awaiting judge/human label)"
            examples.append(
                CalibrationExample(
                    prompt=_prompt(question),
                    response_a=_result(ref, source_tag),
                    response_b=_result(cand, source_tag),
                    gold_class=gold,
                    source_dataset=source_tag,
                    annotation_notes=note,
                )
            )
    return AdapterReport(examples=examples, n_input=n_input, skip_reasons=skips)
