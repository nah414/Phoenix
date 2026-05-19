"""``CrossModelAxis`` — cross-model agreement axis for cognition tasks.

Per Phase 13 build guide Step 4: dispatches the same prompt to two or
more registered cognition providers (typically the primary plus the
cheapest secondary from the same capability tier), then returns the
raw pairwise distance metric.

Step 4 ships the mechanism; the "primary + cheapest secondary"
selection logic concretizes in Step 5+ when the cognition routing
surface is in place. For Step 4, the caller passes the providers
explicitly.

The axis emits ``disagreement_type =
PhoenixDisagreementType.COGNITION_UNCLASSIFIED`` per Step 4 spec; the
Step 5 classifier replaces this with a real class (FACTUAL_AGREEMENT,
STYLISTIC_DIVERGENCE, etc.).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from phoenix.providers.cognition.types import Prompt
from phoenix.verification.agreement_classifier import PhoenixDisagreementType
from phoenix.verification.axes._distance import mean_off_diagonal, pairwise_distance_matrix
from phoenix.verification.axes._result import (
    CognitionAxisProvenance,
    CognitionDisagreementMetric,
)

if TYPE_CHECKING:
    from phoenix.providers.cognition.protocol import CognitionProvider


class CrossModelAxis:
    """Dispatches the same prompt to N providers and measures pairwise
    text distance across the responses.

    Use case: the same physics question asked to Claude + GPT + Gemini
    should produce semantically-equivalent answers; a large pairwise
    distance flags model-specific divergence.
    """

    name: str = "cognition_cross_model"

    def __init__(self, *, providers: list[CognitionProvider]) -> None:
        """Construct.

        Args:
            providers: At least 2 :class:`CognitionProvider` instances to
                dispatch in parallel. The axis raises at :meth:`run` if
                fewer than 2 are configured (the pairwise comparison is
                undefined).
        """
        self._providers: list[CognitionProvider] = list(providers)

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
        responses. ``disagreement_type`` is always
        :attr:`PhoenixDisagreementType.COGNITION_UNCLASSIFIED` at
        Step 4; Step 5's classifier replaces it.
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

        return CognitionDisagreementMetric(
            axis_name=self.name,
            distance=aggregate,
            disagreement_type=PhoenixDisagreementType.COGNITION_UNCLASSIFIED,
            pairwise_distance_matrix=matrix,
            provenance=provenance,
            responses=responses,
            metadata={
                "n_providers": len(self._providers),
                "distance_metric": "exact_string",  # Step 4 P13-3 default
            },
        )
