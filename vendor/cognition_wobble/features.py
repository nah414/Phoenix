"""Feature extractors for the cognition disagreement classifier.

Per Phase 13 build guide Step 5: the GBM classifier consumes a fixed
feature vector built from the prompt + two responses. Step 5b ships
the extractors; Step 5c trains the GBM on a 200+-example calibration
set.

Feature set (per build guide §4.5):

- **semantic_distance** — sentence-embedding cosine similarity between
  the two responses' text (Step 5b: falls back to exact-string when
  the embedding model isn't available; Step 5c commits the 22 MB
  ``all-MiniLM-L6-v2`` model).
- **length_ratio** — ``len(response_a) / len(response_b)`` normalized
  to ``[0, 1]`` (= ``min/max``). High = same length; low = one
  response is much longer.
- **refusal_pattern_match** — heuristic detector for refusal phrases
  (``"I can't"``, ``"I won't"``, ``"As an AI"``, etc.). Returns a
  three-valued feature: ``-1.0`` neither refused, ``0.0`` both refused,
  ``+1.0`` exactly one refused.
- **tool_call_equality** — comparison of tool-call presence + names
  across the two responses. Returns ``1.0`` when both took no tool
  calls OR both took the same tool, ``0.5`` when one took a tool and
  the other took a different one, ``0.0`` when one took a tool and
  the other didn't.
- **factual_claim_heuristic** — fraction of tokens that look like
  factual claims (digits, dates, proper-noun-like patterns). Step 5b
  uses a regex heuristic; Step 5c may swap in a spaCy NER model.

All extractors are pure functions over ``(prompt, response_a,
response_b)`` and return floats. The full feature vector is built
by :func:`extract_features` returning a ``dict[str, float]`` ready
for the GBM.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phoenix.providers.cognition.types import CognitionResult, Prompt


# Refusal patterns — heuristic; covers the most common Anthropic /
# OpenAI / Google refusal openings. Step 5c may expand from collected
# refusal corpora.
_REFUSAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bi (?:can(?:'t|not)|won(?:'t|not)|am unable|am not able)\b",
        r"\bas an ai\b",
        r"\bi'?m (?:not (?:going to|allowed to|able to)|sorry)\b",
        r"\bi don'?t (?:assist|help|provide) (?:with|in)\b",
        r"\b(?:refuse to|cannot assist with|cannot help with)\b",
        r"\b(?:blocked|refused) on (?:safety|policy|capability)\b",
    )
)


# Regex shortcuts for factual-claim heuristic.
_DIGIT_RE = re.compile(r"\d")
_DATE_LIKE_RE = re.compile(r"\b(?:19|20)\d{2}\b")  # 1900-2099 year-likes
_PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-z]{2,}\b")  # crude capitalized words


def _looks_like_refusal(text: str) -> bool:
    """Return True when ``text`` matches any refusal pattern."""
    if not text:
        return False
    for pattern in _REFUSAL_PATTERNS:
        if pattern.search(text):
            return True
    return False


def length_ratio(text_a: str, text_b: str) -> float:
    """Normalized length ratio in ``[0, 1]``.

    Returns ``1.0`` when both texts are empty (no signal) or have the
    same length; lower values when one text is much longer than the
    other.
    """
    la = len(text_a)
    lb = len(text_b)
    if la == 0 and lb == 0:
        return 1.0
    if la == 0 or lb == 0:
        return 0.0
    return float(min(la, lb)) / float(max(la, lb))


def refusal_pattern_match(text_a: str, text_b: str) -> float:
    """Three-valued refusal feature.

    Returns:
        - ``-1.0`` if neither response refused.
        - ``0.0`` if both refused.
        - ``+1.0`` if exactly one refused (asymmetric refusal — strong
          signal for REFUSAL_DIVERGENCE).
    """
    a_refused = _looks_like_refusal(text_a)
    b_refused = _looks_like_refusal(text_b)
    if a_refused and b_refused:
        return 0.0
    if a_refused ^ b_refused:
        return 1.0
    return -1.0


def tool_call_equality(
    response_a: CognitionResult,
    response_b: CognitionResult,
) -> float:
    """Tool-call agreement feature.

    Returns:
        - ``1.0`` if both responses took no tool calls OR both called
          the same set of tools (by name; argument equality NOT
          checked).
        - ``0.5`` if both responses took tool calls but with different
          tool names.
        - ``0.0`` if exactly one response took a tool call (strong
          TOOL_CHOICE_DIVERGENCE signal).
    """
    a_tools = {tc.name for tc in response_a.tool_calls}
    b_tools = {tc.name for tc in response_b.tool_calls}
    if not a_tools and not b_tools:
        return 1.0
    if not a_tools or not b_tools:
        return 0.0
    return 1.0 if a_tools == b_tools else 0.5


def factual_claim_heuristic(text: str) -> float:
    """Heuristic factual-claim density.

    Returns the fraction of the text occupied by "factual-claim-like"
    tokens (digits, date-like patterns, proper-noun-like patterns).
    Range ``[0, 1]``. Step 5c may replace with spaCy NER for
    precision.

    Returns ``0.0`` for empty text.
    """
    if not text:
        return 0.0
    text_len = len(text)
    if text_len == 0:
        return 0.0
    matches = 0
    matches += sum(len(m) for m in _DIGIT_RE.findall(text))
    # Don't double-count digits inside date matches; the date regex matches
    # 4 digits, the digit regex matches 1 each. The bias toward dates is
    # intentional — dates are strong factual signals.
    matches += sum(len(m) for m in _DATE_LIKE_RE.findall(text)) * 2
    matches += sum(len(m) for m in _PROPER_NOUN_RE.findall(text))
    return min(1.0, matches / text_len)


def semantic_distance(text_a: str, text_b: str) -> float:
    """Semantic distance between two texts.

    Delegates to :mod:`cognition_wobble.embeddings.loader` for the
    sentence-embedding cosine path; falls back to exact-string
    distance when the embedding model isn't available. Step 5c commits
    the 22 MB ``all-MiniLM-L6-v2`` model so this stops falling back in
    production.
    """
    # Late import: keeps the features module import-able without the
    # optional sentence-transformers extra installed.
    from cognition_wobble.embeddings.loader import compute_semantic_distance

    return compute_semantic_distance(text_a, text_b)


def extract_features(
    prompt: Prompt,
    response_a: CognitionResult,
    response_b: CognitionResult,
) -> dict[str, float]:
    """Build the full feature vector for the GBM classifier.

    Returns a dict ready to feed into
    :meth:`GBMClassifier.classify`. Step 5c's trained model expects
    exactly the keys returned here; adding a new feature requires
    retraining.
    """
    del prompt  # Step 5b features are response-only; Step 5c may add prompt features.
    return {
        "semantic_distance": semantic_distance(response_a.text, response_b.text),
        "length_ratio": length_ratio(response_a.text, response_b.text),
        "refusal_pattern_match": refusal_pattern_match(response_a.text, response_b.text),
        "tool_call_equality": tool_call_equality(response_a, response_b),
        "factual_claim_a": factual_claim_heuristic(response_a.text),
        "factual_claim_b": factual_claim_heuristic(response_b.text),
    }


# The feature names in the order the GBM expects them. Pinned: changing
# this order requires retraining. Step 5c's training pipeline reads
# this tuple at training time.
FEATURE_NAMES: tuple[str, ...] = (
    "semantic_distance",
    "length_ratio",
    "refusal_pattern_match",
    "tool_call_equality",
    "factual_claim_a",
    "factual_claim_b",
)
