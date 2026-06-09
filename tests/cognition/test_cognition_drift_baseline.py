"""Tests for ``phoenix.verification.cognition_drift_baseline`` (Phase 13.5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from phoenix.verification.cognition_drift_baseline import (
    BaselineSchemaVersionMismatch,
    CognitionDriftBaseline,
)
from phoenix.verification.cognition_drift_features import (
    CognitionDriftFeatures,
)


def _make_features(*, sample_size: int = 100) -> CognitionDriftFeatures:
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
        sample_size=sample_size,
    )


class TestBaselineWriteRead:
    def test_write_and_read_roundtrip(self, tmp_path: Path) -> None:
        baseline = CognitionDriftBaseline(baseline_path=tmp_path / "baseline.json")
        features = _make_features()
        baseline.write_current(features, phoenix_version="1.1.0.dev0")

        loaded = baseline.read_baseline_for_version("1.1.0.dev0")
        assert loaded is not None
        assert (
            loaded.classifier_verdict_bit_exact_rate == features.classifier_verdict_bit_exact_rate
        )
        assert loaded.sample_size == features.sample_size

    def test_returns_none_when_no_baseline(self, tmp_path: Path) -> None:
        baseline = CognitionDriftBaseline(baseline_path=tmp_path / "baseline.json")
        loaded = baseline.read_baseline_for_version("1.1.0.dev0")
        assert loaded is None

    def test_write_is_atomic_leaves_no_temp_file(self, tmp_path: Path) -> None:
        """write_current writes via a temp file + os.replace; after a write
        (and an overwrite) only the final baseline file remains, so a
        concurrent reader can never observe a truncated/partial file."""
        baseline = CognitionDriftBaseline(baseline_path=tmp_path / "baseline.json")
        baseline.write_current(_make_features(sample_size=10), phoenix_version="1.1.0.dev0")
        # Overwrite to exercise the replace-over-existing path.
        baseline.write_current(_make_features(sample_size=20), phoenix_version="1.1.0.dev0")

        names = sorted(p.name for p in tmp_path.iterdir())
        assert names == ["baseline.json"]  # no *.tmp leftovers
        loaded = baseline.read_baseline_for_version("1.1.0.dev0")
        assert loaded is not None
        assert loaded.sample_size == 20

    def test_version_mismatch_returns_none(self, tmp_path: Path) -> None:
        """Baseline recorded for v1.1.0.dev0 → reading for v1.2.0 returns None."""
        baseline = CognitionDriftBaseline(baseline_path=tmp_path / "baseline.json")
        baseline.write_current(_make_features(), phoenix_version="1.1.0.dev0")
        loaded = baseline.read_baseline_for_version("1.2.0")
        assert loaded is None

    def test_schema_version_mismatch_raises(self, tmp_path: Path) -> None:
        """Baseline file written with schema_version=999 → BaselineSchemaVersionMismatch."""
        path = tmp_path / "baseline.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 999,
                    "phoenix_version": "1.1.0.dev0",
                    "captured_unix": 0.0,
                    "features": {},
                }
            )
        )
        baseline = CognitionDriftBaseline(baseline_path=path)
        with pytest.raises(BaselineSchemaVersionMismatch):
            baseline.read_baseline_for_version("1.1.0.dev0")


class TestDistanceComputation:
    def test_distance_zero_for_identical_features(self) -> None:
        baseline = CognitionDriftBaseline(baseline_path=None)
        f = _make_features()
        d = baseline.compute_distance(f, f)
        assert d == pytest.approx(0.0)

    def test_distance_nonzero_for_different_features(self) -> None:
        baseline = CognitionDriftBaseline(baseline_path=None)
        f1 = _make_features()
        # Build f2 by shifting one feature substantially.
        from dataclasses import replace

        f2 = replace(f1, classifier_verdict_bit_exact_rate=0.9)
        d = baseline.compute_distance(f1, f2)
        assert d > 0.1


class TestBaselinePath:
    def test_default_path_under_runtime(self) -> None:
        """If no path given, baseline uses the conventional runtime location."""
        baseline = CognitionDriftBaseline()
        assert baseline.baseline_path.name == "cognition_drift_baseline.json"
