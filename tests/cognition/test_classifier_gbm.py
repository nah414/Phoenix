"""Tests for the GBMClassifier (mocked lightgbm booster)."""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

from cognition_wobble.classifier_gbm import _GBM_CLASS_ORDER, GBMClassifier
from cognition_wobble.disagreement_types import CognitionDisagreementType
from phoenix.providers.cognition.errors import MissingOptionalDependency
from phoenix.providers.cognition.types import CognitionResult, Prompt, TokenUsage


def _result(text: str) -> CognitionResult:
    return CognitionResult(
        text=text,
        tool_calls=[],
        usage=TokenUsage(input_tokens=1, output_tokens=1),
        latency_ms=1.0,
        provider_fingerprint="test|fixture",
    )


def _prompt() -> Prompt:
    return Prompt(system=None, messages=[{"role": "user", "content": "q"}])


def _booster_returning(probs: list[float]) -> Any:
    """Return a mock booster whose predict() returns [probs]."""
    booster = MagicMock()
    booster.predict.return_value = [list(probs)]
    return booster


def test_argmax_class_returned_when_above_threshold() -> None:
    """High-confidence prediction → argmax class."""
    # Position 0 in _GBM_CLASS_ORDER is FACTUAL_AGREEMENT; give it 0.9.
    probs = [0.9, 0.02, 0.02, 0.02, 0.02, 0.02]
    booster = _booster_returning(probs)
    clf = GBMClassifier(booster=booster)
    result = clf.classify(_prompt(), [_result("a"), _result("b")], confidence_threshold=0.5)
    assert result.disagreement_type is _GBM_CLASS_ORDER[0]
    assert result.confidence == pytest.approx(0.9)
    assert result.escalated_to_judge is False


def test_unclassified_when_below_threshold() -> None:
    """All probabilities below threshold → UNCLASSIFIED."""
    probs = [0.2, 0.2, 0.15, 0.15, 0.15, 0.15]
    booster = _booster_returning(probs)
    clf = GBMClassifier(booster=booster)
    result = clf.classify(_prompt(), [_result("a"), _result("b")], confidence_threshold=0.5)
    assert result.disagreement_type is CognitionDisagreementType.UNCLASSIFIED


def test_classifier_version_stable() -> None:
    booster = _booster_returning([1.0, 0, 0, 0, 0, 0])
    clf = GBMClassifier(booster=booster)
    result = clf.classify(_prompt(), [_result("a"), _result("b")])
    assert result.classifier_version == clf.version
    assert "step5b-scaffold" in clf.version  # version-flag noted in docstring


def test_features_recorded_in_result() -> None:
    """The feature vector used at classification time is preserved in the result."""
    booster = _booster_returning([1.0, 0, 0, 0, 0, 0])
    clf = GBMClassifier(booster=booster)
    result = clf.classify(_prompt(), [_result("a"), _result("b")])
    # All six feature names should be present.
    assert "semantic_distance" in result.feature_values
    assert "length_ratio" in result.feature_values
    assert "refusal_pattern_match" in result.feature_values


def test_fewer_than_2_responses_raises() -> None:
    booster = _booster_returning([1.0, 0, 0, 0, 0, 0])
    clf = GBMClassifier(booster=booster)
    with pytest.raises(ValueError):
        clf.classify(_prompt(), [_result("only one")])


def test_missing_lightgbm_raises_missing_optional() -> None:
    """Constructing without lightgbm and without an injected booster raises."""
    # Force lightgbm absent in sys.modules.
    saved = sys.modules.pop("lightgbm", None)
    sys.modules["lightgbm"] = None  # type: ignore[assignment]
    try:
        with pytest.raises(MissingOptionalDependency):
            GBMClassifier()
    finally:
        if saved is not None:
            sys.modules["lightgbm"] = saved
        else:
            del sys.modules["lightgbm"]


def test_missing_model_file_raises_filenotfounderror(tmp_path) -> None:
    """If lightgbm IS available but the model file is missing → FileNotFoundError."""
    fake_lgb = types.ModuleType("lightgbm")
    fake_lgb.Booster = MagicMock()  # type: ignore[attr-defined]
    saved = sys.modules.get("lightgbm")
    sys.modules["lightgbm"] = fake_lgb
    try:
        with pytest.raises(FileNotFoundError):
            GBMClassifier(model_path=tmp_path / "does-not-exist.txt")
    finally:
        if saved is not None:
            sys.modules["lightgbm"] = saved
        else:
            del sys.modules["lightgbm"]
