"""``CognitionDisagreementType`` — cognition classifier's seven-class taxonomy.

Per Phase 13 build guide Step 5 + 13-D3: independent from
:class:`phoenix.verification.agreement_classifier.PhoenixDisagreementType`
(the physics-side enum). The independence is intentional —
physics disagreements are numerical and the physics classifier doesn't
generalize to the semantic-and-pragmatic character of cognition
disagreements.

The seven classes (six classified + one escape hatch):

- :attr:`FACTUAL_AGREEMENT` — models agree on the load-bearing claims.
- :attr:`STYLISTIC_DIVERGENCE` — models agree on facts; differ in
  presentation or detail level.
- :attr:`FACTUAL_DISAGREEMENT` — models disagree on a verifiable claim.
- :attr:`INTERPRETIVE_DIVERGENCE` — models read the prompt differently;
  one answers question A, another answers question B.
- :attr:`REFUSAL_DIVERGENCE` — one model answered, another refused on
  safety / policy / capability grounds.
- :attr:`TOOL_CHOICE_DIVERGENCE` — models chose different tools (or
  one chose to call no tool when another did).
- :attr:`UNCLASSIFIED` — escape hatch; emitted whenever classifier
  confidence is below threshold. NEVER hallucinated as a forced
  classification; the verification gate consumes raw distance scores
  when UNCLASSIFIED is returned.

The taxonomy is locked in 13-D3 as the v1.1 default. v1.2 may add
classes (``CITATION_DIVERGENCE``, ``NUMERIC_PRECISION_DIVERGENCE``)
as production traffic surfaces under-represented patterns.
"""

from __future__ import annotations

from enum import Enum


class CognitionDisagreementType(Enum):
    """Cognition classifier taxonomy (locked 2026-05-18, 13-D3).

    Stable string values; new values append. The Step 5b classifier's
    output is one of these seven values; the Step 5a eval framework
    grades classifier outputs against the six classified classes
    (UNCLASSIFIED is not graded — it's the calibrated escape valve).
    """

    FACTUAL_AGREEMENT = "factual_agreement"
    """Models agree on the load-bearing claims. Surface phrasing
    may vary but the facts asserted are equivalent."""

    STYLISTIC_DIVERGENCE = "stylistic_divergence"
    """Models agree on facts; differ in presentation (formality,
    detail level, framing). Cross-model agreement is high once
    style is normalized."""

    FACTUAL_DISAGREEMENT = "factual_disagreement"
    """Models disagree on a verifiable claim (a date, a value, a
    boolean fact). Distinct from interpretive divergence: both models
    answer the same question, but with different facts."""

    INTERPRETIVE_DIVERGENCE = "interpretive_divergence"
    """Models read the prompt differently — one answers question A,
    another answers question B. The disagreement is about *what was
    asked*, not about the answer."""

    REFUSAL_DIVERGENCE = "refusal_divergence"
    """One model answered the prompt; another refused on safety,
    policy, or capability grounds. Asymmetry between models matters
    for downstream consumers (e.g., a Phoenix consumer that needs an
    answer can route around the refusing provider)."""

    TOOL_CHOICE_DIVERGENCE = "tool_choice_divergence"
    """Models chose different tools, or one chose to call no tool
    when another did. The disagreement is about *which capability to
    invoke*, not about the final answer text."""

    UNCLASSIFIED = "unclassified"
    """Escape hatch. The classifier returns this whenever its
    confidence falls below the configured threshold. The verification
    gate consumes the raw distance score in this case; downstream
    consumers can treat it as "the classifier doesn't know". NEVER
    hallucinated as a forced classification — that's the load-bearing
    discipline of 13-D3."""


# Convenience set: the six classes the classifier is graded on
# (UNCLASSIFIED is the calibrated escape valve; not graded).
GRADED_CLASSES: frozenset[CognitionDisagreementType] = frozenset(
    {
        CognitionDisagreementType.FACTUAL_AGREEMENT,
        CognitionDisagreementType.STYLISTIC_DIVERGENCE,
        CognitionDisagreementType.FACTUAL_DISAGREEMENT,
        CognitionDisagreementType.INTERPRETIVE_DIVERGENCE,
        CognitionDisagreementType.REFUSAL_DIVERGENCE,
        CognitionDisagreementType.TOOL_CHOICE_DIVERGENCE,
    }
)
