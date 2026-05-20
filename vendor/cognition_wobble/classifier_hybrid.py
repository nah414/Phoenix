"""``HybridClassifier`` — GBM primary + LLM-as-judge escalation.

Per Phase 13 build guide Step 5 + ``[OPEN: P13-4]`` default: the
production cognition classifier composes the GBM primary
(fast, cheap, calibrated on the 200+-example training set) with the
LLM-as-judge escalation (one extra LLM call per ambiguous case).

The hybrid orchestrator's decision tree:

1. Run the GBM classifier.
2. If GBM's output is UNCLASSIFIED (confidence below threshold),
   escalate to the LLM judge.
3. Return the judge's output (which may itself be UNCLASSIFIED if
   the judge can't classify either — the load-bearing escape valve
   is preserved end-to-end).

Cost characterization:

- Common case (GBM confident): one feature-extraction pass + one
  GBM forward pass. Sub-millisecond.
- Escalation case (GBM unconfident): also one LLM call to the judge.
  Latency dominated by the judge's provider.

The escalation rate is the key metric the operator tunes via the
``confidence_threshold`` parameter: higher threshold → more
escalations → higher cost but better classification accuracy on
edge cases. Step 5c will measure this rate on the 200+-example set.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cognition_wobble.classifier import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    ClassificationResult,
)
from cognition_wobble.disagreement_types import CognitionDisagreementType

if TYPE_CHECKING:
    from cognition_wobble.classifier import CognitionClassifier
    from phoenix.providers.cognition.types import CognitionResult, Prompt


class HybridClassifier:
    """Compose a GBM primary classifier with an LLM-as-judge fallback.

    Both ``primary`` and ``judge`` must satisfy the
    :class:`CognitionClassifier` Protocol. Typical construction:

    .. code-block:: python

        gbm = GBMClassifier()
        judge = LLMJudgeClassifier(provider=anthropic_haiku_provider)
        hybrid = HybridClassifier(primary=gbm, judge=judge)
    """

    def __init__(
        self,
        *,
        primary: CognitionClassifier,
        judge: CognitionClassifier,
    ) -> None:
        self._primary = primary
        self._judge = judge

    @property
    def version(self) -> str:
        """Composite version reflecting both wrapped classifiers."""
        return f"hybrid({self._primary.version}+{self._judge.version})"

    def classify(
        self,
        prompt: Prompt,
        responses: list[CognitionResult],
        *,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> ClassificationResult:
        """Classify via primary, escalating to judge when primary
        returns UNCLASSIFIED."""
        primary_result = self._primary.classify(
            prompt, responses, confidence_threshold=confidence_threshold
        )

        if primary_result.disagreement_type is not CognitionDisagreementType.UNCLASSIFIED:
            return primary_result

        # Escalate.
        judge_result = self._judge.classify(
            prompt, responses, confidence_threshold=confidence_threshold
        )

        # Return the judge's classification with the hybrid version
        # string + preserve the primary's feature values for ledger
        # debugging (the operator wants to see WHY the primary
        # abstained even when the judge produced a final class).
        return ClassificationResult(
            disagreement_type=judge_result.disagreement_type,
            confidence=judge_result.confidence,
            classifier_version=self.version,
            feature_values=primary_result.feature_values,
            escalated_to_judge=True,
        )
