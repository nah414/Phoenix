"""Tests for ``CognitionClassifierProvenance``."""

from __future__ import annotations

import dataclasses

import pytest

from cognition_wobble.disagreement_types import CognitionDisagreementType
from phoenix.verification.cognition_classifier_provenance import (
    CognitionClassifierProvenance,
)


def test_provenance_carries_required_fields() -> None:
    prov = CognitionClassifierProvenance(
        classifier_version="gbm-v0.1.0",
        axis_name="cognition_cross_model",
        input_response_fingerprints=["anthropic|sonnet|fp1", "openai|gpt-4o|fp2"],
        output_class=CognitionDisagreementType.FACTUAL_AGREEMENT,
        confidence=0.92,
        confidence_threshold=0.5,
    )
    assert prov.classifier_version == "gbm-v0.1.0"
    assert prov.axis_name == "cognition_cross_model"
    assert len(prov.input_response_fingerprints) == 2
    assert prov.output_class is CognitionDisagreementType.FACTUAL_AGREEMENT
    assert prov.confidence == 0.92
    assert prov.escalated_to_judge is False
    assert prov.feature_values_summary == {}


def test_provenance_with_escalation_flag() -> None:
    prov = CognitionClassifierProvenance(
        classifier_version="hybrid-v0.1.0",
        axis_name="cognition_self_consistency",
        input_response_fingerprints=["fp1", "fp2", "fp3"],
        output_class=CognitionDisagreementType.UNCLASSIFIED,
        confidence=0.4,
        confidence_threshold=0.5,
        escalated_to_judge=True,
        feature_values_summary={"semantic_distance": 0.62, "length_ratio": 1.4},
    )
    assert prov.escalated_to_judge is True
    assert prov.feature_values_summary["semantic_distance"] == 0.62


def test_provenance_is_frozen() -> None:
    prov = CognitionClassifierProvenance(
        classifier_version="v0",
        axis_name="cognition_cross_model",
        input_response_fingerprints=[],
        output_class=CognitionDisagreementType.UNCLASSIFIED,
        confidence=0.0,
        confidence_threshold=0.5,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        prov.confidence = 1.0  # type: ignore[misc]
