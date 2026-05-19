"""``SelfConsistencyAxis`` — self-consistency axis for cognition tasks.

Per Phase 13 build guide Step 4: dispatches the same prompt to the
same provider multiple times with varied temperatures, then returns
the pairwise distance matrix. Default ``n=3``, temperatures
``[0.0, 0.5, 0.7]``.

Use case: a task with a single load-bearing factual claim should
yield the same answer across temperatures; large pairwise distance
indicates the provider hedges or hallucinates.

The axis emits ``disagreement_type =
PhoenixDisagreementType.COGNITION_UNCLASSIFIED`` per Step 4 spec.
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
    ) -> None:
        """Construct.

        Args:
            provider: The :class:`CognitionProvider` to query multiple
                times.
            temperatures: One float per dispatch. Defaults to
                ``[0.0, 0.5, 0.7]`` per Step 4 spec. The list length
                determines ``n``; a length of 1 is allowed (trivial
                self-consistency check, distance always 0.0).
        """
        self._provider = provider
        self._temperatures: tuple[float, ...] = (
            tuple(temperatures) if temperatures is not None else _DEFAULT_TEMPERATURES
        )

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
        """Dispatch the prompt at each configured temperature.

        Returns a :class:`CognitionDisagreementMetric` with the full
        pairwise matrix across responses.
        """
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

        return CognitionDisagreementMetric(
            axis_name=self.name,
            distance=aggregate,
            disagreement_type=PhoenixDisagreementType.COGNITION_UNCLASSIFIED,
            pairwise_distance_matrix=matrix,
            provenance=provenance,
            responses=responses,
            metadata={
                "n_samples": len(self._temperatures),
                "temperatures": list(self._temperatures),
                "distance_metric": "exact_string",
            },
        )
