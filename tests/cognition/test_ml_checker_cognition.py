"""Tests for MLStatisticalChecker's cognition-baseline integration (Phase 13.5).

Per the existing :class:`CheckerResult` shape (Phase 6b),
``fired`` maps to ``drifting`` and ``reason`` maps to ``summary``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from phoenix.verification.cognition_drift_baseline import CognitionDriftBaseline
from phoenix.verification.cognition_drift_features import CognitionDriftFeatures
from phoenix.verification.drift_detector import MLStatisticalChecker


def _make_features(*, bit_exact_rate: float = 0.5) -> CognitionDriftFeatures:
    return CognitionDriftFeatures(
        classifier_verdict_bit_exact_rate=bit_exact_rate,
        classifier_verdict_semantic_match_rate=0.2,
        classifier_verdict_divergence_rate=0.2,
        classifier_verdict_unclassified_rate=0.1,
        classifier_confidence_mean=0.85,
        classifier_confidence_p10=0.6,
        cognition_disagreement_mean=0.1,
        cognition_disagreement_p90=0.3,
        provider_error_rate_overall=0.01,
        provider_refusal_rate_overall=0.02,
        cognition_latency_ms_p95=400.0,
        disposition_hash_only_rate=0.7,
        disposition_verbatim_rate=0.2,
        disposition_encrypted_opt_in_rate=0.1,
        sample_size=100,
    )


class TestMLCheckerWithBaseline:
    def test_no_provider_does_not_fire(self) -> None:
        checker = MLStatisticalChecker(feature_provider=None)
        result = checker.run()
        assert result.drifting is False
        assert "no_provider" in result.summary

    def test_provider_returns_none_does_not_fire(self) -> None:
        def provider() -> np.ndarray | None:
            return None

        checker = MLStatisticalChecker(feature_provider=provider)
        result = checker.run()
        assert result.drifting is False
        assert (
            "insufficient_data" in result.summary
            or "no_provider" in result.summary
            or "empty_features" in result.summary
            or "no features" in result.summary.lower()
        )

    def test_no_baseline_does_not_fire(self, tmp_path: Path) -> None:
        baseline_storage = CognitionDriftBaseline(baseline_path=tmp_path / "b.json")

        def provider() -> np.ndarray | None:
            return _make_features().as_vector()

        checker = MLStatisticalChecker(
            feature_provider=provider,
            cognition_baseline=baseline_storage,
            phoenix_version="1.1.0.dev0",
        )
        result = checker.run()
        assert result.drifting is False
        assert "no_baseline" in result.summary

    def test_under_threshold_distance_does_not_fire(self, tmp_path: Path) -> None:
        """Current matches baseline -> distance ~ 0 -> does not fire."""
        baseline_storage = CognitionDriftBaseline(baseline_path=tmp_path / "b.json")
        baseline_storage.write_current(_make_features(), phoenix_version="1.1.0.dev0")

        def provider() -> np.ndarray | None:
            return _make_features().as_vector()

        checker = MLStatisticalChecker(
            feature_provider=provider,
            cognition_baseline=baseline_storage,
            phoenix_version="1.1.0.dev0",
            distance_threshold=0.5,
        )
        result = checker.run()
        assert result.drifting is False

    def test_over_threshold_distance_fires(self, tmp_path: Path) -> None:
        """Current diverges from baseline -> distance > threshold -> fires."""
        baseline_storage = CognitionDriftBaseline(baseline_path=tmp_path / "b.json")
        baseline_storage.write_current(
            _make_features(bit_exact_rate=0.5), phoenix_version="1.1.0.dev0"
        )

        def provider() -> np.ndarray | None:
            return _make_features(bit_exact_rate=0.05).as_vector()

        checker = MLStatisticalChecker(
            feature_provider=provider,
            cognition_baseline=baseline_storage,
            phoenix_version="1.1.0.dev0",
            distance_threshold=0.5,
        )
        result = checker.run()
        # distance ~ |0.5 - 0.05| / 0.10 = 4.5; threshold 0.5; clearly fires
        assert result.drifting is True
