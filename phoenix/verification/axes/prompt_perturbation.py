"""``PromptPerturbationAxis`` — paraphrase-and-dispatch axis for cognition.

Per Phase 13 build guide Step 4: generates semantically-equivalent
rewrites of the prompt (initial implementation: paraphrase via a
lightweight LLM call to the same primary provider; v1.2 may use
embedding-space neighbor sampling), then dispatches each perturbation
to the primary provider and measures pairwise distance across the
responses.

Use case: a task that depends on a load-bearing factual claim should
be robust to surface-phrasing changes; large pairwise distance
indicates the provider's answer depends on prompt phrasing rather
than the underlying intent.

**Paraphrase strategy.** The axis accepts either:

- A ``perturbations`` list at :meth:`run` (caller pre-computed the
  paraphrases) — preferred when the caller already has them.
- A ``paraphraser`` callable at construction
  (``Callable[[Prompt, int], list[Prompt]]``) — for programmatic
  paraphrase pipelines.
- A default in-axis paraphraser that uses the primary provider with
  a meta-prompt. Convenient but adds one LLM call to the axis cost.

The axis emits ``disagreement_type =
PhoenixDisagreementType.COGNITION_UNCLASSIFIED`` per Step 4 spec.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Callable

from phoenix.providers.cognition.types import Prompt
from phoenix.verification.agreement_classifier import PhoenixDisagreementType
from phoenix.verification.axes._distance import mean_off_diagonal, pairwise_distance_matrix
from phoenix.verification.axes._result import (
    CognitionAxisProvenance,
    CognitionDisagreementMetric,
)

if TYPE_CHECKING:
    from phoenix.providers.cognition.protocol import CognitionProvider


class PromptPerturbationAxis:
    """Generates paraphrased prompts and dispatches each to the
    primary provider; measures pairwise distance across responses."""

    name: str = "cognition_prompt_perturbation"

    def __init__(
        self,
        *,
        provider: CognitionProvider,
        paraphraser: Callable[[Prompt, int], list[Prompt]] | None = None,
        n_perturbations: int = 3,
    ) -> None:
        """Construct.

        Args:
            provider: The primary :class:`CognitionProvider` that the
                axis dispatches perturbations to.
            paraphraser: Optional callable that produces
                ``n_perturbations`` paraphrased :class:`Prompt`
                instances. When ``None``, the axis uses an in-axis
                default paraphraser that calls the same primary
                provider with a meta-prompt.
            n_perturbations: Number of paraphrased prompts to
                generate. Default 3.
        """
        self._provider = provider
        self._paraphraser = paraphraser
        self._n_perturbations = n_perturbations

    def applies_to(self, prompt: Prompt) -> bool:
        """At least 1 perturbation must be requested."""
        del prompt
        return self._n_perturbations >= 1

    def run(
        self,
        prompt: Prompt,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        perturbations: list[Prompt] | None = None,
    ) -> CognitionDisagreementMetric:
        """Dispatch the paraphrased prompts to the primary provider.

        Args:
            prompt: The original prompt.
            max_tokens: Per-dispatch token cap.
            temperature: Per-dispatch temperature.
            perturbations: Optional pre-computed paraphrased prompts.
                When provided, the axis skips the paraphraser step and
                dispatches these directly.
        """
        if self._n_perturbations < 1:
            raise ValueError(
                f"{self.name}: n_perturbations must be >= 1; got {self._n_perturbations}."
            )

        if perturbations is None:
            paraphraser = self._paraphraser or self._default_paraphraser
            perturbations = paraphraser(prompt, self._n_perturbations)

        if not perturbations:
            raise ValueError(f"{self.name}: paraphraser returned no perturbations.")

        responses = []
        provenance: list[CognitionAxisProvenance] = []
        perturbation_texts: list[str] = []
        for pert in perturbations:
            result = self._provider.complete(pert, max_tokens=max_tokens, temperature=temperature)
            responses.append(result)
            provenance.append(
                CognitionAxisProvenance(
                    provider_id=self._provider.provider_id,
                    model=self._provider.model,
                    latency_ms=result.latency_ms,
                    usage=result.usage,
                    temperature=temperature,
                    provider_fingerprint=result.provider_fingerprint,
                )
            )
            # Capture the user-side text of the perturbation for ledger
            # provenance. Step 8's Omega Ledger respects the
            # prompt_disposition setting per 13-D2.
            perturbation_texts.append(_extract_user_text(pert))

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
                "n_perturbations": len(perturbations),
                "perturbation_texts": perturbation_texts,
                "distance_metric": "exact_string",
            },
        )

    def _default_paraphraser(self, prompt: Prompt, n: int) -> list[Prompt]:
        """Default in-axis paraphraser.

        Uses the primary provider with a meta-prompt to generate ``n``
        semantically-equivalent rewrites. Parses the response as a
        JSON list of strings (relaxed parser falls back to splitting
        on newlines if JSON fails).
        """
        user_text = _extract_user_text(prompt)
        meta_system = (
            f"Generate exactly {n} semantically-equivalent paraphrases of "
            f"the following user message. Output ONLY a JSON array of "
            f"strings — one paraphrase per element, no extra commentary. "
            f"Preserve meaning exactly; vary only surface phrasing."
        )
        meta_prompt = Prompt(
            system=meta_system,
            messages=[{"role": "user", "content": user_text}],
        )
        # Use moderate temperature so the paraphraser actually varies
        # phrasing instead of returning the input verbatim.
        result = self._provider.complete(meta_prompt, max_tokens=2048, temperature=0.7)

        parsed = _parse_paraphrase_list(result.text, n)
        out: list[Prompt] = []
        for text in parsed:
            stripped = text.strip()
            if not stripped:
                continue
            out.append(
                Prompt(
                    system=prompt.system,
                    messages=[{"role": "user", "content": stripped}],
                    metadata={**prompt.metadata, "is_perturbation": True},
                )
            )
        return out[:n]


def _extract_user_text(prompt: Prompt) -> str:
    """Pull the user-side text from a Phoenix Prompt.

    Concatenates the ``content`` of every ``role == "user"`` message.
    The default paraphraser uses this as the source text to rewrite.
    """
    parts: list[str] = []
    for msg in prompt.messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                parts.append(content)
    return "\n".join(parts)


def _parse_paraphrase_list(text: str, expected_n: int) -> list[str]:
    """Parse a paraphraser response into a list of strings.

    Tries JSON first; falls back to line-splitting if JSON fails.
    Tolerates the common JSON-in-markdown-fence wrapping
    (``"```json\n[...]\n```"``) by stripping fences.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Drop the first ```... line and the trailing ``` line.
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    # Try JSON.
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except json.JSONDecodeError:
        pass

    # Fallback: split on newlines, drop numbered-list / bullet prefixes.
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    stripped: list[str] = []
    for line in lines:
        # Drop leading "1.", "1)", "-", "*" markers.
        for prefix in ("- ", "* "):
            if line.startswith(prefix):
                line = line[len(prefix) :]
                break
        else:
            # Numbered prefix? e.g. "1. ", "12. "
            head, sep, tail = line.partition(". ")
            if sep and head.isdigit():
                line = tail
        stripped.append(line)
    return stripped[:expected_n] if stripped else [cleaned]
