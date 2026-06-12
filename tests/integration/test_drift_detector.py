"""Tests for phoenix.verification.drift_detector + drift_state (Phase 6b Step 7).

Coverage:

- Each of the three checkers (Tier-1 analytical, ML statistical,
  cross-version) returns the expected :class:`CheckerResult` shape.
- :class:`DriftDetector` aggregation rule: 0 firing -> healthy, 1
  firing -> warning, 2+ firing -> high_confidence_warning.
- Crashed checker counts as drift (fail-closed).
- Snapshot persistence to a mocked :class:`StateBackend`.
- Snapshot rehydration from the backend on construction.
- :meth:`DriftDetector.register_drift_callback` fires callbacks
  synchronously after persistence.
- :func:`read_drift_state` (rewired in Step 7) returns
  :class:`DriftState` for healthy / warning / high-confidence
  snapshots.
- :class:`DriftStateUnavailable` raised when no snapshot exists or
  the snapshot is stale (> 2 * cadence).
- ``$PHOENIX_DRIFT_CADENCE_HOURS`` env var configures cadence; bad
  values fall back to 6h.
- Singleton lifecycle: :func:`get_detector` / :func:`reset_detector`.

**Crucially, no test runs the real Tier-1 battery.** Each checker
exercise injects a mocked runner / provider so the vendored solvers
aren't invoked. The DriftDetector scheduler is also never started
-- explicit :meth:`run_cycle` calls drive the orchestration tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

# Import phoenix to trigger sys.path injection for vendored modules
# (needed before any vendored import like `from ml.drift_ensemble ...`).
import phoenix  # noqa: F401

from phoenix.verification.drift_detector import (
    CheckerResult,
    CrossVersionChecker,
    DriftDetector,
    DriftSnapshot,
    MLStatisticalChecker,
    Tier1AnalyticalChecker,
    _resolve_auto_capture_cycles,
    _resolve_auto_capture_enabled,
    _resolve_cadence_seconds,
    _snapshot_from_record,
    _snapshot_to_record,
    get_detector,
    reset_detector,
)
from phoenix.verification.drift_state import (
    DriftState,
    DriftStateUnavailable,
    read_drift_state,
)


@pytest.fixture(autouse=True)
def _reset_detector_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure each test starts with a clean detector singleton + a
    known env-var baseline."""
    reset_detector()
    monkeypatch.delenv("PHOENIX_DRIFT_CADENCE_HOURS", raising=False)
    monkeypatch.delenv("PHOENIX_SKIP_STARTUP_DRIFT_CYCLE", raising=False)
    monkeypatch.delenv("PHOENIX_DRIFT_AUTO_CAPTURE", raising=False)
    monkeypatch.delenv("PHOENIX_DRIFT_AUTO_CAPTURE_CYCLES", raising=False)
    yield
    reset_detector()


# ---------------------------------------------------------------------------
# Tier1AnalyticalChecker


class TestTier1AnalyticalChecker:
    def test_all_passing(self) -> None:
        def fake_runner() -> dict[str, tuple[bool, str]]:
            return {
                "HO-1": (True, "ok"),
                "ISW-1": (True, "ok"),
                "H1S-1": (True, "ok"),
                "RABI-1": (True, "ok"),
                "SCG-1": (True, "ok"),
            }

        checker = Tier1AnalyticalChecker(runner=fake_runner)
        result = checker.run()
        assert result.drifting is False
        assert "all 5" in result.summary
        assert checker.last_results() == {
            "HO-1": True,
            "ISW-1": True,
            "H1S-1": True,
            "RABI-1": True,
            "SCG-1": True,
        }

    def test_one_failure_marks_drifting(self) -> None:
        def fake_runner() -> dict[str, tuple[bool, str]]:
            return {
                "HO-1": (True, "ok"),
                "ISW-1": (False, "ratio out of tolerance"),
                "H1S-1": (True, "ok"),
                "RABI-1": (True, "ok"),
                "SCG-1": (True, "ok"),
            }

        checker = Tier1AnalyticalChecker(runner=fake_runner)
        result = checker.run()
        assert result.drifting is True
        assert "ISW-1" in result.summary
        assert "1/5" in result.summary
        assert checker.last_results()["ISW-1"] is False

    def test_last_results_empty_before_first_run(self) -> None:
        checker = Tier1AnalyticalChecker(runner=lambda: {})
        assert checker.last_results() == {}


# ---------------------------------------------------------------------------
# MLStatisticalChecker


class TestMLStatisticalChecker:
    def test_no_provider_returns_skipped(self) -> None:
        checker = MLStatisticalChecker(feature_provider=None)
        result = checker.run()
        assert result.drifting is False
        assert result.metadata.get("skipped") is True
        assert result.metadata.get("reason") == "no_provider"

    def test_empty_features_returns_skipped(self) -> None:
        import numpy as np

        checker = MLStatisticalChecker(feature_provider=lambda: np.array([]))
        result = checker.run()
        assert result.drifting is False
        assert result.metadata.get("reason") == "empty_features"

    def test_provider_raises_counts_as_drift(self) -> None:
        def boom() -> Any:
            raise RuntimeError("synthetic feature provider failure")

        checker = MLStatisticalChecker(feature_provider=boom)
        result = checker.run()
        assert result.drifting is True
        assert "synthetic" in result.summary

    def test_real_predict_scale_separated_stationary(self) -> None:
        """With small random features the vendored DriftEnsemble
        reports stationary -> drifting=False."""
        import numpy as np

        rng = np.random.default_rng(42)
        features = rng.standard_normal((4, 16, 3))
        checker = MLStatisticalChecker(
            feature_provider=lambda: features,
            drift_snr_threshold=10.0,  # well above the snr that random data produces
        )
        result = checker.run()
        # With a high threshold the stationary signal stays below; not drifting.
        assert result.drifting is False
        assert "drift_snr" in result.summary

    def test_real_predict_scale_separated_drifting(self) -> None:
        """A feature buffer with a deliberate IR/UV asymmetry trips the
        drift threshold."""
        import numpy as np

        n_samples, t_steps, n_features = 4, 16, 3
        features = np.zeros((n_samples, t_steps, n_features))
        # First half: ~0; second half: large constant shift. Drift_snr
        # should be high.
        features[:, t_steps // 2 :, :] = 10.0
        checker = MLStatisticalChecker(
            feature_provider=lambda: features,
            drift_snr_threshold=0.5,
        )
        result = checker.run()
        assert result.drifting is True


# ---------------------------------------------------------------------------
# CrossVersionChecker


class TestCrossVersionChecker:
    def test_no_current_results_skipped(self, tmp_path: Path) -> None:
        checker = CrossVersionChecker(
            current_version="1.0.0.dev7",
            current_results_provider=lambda: {},
            history_dir=tmp_path,
        )
        result = checker.run()
        assert result.drifting is False
        assert result.metadata.get("reason") == "no_current"

    def test_no_prior_writes_current(self, tmp_path: Path) -> None:
        current = {"HO-1": True, "ISW-1": True}
        checker = CrossVersionChecker(
            current_version="1.0.0.dev7",
            current_results_provider=lambda: current,
            history_dir=tmp_path,
        )
        result = checker.run()
        assert result.drifting is False
        assert result.metadata.get("reason") == "no_prior"
        # Current results persisted for the next version.
        recorded = tmp_path / "1.0.0.dev7.json"
        assert recorded.exists()

    def test_no_regression_reports_healthy(self, tmp_path: Path) -> None:
        # Pre-populate prior version with all passing.
        prior = tmp_path / "1.0.0.dev6.json"
        prior.write_text(
            '{"HO-1": true, "ISW-1": true}',
            encoding="utf-8",
        )
        # Current matches: no regressions.
        current = {"HO-1": True, "ISW-1": True}
        checker = CrossVersionChecker(
            current_version="1.0.0.dev7",
            current_results_provider=lambda: current,
            history_dir=tmp_path,
        )
        result = checker.run()
        assert result.drifting is False
        assert result.metadata["prior_version"] == "1.0.0.dev6"
        assert result.metadata["regressions"] == []

    def test_regression_marks_drift(self, tmp_path: Path) -> None:
        prior = tmp_path / "1.0.0.dev6.json"
        prior.write_text(
            '{"HO-1": true, "ISW-1": true}',
            encoding="utf-8",
        )
        # ISW-1 regressed.
        current = {"HO-1": True, "ISW-1": False}
        checker = CrossVersionChecker(
            current_version="1.0.0.dev7",
            current_results_provider=lambda: current,
            history_dir=tmp_path,
        )
        result = checker.run()
        assert result.drifting is True
        assert "ISW-1" in result.summary
        assert "ISW-1" in result.metadata["regressions"]

    def test_already_failing_benchmark_is_not_regression(self, tmp_path: Path) -> None:
        """A benchmark that already failed in the prior version isn't
        a regression when it fails again."""
        prior = tmp_path / "1.0.0.dev6.json"
        prior.write_text(
            '{"HO-1": true, "ISW-1": false}',
            encoding="utf-8",
        )
        current = {"HO-1": True, "ISW-1": False}
        checker = CrossVersionChecker(
            current_version="1.0.0.dev7",
            current_results_provider=lambda: current,
            history_dir=tmp_path,
        )
        result = checker.run()
        assert result.drifting is False
        assert result.metadata["regressions"] == []


# ---------------------------------------------------------------------------
# DriftDetector orchestration


class _ConstantChecker:
    """Tiny fake checker for orchestration tests."""

    def __init__(self, name: str, drifting: bool, summary: str = "") -> None:
        self.name = name
        self._result = CheckerResult(
            name=name, drifting=drifting, summary=summary or f"{name}: {drifting}"
        )

    def run(self) -> CheckerResult:
        return self._result


class _CrashingChecker:
    name = "crashing"

    def run(self) -> CheckerResult:
        raise RuntimeError("synthetic crash")


class _RecordingBackend:
    """Minimal StateBackend stub recording put_drift_state_snapshot calls."""

    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self.snapshots: list[dict[str, Any]] = []
        self._initial = initial

    def get_drift_state_snapshot(self) -> dict[str, Any] | None:
        return self._initial

    def put_drift_state_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.snapshots.append(dict(snapshot))


class TestDriftDetectorAggregation:
    def test_no_firing_is_healthy(self) -> None:
        detector = DriftDetector(
            checkers=[
                _ConstantChecker("a", False),
                _ConstantChecker("b", False),
                _ConstantChecker("c", False),
            ]
        )
        snapshot = detector.run_cycle()
        assert snapshot.state == "healthy"
        assert snapshot.firing_detectors == []

    def test_one_firing_is_warning(self) -> None:
        detector = DriftDetector(
            checkers=[
                _ConstantChecker("tier1_analytical", True),
                _ConstantChecker("ml_statistical", False),
                _ConstantChecker("cross_version", False),
            ]
        )
        snapshot = detector.run_cycle()
        assert snapshot.state == "warning"
        assert snapshot.firing_detectors == ["tier1_analytical"]

    def test_two_firing_is_high_confidence(self) -> None:
        detector = DriftDetector(
            checkers=[
                _ConstantChecker("tier1_analytical", True),
                _ConstantChecker("ml_statistical", True),
                _ConstantChecker("cross_version", False),
            ]
        )
        snapshot = detector.run_cycle()
        assert snapshot.state == "high_confidence_warning"
        assert set(snapshot.firing_detectors) == {"tier1_analytical", "ml_statistical"}

    def test_three_firing_is_high_confidence(self) -> None:
        detector = DriftDetector(
            checkers=[
                _ConstantChecker("a", True),
                _ConstantChecker("b", True),
                _ConstantChecker("c", True),
            ]
        )
        snapshot = detector.run_cycle()
        assert snapshot.state == "high_confidence_warning"

    def test_crashed_checker_counts_as_drift(self) -> None:
        detector = DriftDetector(
            checkers=[
                _ConstantChecker("a", False),
                _CrashingChecker(),
            ]
        )
        snapshot = detector.run_cycle()
        assert snapshot.state == "warning"
        assert "crashing" in snapshot.firing_detectors
        # The crashed checker's summary names the exception type.
        assert "RuntimeError" in snapshot.detector_summaries["crashing"]


class TestDriftDetectorSnapshotPersistence:
    def test_snapshot_persisted_to_backend(self) -> None:
        backend = _RecordingBackend()
        detector = DriftDetector(
            state_backend=backend,
            checkers=[_ConstantChecker("a", False)],
        )
        detector.run_cycle()
        assert len(backend.snapshots) == 1
        record = backend.snapshots[0]
        assert record["state"] == "healthy"
        assert "cycle_unix" in record

    def test_backend_failure_does_not_raise(self) -> None:
        class FailingBackend:
            def get_drift_state_snapshot(self) -> None:
                return None

            def put_drift_state_snapshot(self, _snapshot: Any) -> None:
                raise RuntimeError("backend down")

        detector = DriftDetector(
            state_backend=FailingBackend(),
            checkers=[_ConstantChecker("a", False)],
        )
        # Must not propagate.
        snapshot = detector.run_cycle()
        assert snapshot.state == "healthy"

    def test_snapshot_rehydrated_on_construction(self) -> None:
        prior = {
            "cycle_unix": 1234567890.0,
            "state": "warning",
            "firing_detectors": ["tier1_analytical"],
            "detector_summaries": {"tier1_analytical": "ISW-1 failed"},
        }
        backend = _RecordingBackend(initial=prior)
        detector = DriftDetector(
            state_backend=backend,
            checkers=[_ConstantChecker("a", False)],
        )
        snap = detector.last_snapshot()
        assert snap is not None
        assert snap.state == "warning"
        assert snap.firing_detectors == ["tier1_analytical"]
        assert snap.cycle_unix == 1234567890.0


class TestDriftDetectorCallbacks:
    def test_register_and_fire(self) -> None:
        received: list[DriftSnapshot] = []
        detector = DriftDetector(checkers=[_ConstantChecker("a", False)])
        detector.register_drift_callback(received.append)
        snapshot = detector.run_cycle()
        assert len(received) == 1
        assert received[0] is snapshot

    def test_multiple_callbacks_fire_in_order(self) -> None:
        calls: list[str] = []
        detector = DriftDetector(checkers=[_ConstantChecker("a", False)])
        detector.register_drift_callback(lambda _snap: calls.append("first"))
        detector.register_drift_callback(lambda _snap: calls.append("second"))
        detector.run_cycle()
        assert calls == ["first", "second"]

    def test_callback_exception_does_not_block_next(self) -> None:
        calls: list[str] = []

        def crashy(_snap: DriftSnapshot) -> None:
            calls.append("crashy")
            raise RuntimeError("nope")

        def ok(_snap: DriftSnapshot) -> None:
            calls.append("ok")

        detector = DriftDetector(checkers=[_ConstantChecker("a", False)])
        detector.register_drift_callback(crashy)
        detector.register_drift_callback(ok)
        detector.run_cycle()
        assert calls == ["crashy", "ok"]


# ---------------------------------------------------------------------------
# DriftDetector auto-capture wiring (Phase 13.x.9)


class TestDriftDetectorAutoCaptureWiring:
    """run_cycle drives maybe_auto_capture_baseline behind the opt-in flag."""

    @staticmethod
    def _provider() -> Any:
        from phoenix.verification.cognition_drift_features import (
            CognitionDriftFeatures,
        )

        features = CognitionDriftFeatures(
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
        return lambda: features.as_vector()

    @staticmethod
    def _baseline(tmp_path: Path) -> Any:
        from phoenix.verification.cognition_drift_baseline import (
            CognitionDriftBaseline,
        )

        return CognitionDriftBaseline(baseline_path=tmp_path / "baseline.json")

    def test_captures_after_n_healthy_cycles(self, tmp_path: Path) -> None:
        baseline = self._baseline(tmp_path)
        detector = DriftDetector(
            checkers=[_ConstantChecker("a", False)],
            cognition_provider=self._provider(),
            cognition_baseline=baseline,
            cognition_phoenix_version="1.1.0.dev0",
            auto_capture_enabled=True,
            auto_capture_cycles=3,
        )
        detector.run_cycle()
        detector.run_cycle()
        assert not baseline.baseline_path.is_file()  # below threshold
        detector.run_cycle()
        assert baseline.baseline_path.is_file()  # threshold met -> capture

    def test_non_healthy_cycle_resets_counter(self, tmp_path: Path) -> None:
        baseline = self._baseline(tmp_path)
        checker = _ConstantChecker("a", False)
        detector = DriftDetector(
            checkers=[checker],
            cognition_provider=self._provider(),
            cognition_baseline=baseline,
            cognition_phoenix_version="1.1.0.dev0",
            auto_capture_enabled=True,
            auto_capture_cycles=3,
        )
        detector.run_cycle()  # healthy 1
        detector.run_cycle()  # healthy 2
        checker._result = CheckerResult(name="a", drifting=True, summary="boom")
        detector.run_cycle()  # warning -> counter resets
        checker._result = CheckerResult(name="a", drifting=False, summary="ok")
        detector.run_cycle()  # healthy 1
        detector.run_cycle()  # healthy 2
        assert not baseline.baseline_path.is_file()
        detector.run_cycle()  # healthy 3 -> capture
        assert baseline.baseline_path.is_file()

    def test_counter_resets_after_capture(self, tmp_path: Path) -> None:
        baseline = self._baseline(tmp_path)
        detector = DriftDetector(
            checkers=[_ConstantChecker("a", False)],
            cognition_provider=self._provider(),
            cognition_baseline=baseline,
            cognition_phoenix_version="1.1.0.dev0",
            auto_capture_enabled=True,
            auto_capture_cycles=2,
        )
        detector.run_cycle()
        detector.run_cycle()  # capture at cycle 2
        assert baseline.baseline_path.is_file()
        baseline.baseline_path.unlink()  # remove to observe re-capture cadence
        detector.run_cycle()  # healthy 1 post-reset -> no capture
        assert not baseline.baseline_path.is_file()
        detector.run_cycle()  # healthy 2 -> re-capture
        assert baseline.baseline_path.is_file()

    def test_disabled_does_not_capture(self, tmp_path: Path) -> None:
        baseline = self._baseline(tmp_path)
        detector = DriftDetector(
            checkers=[_ConstantChecker("a", False)],
            cognition_provider=self._provider(),
            cognition_baseline=baseline,
            cognition_phoenix_version="1.1.0.dev0",
            auto_capture_enabled=False,
            auto_capture_cycles=2,
        )
        for _ in range(5):
            detector.run_cycle()
        assert not baseline.baseline_path.is_file()

    def test_missing_deps_is_inert(self) -> None:
        detector = DriftDetector(
            checkers=[_ConstantChecker("a", False)],
            auto_capture_enabled=True,
            auto_capture_cycles=1,
        )
        snapshot = detector.run_cycle()  # must not raise despite no deps
        assert snapshot.state == "healthy"

    def test_capture_failure_does_not_break_cycle(self, tmp_path: Path) -> None:
        baseline = self._baseline(tmp_path)

        def boom() -> Any:
            raise RuntimeError("provider exploded")

        detector = DriftDetector(
            checkers=[_ConstantChecker("a", False)],
            cognition_provider=boom,
            cognition_baseline=baseline,
            cognition_phoenix_version="1.1.0.dev0",
            auto_capture_enabled=True,
            auto_capture_cycles=1,
        )
        snapshot = detector.run_cycle()  # capture raises internally
        assert snapshot.state == "healthy"  # cycle still succeeds
        assert not baseline.baseline_path.is_file()

    def test_env_flag_drives_enablement(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PHOENIX_DRIFT_AUTO_CAPTURE", "1")
        monkeypatch.setenv("PHOENIX_DRIFT_AUTO_CAPTURE_CYCLES", "1")
        baseline = self._baseline(tmp_path)
        # auto_capture_enabled/cycles omitted -> resolved from env.
        detector = DriftDetector(
            checkers=[_ConstantChecker("a", False)],
            cognition_provider=self._provider(),
            cognition_baseline=baseline,
            cognition_phoenix_version="1.1.0.dev0",
        )
        detector.run_cycle()
        assert baseline.baseline_path.is_file()

    def test_counter_increments_while_disabled(self, tmp_path: Path) -> None:
        """The consecutive-healthy counter is tracked unconditionally, even
        when auto-capture is disabled (documented contract)."""
        baseline = self._baseline(tmp_path)
        detector = DriftDetector(
            checkers=[_ConstantChecker("a", False)],
            cognition_provider=self._provider(),
            cognition_baseline=baseline,
            cognition_phoenix_version="1.1.0.dev0",
            auto_capture_enabled=False,
            auto_capture_cycles=3,
        )
        for _ in range(4):
            detector.run_cycle()
        assert detector._consecutive_healthy == 4
        assert not baseline.baseline_path.is_file()

    def test_capture_failure_preserves_trigger_then_recovers(self, tmp_path: Path) -> None:
        """A transient capture failure must NOT consume the trigger: the
        counter is not reset, so the next healthy cycle still captures."""
        baseline = self._baseline(tmp_path)
        good = self._provider()
        calls = {"n": 0}

        def flaky() -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient provider failure")
            return good()

        detector = DriftDetector(
            checkers=[_ConstantChecker("a", False)],
            cognition_provider=flaky,
            cognition_baseline=baseline,
            cognition_phoenix_version="1.1.0.dev0",
            auto_capture_enabled=True,
            auto_capture_cycles=1,
        )
        detector.run_cycle()  # threshold met, capture raises -> swallowed
        assert not baseline.baseline_path.is_file()
        assert detector._consecutive_healthy == 1  # trigger NOT consumed
        detector.run_cycle()  # provider recovers -> capture happens
        assert baseline.baseline_path.is_file()

    def test_provider_returns_none_is_inert(self, tmp_path: Path) -> None:
        """Provider present but yielding None at capture time: no raise, no
        write, trigger not consumed (distinct from the provider-raises path)."""
        baseline = self._baseline(tmp_path)
        detector = DriftDetector(
            checkers=[_ConstantChecker("a", False)],
            cognition_provider=lambda: None,
            cognition_baseline=baseline,
            cognition_phoenix_version="1.1.0.dev0",
            auto_capture_enabled=True,
            auto_capture_cycles=1,
        )
        snapshot = detector.run_cycle()
        assert snapshot.state == "healthy"
        assert not baseline.baseline_path.is_file()
        assert detector._consecutive_healthy == 1  # not reset, retried next cycle

    def test_high_confidence_warning_resets_counter(self, tmp_path: Path) -> None:
        """A 2+-firing (high_confidence_warning) cycle resets the counter,
        same as a single-firing warning."""
        baseline = self._baseline(tmp_path)
        c1 = _ConstantChecker("a", False)
        c2 = _ConstantChecker("b", False)
        detector = DriftDetector(
            checkers=[c1, c2],
            cognition_provider=self._provider(),
            cognition_baseline=baseline,
            cognition_phoenix_version="1.1.0.dev0",
            auto_capture_enabled=True,
            auto_capture_cycles=3,
        )
        detector.run_cycle()  # healthy 1
        detector.run_cycle()  # healthy 2
        c1._result = CheckerResult(name="a", drifting=True, summary="x")
        c2._result = CheckerResult(name="b", drifting=True, summary="y")
        snapshot = detector.run_cycle()
        assert snapshot.state == "high_confidence_warning"
        assert detector._consecutive_healthy == 0
        assert not baseline.baseline_path.is_file()


# ---------------------------------------------------------------------------
# Cadence env-var resolution


def test_default_cadence_is_6h() -> None:
    assert _resolve_cadence_seconds() == 6 * 60 * 60


def test_cadence_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIX_DRIFT_CADENCE_HOURS", "12")
    assert _resolve_cadence_seconds() == 12 * 60 * 60


def test_fractional_cadence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIX_DRIFT_CADENCE_HOURS", "0.5")
    assert _resolve_cadence_seconds() == int(0.5 * 3600)


def test_invalid_cadence_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIX_DRIFT_CADENCE_HOURS", "not-a-number")
    assert _resolve_cadence_seconds() == 6 * 60 * 60


def test_non_positive_cadence_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIX_DRIFT_CADENCE_HOURS", "-1")
    assert _resolve_cadence_seconds() == 6 * 60 * 60


# ---------------------------------------------------------------------------
# Auto-capture env-var resolution


def test_auto_capture_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHOENIX_DRIFT_AUTO_CAPTURE", raising=False)
    assert _resolve_auto_capture_enabled() is False


def test_auto_capture_enabled_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIX_DRIFT_AUTO_CAPTURE", "1")
    assert _resolve_auto_capture_enabled() is True


def test_auto_capture_non_one_value_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIX_DRIFT_AUTO_CAPTURE", "true")
    assert _resolve_auto_capture_enabled() is False


def test_auto_capture_cycles_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHOENIX_DRIFT_AUTO_CAPTURE_CYCLES", raising=False)
    assert _resolve_auto_capture_cycles() == 20


def test_auto_capture_cycles_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIX_DRIFT_AUTO_CAPTURE_CYCLES", "10")
    assert _resolve_auto_capture_cycles() == 10


def test_auto_capture_cycles_invalid_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIX_DRIFT_AUTO_CAPTURE_CYCLES", "nope")
    assert _resolve_auto_capture_cycles() == 20


def test_auto_capture_cycles_non_positive_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIX_DRIFT_AUTO_CAPTURE_CYCLES", "0")
    assert _resolve_auto_capture_cycles() == 20


# ---------------------------------------------------------------------------
# Singleton lifecycle


def test_get_detector_returns_singleton() -> None:
    first = get_detector()
    second = get_detector()
    assert first is second


def test_reset_detector_clears_singleton() -> None:
    first = get_detector()
    reset_detector()
    second = get_detector()
    assert first is not second


def test_get_detector_wires_auto_capture_deps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When a state backend is available, get_detector() must pass the
    cognition provider/baseline/version into the detector so auto-capture
    can run (the deps must not stay None when checkers wired OK)."""
    # Explicit isolation: point the baseline at tmp_path so this test can
    # never touch the developer's real ~/.phoenix baseline, even if a future
    # change makes get_detector() (or a startup hook) write one.
    monkeypatch.setenv("PHOENIX_COGNITION_DRIFT_BASELINE_PATH", str(tmp_path / "baseline.json"))
    reset_detector()
    detector = get_detector()
    # A backend is available in the test env, so the cognition deps wire.
    # (If wiring failed, all three would be None together.)
    assert detector._cognition_baseline is not None
    assert detector._cognition_provider is not None
    assert detector._cognition_phoenix_version is not None
    reset_detector()


# ---------------------------------------------------------------------------
# drift_state.read_drift_state -- the wired contract


def test_read_drift_state_defaults_to_healthy_when_no_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cold start: fresh detector, no cycle run, no backend snapshot.

    The verification gate must be able to proceed during daemon
    startup before the first drift cycle. Phase 6b Step 7 preserves
    this Phase 5 stub contract; the fail-closed rule kicks in only
    once the detector has produced (and then lost) a snapshot.
    """
    # Construct an isolated detector with no backend so no rehydration.
    detector = DriftDetector(checkers=[_ConstantChecker("a", False)])

    import phoenix.verification.drift_detector as dd_mod

    monkeypatch.setattr(dd_mod, "_DETECTOR", detector)

    state = read_drift_state()
    assert state.is_healthy is True
    assert state.is_warning is False


def test_read_drift_state_returns_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    detector = DriftDetector(checkers=[_ConstantChecker("a", False)])
    detector.run_cycle()

    import phoenix.verification.drift_detector as dd_mod

    monkeypatch.setattr(dd_mod, "_DETECTOR", detector)

    state = read_drift_state()
    assert isinstance(state, DriftState)
    assert state.is_healthy is True
    assert state.is_warning is False
    assert state.is_high_confidence_warning is False


def test_read_drift_state_returns_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    detector = DriftDetector(
        checkers=[
            _ConstantChecker("tier1_analytical", True),
            _ConstantChecker("ml_statistical", False),
        ]
    )
    detector.run_cycle()

    import phoenix.verification.drift_detector as dd_mod

    monkeypatch.setattr(dd_mod, "_DETECTOR", detector)

    state = read_drift_state()
    assert state.is_warning is True
    assert state.is_high_confidence_warning is False
    assert "tier1_analytical" in (state.detector_summaries or {})


def test_read_drift_state_high_confidence_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detector = DriftDetector(
        checkers=[
            _ConstantChecker("tier1_analytical", True),
            _ConstantChecker("ml_statistical", True),
        ]
    )
    detector.run_cycle()

    import phoenix.verification.drift_detector as dd_mod

    monkeypatch.setattr(dd_mod, "_DETECTOR", detector)

    state = read_drift_state()
    assert state.is_warning is True
    assert state.is_high_confidence_warning is True


def test_read_drift_state_stale_snapshot_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A snapshot older than 2 * cadence triggers DriftStateUnavailable."""
    import time

    detector = DriftDetector(
        cadence_seconds=60,  # 60s cadence -> stale at >120s
        checkers=[_ConstantChecker("a", False)],
    )
    # Inject a stale snapshot by running a cycle, then rewinding its
    # cycle_unix beyond the stale threshold.
    detector.run_cycle()
    stale = DriftSnapshot(
        cycle_unix=time.time() - 200,  # 200s old > 2 * 60s stale threshold
        state="healthy",
        firing_detectors=[],
        detector_summaries={},
    )
    detector._last_snapshot = stale  # type: ignore[attr-defined]

    import phoenix.verification.drift_detector as dd_mod

    monkeypatch.setattr(dd_mod, "_DETECTOR", detector)

    with pytest.raises(DriftStateUnavailable, match="stale"):
        read_drift_state()


# ---------------------------------------------------------------------------
# Snapshot record round-trip


def test_snapshot_record_round_trip() -> None:
    original = DriftSnapshot(
        cycle_unix=1717400000.0,
        state="warning",
        firing_detectors=["tier1_analytical"],
        detector_summaries={"tier1_analytical": "ISW-1 ratio off"},
    )
    record = _snapshot_to_record(original)
    restored = _snapshot_from_record(record)
    assert restored.cycle_unix == original.cycle_unix
    assert restored.state == original.state
    assert restored.firing_detectors == original.firing_detectors
    assert restored.detector_summaries == original.detector_summaries
