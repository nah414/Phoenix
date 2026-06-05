"""Tests for the Phase 13.5 auto-capture cycle helper."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock


from phoenix.verification.cognition_drift_baseline import CognitionDriftBaseline
from phoenix.verification.cognition_drift_features import CognitionDriftFeatures


def _features() -> CognitionDriftFeatures:
    return CognitionDriftFeatures(
        classifier_verdict_bit_exact_rate=0.5,
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


class TestAutoCapture:
    def test_helper_exists(self) -> None:
        from phoenix.verification.drift_detector import maybe_auto_capture_baseline

        assert maybe_auto_capture_baseline is not None

    def test_helper_captures_after_n_healthy_cycles(self, tmp_path: Path) -> None:
        from phoenix.verification.drift_detector import maybe_auto_capture_baseline

        baseline_storage = CognitionDriftBaseline(baseline_path=tmp_path / "b.json")
        provider = MagicMock(return_value=_features().as_vector())

        # 4 healthy cycles - should NOT capture yet (default threshold 5)
        for _ in range(4):
            maybe_auto_capture_baseline(
                consecutive_healthy=4,
                state="healthy",
                provider=provider,
                baseline=baseline_storage,
                phoenix_version="1.1.0.dev0",
                cycles_before_capture=5,
            )
        assert not (tmp_path / "b.json").is_file()

        # 5th healthy cycle - should capture
        maybe_auto_capture_baseline(
            consecutive_healthy=5,
            state="healthy",
            provider=provider,
            baseline=baseline_storage,
            phoenix_version="1.1.0.dev0",
            cycles_before_capture=5,
        )
        assert (tmp_path / "b.json").is_file()

    def test_unhealthy_state_does_not_capture(self, tmp_path: Path) -> None:
        from phoenix.verification.drift_detector import maybe_auto_capture_baseline

        baseline_storage = CognitionDriftBaseline(baseline_path=tmp_path / "b.json")
        provider = MagicMock(return_value=_features().as_vector())

        maybe_auto_capture_baseline(
            consecutive_healthy=10,
            state="warning",
            provider=provider,
            baseline=baseline_storage,
            phoenix_version="1.1.0.dev0",
            cycles_before_capture=5,
        )
        assert not (tmp_path / "b.json").is_file()
