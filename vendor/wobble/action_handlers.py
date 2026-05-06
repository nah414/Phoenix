"""
Disagreement Action Handlers (Phase 3D).

Each SuggestedAction value maps to a handler that produces the appropriate
downstream response. These handlers are called by the orchestration layer
after the classifier assigns a disagreement type.

The handlers transform a DisagreementFinding into a structured result
dict that the API layer can surface to the user.
"""

from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional

from wobble.disagreement_types import (
    DisagreementFinding,
    DisagreementType,
    SuggestedAction,
)

logger = logging.getLogger(__name__)


class ActionDispatcher:
    """Routes a classified DisagreementFinding to the correct handler.

    Each handler transforms the finding into a user-facing result dict
    with type-specific structure.
    """

    def __init__(self, llm_client=None):
        """
        Args:
            llm_client: Local brain for frame summarization and hedge extraction.
                        Must have .complete(prompt, max_tokens, temperature) method.
                        Can be None (handlers degrade gracefully without it).
        """
        self._llm = llm_client
        self._handlers = {
            SuggestedAction.ROUTE_TO_GROUND_TRUTH: self._handle_contradiction,
            SuggestedAction.SURFACE_TIMELINE: self._handle_temporal_drift,
            SuggestedAction.SURFACE_FRAMES: self._handle_frame_mismatch,
            SuggestedAction.SURFACE_HEDGE_STRUCTURE: self._handle_hedged_consensus,
            SuggestedAction.SURFACE_RAW: self._handle_unknown,
            SuggestedAction.SEAL_AS_BLIND_SPOT: self._handle_blind_spot,
        }

    def dispatch(self, finding: DisagreementFinding) -> Dict[str, Any]:
        """Route a finding to the appropriate handler.

        Returns:
            Dict with at minimum: action, disagreement_type, raw_wobble_score,
            plus type-specific fields.
        """
        handler = self._handlers.get(finding.suggested_action, self._handle_unknown)
        try:
            result = handler(finding)
        except Exception as e:
            logger.exception("Action handler failed for %s: %s",
                             finding.suggested_action, e)
            result = self._handle_unknown(finding)
            result["handler_error"] = str(e)

        # Always include base fields
        result["action"] = finding.suggested_action.value
        result["disagreement_type"] = finding.disagreement_type.value
        result["raw_wobble_score"] = finding.raw_wobble_score
        result["classifier_confidence"] = finding.classifier_confidence
        result["classifier_rationale"] = finding.classifier_rationale
        return result

    def _handle_contradiction(self, finding: DisagreementFinding) -> Dict[str, Any]:
        """Case A: CONTRADICTION -- route to ground truth.

        Surfaces the disagreement, marks low confidence, and flags for
        external verification (web search or solver). The solver path
        is wired in Phase 4.
        """
        responses = [
            {
                "co_author": p.co_author_name,
                "response": p.response_text,
                "model_version": p.model_version,
            }
            for p in finding.provenance
        ]

        # Check if this might be solver-eligible (Phase 4 bridge)
        solver_eligible = False
        try:
            from synthesis.equations.llm_context import extract_physics_context_from_text
            combined = " ".join(p.response_text for p in finding.provenance)
            physics_ctx = extract_physics_context_from_text(combined)
            solver_eligible = physics_ctx is not None
        except Exception:
            pass

        return {
            "presentation": "contradiction",
            "confidence_label": "low -- factual disagreement detected",
            "responses": responses,
            "evidence": finding.classifier_evidence,
            "solver_eligible": solver_eligible,
            "verification_needed": True,
            "distance_matrix": finding.distance_matrix.tolist(),
        }

    def _handle_temporal_drift(self, finding: DisagreementFinding) -> Dict[str, Any]:
        """Case B: TEMPORAL_DRIFT -- surface as timeline.

        The disagreement IS the finding. Present responses chronologically
        with each model's training context visible. Do not pick a winner.
        """
        # Annotate each response with model metadata
        timeline = []
        for p in finding.provenance:
            timeline.append({
                "co_author": p.co_author_name,
                "response": p.response_text,
                "model_version": p.model_version,
                "timestamp": p.timestamp,
                "latency_ms": p.latency_ms,
            })

        # Sort by model version string (best effort chronological)
        # In production, this would use actual training cutoff dates
        # from the adapter metadata

        return {
            "presentation": "timeline",
            "confidence_label": "the disagreement is the finding",
            "timeline": timeline,
            "evidence": finding.classifier_evidence,
            "note": "Responses disagree because they were trained at different times. "
                    "No single answer is 'correct' -- the timeline IS the answer.",
            "distance_matrix": finding.distance_matrix.tolist(),
        }

    def _handle_frame_mismatch(self, finding: DisagreementFinding) -> Dict[str, Any]:
        """Case C: FRAME_MISMATCH -- surface the frames as the answer.

        Each response operates from a different implicit frame. The honest
        output is the set of framings, not a forced consensus.
        """
        frames = []
        for p in finding.provenance:
            frame_summary = ""
            if self._llm is not None:
                try:
                    prompt = (
                        f"In one sentence, what implicit framing or perspective "
                        f"is this response operating from?\n\n"
                        f"Response: {p.response_text[:500]}"
                    )
                    frame_summary = self._llm.complete(
                        prompt, max_tokens=100, temperature=0.3
                    ).strip()
                except Exception as e:
                    logger.debug("Frame summarization failed for %s: %s",
                                 p.co_author_name, e)

            frames.append({
                "co_author": p.co_author_name,
                "response": p.response_text,
                "frame_summary": frame_summary,
                "model_version": p.model_version,
            })

        return {
            "presentation": "frames",
            "confidence_label": "the question is plural -- multiple valid frames",
            "frames": frames,
            "evidence": finding.classifier_evidence,
            "note": "These responses are not contradicting each other. They are "
                    "answering different implicit questions from different frameworks.",
            "distance_matrix": finding.distance_matrix.tolist(),
        }

    def _handle_hedged_consensus(self, finding: DisagreementFinding) -> Dict[str, Any]:
        """Case D (hedged): HEDGED_CONSENSUS -- surface the hedge structure.

        All co-authors agree the answer is "it depends." Extract the
        dependent variables and present the consolidated structure.
        """
        factors_by_author = {}
        all_factors = set()

        for p in finding.provenance:
            factors = []
            if self._llm is not None:
                try:
                    prompt = (
                        f"What does this response say the answer depends on? "
                        f"Return a JSON list of factors (strings only).\n\n"
                        f"Response: {p.response_text[:500]}"
                    )
                    raw = self._llm.complete(prompt, max_tokens=200, temperature=0.1)
                    import json
                    factors = json.loads(raw.strip())
                    if not isinstance(factors, list):
                        factors = []
                except Exception:
                    pass

            factors_by_author[p.co_author_name] = factors
            all_factors.update(factors)

        return {
            "presentation": "hedge_structure",
            "confidence_label": "consensus that the answer is conditional",
            "union_factors": sorted(all_factors),
            "factors_by_author": factors_by_author,
            "responses": [
                {"co_author": p.co_author_name, "response": p.response_text}
                for p in finding.provenance
            ],
            "evidence": finding.classifier_evidence,
            "note": "All co-authors agree the answer depends on context. "
                    "Here are the factors they identified.",
            "distance_matrix": finding.distance_matrix.tolist(),
        }

    def _handle_unknown(self, finding: DisagreementFinding) -> Dict[str, Any]:
        """UNKNOWN -- surface everything raw and let the human decide."""
        return {
            "presentation": "raw",
            "confidence_label": "classifier uncertain -- showing raw data",
            "responses": [
                {
                    "co_author": p.co_author_name,
                    "response": p.response_text,
                    "model_version": p.model_version,
                }
                for p in finding.provenance
            ],
            "classifier_rationale": finding.classifier_rationale,
            "distance_matrix": finding.distance_matrix.tolist(),
        }

    def _handle_blind_spot(self, finding: DisagreementFinding) -> Dict[str, Any]:
        """SEAL_AS_BLIND_SPOT -- reserved for Phase 4 solver override.

        When consensus is high but a solver disagrees, this handler seals
        the result with an honest caveat about the blind spot.
        """
        return {
            "presentation": "blind_spot_caveat",
            "confidence_label": "WARNING: potential shared blind spot",
            "responses": [
                {"co_author": p.co_author_name, "response": p.response_text}
                for p in finding.provenance
            ],
            "caveat": (
                "This question is in a domain where the consensus pipeline "
                "cannot detect shared blind spots. All co-authors may be "
                "confidently wrong in the same way. Treat with extra caution."
            ),
            "distance_matrix": finding.distance_matrix.tolist(),
        }
