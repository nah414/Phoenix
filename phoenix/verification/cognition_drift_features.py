"""Cognition drift feature provider (Phase 13.5).

Reads recent cognition ledger entries from the state backend, computes
a 14-dim feature vector capturing classifier verdict distribution,
confidence statistics, provider error rates, latency, and disposition
mix. The :class:`MLStatisticalChecker` in
:mod:`phoenix.verification.drift_detector` consumes this provider via
its existing ``feature_provider`` callback seam.

**Privacy:** the provider reads aggregate-only fields. It MUST NOT
access ``prompt_verbatim`` or ``prompt_encrypted`` payload fields;
the privacy contract is enforced by the explicit field whitelist in
:func:`_extract_aggregate_fields`.

**Schema versioning:** :data:`FEATURE_SCHEMA_VERSION` is recorded in
every baseline file; mismatch on load triggers no_baseline state
→ ops must recapture.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from phoenix.state.backend_protocol import StateBackend


log = logging.getLogger(__name__)


FEATURE_SCHEMA_VERSION = 1
"""Bump when feature fields are added, removed, or semantically changed.
Recorded in baseline files; mismatch on load triggers no_baseline state."""


# ---------------------------------------------------------------------------
# CognitionDriftFeatures dataclass.


@dataclass(frozen=True)
class CognitionDriftFeatures:
    """14 features capturing cognition substrate behavior over a window.

    Plus :attr:`sample_size`, the count of cognition entries in the
    window. ``sample_size`` is the "trustworthiness" sentinel; the
    ML checker reports ``insufficient_data`` when below the
    configured minimum.

    All rate-type fields are in ``[0.0, 1.0]``. Latency is in
    milliseconds. Mean / p10 / p90 statistics are computed over the
    window's entries.
    """

    # Verdict distribution (sums to 1.0).
    classifier_verdict_bit_exact_rate: float
    classifier_verdict_semantic_match_rate: float
    classifier_verdict_divergence_rate: float
    classifier_verdict_unclassified_rate: float

    # Classifier confidence.
    classifier_confidence_mean: float
    classifier_confidence_p10: float

    # Cognition wobble signal.
    cognition_disagreement_mean: float
    cognition_disagreement_p90: float

    # Provider behavior.
    provider_error_rate_overall: float
    provider_refusal_rate_overall: float
    cognition_latency_ms_p95: float

    # Disposition mix.
    disposition_hash_only_rate: float
    disposition_verbatim_rate: float
    disposition_encrypted_opt_in_rate: float

    # Sentinel (NOT included in as_vector).
    sample_size: int

    def as_vector(self) -> np.ndarray:
        """Return the 14-dim feature vector (excludes sample_size)."""
        values = [getattr(self, f.name) for f in fields(self) if f.name != "sample_size"]
        return np.array(values, dtype=np.float64)


# ---------------------------------------------------------------------------
# Privacy contract: explicit aggregate-field whitelist.
# Any addition here is a privacy review item.

_AGGREGATE_FIELDS_ALLOWED = frozenset(
    {
        "verdict",
        "classification",
        "cognition_disagreement_metric",
        "cognition_provenance",
        "prompt_disposition",
        "axis",
    }
)


def _extract_aggregate_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Return only the whitelisted aggregate fields from a payload.

    PRIVACY: this is the load-bearing privacy boundary. The provider's
    feature computation reads ONLY from the dict returned here.
    """
    return {k: v for k, v in payload.items() if k in _AGGREGATE_FIELDS_ALLOWED}


# ---------------------------------------------------------------------------
# Feature provider.


class CognitionFeatureProvider:
    """Computes :class:`CognitionDriftFeatures` from recent ledger data.

    Args:
        state_backend: Source of cognition ledger entries.
        window_seconds: Rolling window for feature computation
            (default 24h).
        min_sample_size: Below this, :meth:`__call__` returns None
            (insufficient data; ML checker reports
            ``insufficient_data``).
        max_entries_read: Cap on entries read per call to bound
            query cost. Default 10000.

    The provider is **stateless** between calls; it re-queries the
    backend each invocation. The caller (MLStatisticalChecker) owns
    invocation cadence.
    """

    def __init__(
        self,
        *,
        state_backend: "StateBackend",
        window_seconds: float = 86400.0,
        min_sample_size: int = 50,
        max_entries_read: int = 10_000,
    ) -> None:
        self._backend = state_backend
        self._window_seconds = window_seconds
        self._min_sample_size = min_sample_size
        self._max_entries_read = max_entries_read

    def __call__(self) -> np.ndarray | None:
        """Compute features; return None if sample size below minimum."""
        features = self.compute()
        if features is None:
            return None
        return features.as_vector()

    def compute(self) -> CognitionDriftFeatures | None:
        """Returns the full :class:`CognitionDriftFeatures` dataclass
        (None on insufficient data)."""
        since_unix = time.time() - self._window_seconds
        rows = self._backend.list_ledger_entries(
            since_unix=since_unix, limit=self._max_entries_read
        )

        cognition_payloads: list[dict[str, Any]] = []
        for row in rows:
            if row.get("entry_kind") != "cognition":
                continue
            try:
                full_payload = json.loads(row["payload_json"])
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            # PRIVACY: only the aggregate-whitelisted fields enter
            # downstream computation.
            cognition_payloads.append(_extract_aggregate_fields(full_payload))

        if len(cognition_payloads) < self._min_sample_size:
            return None

        return _compute_features(cognition_payloads)


# ---------------------------------------------------------------------------
# Pure feature computation.


def _compute_features(
    payloads: Iterable[dict[str, Any]],
) -> CognitionDriftFeatures:
    """Pure function: payloads → CognitionDriftFeatures."""
    payloads_list = list(payloads)
    n = len(payloads_list)

    verdict_counts = {
        "bit_exact": 0,
        "semantic_match": 0,
        "divergence": 0,
        "unclassified": 0,
    }
    confidences: list[float] = []
    disagreements: list[float] = []
    provider_errors = 0
    provider_refusals = 0
    latencies: list[float] = []
    disposition_counts = {
        "HASH_ONLY": 0,
        "VERBATIM": 0,
        "ENCRYPTED_OPT_IN": 0,
    }

    for p in payloads_list:
        verdict = str(p.get("verdict", "unclassified"))
        if verdict in verdict_counts:
            verdict_counts[verdict] += 1
        else:
            verdict_counts["unclassified"] += 1

        classification = p.get("classification") or {}
        conf = classification.get("confidence")
        if isinstance(conf, (int, float)):
            confidences.append(float(conf))

        disagreement = p.get("cognition_disagreement_metric") or {}
        d = disagreement.get("distance")
        if isinstance(d, (int, float)):
            disagreements.append(float(d))

        provenance = p.get("cognition_provenance") or {}
        if provenance.get("error"):
            provider_errors += 1
        if provenance.get("refused"):
            provider_refusals += 1
        lat = provenance.get("latency_ms")
        if isinstance(lat, (int, float)):
            latencies.append(float(lat))

        disposition = str(p.get("prompt_disposition", "HASH_ONLY"))
        if disposition in disposition_counts:
            disposition_counts[disposition] += 1

    return CognitionDriftFeatures(
        classifier_verdict_bit_exact_rate=verdict_counts["bit_exact"] / n,
        classifier_verdict_semantic_match_rate=verdict_counts["semantic_match"] / n,
        classifier_verdict_divergence_rate=verdict_counts["divergence"] / n,
        classifier_verdict_unclassified_rate=verdict_counts["unclassified"] / n,
        classifier_confidence_mean=float(np.mean(confidences)) if confidences else 0.0,
        classifier_confidence_p10=float(np.percentile(confidences, 10)) if confidences else 0.0,
        cognition_disagreement_mean=float(np.mean(disagreements)) if disagreements else 0.0,
        cognition_disagreement_p90=float(np.percentile(disagreements, 90))
        if disagreements
        else 0.0,
        provider_error_rate_overall=provider_errors / n,
        provider_refusal_rate_overall=provider_refusals / n,
        cognition_latency_ms_p95=float(np.percentile(latencies, 95)) if latencies else 0.0,
        disposition_hash_only_rate=disposition_counts["HASH_ONLY"] / n,
        disposition_verbatim_rate=disposition_counts["VERBATIM"] / n,
        disposition_encrypted_opt_in_rate=disposition_counts["ENCRYPTED_OPT_IN"] / n,
        sample_size=n,
    )


__all__ = [
    "FEATURE_SCHEMA_VERSION",
    "CognitionDriftFeatures",
    "CognitionFeatureProvider",
]
