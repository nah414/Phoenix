"""Tests for ``phoenix.ledger.cognition_classifier`` registry (Phase 13.x.4).

Coverage:

- Ship default is :class:`AlwaysUnclassifiedClassifier`.
- :func:`set_cognition_classifier` updates the global.
- :func:`reset_cognition_classifier` restores the ship default.
- The autouse fixture isolates tests from each other.
"""

from __future__ import annotations

import pytest

from cognition_wobble.disagreement_types import CognitionDisagreementType
from phoenix.ledger.cognition_classifier import (
    AlwaysUnclassifiedClassifier,
    ClassificationResult,
    get_cognition_classifier,
    reset_cognition_classifier,
    set_cognition_classifier,
)


class _StubClassifier:
    """A test stub that returns a fixed version + FACTUAL_AGREEMENT."""

    version: str = "stub-test-v1"

    def classify(self, prompt, responses, *, confidence_threshold=0.5):
        del prompt, responses, confidence_threshold
        return ClassificationResult(
            disagreement_type=CognitionDisagreementType.FACTUAL_AGREEMENT,
            confidence=1.0,
            classifier_version=self.version,
            feature_values={},
            escalated_to_judge=False,
        )


@pytest.fixture(autouse=True)
def _reset_classifier():
    """Every test starts and ends with the ship default."""
    reset_cognition_classifier()
    yield
    reset_cognition_classifier()


class TestCognitionClassifierRegistry:
    def test_get_returns_ship_default_AlwaysUnclassifiedClassifier(self) -> None:
        c = get_cognition_classifier()
        assert isinstance(c, AlwaysUnclassifiedClassifier)
        assert c.version == "stub-always-unclassified-v1"

    def test_set_then_get_returns_custom(self) -> None:
        stub = _StubClassifier()
        set_cognition_classifier(stub)
        retrieved = get_cognition_classifier()
        assert retrieved is stub
        assert retrieved.version == "stub-test-v1"

    def test_reset_restores_ship_default(self) -> None:
        set_cognition_classifier(_StubClassifier())
        reset_cognition_classifier()
        assert isinstance(get_cognition_classifier(), AlwaysUnclassifiedClassifier)

    def test_isolation_between_tests(self) -> None:
        # If isolation works, the ship default is in place at test start.
        assert isinstance(get_cognition_classifier(), AlwaysUnclassifiedClassifier)
