"""Deterministic SYNTHETIC cognition corpus generator (Step 5c harness).

**THIS IS NOT A REAL CORPUS.** Every example here is machine-generated
with exaggerated, separable feature signatures whose only purpose is to
exercise the load -> train -> evaluate pipeline end-to-end without the
real labeled corpus. The text is nonsense-with-structure, not genuine
model disagreement data. Training a shippable classifier on this set
would be meaningless; it exists purely so the harness has something to
chew on in tests and demos. Real Step 5c training waits on Adam's
labeled corpus (SAC3 / FELM / FINCH-ZK / Phoenix-generated).

Design (see also the module-level note in
``cognition_wobble.corpus``): with the ``all-MiniLM-L6-v2`` embedding
model absent, ``semantic_distance`` is binary (0.0 identical / 1.0
different), so the six graded classes are made separable on the *other*
features:

==========================  =========================================
class                       distinguishing signature
==========================  =========================================
FACTUAL_AGREEMENT           identical responses -> semantic_distance 0
REFUSAL_DIVERGENCE          exactly one refuses -> refusal_match +1
TOOL_CHOICE_DIVERGENCE      tool_call_equality < 1.0
STYLISTIC_DIVERGENCE        very asymmetric length, low factual density
FACTUAL_DISAGREEMENT        symmetric length, high factual density
INTERPRETIVE_DIVERGENCE     symmetric length, low factual density
==========================  =========================================

Determinism: the generator is seeded so the emitted corpus (and the
committed JSONL fixture built from it) is byte-stable run to run.
"""

from __future__ import annotations

import random

from cognition_wobble.disagreement_types import CognitionDisagreementType
from cognition_wobble.eval import CalibrationExample
from phoenix.providers.cognition.types import (
    CognitionResult,
    Prompt,
    TokenUsage,
    ToolCall,
)

__all__ = [
    "SYNTHETIC_SOURCE_TAG",
    "generate_synthetic_corpus",
]

SYNTHETIC_SOURCE_TAG: str = "synthetic-fixture"

_USAGE = TokenUsage(input_tokens=0, output_tokens=0)

# Proper-noun-like tokens (match _PROPER_NOUN_RE: capitalized, len>=3) to
# drive factual_claim_heuristic UP for the high-factual classes.
_PROPER_NOUNS = (
    "Aurora", "Borealis", "Cascadia", "Drummond", "Everest", "Fairbanks",
    "Granger", "Halcyon", "Ingram", "Juniper", "Kelvin", "Langley",
    "Meridian", "Newton", "Orion", "Pembroke", "Quill", "Raleigh",
)
# Common lowercase filler (NO digits, NO capitals) to keep factual density
# DOWN for the low-factual classes.
_FILLER = (
    "the matter depends on how you look at it and what you value most",
    "it really comes down to your own situation and the tradeoffs you accept",
    "there are several ways to think about this depending on the context",
    "people tend to weigh the options differently based on their goals",
    "the honest answer is that it varies quite a lot from case to case",
    "you could approach it from either side and still be reasonable",
)
_TOOLS = ("get_weather", "web_search", "run_query", "calendar_lookup", "unit_convert")
_REFUSALS = (
    "I can't help with that request.",
    "I won't assist with this.",
    "I'm not able to provide that.",
    "As an AI, I cannot do that.",
)


def _result(text: str, *, tool_calls: list[ToolCall] | None = None) -> CognitionResult:
    return CognitionResult(
        text=text,
        tool_calls=tool_calls or [],
        usage=_USAGE,
        latency_ms=0.0,
        provider_fingerprint=f"corpus|{SYNTHETIC_SOURCE_TAG}",
    )


def _prompt(text: str) -> Prompt:
    return Prompt(system=None, messages=[{"role": "user", "content": text}])


def _factual_agreement(rng: random.Random, i: int) -> CalibrationExample:
    # Identical responses -> semantic_distance 0.0 (unique signature).
    n = _PROPER_NOUNS[i % len(_PROPER_NOUNS)]
    text = f"The recommended option here is {n}."
    return CalibrationExample(
        prompt=_prompt(f"Which option do you recommend? (#{i})"),
        response_a=_result(text),
        response_b=_result(text),
        gold_class=CognitionDisagreementType.FACTUAL_AGREEMENT,
        source_dataset=SYNTHETIC_SOURCE_TAG,
        annotation_notes="synthetic: identical responses",
    )


def _stylistic(rng: random.Random, i: int) -> CalibrationExample:
    # Same gist, very asymmetric length, low factual density.
    f = _FILLER[i % len(_FILLER)]
    short = "yep sounds good to me"
    long = (short + ", and to expand on that, " + f + ", " + f + ", "
            + f + ", so overall it works out fine in the end")
    return CalibrationExample(
        prompt=_prompt(f"Is this a good idea? (#{i})"),
        response_a=_result(short),
        response_b=_result(long),
        gold_class=CognitionDisagreementType.STYLISTIC_DIVERGENCE,
        source_dataset=SYNTHETIC_SOURCE_TAG,
        annotation_notes="synthetic: same gist, terse vs verbose",
    )


def _factual_disagreement(rng: random.Random, i: int) -> CalibrationExample:
    # Symmetric length, HIGH factual density (years + proper nouns + digits).
    n = _PROPER_NOUNS[i % len(_PROPER_NOUNS)]
    y1 = 1900 + (i % 80)
    y2 = y1 + 1 + (i % 5)
    a = f"{n} {y1} was {y1} and {n} {y1} {y1}."
    b = f"{n} {y2} was {y2} and {n} {y2} {y2}."
    return CalibrationExample(
        prompt=_prompt(f"When did {n} happen? (#{i})"),
        response_a=_result(a),
        response_b=_result(b),
        gold_class=CognitionDisagreementType.FACTUAL_DISAGREEMENT,
        source_dataset=SYNTHETIC_SOURCE_TAG,
        annotation_notes="synthetic: conflicting dates, high factual density",
    )


def _interpretive(rng: random.Random, i: int) -> CalibrationExample:
    # Symmetric length, LOW factual density (lowercase filler, no digits).
    fa = _FILLER[i % len(_FILLER)]
    fb = _FILLER[(i + 3) % len(_FILLER)]
    return CalibrationExample(
        prompt=_prompt(f"how should i handle this case? (#{i})"),
        response_a=_result(fa),
        response_b=_result(fb),
        gold_class=CognitionDisagreementType.INTERPRETIVE_DIVERGENCE,
        source_dataset=SYNTHETIC_SOURCE_TAG,
        annotation_notes="synthetic: two readings, low factual density",
    )


def _refusal(rng: random.Random, i: int) -> CalibrationExample:
    # Exactly one response refuses -> refusal_pattern_match +1 (unique).
    refusal = _REFUSALS[i % len(_REFUSALS)]
    answer = _FILLER[i % len(_FILLER)]
    # Alternate which side refuses so the class isn't position-correlated.
    if i % 2 == 0:
        a, b = refusal, answer
    else:
        a, b = answer, refusal
    return CalibrationExample(
        prompt=_prompt(f"Do the disallowed thing. (#{i})"),
        response_a=_result(a),
        response_b=_result(b),
        gold_class=CognitionDisagreementType.REFUSAL_DIVERGENCE,
        source_dataset=SYNTHETIC_SOURCE_TAG,
        annotation_notes="synthetic: asymmetric refusal",
    )


def _tool_choice(rng: random.Random, i: int) -> CalibrationExample:
    # tool_call_equality < 1.0 (unique). Alternate 0.0 (one-tool-vs-none)
    # and 0.5 (different-tools) cases.
    t1 = _TOOLS[i % len(_TOOLS)]
    if i % 2 == 0:
        # one tool vs none -> 0.0
        a = _result("", tool_calls=[ToolCall(call_id=f"c{i}", name=t1, arguments={})])
        b = _result("here is a plain text answer without any tool use at all")
    else:
        # different tools -> 0.5
        t2 = _TOOLS[(i + 1) % len(_TOOLS)]
        a = _result("", tool_calls=[ToolCall(call_id=f"a{i}", name=t1, arguments={})])
        b = _result("", tool_calls=[ToolCall(call_id=f"b{i}", name=t2, arguments={})])
    return CalibrationExample(
        prompt=_prompt(f"Get me the data. (#{i})"),
        response_a=a,
        response_b=b,
        gold_class=CognitionDisagreementType.TOOL_CHOICE_DIVERGENCE,
        source_dataset=SYNTHETIC_SOURCE_TAG,
        annotation_notes="synthetic: divergent tool choice",
    )


_GENERATORS = (
    _factual_agreement,
    _stylistic,
    _factual_disagreement,
    _interpretive,
    _refusal,
    _tool_choice,
)


def generate_synthetic_corpus(
    *,
    n_per_class: int = 40,
    n_unclassified: int = 12,
    seed: int = 1337,
) -> list[CalibrationExample]:
    """Generate a deterministic synthetic corpus.

    Args:
        n_per_class: Examples per graded class (six graded classes).
        n_unclassified: Gold-UNCLASSIFIED rows appended (excluded from
            training; included so the loader + abstention paths see
            them).
        seed: RNG seed (kept for forward-compat; the current generators
            are index-driven and deterministic regardless).

    Returns:
        ``6 * n_per_class + n_unclassified`` examples, all tagged
        ``source_dataset="synthetic-fixture"``.
    """
    rng = random.Random(seed)
    examples: list[CalibrationExample] = []
    for i in range(n_per_class):
        for gen in _GENERATORS:
            examples.append(gen(rng, i))

    for j in range(n_unclassified):
        # Genuinely low-signal rows the classifier should abstain on.
        examples.append(
            CalibrationExample(
                prompt=_prompt(f"hmm (#{j})"),
                response_a=_result("ok" if j % 2 == 0 else ""),
                response_b=_result("?" if j % 2 == 0 else ""),
                gold_class=CognitionDisagreementType.UNCLASSIFIED,
                source_dataset=SYNTHETIC_SOURCE_TAG,
                annotation_notes="synthetic: insufficient signal",
            )
        )
    return examples
