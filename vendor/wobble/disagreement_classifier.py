"""
Classifies a DisagreementFinding into one of:
CONTRADICTION, TEMPORAL_DRIFT, FRAME_MISMATCH, HEDGED_CONSENSUS, UNKNOWN

Uses the local brain (Qwen3-4B on NPU) as the arbiter. The arbiter is
explicitly NOT one of the five co-authors being judged.

The classifier can also run with a mock client for testing.
"""

from __future__ import annotations
import json
import logging
from typing import Optional, Set

from wobble.disagreement_types import (
    DisagreementFinding,
    DisagreementType,
    SuggestedAction,
)

logger = logging.getLogger(__name__)


CLASSIFIER_PROMPT_TEMPLATE = """You are an arbiter examining responses from five independent AI co-authors to a query. Your job is to classify the STRUCTURE of their disagreement, not to decide who is right.

QUERY:
{query}

RESPONSES:
{numbered_responses}

PAIRWISE SEMANTIC DISTANCE MATRIX (0=identical, 1=maximally different):
{matrix}

Classify the disagreement into ONE of these categories:

A) CONTRADICTION -- The responses make incompatible factual claims about something with a definite answer that could be looked up. Examples: dates, numbers, names, technical specifications. The disagreement is about *what is true*.

B) TEMPORAL_DRIFT -- The responses agree on the structure of the answer but disagree on details that depend on when each model was trained or which sources it weighted. Examples: "current best practices for X," "recent developments in Y." The disagreement is about *when*.

C) FRAME_MISMATCH -- The responses are not contradicting each other; they are answering different implicit questions. Each response is internally consistent but operates from a different frame, definition, or set of assumptions. Examples: "is X ethical," "should we prioritize Y," "what is consciousness." The disagreement is about *what question is being asked*.

D) HEDGED_CONSENSUS -- All five responses agree the question is hard, dependent, or contextual. The surface text differs but the structure of "it depends" is shared. The disagreement is *cosmetic*; the substance is shared.

If you cannot confidently place the disagreement into one of A, B, C, or D, return UNKNOWN. Do not guess.

Return ONLY a JSON object, no preamble, no markdown:

{{"type": "CONTRADICTION" | "TEMPORAL_DRIFT" | "FRAME_MISMATCH" | "HEDGED_CONSENSUS" | "UNKNOWN", "confidence": <float between 0.0 and 1.0>, "rationale": "<one to three sentences explaining the classification>", "evidence": ["<specific phrase or difference 1>", "<specific phrase or difference 2>"]}}
"""


CONFIDENCE_THRESHOLD = 0.65  # below this, fall back to UNKNOWN


class DisagreementClassifier:
    """Classifies disagreement findings using a local LLM arbiter.

    The arbiter must NOT be one of the five co-authors being judged.
    This protects against self-vote bias.
    """

    def __init__(self, llm_client, co_author_models: Optional[Set[str]] = None):
        """
        Args:
            llm_client: Any object with a .complete(prompt, max_tokens, temperature) method
                        that returns a string. For production: LocalBrainOrchestrator.
                        For testing: a mock that returns pre-built JSON.
            co_author_models: Set of model IDs used as co-authors. The classifier
                              asserts the arbiter is not among them.
        """
        self.llm = llm_client
        self._co_author_models = co_author_models or set()

        # Safety: assert arbiter is not a co-author
        arbiter_id = getattr(llm_client, 'model_id', None)
        if arbiter_id and arbiter_id in self._co_author_models:
            raise ValueError(
                "Classifier arbiter must not be one of the co-authors being judged. "
                "This protects against self-vote bias."
            )

    def classify(self, finding: DisagreementFinding) -> DisagreementFinding:
        """Classify a finding's disagreement type.

        Takes a DisagreementFinding with type=UNKNOWN, returns one with
        a real classification (or UNKNOWN if the classifier is uncertain).
        """
        if finding.disagreement_type != DisagreementType.UNKNOWN:
            logger.warning(
                "classify() called on finding that already has type: %s",
                finding.disagreement_type,
            )
            return finding

        prompt = self._build_prompt(finding)

        try:
            raw_output = self.llm.complete(prompt, max_tokens=400, temperature=0.1)
            parsed = self._parse_output(raw_output)
        except Exception as e:
            logger.exception("Classifier call failed: %s", e)
            return self._mark_unknown(finding, reason="classifier_call_failed")

        if parsed is None:
            return self._mark_unknown(finding, reason="classifier_output_unparseable")

        if parsed["confidence"] < CONFIDENCE_THRESHOLD:
            return self._mark_unknown(
                finding,
                reason=f"confidence_below_threshold ({parsed['confidence']:.2f})",
            )

        return self._apply_classification(finding, parsed)

    def _build_prompt(self, finding: DisagreementFinding) -> str:
        numbered = "\n".join(
            f"[{i+1}] ({p.co_author_name}): {p.response_text}"
            for i, p in enumerate(finding.provenance)
        )
        matrix_str = self._format_matrix(finding.distance_matrix)
        return CLASSIFIER_PROMPT_TEMPLATE.format(
            query=finding.frame.query_text,
            numbered_responses=numbered,
            matrix=matrix_str,
        )

    def _format_matrix(self, matrix) -> str:
        rows = []
        for row in matrix:
            rows.append("  ".join(f"{v:.2f}" for v in row))
        return "\n".join(rows)

    def _parse_output(self, raw: str) -> Optional[dict]:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not all(k in data for k in ("type", "confidence", "rationale", "evidence")):
            return None
        valid_types = {
            "CONTRADICTION", "TEMPORAL_DRIFT", "FRAME_MISMATCH",
            "HEDGED_CONSENSUS", "UNKNOWN",
        }
        if data["type"] not in valid_types:
            return None
        return data

    def _apply_classification(
        self, finding: DisagreementFinding, parsed: dict
    ) -> DisagreementFinding:
        type_map = {
            "CONTRADICTION": (DisagreementType.CONTRADICTION, SuggestedAction.ROUTE_TO_GROUND_TRUTH),
            "TEMPORAL_DRIFT": (DisagreementType.TEMPORAL_DRIFT, SuggestedAction.SURFACE_TIMELINE),
            "FRAME_MISMATCH": (DisagreementType.FRAME_MISMATCH, SuggestedAction.SURFACE_FRAMES),
            "HEDGED_CONSENSUS": (DisagreementType.HEDGED_CONSENSUS, SuggestedAction.SURFACE_HEDGE_STRUCTURE),
            "UNKNOWN": (DisagreementType.UNKNOWN, SuggestedAction.SURFACE_RAW),
        }
        d_type, action = type_map[parsed["type"]]
        finding.disagreement_type = d_type
        finding.classifier_confidence = parsed["confidence"]
        finding.classifier_rationale = parsed["rationale"]
        finding.classifier_evidence = parsed["evidence"]
        finding.suggested_action = action
        return finding

    def _mark_unknown(
        self, finding: DisagreementFinding, reason: str
    ) -> DisagreementFinding:
        finding.disagreement_type = DisagreementType.UNKNOWN
        finding.classifier_confidence = 0.0
        finding.classifier_rationale = f"Could not classify: {reason}"
        finding.classifier_evidence = []
        finding.suggested_action = SuggestedAction.SURFACE_RAW
        return finding
