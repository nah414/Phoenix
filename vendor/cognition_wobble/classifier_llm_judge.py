"""``LLMJudgeClassifier`` — escalation classifier using an LLM-as-judge.

Per Phase 13 build guide Step 5 + ``[OPEN: P13-4]`` hybrid default:
when the GBM primary returns UNCLASSIFIED (confidence below
threshold), the hybrid orchestrator escalates to this LLM-as-judge
classifier. The judge calls a configured :class:`CognitionProvider`
with a meta-prompt asking it to classify the disagreement directly.

**Cost:** one extra LLM call per UNCLASSIFIED case. The hybrid
orchestrator decides whether to escalate; this classifier doesn't.

**SAFETY:** the meta-prompt is constructed from the user prompt +
two responses. Per 13-D2, the judge call inherits the same
``prompt_disposition`` semantics as the original prompt — the
verification gate is responsible for honoring that at the dispatch
site.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from cognition_wobble.classifier import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    ClassificationResult,
)
from cognition_wobble.disagreement_types import CognitionDisagreementType

if TYPE_CHECKING:
    from phoenix.providers.cognition.protocol import CognitionProvider
    from phoenix.providers.cognition.types import CognitionResult, Prompt


_JUDGE_SYSTEM_PROMPT: str = """You are a classifier for disagreements between LLM responses.

Given a user prompt and two model responses, classify the disagreement using EXACTLY ONE of these classes:

- FACTUAL_AGREEMENT: Both responses agree on the load-bearing claims. Surface phrasing may vary but the facts asserted are equivalent.
- STYLISTIC_DIVERGENCE: Both responses agree on facts but differ in presentation, formality, or detail level.
- FACTUAL_DISAGREEMENT: The responses disagree on a verifiable claim (a date, a value, a boolean fact).
- INTERPRETIVE_DIVERGENCE: The responses read the prompt differently — one answers question A, another answers question B.
- REFUSAL_DIVERGENCE: One model answered, the other refused on safety / policy / capability grounds.
- TOOL_CHOICE_DIVERGENCE: The responses chose different tools, or one chose to call no tool when the other did.
- UNCLASSIFIED: Signal is too ambiguous to classify confidently.

Output ONLY a JSON object with this exact shape (no commentary, no markdown fence):
{"class": "<CLASS_NAME>", "confidence": <float in [0, 1]>, "reasoning": "<one short sentence>"}
"""


# Map from class name (string the LLM emits) back to the enum value.
_NAME_TO_CLASS: dict[str, CognitionDisagreementType] = {
    cls.name: cls for cls in CognitionDisagreementType
}


class LLMJudgeClassifier:
    """LLM-as-judge cognition classifier.

    Calls a configured :class:`CognitionProvider` with a meta-prompt
    asking it to classify the disagreement. Used by the hybrid
    orchestrator as the escalation path when the GBM primary
    abstains.
    """

    version: str = "llm-judge-v1-step5b"

    def __init__(
        self,
        *,
        provider: CognitionProvider,
        max_tokens: int = 512,
        temperature: float = 0.0,
    ) -> None:
        """Construct.

        Args:
            provider: The :class:`CognitionProvider` the judge will
                dispatch the meta-prompt to. Typically a cheap,
                fast model (Haiku, Mini, Flash) since the judge
                doesn't need the strongest model — it just needs to
                follow instructions reliably.
            max_tokens: Cap on the judge's output. The expected
                response is a short JSON object; 512 is generous.
            temperature: Judge temperature. Default ``0.0`` for
                deterministic-as-possible classification.
        """
        self._provider = provider
        self._max_tokens = max_tokens
        self._temperature = temperature

    def classify(
        self,
        prompt: Prompt,
        responses: list[CognitionResult],
        *,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> ClassificationResult:
        """Classify via one LLM call to the judge provider.

        Returns UNCLASSIFIED if the judge's output can't be parsed
        OR if the judge's reported confidence is below threshold.
        """
        if len(responses) < 2:
            raise ValueError(
                f"LLMJudgeClassifier requires at least 2 responses; "
                f"got {len(responses)}."
            )

        meta_prompt = self._build_meta_prompt(prompt, responses[0], responses[1])
        judge_response = self._provider.complete(
            meta_prompt,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )

        parsed = _parse_judge_output(judge_response.text)
        if parsed is None:
            return ClassificationResult(
                disagreement_type=CognitionDisagreementType.UNCLASSIFIED,
                confidence=0.0,
                classifier_version=self.version,
                feature_values={"judge_parse_failed": 1.0},
                escalated_to_judge=False,  # the judge IS this; not escalated FROM here
            )

        cls_name, confidence, _reasoning = parsed
        cls = _NAME_TO_CLASS.get(cls_name, CognitionDisagreementType.UNCLASSIFIED)

        if confidence < confidence_threshold:
            cls = CognitionDisagreementType.UNCLASSIFIED

        return ClassificationResult(
            disagreement_type=cls,
            confidence=confidence,
            classifier_version=self.version,
            feature_values={},
            escalated_to_judge=False,
        )

    def _build_meta_prompt(
        self,
        prompt: Prompt,
        response_a: CognitionResult,
        response_b: CognitionResult,
    ) -> Prompt:
        """Construct the meta-prompt the judge classifies on."""
        # Use a lightweight in-message rendering of the source prompt;
        # don't pass through tool definitions or system prompt from
        # the original (the judge classifies the responses, not the
        # full original conversation context).
        from phoenix.providers.cognition.types import Prompt as _Prompt

        user_messages = "\n".join(
            str(msg.get("content", ""))
            for msg in prompt.messages
            if msg.get("role") == "user"
        )
        body = (
            f"User prompt:\n{user_messages}\n\n"
            f"Response A:\n{response_a.text}\n\n"
            f"Response B:\n{response_b.text}"
        )
        return _Prompt(
            system=_JUDGE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": body}],
        )


def _parse_judge_output(text: str) -> tuple[str, float, str] | None:
    """Parse the judge's JSON output.

    Returns ``(class_name, confidence, reasoning)`` or ``None`` on
    parse failure. Tolerates JSON-in-markdown-fence wrapping.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, dict):
        return None

    cls_name = parsed.get("class")
    confidence = parsed.get("confidence")
    reasoning = parsed.get("reasoning", "")

    if not isinstance(cls_name, str):
        return None
    if confidence is None or not isinstance(confidence, (int, float, str)):
        return None
    try:
        conf_value = float(confidence)
    except (TypeError, ValueError):
        return None

    return cls_name, max(0.0, min(1.0, conf_value)), str(reasoning)
