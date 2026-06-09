"""Cognition drift baseline storage (Phase 13.5).

Persists a :class:`phoenix.verification.cognition_drift_features.CognitionDriftFeatures`
snapshot as the "known-healthy" baseline for a specific Phoenix version.
The :class:`MLStatisticalChecker` consumes it via
:meth:`CognitionDriftBaseline.compute_distance` to detect drift from
baseline.

**Lifecycle:**

- First daemon start: no baseline exists → ML checker reports
  ``no_baseline`` → ops must capture via the admin endpoint.
- On Phoenix version bump: the stored baseline's
  ``phoenix_version`` won't match the running version → returns
  ``None`` → ops must recapture.
- On feature schema bump (:data:`FEATURE_SCHEMA_VERSION` from
  :mod:`phoenix.verification.cognition_drift_features`):
  :class:`BaselineSchemaVersionMismatch` is raised; the daemon
  surfaces this as a noisy startup warning prompting recapture.

**File format:** single JSON file (default
``~/.phoenix/runtime/cognition_drift_baseline.json``) containing
schema_version + phoenix_version + captured_unix + features dict.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

from phoenix.verification.cognition_drift_features import (
    FEATURE_SCHEMA_VERSION,
    CognitionDriftFeatures,
    _VECTOR_FIELDS,
)


log = logging.getLogger(__name__)


_DEFAULT_RUNTIME_REL = Path(".phoenix") / "runtime"
_DEFAULT_FILENAME = "cognition_drift_baseline.json"


# ---------------------------------------------------------------------------
# Typed errors.


class BaselineLoadError(Exception):
    """Base for baseline-load failures."""


class BaselineSchemaVersionMismatch(BaselineLoadError):
    """The on-disk baseline was written with a different
    :data:`FEATURE_SCHEMA_VERSION` than the current code expects.

    Ops must recapture after a feature-schema bump.
    """

    def __init__(self, *, on_disk: int, expected: int) -> None:
        super().__init__(
            f"cognition_drift_baseline schema_version on disk = {on_disk}; "
            f"current expected = {expected}. Recapture via the admin endpoint."
        )
        self.on_disk = on_disk
        self.expected = expected


# ---------------------------------------------------------------------------
# Per-feature drift scales (heuristic; tuned for v1.1).

# Each feature's "1.0 unit of drift" represents a meaningful change.
# Distance is weighted-L2 with the per-feature inverse-of-scale as weight.
_FEATURE_DRIFT_SCALES: dict[str, float] = {
    "classifier_verdict_bit_exact_rate": 0.10,
    "classifier_verdict_semantic_match_rate": 0.10,
    "classifier_verdict_divergence_rate": 0.05,
    "classifier_verdict_unclassified_rate": 0.10,
    "classifier_confidence_mean": 0.10,
    "classifier_confidence_p10": 0.10,
    "cognition_disagreement_mean": 0.10,
    "cognition_disagreement_p90": 0.15,
    "provider_error_rate_overall": 0.05,
    "provider_refusal_rate_overall": 0.05,
    "cognition_latency_ms_p95": 200.0,  # ms
    "disposition_hash_only_rate": 0.20,
    "disposition_verbatim_rate": 0.20,
    "disposition_encrypted_opt_in_rate": 0.20,
}


# Module-level guard: scales dict must cover every vector field.
assert set(_FEATURE_DRIFT_SCALES.keys()) == set(_VECTOR_FIELDS), (
    "_FEATURE_DRIFT_SCALES keys must exactly match _VECTOR_FIELDS; "
    "did you add a feature without updating the drift-scale dict?"
)


def _build_weight_vector() -> np.ndarray:
    """Return per-feature weights in the same order as as_vector()."""
    return np.array(
        [1.0 / _FEATURE_DRIFT_SCALES[name] for name in _VECTOR_FIELDS],
        dtype=np.float64,
    )


# ---------------------------------------------------------------------------
# Baseline storage.


class CognitionDriftBaseline:
    """Per-Phoenix-version baseline storage with schema versioning."""

    def __init__(self, baseline_path: Path | None = None) -> None:
        if baseline_path is None:
            baseline_path = (Path.home() / _DEFAULT_RUNTIME_REL / _DEFAULT_FILENAME).resolve()
        self._baseline_path = baseline_path
        self._weights = _build_weight_vector()

    @property
    def baseline_path(self) -> Path:
        return self._baseline_path

    def write_current(
        self,
        features: CognitionDriftFeatures,
        *,
        phoenix_version: str,
    ) -> None:
        """Record this snapshot as the baseline for ``phoenix_version``.

        Overwrites any existing baseline at :attr:`baseline_path`.

        The write is atomic: the record is written to a uniquely-named
        temp file in the same directory and then :func:`os.replace`d onto
        :attr:`baseline_path`. This guarantees a concurrent reader (e.g.
        the ML checker reading the baseline mid-cycle) never observes a
        truncated or partially-written file, and two concurrent writers
        cannot corrupt each other (each has its own temp file; the final
        replace is last-writer-wins with a complete file either way).
        ``os.replace`` is atomic on the same filesystem on both POSIX and
        Windows.
        """
        parent = self._baseline_path.parent
        parent.mkdir(parents=True, exist_ok=True)
        record = {
            "schema_version": FEATURE_SCHEMA_VERSION,
            "phoenix_version": phoenix_version,
            "captured_unix": time.time(),
            "features": asdict(features),
        }
        payload = json.dumps(record, indent=2)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(parent), prefix=self._baseline_path.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp_name, self._baseline_path)
        except BaseException:
            # Best-effort cleanup if the replace never happened.
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def read_baseline_for_version(self, phoenix_version: str) -> CognitionDriftFeatures | None:
        """Read the baseline IFF the on-disk version matches the requested version.

        Returns None when:

        - no baseline file exists
        - on-disk ``phoenix_version`` differs from requested

        Raises:
            BaselineSchemaVersionMismatch: schema_version mismatch
                (ops must recapture).
        """
        if not self._baseline_path.is_file():
            return None

        try:
            raw = json.loads(self._baseline_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("baseline read failed: %s", exc)
            return None

        on_disk_schema = int(raw.get("schema_version", 0))
        if on_disk_schema != FEATURE_SCHEMA_VERSION:
            raise BaselineSchemaVersionMismatch(
                on_disk=on_disk_schema, expected=FEATURE_SCHEMA_VERSION
            )

        on_disk_version = str(raw.get("phoenix_version", ""))
        if on_disk_version != phoenix_version:
            return None

        features_dict = raw.get("features") or {}
        try:
            return CognitionDriftFeatures(**features_dict)
        except (TypeError, ValueError) as exc:
            log.warning("baseline features dict malformed: %s", exc)
            return None

    def compute_distance(
        self,
        current: CognitionDriftFeatures,
        baseline: CognitionDriftFeatures,
    ) -> float:
        """Weighted-L2 distance between two feature vectors.

        Per-feature weights come from :data:`_FEATURE_DRIFT_SCALES`
        (inverse of "meaningful drift unit"). Higher values indicate
        more drift.
        """
        v1 = current.as_vector()
        v2 = baseline.as_vector()
        diff = v1 - v2
        weighted = diff * self._weights
        return float(np.linalg.norm(weighted))


__all__ = [
    "BaselineLoadError",
    "BaselineSchemaVersionMismatch",
    "CognitionDriftBaseline",
]
