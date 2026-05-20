"""``SelfConsistencyAxis`` — self-consistency axis for cognition tasks.

Per Phase 13 build guide Step 4: dispatches the same prompt to the
same provider multiple times with varied temperatures, then returns
the pairwise distance matrix. Default ``n=3``, temperatures
``[0.0, 0.5, 0.7]``.

Use case: a task with a single load-bearing factual claim should
yield the same answer across temperatures; large pairwise distance
indicates the provider hedges or hallucinates.

**Step 5b update:** optional ``classifier`` kwarg threads through
the same way as :class:`CrossModelAxis`. The classifier classifies
the first two responses (the lowest-temperature pair).
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


_DEFAULT_TEMPERATURES: tuple[float, ...] = (0.0, 0.5, 0.7)


class SelfConsistencyAxis:
    """Dispatches the same prompt to one provider N times with varied
    temperatures; measures pairwise distance across the N responses."""

    name: str = "cognition_self_consistency"

    def __init__(
        self,
        *,
        provider: CognitionProvider,
        temperatures: list[float] | tuple[float, ...] | None = None,
        classifier: CognitionClassifier | None = None,
    ) -> None:
        """Construct.

        Args:
            provider: The :class:`CognitionProvider` to query multiple
                times.
            temperatures: One float per dispatch. Defaults to
                ``[0.0, 0.5, 0.7]`` per Step 4 spec. The list length
                determines ``n``; a length of 1 is allowed (trivial
                self-consistency check, distance always 0.0).
            classifier: Optional cognition disagreement classifier
                (Step 5b). See :class:`CrossModelAxis` for the same
                semantics.
        """
        self._provider = provider
        self._temperatures: tuple[float, ...] = (
            tuple(temperatures) if temperatures is not None else _DEFAULT_TEMPERATURES
        )
        self._classifier = classifier

    def applies_to(self, prompt: Prompt) -> bool:
        """The axis always applies for a non-empty temperature list."""
        del prompt
        return len(self._temperatures) >= 1

    def run(
        self,
        prompt: Prompt,
        *,
        max_tokens: int = 1024,
    ) -> CognitionDisagreementMetric:
        """Dispatch the prompt at each configured temperature."""
        if not self._temperatures:
            raise ValueError(f"{self.name}: requires at least 1 temperature.")

        responses = []
        provenance: list[CognitionAxisProvenance] = []
        for temp in self._temperatures:
            result = self._provider.complete(prompt, max_tokens=max_tokens, temperature=temp)
            responses.append(result)
            provenance.append(
                CognitionAxisProvenance(
                    provider_id=self._provider.provider_id,
                    model=self._provider.model,
                    latency_ms=result.latency_ms,
                    usage=result.usage,
                    temperature=temp,
                    provider_fingerprint=result.provider_fingerprint,
                )
            )

        texts = [r.text for r in responses]
        matrix = pairwise_distance_matrix(texts)
        aggregate = mean_off_diagonal(matrix)

        # Optional classifier integration (Step 5b). Need >=2 responses.
        disagreement_type = CognitionDisagreementType.UNCLASSIFIED
        classifier_confidence: float | None = None
        classifier_version: str | None = None
        if self._classifier is not None and len(responses) >= 2:
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
                "n_samples": len(self._temperatures),
                "temperatures": list(self._temperatures),
                "distance_metric": "semantic_with_fallback",
                "classifier_attached": self._classifier is not None,
            },
        )
