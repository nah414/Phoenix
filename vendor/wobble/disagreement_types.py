"""
Disagreement type definitions for the wobble layer rebuild.

These types are designed to be forward-compatible with a future
EpistemicState unification (see Section 0.5 of the build guide).
Do not collapse the structured uncertainty fields to scalars.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any, Dict, List
import numpy as np


class DisagreementType(Enum):
    """The four cases of consensus failure, plus UNKNOWN for unclassifiable."""

    CONTRADICTION = "contradiction"
    """Case A: factual disagreement, ground-truth-lookupable."""

    TEMPORAL_DRIFT = "temporal_drift"
    """Case B: moving target, the disagreement is the finding."""

    FRAME_MISMATCH = "frame_mismatch"
    """Case C: orthogonal frames, the question is plural."""

    HEDGED_CONSENSUS = "hedged_consensus"
    """All five agreed the answer is 'it depends'; surface the structure."""

    UNKNOWN = "unknown"
    """Classifier could not place this confidently. Surface the raw structure."""


class SuggestedAction(Enum):
    """The downstream action implied by each disagreement type."""

    ROUTE_TO_GROUND_TRUTH = "route_to_ground_truth"
    """Case A: hand off to web search, database, or solver."""

    SURFACE_TIMELINE = "surface_timeline"
    """Case B: present the disagreement with timestamps and source weights."""

    SURFACE_FRAMES = "surface_frames"
    """Case C: present the framings as the answer."""

    SURFACE_HEDGE_STRUCTURE = "surface_hedge_structure"
    """Case D (hedged): consolidate the 'depends on X, Y, Z' structure."""

    SEAL_AS_BLIND_SPOT = "seal_as_blind_spot"
    """Reserved for the Case D solver-override path (Phase 4)."""

    SURFACE_RAW = "surface_raw"
    """Default for UNKNOWN: present the raw matrix and let the human decide."""


@dataclass
class Frame:
    """
    The frame in which a set of co-author responses was produced.
    Forward-compatible with EpistemicState.frame.
    """
    system_prompt_version: str
    query_text: str
    query_timestamp: str  # ISO 8601
    co_author_models: Dict[str, str]  # name -> model version string
    temperature: Dict[str, float]  # name -> temperature
    additional_context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProvenanceEntry:
    """A single entry in a provenance chain."""
    co_author_name: str
    response_text: str
    latency_ms: float
    token_count: int
    model_version: str
    timestamp: str


@dataclass
class DisagreementFinding:
    """
    The replacement for the scalar wobble score.

    Carries the FULL structured uncertainty (the distance matrix), the
    typed classification, the suggested downstream action, AND the raw
    scalar score for backward compatibility during migration.

    Forward-compatible with EpistemicState -- see Section 0.5.
    """

    # The classification
    disagreement_type: DisagreementType
    classifier_confidence: float  # 0.0 to 1.0
    classifier_rationale: str
    classifier_evidence: List[str]  # specific phrases or differences that drove the call

    # The structured uncertainty (DO NOT COLLAPSE)
    distance_matrix: np.ndarray  # NxN pairwise semantic+lexical distances

    # The provenance
    frame: Frame
    provenance: List[ProvenanceEntry]

    # The downstream action
    suggested_action: SuggestedAction

    # Backward compatibility
    raw_wobble_score: float  # the old scalar, kept so existing consumers don't break

    # Open metadata bag for future fields
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize for the Omega Ledger and the API layer."""
        return {
            "disagreement_type": self.disagreement_type.value,
            "classifier_confidence": self.classifier_confidence,
            "classifier_rationale": self.classifier_rationale,
            "classifier_evidence": self.classifier_evidence,
            "distance_matrix": self.distance_matrix.tolist(),
            "frame": {
                "system_prompt_version": self.frame.system_prompt_version,
                "query_text": self.frame.query_text,
                "query_timestamp": self.frame.query_timestamp,
                "co_author_models": self.frame.co_author_models,
                "temperature": self.frame.temperature,
                "additional_context": self.frame.additional_context,
            },
            "provenance": [
                {
                    "co_author_name": p.co_author_name,
                    "response_text": p.response_text,
                    "latency_ms": p.latency_ms,
                    "token_count": p.token_count,
                    "model_version": p.model_version,
                    "timestamp": p.timestamp,
                }
                for p in self.provenance
            ],
            "suggested_action": self.suggested_action.value,
            "raw_wobble_score": self.raw_wobble_score,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DisagreementFinding":
        """Deserialize from dict (for API consumption and testing)."""
        return cls(
            disagreement_type=DisagreementType(data["disagreement_type"]),
            classifier_confidence=data["classifier_confidence"],
            classifier_rationale=data["classifier_rationale"],
            classifier_evidence=data["classifier_evidence"],
            distance_matrix=np.array(data["distance_matrix"]),
            frame=Frame(
                system_prompt_version=data["frame"]["system_prompt_version"],
                query_text=data["frame"]["query_text"],
                query_timestamp=data["frame"]["query_timestamp"],
                co_author_models=data["frame"]["co_author_models"],
                temperature=data["frame"]["temperature"],
                additional_context=data["frame"].get("additional_context", {}),
            ),
            provenance=[
                ProvenanceEntry(**p) for p in data["provenance"]
            ],
            suggested_action=SuggestedAction(data["suggested_action"]),
            raw_wobble_score=data["raw_wobble_score"],
            metadata=data.get("metadata", {}),
        )
