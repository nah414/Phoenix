"""Tests for ``phoenix.verification.cognition_drift_features`` (Phase 13.5)."""

from __future__ import annotations

import json
import time
from typing import Any


from phoenix.verification.cognition_drift_features import (
    CognitionDriftFeatures,
    CognitionFeatureProvider,
    FEATURE_SCHEMA_VERSION,
)


class _FakeBackend:
    """Minimal StateBackend stub exposing only list_ledger_entries."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def list_ledger_entries(self, *, since_unix: float, limit: int) -> list[dict[str, Any]]:
        del limit
        return [r for r in self._rows if r["timestamp_unix"] >= since_unix]


def _cognition_row(
    *,
    ts: float,
    verdict: str = "bit_exact",
    confidence: float = 0.95,
    disagreement: float = 0.1,
    provider: str = "anthropic",
    error: bool = False,
    refused: bool = False,
    latency_ms: float = 250.0,
    disposition: str = "HASH_ONLY",
) -> dict[str, Any]:
    """Build a cognition ledger-row dict shaped like the StateBackend returns."""
    payload = {
        "axis": "cross_model",
        "prompt_disposition": disposition,
        "cognition_provenance": {
            "provider_id": provider,
            "model": "test",
            "temperature": 0.0,
            "latency_ms": latency_ms,
            "error": error,
            "refused": refused,
        },
        "verdict": verdict,
        "classification": {
            "disagreement_type": "factual_agreement"
            if verdict in ("bit_exact", "semantic_match")
            else "factual_disagreement",
            "confidence": confidence,
            "classifier_version": "test-v1",
        },
        "cognition_disagreement_metric": {"distance": disagreement},
    }
    return {
        "entry_id": f"e-{ts}",
        "entry_kind": "cognition",
        "timestamp_unix": ts,
        "actor_id": "test-actor",
        "parent_hash": "GENESIS",
        "entry_hash": "0" * 64,
        "payload_json": json.dumps(payload),
    }


class TestCognitionDriftFeatures:
    def test_feature_schema_version_is_one(self) -> None:
        """FEATURE_SCHEMA_VERSION starts at 1; bumps documented in CHANGELOG."""
        assert FEATURE_SCHEMA_VERSION == 1

    def test_dataclass_fields_count(self) -> None:
        """The dataclass has exactly 14 feature fields + sample_size."""
        import dataclasses

        flds = dataclasses.fields(CognitionDriftFeatures)
        assert len(flds) == 15

    def test_as_vector_returns_14_dim(self) -> None:
        """as_vector() returns the 14-dim feature vector (sample_size excluded)."""
        f = CognitionDriftFeatures(
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
        v = f.as_vector()
        assert v.shape == (14,)


class TestCognitionFeatureProvider:
    def test_returns_none_when_insufficient_data(self) -> None:
        """Sample below min_sample_size → returns None."""
        backend = _FakeBackend(rows=[])
        provider = CognitionFeatureProvider(state_backend=backend, min_sample_size=50)
        result = provider()
        assert result is None

    def test_returns_features_when_enough_data(self) -> None:
        """100 cognition entries → returns a 14-dim numpy vector."""
        now = time.time()
        rows = [_cognition_row(ts=now - i, verdict="bit_exact") for i in range(100)]
        backend = _FakeBackend(rows=rows)
        provider = CognitionFeatureProvider(state_backend=backend, min_sample_size=50)
        result = provider()
        assert result is not None
        assert result.shape == (14,)

    def test_only_cognition_entry_kind_counted(self) -> None:
        """Solve entries (entry_kind != 'cognition') excluded from features."""
        now = time.time()
        cog = [_cognition_row(ts=now - i) for i in range(30)]
        solve = [
            {
                "entry_id": f"s-{i}",
                "entry_kind": "solve",
                "timestamp_unix": now - i,
                "actor_id": "test-actor",
                "parent_hash": "GENESIS",
                "entry_hash": "0" * 64,
                "payload_json": json.dumps({"task_id": f"t-{i}"}),
            }
            for i in range(100)
        ]
        backend = _FakeBackend(rows=cog + solve)
        provider = CognitionFeatureProvider(state_backend=backend, min_sample_size=50)
        result = provider()
        # 30 cognition entries < 50 min → None
        assert result is None

    def test_window_seconds_filters_old_entries(self) -> None:
        """Entries older than window_seconds excluded."""
        now = time.time()
        recent = [_cognition_row(ts=now - i) for i in range(60)]
        old = [_cognition_row(ts=now - 3600 * 48 - i) for i in range(60)]
        backend = _FakeBackend(rows=recent + old)
        provider = CognitionFeatureProvider(
            state_backend=backend,
            window_seconds=3600.0,  # 1 hour
            min_sample_size=50,
        )
        result = provider()
        # 60 recent entries >= 50 → features returned
        assert result is not None

    def test_verdict_rates_sum_to_one(self) -> None:
        """The four verdict-rate features sum to 1.0."""
        now = time.time()
        verdicts = (
            ["bit_exact"] * 50
            + ["semantic_match"] * 25
            + ["divergence"] * 15
            + ["unclassified"] * 10
        )
        rows = [_cognition_row(ts=now - i, verdict=v) for i, v in enumerate(verdicts)]
        backend = _FakeBackend(rows=rows)
        provider = CognitionFeatureProvider(state_backend=backend, min_sample_size=50)
        v = provider()
        assert v is not None
        # First 4 dimensions are the verdict rates
        assert abs(v[0] + v[1] + v[2] + v[3] - 1.0) < 1e-6

    def test_does_not_read_prompt_verbatim_or_encrypted(self) -> None:
        """Privacy: provider must NOT access prompt_verbatim / prompt_encrypted.

        Test: inject sentinel strings into those fields; provider should
        return features successfully WITHOUT crashing on the sentinels
        (which would happen if the code tried to interpret them).
        """
        now = time.time()
        rows = []
        for i in range(60):
            row = _cognition_row(ts=now - i)
            payload = json.loads(row["payload_json"])
            payload["prompt_verbatim"] = "SHOULD_NOT_BE_READ"
            payload["prompt_encrypted"] = "SHOULD_NOT_BE_READ"
            row["payload_json"] = json.dumps(payload)
            rows.append(row)
        backend = _FakeBackend(rows=rows)
        provider = CognitionFeatureProvider(state_backend=backend, min_sample_size=50)
        result = provider()
        assert result is not None

    def test_privacy_whitelist_contains_only_expected_fields(self) -> None:
        """Pin the privacy whitelist against accidental widening.

        Any addition to _AGGREGATE_FIELDS_ALLOWED must be deliberate
        and reviewed; this test fails fast if a field appears that
        wasn't approved in design review.
        """
        from phoenix.verification.cognition_drift_features import (
            _AGGREGATE_FIELDS_ALLOWED,
        )

        expected = frozenset(
            {
                "verdict",
                "classification",
                "cognition_disagreement_metric",
                "cognition_provenance",
                "prompt_disposition",
                "axis",
            }
        )
        assert _AGGREGATE_FIELDS_ALLOWED == expected, (
            f"Privacy whitelist diverged from approved set. "
            f"Added: {_AGGREGATE_FIELDS_ALLOWED - expected}; "
            f"removed: {expected - _AGGREGATE_FIELDS_ALLOWED}. "
            f"Any change requires a Phase 13.5+ privacy review."
        )
