"""``CognitionClassifierProvenance`` — ledger row for one classification event.

Per Phase 13 build guide Step 5: every classification result is recorded
into the Step 8 Omega Ledger so replay can verify classification
stability across classifier upgrades. If the classifier version
changes between solve and replay, replay flags the row as
``classifier-version-mismatch`` rather than failing — the original
classification is the source of truth.

Per 13-D2: the feature_values_summary stored here is a hashed
projection when ``prompt_disposition == "HASH_ONLY"``; the full
feature dict is dropped (privacy: feature names may carry semantic
hints about prompt content). When ``prompt_disposition == "VERBATIM"``
the full dict is preserved.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cognition_wobble.disagreement_types import CognitionDisagreementType


@dataclass(frozen=True)
class CognitionClassifierProvenance:
    """One classification event's ledger row.

    Fields:
        classifier_version: The :attr:`CognitionClassifier.version`
            at solve time. Replay reads this to detect classifier
            upgrades.
        axis_name: Which axis triggered this classification
            (e.g. ``"cognition_cross_model"``).
        input_response_fingerprints: ``CognitionResult.provider_fingerprint``
            of each input response, in dispatch order. Lets the
            ledger join responses to the classification without
            requiring the full response text (per 13-D2 privacy).
        output_class: The :class:`CognitionDisagreementType` the
            classifier emitted. May be UNCLASSIFIED.
        confidence: Classifier confidence in ``[0, 1]``.
        confidence_threshold: The threshold used at classification
            time. Lets replay reconstruct the
            classifier-vs-UNCLASSIFIED decision boundary.
        escalated_to_judge: ``True`` when the hybrid classifier
            escalated to LLM-as-judge for this call (Step 5b's
            hybrid impl).
        feature_values_summary: Selected features for debugging.
            Step 8's ledger respects ``prompt_disposition`` per 13-D2:
            HASH_ONLY → empty dict; VERBATIM → full dict.
    """

    classifier_version: str
    axis_name: str
    input_response_fingerprints: list[str]
    output_class: CognitionDisagreementType
    confidence: float
    confidence_threshold: float
    escalated_to_judge: bool = False
    feature_values_summary: dict[str, float] = field(default_factory=dict)
