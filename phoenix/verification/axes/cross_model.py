"""``CrossModelAxis`` — cross-model agreement axis for cognition tasks.

Per Phase 13 build guide Step 4: dispatches the same prompt to two or
more registered cognition providers (typically the primary plus the
cheapest secondary from the same capability tier), then returns the
raw pairwise distance metric.

**Step 5b update:** optional ``classifier`` kwarg. When provided, the
axis calls the classifier on the (prompt, responses) tuple and
populates ``CognitionDisagreementMetric.disagreement_type`` with the
classifier's output class instead of the bare ``UNCLASSIFIED``. The
classifier is structurally-typed (any
:class:`cognition_wobble.classifier.CognitionClassifier`-conformant
object) so the verification gate (Step 9+) can inject the hybrid
GBM/judge classifier from cognition_wobble without coupling the
axis to a specific classifier impl.

For multi-response axes the classifier is called on the first two
responses; pairwise classification across N>2 responses is a
Step 5c+ enhancement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cognition_wobble.disagreement_types import CognitionDisagreementType
from phoenix.providers.cognition.types import Prompt
from phoenix.verification.axes._distance import mean_off_diagonal, pairwise_distance_matrix
from phoenix.verification.axes._result import (
    CognitionAxisProvenance,
    CognitionDisagreementMetric,
)

if TYPE_CHECKING:
    from cognition_wobble.classifier import CognitionClassifier
    from phoenix.providers.cognition.protocol import CognitionProvider


class CrossModelAxis:
    """Dispatches the same prompt to N providers and measures pairwise
    text distance across the responses.

    Use case: the same physics question asked to Claude + GPT + Gemini
    should produce semantically-equivalent answers; a large pairwise
    distance flags model-specific divergence.
    """

    name: str = "cognition_cross_model"

    def __init__(
        self,
        *,
        providers: list[CognitionProvider],
        classifier: CognitionClassifier | None = None,
    ) -> None:
        """Construct.

        Args:
            providers: At least 2 :class:`CognitionProvider` instances to
                dispatch in parallel. The axis raises at :meth:`run` if
                fewer than 2 are configured (the pairwise comparison is
                undefined).
            classifier: Optional cognition disagreement classifier. When
                provided, the axis calls
                :meth:`CognitionClassifier.classify` on the first two
                responses and populates
                ``CognitionDisagreementMetric.disagreement_type``
                accordingly. When ``None``, the axis emits raw
                ``UNCLASSIFIED`` (the Step 4 behavior).
        """
        self._providers: list[CognitionProvider] = list(providers)
        self._classifier = classifier

    def applies_to(self, prompt: Prompt) -> bool:
        """At least 2 providers must be configured for the axis to apply."""
        del prompt
        return len(self._providers) >= 2

    def run(
        self,
        prompt: Prompt,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> CognitionDisagreementMetric:
        """Dispatch the prompt to each configured provider and compute
        pairwise text distance.

        Returns a :class:`CognitionDisagreementMetric` with the full
        pairwise matrix, per-provider provenance, and the raw
        responses. If a classifier was configured, ``disagreement_type``
        is the classifier's output; otherwise it is ``UNCLASSIFIED``.
        """
        if len(self._providers) < 2:
            raise ValueError(
                f"{self.name}: requires at least 2 providers; got {len(self._providers)}."
            )

        responses = []
        provenance: list[CognitionAxisProvenance] = []
        for provider in self._providers:
            result = provider.complete(prompt, max_tokens=max_tokens, temperature=temperature)
            responses.append(result)
            provenance.append(
                CognitionAxisProvenance(
                    provider_id=provider.provider_id,
                    model=provider.model,
                    latency_ms=result.latency_ms,
                    usage=result.usage,
                    temperature=temperature,
                    provider_fingerprint=result.provider_fingerprint,
                )
            )

        texts = [r.text for r in responses]
        matrix = pairwise_distance_matrix(texts)
        aggregate = mean_off_diagonal(matrix)

        # Optional classifier integration (Step 5b).
        disagreement_type = CognitionDisagreementType.UNCLASSIFIED
        classifier_confidence: float | None = None
        classifier_version: str | None = None
        if self._classifier is not None:
            classification = self._classifier.classify(prompt, responses[:2])
            disagreement_type = classification.disagreement_type
            classifier_confidence = classification.confidence
            classifier_version = classification.classifier_version

        return CognitionDisagreementMetric(
            axis_name=self.name,
            distance=aggregate,
            disagreement_type=disagreement_type,
            pairwise_distance_matrix=matrix,
            provenance=provenance,
            responses=responses,
            classifier_confidence=classifier_confidence,
            classifier_version=classifier_version,
            metadata={
                "n_providers": len(self._providers),
                "distance_metric": "semantic_with_fallback",  # Step 5b upgrade
                "classifier_attached": self._classifier is not None,
            },
        )
