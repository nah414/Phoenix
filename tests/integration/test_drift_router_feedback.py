"""Phase 7 Step 9 -- drift → router intelligence feedback tests.

Coverage:

- :func:`_detector_to_provider_id` correctly strips the
  ``_drift`` suffix and rejects detectors that don't follow the
  convention.
- :func:`on_drift_snapshot` updates the per-provider multiplier
  table per :data:`_DRIFT_STATE_MULTIPLIER` -- healthy 1.0,
  warning 0.9, high_confidence_warning 0.7.
- A healthy snapshot RESETS all multipliers to default (1.0). The
  table is a current-state snapshot, not a sticky-penalty log.
- :func:`estimate_fidelity` actually scales by the multiplier when
  a provider is drifted -- the routing layer sees the haircut.
- :func:`register_for_drift_updates` actually wires the callback
  into the singleton :class:`DriftDetector`; a manual snapshot fired
  via the detector's API propagates to the router.
- Multiple providers in ``firing_detectors`` all get penalized in
  one snapshot.
- Detectors that don't follow the ``<provider>_drift`` naming
  convention are silently ignored (they aren't router-targeted).
"""

from __future__ import annotations

import pytest

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix.router.intelligence import (
    _DRIFT_STATE_MULTIPLIER,
    _detector_to_provider_id,
    drift_multiplier_for,
    estimate_fidelity,
    on_drift_snapshot,
    register_for_drift_updates,
    reset_drift_multipliers,
)
from phoenix.verification.drift_detector import DriftSnapshot, reset_detector


@pytest.fixture(autouse=True)
def _reset_intelligence_state() -> None:
    """Each test starts with a clean drift-multiplier table + fresh
    detector singleton."""
    reset_drift_multipliers()
    reset_detector()
    yield
    reset_drift_multipliers()
    reset_detector()


# ---------------------------------------------------------------------------
# Detector name parsing


class TestDetectorNameParsing:
    def test_provider_scoped_detector_strips_suffix(self) -> None:
        assert _detector_to_provider_id("ibm_quantum_drift") == "ibm_quantum"

    def test_underscore_separated_provider_id_preserved(self) -> None:
        # Some provider ids have underscores; only the trailing _drift
        # is stripped.
        assert _detector_to_provider_id("aws_braket_cobalt_drift") == "aws_braket_cobalt"

    def test_non_provider_detector_returns_none(self) -> None:
        assert _detector_to_provider_id("cross_provider_validator") is None
        assert _detector_to_provider_id("vendor_calibration_drift_anomaly") is None

    def test_just_drift_suffix_returns_none(self) -> None:
        """An empty stem before ``_drift`` is rejected (no provider)."""
        assert _detector_to_provider_id("_drift") is None


# ---------------------------------------------------------------------------
# on_drift_snapshot


class TestOnDriftSnapshot:
    def test_unknown_provider_defaults_to_1(self) -> None:
        assert drift_multiplier_for("ibm_quantum") == 1.0

    def test_warning_lowers_to_0_90(self) -> None:
        snap = DriftSnapshot(
            cycle_unix=1.0,
            state="warning",
            firing_detectors=["ibm_quantum_drift"],
            detector_summaries={"ibm_quantum_drift": "measured drift"},
        )
        on_drift_snapshot(snap)
        assert drift_multiplier_for("ibm_quantum") == 0.90

    def test_high_confidence_warning_lowers_to_0_70(self) -> None:
        snap = DriftSnapshot(
            cycle_unix=1.0,
            state="high_confidence_warning",
            firing_detectors=["ibm_quantum_drift"],
            detector_summaries={"ibm_quantum_drift": "measured drift"},
        )
        on_drift_snapshot(snap)
        assert drift_multiplier_for("ibm_quantum") == 0.70

    def test_healthy_resets_multipliers(self) -> None:
        # First fire a warning so the table has a non-default entry.
        on_drift_snapshot(
            DriftSnapshot(
                cycle_unix=1.0,
                state="warning",
                firing_detectors=["ibm_quantum_drift"],
                detector_summaries={},
            )
        )
        assert drift_multiplier_for("ibm_quantum") == 0.90

        # Now a healthy cycle clears it.
        on_drift_snapshot(
            DriftSnapshot(
                cycle_unix=2.0,
                state="healthy",
                firing_detectors=[],
                detector_summaries={},
            )
        )
        assert drift_multiplier_for("ibm_quantum") == 1.0

    def test_multiple_providers_in_one_snapshot(self) -> None:
        snap = DriftSnapshot(
            cycle_unix=1.0,
            state="warning",
            firing_detectors=[
                "ibm_quantum_drift",
                "aws_braket_drift",
                "cross_provider_validator",  # not router-targeted
            ],
            detector_summaries={},
        )
        on_drift_snapshot(snap)
        assert drift_multiplier_for("ibm_quantum") == 0.90
        assert drift_multiplier_for("aws_braket") == 0.90
        # Untouched provider still 1.0.
        assert drift_multiplier_for("ionq") == 1.0

    def test_non_provider_detectors_ignored(self) -> None:
        snap = DriftSnapshot(
            cycle_unix=1.0,
            state="warning",
            firing_detectors=["cross_provider_validator"],
            detector_summaries={},
        )
        on_drift_snapshot(snap)
        # No provider penalized.
        assert drift_multiplier_for("ibm_quantum") == 1.0

    def test_state_multiplier_values_match_doc(self) -> None:
        """Sanity-check the documented multiplier ladder is what ships."""
        assert _DRIFT_STATE_MULTIPLIER["healthy"] == 1.0
        assert _DRIFT_STATE_MULTIPLIER["warning"] == 0.90
        assert _DRIFT_STATE_MULTIPLIER["high_confidence_warning"] == 0.70

    def test_callback_does_not_raise_on_malformed_snapshot(self) -> None:
        """A snapshot with an unexpected state string falls back to 1.0
        instead of raising -- audit-emit-style fire-and-forget."""

        class _ShimSnapshot:
            cycle_unix = 1.0
            state = "totally_made_up_state"
            firing_detectors = ["ibm_quantum_drift"]
            detector_summaries: dict = {}
            metadata: dict = {}

        on_drift_snapshot(_ShimSnapshot())  # type: ignore[arg-type]
        # Unknown state -> default multiplier 1.0 -> no penalty.
        assert drift_multiplier_for("ibm_quantum") == 1.0


# ---------------------------------------------------------------------------
# estimate_fidelity scaling


class TestFidelityScaling:
    def test_estimate_fidelity_applies_multiplier(self) -> None:
        """Build a real ProviderEntry from the default registry; fire a
        warning snapshot; verify estimate_fidelity drops by 10%."""
        from phoenix.router.provider_registry import build_default_registry

        registry = build_default_registry()
        # Pick a provider that exists in the default registry. The
        # local simulator's fidelity is 1.0 (no hardware params); any
        # cloud provider sees a real haircut.
        entry = registry.get("ibm_quantum")
        assert entry is not None

        baseline = estimate_fidelity(entry)
        assert baseline > 0
        assert baseline <= 1.0

        on_drift_snapshot(
            DriftSnapshot(
                cycle_unix=1.0,
                state="warning",
                firing_detectors=["ibm_quantum_drift"],
                detector_summaries={},
            )
        )
        drifted = estimate_fidelity(entry)
        assert drifted == pytest.approx(baseline * 0.90, rel=1e-9)

    def test_local_simulator_fidelity_can_be_drifted(self) -> None:
        """Even providers without HardwareParams (local sim) honor the
        drift multiplier -- the multiplier is the second factor."""
        from phoenix.router.provider_registry import build_default_registry

        registry = build_default_registry()
        local = registry.get("phoenix.local_simulator")
        if local is None:
            pytest.skip("local simulator not in default registry")

        # Baseline 1.0
        assert estimate_fidelity(local) == 1.0

        # Forcibly mark the local simulator as drifted (synthetic test).
        on_drift_snapshot(
            DriftSnapshot(
                cycle_unix=1.0,
                state="high_confidence_warning",
                firing_detectors=["phoenix.local_simulator_drift"],
                detector_summaries={},
            )
        )
        assert estimate_fidelity(local) == pytest.approx(0.70, rel=1e-9)

    def test_provider_unaffected_when_other_provider_drifts(self) -> None:
        from phoenix.router.provider_registry import build_default_registry

        registry = build_default_registry()
        unrelated = registry.get("ibm_quantum")
        assert unrelated is not None
        baseline = estimate_fidelity(unrelated)

        # Fire a snapshot for a DIFFERENT provider.
        on_drift_snapshot(
            DriftSnapshot(
                cycle_unix=1.0,
                state="high_confidence_warning",
                firing_detectors=["aws_braket_drift"],
                detector_summaries={},
            )
        )
        # ibm_quantum is unaffected.
        assert estimate_fidelity(unrelated) == pytest.approx(baseline, rel=1e-9)


# ---------------------------------------------------------------------------
# Detector → router wiring


class TestDriftDetectorWiring:
    def test_register_for_drift_updates_attaches_callback(self) -> None:
        """After register_for_drift_updates, a snapshot fired via the
        detector's emit path propagates to the router's multiplier."""
        from phoenix.verification.drift_detector import get_detector

        register_for_drift_updates()

        detector = get_detector()
        # Simulate a cycle by directly invoking the registered callbacks
        # with a hand-built snapshot (avoids running real detectors).
        snap = DriftSnapshot(
            cycle_unix=1.0,
            state="warning",
            firing_detectors=["ibm_quantum_drift"],
            detector_summaries={},
        )
        for cb in detector._callbacks:  # type: ignore[attr-defined]
            cb(snap)

        assert drift_multiplier_for("ibm_quantum") == 0.90

    def test_register_is_idempotent_across_lifespan_starts(self) -> None:
        """Calling register twice doesn't double-register the callback
        (DriftDetector dedupes by callable identity)."""
        from phoenix.verification.drift_detector import get_detector

        register_for_drift_updates()
        register_for_drift_updates()
        detector = get_detector()
        # on_drift_snapshot should appear at most once in the callback
        # list -- the detector dedupes.
        callbacks = list(detector._callbacks)  # type: ignore[attr-defined]
        matching = [c for c in callbacks if c.__name__ == "on_drift_snapshot"]
        assert len(matching) == 1
