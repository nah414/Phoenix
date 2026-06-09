# Auto-Capture Baseline Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the already-shipped `maybe_auto_capture_baseline()` helper into `DriftDetector.run_cycle` so the cognition-drift baseline auto-refreshes after N consecutive healthy cycles — opt-in, fail-safe, and version-pinned.

**Architecture:** The helper is already dependency-injection-ready. We give `DriftDetector` the four things the helper needs (cognition provider, baseline, phoenix version, and an owned consecutive-healthy counter), call it at the tail of `run_cycle` behind a guard, and have `get_detector()` pass the same provider/baseline/version it already builds for the ML checker. Auto-capture is **opt-in** via `PHOENIX_DRIFT_AUTO_CAPTURE=1` (default OFF) and **resets the counter after a capture** so re-baselining happens once every N healthy cycles rather than every cycle.

**Tech Stack:** Python 3, numpy, pytest. No new dependencies.

---

## Design Decisions (locked with the user 2026-06-09)

1. **Enablement:** opt-in. New env var `PHOENIX_DRIFT_AUTO_CAPTURE` (`"1"` enables; anything else / unset = OFF). Preserves current production behavior until ops deliberately turns it on. Default OFF matches the CHANGELOG's "deferred follow-up" framing.
2. **Re-capture cadence:** reset the consecutive-healthy counter to `0` after a successful capture → re-baselines once every N healthy cycles. Bounds disk writes and reduces slow-drift masking.
3. **Threshold:** the wired path defaults `cycles_before_capture` to `20` (~5 days at the 6h cadence) — most conservative posture, favoring *not* absorbing slow/creeping drift. This **intentionally diverges** from the shipped helper's standalone default of `5`; the resolver carries a comment explaining why. Overrideable via `PHOENIX_DRIFT_AUTO_CAPTURE_CYCLES`.
4. **Dependency flow:** pass provider/baseline/version into `DriftDetector.__init__` explicitly. Do **not** reach into `MLStatisticalChecker`'s private attrs.
5. **Fail-safe:** capture failures are caught + logged inside `run_cycle`, never propagated — same contract as snapshot persistence and callbacks.

## Why these guards matter (the subtle risk)

Auto-capture **overwrites the "known-healthy" reference baseline**. If it fires too eagerly, slow drift gets continuously absorbed into the baseline and the ML checker never trips. The opt-in flag + counter-reset cadence + version pin (`read_baseline_for_version` already returns `None` on a version mismatch, forcing recapture) together bound that risk.

---

## File Structure

| File | Responsibility | Change |
| --- | --- | --- |
| `phoenix/verification/drift_detector.py` | Detector orchestration + helper | Modify: add env resolvers, `__init__` params + counter, `_maybe_auto_capture`, `run_cycle` call, `get_detector()` wiring |
| `tests/integration/test_drift_detector.py` | Orchestration tests | Modify: new test class + env-resolver tests + fixture env cleanup |
| `CHANGELOG.md` | Release notes | Modify: add Phase 13.x.9 entry; annotate the 13.5 "deferred" bullet |

No new files. The shipped helper `maybe_auto_capture_baseline()` ([drift_detector.py:1026](phoenix/verification/drift_detector.py:1026)) and its existing tests ([tests/cognition/test_drift_detector_auto_capture.py](tests/cognition/test_drift_detector_auto_capture.py)) are **unchanged** — they keep validating the helper in isolation.

---

## Task 1: Env-var resolvers (TDD)

**Files:**
- Modify: `phoenix/verification/drift_detector.py` (add two functions next to `_resolve_cadence_seconds`, ~line 881)
- Test: `tests/integration/test_drift_detector.py`

- [ ] **Step 1: Add the failing tests**

In `tests/integration/test_drift_detector.py`, extend the import block (currently lines 40-52) to add the two new symbols:

```python
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
```

Then add these tests after the cadence env-var block (after `test_non_positive_cadence_falls_back`, ~line 476):

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/integration/test_drift_detector.py -k auto_capture -v`
Expected: FAIL — `ImportError: cannot import name '_resolve_auto_capture_enabled'`

- [ ] **Step 3: Implement the resolvers**

In `phoenix/verification/drift_detector.py`, immediately after `_resolve_cadence_seconds` (ends ~line 894), add:

```python
def _resolve_auto_capture_enabled() -> bool:
    """Read ``$PHOENIX_DRIFT_AUTO_CAPTURE`` (default off).

    Auto-capture refreshes the cognition baseline after N consecutive
    healthy cycles. It is opt-in: ops sets ``PHOENIX_DRIFT_AUTO_CAPTURE=1``
    to enable; otherwise :meth:`DriftDetector.run_cycle` never overwrites
    the baseline. Any value other than ``"1"`` (and unset) means disabled.
    """
    return os.environ.get("PHOENIX_DRIFT_AUTO_CAPTURE") == "1"


def _resolve_auto_capture_cycles() -> int:
    """Read ``$PHOENIX_DRIFT_AUTO_CAPTURE_CYCLES`` or fall back to 20.

    The wired-path default (20 ~= 5 days at the 6h cadence) is
    deliberately more conservative than the standalone
    :func:`maybe_auto_capture_baseline` default of 5: re-baselining
    should require a long run of clean cycles so slow/creeping drift is
    not silently absorbed into the baseline. Non-integer and non-positive
    values fall back to the default so a typo can't weaken the guard.
    """
    raw = os.environ.get("PHOENIX_DRIFT_AUTO_CAPTURE_CYCLES")
    if not raw:
        return 20
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 20
    return value if value > 0 else 20
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/integration/test_drift_detector.py -k auto_capture -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```powershell
git add phoenix/verification/drift_detector.py tests/integration/test_drift_detector.py
git commit -m "feat(drift): add auto-capture env resolvers"
```

---

## Task 2: DriftDetector counter + `_maybe_auto_capture` + `run_cycle` call (TDD)

**Files:**
- Modify: `phoenix/verification/drift_detector.py` (`DriftDetector.__init__` ~714-751, `run_cycle` ~837, new method after `run_cycle`)
- Test: `tests/integration/test_drift_detector.py`

- [ ] **Step 1: Add fixture env cleanup + the failing tests**

In the autouse fixture `_reset_detector_singleton` (lines 60-68), add the two new env deletions so detector construction in other tests doesn't pick up a developer's shell env:

```python
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
```

Then add this test class after `TestDriftDetectorCallbacks` (ends ~line 447):

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest "tests/integration/test_drift_detector.py::TestDriftDetectorAutoCaptureWiring" -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'cognition_provider'`

- [ ] **Step 3: Extend `DriftDetector.__init__`**

Replace the signature (lines 714-722) with:

```python
    def __init__(
        self,
        *,
        state_backend: StateBackend | None = None,
        cadence_seconds: int | None = None,
        checkers: list[DriftCheckerProtocol] | None = None,
        phoenix_release: str = "1.0.0rc1",
        feature_provider: Callable[[], np.ndarray | None] | None = None,
        cognition_provider: Callable[[], np.ndarray | None] | None = None,
        cognition_baseline: CognitionDriftBaseline | None = None,
        cognition_phoenix_version: str | None = None,
        auto_capture_enabled: bool | None = None,
        auto_capture_cycles: int | None = None,
    ) -> None:
```

Then, immediately after `self._stop_event = threading.Event()` (line 741, before the "Hydrate from backend" block at line 743), add:

```python
        # Phase 13.x.9: auto-capture wiring. Deps come from get_detector()
        # (same provider/baseline/version it builds for the ML checker).
        # Auto-capture is opt-in via $PHOENIX_DRIFT_AUTO_CAPTURE and resets
        # the counter after a capture (re-baseline every N healthy cycles).
        self._cognition_provider = cognition_provider
        self._cognition_baseline = cognition_baseline
        self._cognition_phoenix_version = cognition_phoenix_version
        self._auto_capture_enabled = (
            auto_capture_enabled
            if auto_capture_enabled is not None
            else _resolve_auto_capture_enabled()
        )
        self._auto_capture_cycles = (
            auto_capture_cycles
            if auto_capture_cycles is not None
            else _resolve_auto_capture_cycles()
        )
        self._consecutive_healthy = 0
```

- [ ] **Step 4: Call the new hook from `run_cycle`**

In `run_cycle`, replace the tail (lines 831-837):

```python
        for cb in callbacks:
            try:
                cb(snapshot)
            except Exception:
                logger.exception("Drift callback raised")

        return snapshot
```

with:

```python
        for cb in callbacks:
            try:
                cb(snapshot)
            except Exception:
                logger.exception("Drift callback raised")

        self._maybe_auto_capture(state)

        return snapshot
```

- [ ] **Step 5: Add the `_maybe_auto_capture` method**

Insert immediately after `run_cycle` (after the new `return snapshot`, before `start_scheduler` at line 839):

```python
    def _maybe_auto_capture(self, state: str) -> None:
        """Phase 13.x.9: track consecutive-healthy cycles and, when
        auto-capture is enabled and cognition deps are wired, refresh the
        per-version baseline after ``auto_capture_cycles`` healthy cycles.

        The counter is updated unconditionally (it reflects cycle health
        regardless of whether capture is enabled). Capture is gated on
        :attr:`_auto_capture_enabled` and the presence of provider +
        baseline + version. A successful capture resets the counter so
        re-baselining happens once every N healthy cycles, not every
        cycle. Any failure is logged and swallowed so it never breaks a
        cycle (same fail-safe contract as snapshot persistence and
        callbacks).
        """
        with self._lock:
            if state == "healthy":
                self._consecutive_healthy += 1
            else:
                self._consecutive_healthy = 0
            consecutive = self._consecutive_healthy

        if not self._auto_capture_enabled:
            return
        if (
            self._cognition_provider is None
            or self._cognition_baseline is None
            or self._cognition_phoenix_version is None
        ):
            return

        try:
            captured = maybe_auto_capture_baseline(
                consecutive_healthy=consecutive,
                state=state,
                provider=self._cognition_provider,
                baseline=self._cognition_baseline,
                phoenix_version=self._cognition_phoenix_version,
                cycles_before_capture=self._auto_capture_cycles,
            )
        except Exception:
            logger.exception("auto-capture of cognition baseline failed")
            return

        if captured:
            with self._lock:
                self._consecutive_healthy = 0
```

> Note: `maybe_auto_capture_baseline` is defined later in the module (line ~1026). Because it's resolved via module globals at call time, the forward reference is fine — same pattern as `get_detector` referencing the class defined above it.

- [ ] **Step 6: Run the wiring tests to verify they pass**

Run: `python -m pytest "tests/integration/test_drift_detector.py::TestDriftDetectorAutoCaptureWiring" -v`
Expected: PASS (7 passed)

- [ ] **Step 7: Run the full drift_detector test module (no regressions)**

Run: `python -m pytest tests/integration/test_drift_detector.py -v`
Expected: PASS (all prior tests + 14 new)

- [ ] **Step 8: Commit**

```powershell
git add phoenix/verification/drift_detector.py tests/integration/test_drift_detector.py
git commit -m "feat(drift): wire auto-capture into DriftDetector.run_cycle"
```

---

## Task 3: Wire deps through `get_detector()` (TDD)

**Files:**
- Modify: `phoenix/verification/drift_detector.py` (`get_detector`, lines 962-1002)
- Test: `tests/integration/test_drift_detector.py`

- [ ] **Step 1: Add the failing test**

Add after the singleton lifecycle tests (after `test_reset_detector_clears_singleton`, ~line 492):

```python
def test_get_detector_wires_auto_capture_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a state backend is available, get_detector() must pass the
    cognition provider/baseline/version into the detector so auto-capture
    can run (the deps must not stay None when checkers wired OK)."""
    reset_detector()
    detector = get_detector()
    # A backend is available in the test env, so the cognition deps wire.
    # (If wiring failed, all three would be None together.)
    assert detector._cognition_baseline is not None
    assert detector._cognition_provider is not None
    assert detector._cognition_phoenix_version is not None
    reset_detector()
```

> If the test environment has **no** state backend, this test will instead see all three as `None`. Confirm during Step 2: if `get_detector()._cognition_baseline is None` even on `main`'s current behavior (i.e. `detector._checkers` is the default list), skip this assertion-style test and replace with a direct unit test of the wiring by constructing with an explicit backend stub. Decide based on the Step 2 run.

- [ ] **Step 2: Run the test (diagnose environment)**

Run: `python -m pytest "tests/integration/test_drift_detector.py::test_get_detector_wires_auto_capture_deps" -v`
Expected: FAIL — `assert None is not None` (deps not yet passed through). If it errors because no backend is configured, switch to the backend-stub variant below before implementing.

- [ ] **Step 3: Hoist the deps and pass them into the constructor**

In `get_detector()`, replace the block from line 962 (`checkers: list[...] = None`) through the construction at line 1002. New version:

```python
            checkers: list[DriftCheckerProtocol] | None = None
            cognition_provider: Callable[[], np.ndarray | None] | None = None
            cognition_baseline: CognitionDriftBaseline | None = None
            cognition_version: str | None = None
            if backend is not None:
                try:
                    from phoenix import __version__ as _phoenix_version
                    from phoenix.verification.cognition_drift_baseline import (
                        CognitionDriftBaseline,
                    )
                    from phoenix.verification.cognition_drift_features import (
                        CognitionFeatureProvider,
                    )

                    cognition_provider = CognitionFeatureProvider(state_backend=backend)
                    cognition_baseline_path_str = os.environ.get(
                        "PHOENIX_COGNITION_DRIFT_BASELINE_PATH"
                    )
                    cognition_baseline = CognitionDriftBaseline(
                        baseline_path=(
                            Path(cognition_baseline_path_str)
                            if cognition_baseline_path_str
                            else None
                        )
                    )
                    cognition_version = _phoenix_version
                    tier1 = Tier1AnalyticalChecker()
                    ml = MLStatisticalChecker(
                        feature_provider=cognition_provider,
                        cognition_baseline=cognition_baseline,
                        phoenix_version=_phoenix_version,
                    )
                    cross = CrossVersionChecker(
                        current_version=_phoenix_version,
                        current_results_provider=tier1.last_results,
                    )
                    checkers = [tier1, ml, cross]
                except Exception:
                    logger.exception(
                        "Failed to wire cognition drift baseline into MLStatisticalChecker; "
                        "falling back to default checker list"
                    )
                    checkers = None
                    cognition_provider = None
                    cognition_baseline = None
                    cognition_version = None

            _DETECTOR = DriftDetector(
                state_backend=backend,
                checkers=checkers,
                cognition_provider=cognition_provider,
                cognition_baseline=cognition_baseline,
                cognition_phoenix_version=cognition_version,
            )
        return _DETECTOR
```

> The added local annotations reference `np` / `CognitionDriftBaseline` (TYPE_CHECKING-only imports). Per PEP 526 local variable annotations are **not** evaluated at runtime, so this is safe — and it matches the existing `checkers: list[DriftCheckerProtocol] | None = None` local annotation already in this function.

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest "tests/integration/test_drift_detector.py::test_get_detector_wires_auto_capture_deps" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git add phoenix/verification/drift_detector.py tests/integration/test_drift_detector.py
git commit -m "feat(drift): pass cognition deps through get_detector for auto-capture"
```

---

## Task 4: CHANGELOG entry

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the Phase 13.x.9 entry**

Immediately after the `## [1.1.0.dev0] — 2026-05-20` header (line 41), insert:

```markdown

### Phase 13.x.9: auto-capture baseline wiring (2026-06-09)

**Wired:** `maybe_auto_capture_baseline()` is now called at the tail of
`DriftDetector.run_cycle`, completing the v1.1.x follow-up deferred at
Phase 13.5. The detector owns a consecutive-healthy counter; after N
consecutive healthy cycles it refreshes the per-version cognition
baseline, then resets the counter (re-baseline every N healthy cycles).

**Opt-in:** off by default. Set `PHOENIX_DRIFT_AUTO_CAPTURE=1` to enable;
`PHOENIX_DRIFT_AUTO_CAPTURE_CYCLES` overrides the threshold (wired-path
default 20 ≈ 5 days at the 6h cadence — intentionally more conservative
than the standalone helper's default of 5, to avoid absorbing slow drift).

**Fail-safe:** capture failures are caught and logged inside `run_cycle`,
never propagated — same contract as snapshot persistence and callbacks.
Deps (provider + per-version baseline + version) flow from
`get_detector()`; in degraded environments (no state backend) auto-capture
stays inert.

**Tests added:** 14 in `tests/integration/test_drift_detector.py`
(7 wiring + 7 env-resolution). The shipped-helper tests in
`tests/cognition/test_drift_detector_auto_capture.py` are unchanged.
```

- [ ] **Step 2: Annotate the Phase 13.5 deferred bullet**

In the Phase 13.5 "NOT shipped" list, update the line at ~228-229:

Old:
```markdown
- Full integration of `maybe_auto_capture_baseline` into
  `DriftDetector.run_cycle` (helper is shipped; auto-cycle wiring deferred)
```

New:
```markdown
- Full integration of `maybe_auto_capture_baseline` into
  `DriftDetector.run_cycle` (helper is shipped; auto-cycle wiring deferred)
  — **shipped in Phase 13.x.9, 2026-06-09**
```

- [ ] **Step 3: Commit**

```powershell
git add CHANGELOG.md
git commit -m "docs(changelog): record Phase 13.x.9 auto-capture wiring"
```

---

## Task 5: Full verification

- [ ] **Step 1: Run the two directly-affected test modules**

Run:
```powershell
python -m pytest tests/integration/test_drift_detector.py tests/cognition/test_drift_detector_auto_capture.py -v
```
Expected: PASS (all green; the helper tests still pass unchanged).

- [ ] **Step 2: Run the adjacent cognition-drift suite (no regressions)**

Run:
```powershell
python -m pytest tests/cognition/test_ml_checker_cognition.py tests/cognition/test_cognition_drift_baseline.py tests/cognition/test_cognition_drift_features.py -q
```
Expected: PASS.

- [ ] **Step 3: Report results** — paste the pytest summary lines back for review. Do not merge to `main`; the branch is ready for a PR.

---

## Edge Cases & Guard Conditions (covered above)

| Case | Handling | Test |
| --- | --- | --- |
| Auto-capture disabled (default) | `_auto_capture_enabled` False → counter still tracked, no capture | `test_disabled_does_not_capture` |
| No cognition deps (no backend) | Early return; `run_cycle` unaffected | `test_missing_deps_is_inert` |
| Provider / write raises | Caught + logged in `_maybe_auto_capture`; cycle returns normally | `test_capture_failure_does_not_break_cycle` |
| Non-healthy cycle | Counter resets to 0 | `test_non_healthy_cycle_resets_counter` |
| Re-capture cadence | Counter resets after capture → every N cycles, not every cycle | `test_counter_resets_after_capture` |
| Version mismatch | `read_baseline_for_version` already returns None elsewhere; write always pins current version | existing baseline tests |
| Thread safety | Counter mutated under `self._lock` | (scheduler runs cycles serially) |
| Env override path | Resolvers read env when `__init__` args omitted | `test_env_flag_drives_enablement`, resolver tests |

## Out of Scope (unchanged)

- The shipped helper `maybe_auto_capture_baseline()` body and its existing tests.
- `PHOENIX_ARCHITECTURE_v1.md` (locked v1 baseline — env vars documented in CHANGELOG + docstrings instead).
- Per-provider drift attribution, drift-triggered rerouting (still deferred per Phase 13.5).

## Self-Review

- **Spec coverage:** integration point (run_cycle tail) ✓, counter ownership ✓, guards (enabled/deps/fail-safe) ✓, config flags (`PHOENIX_DRIFT_AUTO_CAPTURE`, `PHOENIX_DRIFT_AUTO_CAPTURE_CYCLES`) ✓, get_detector wiring ✓, tests ✓.
- **Placeholder scan:** all steps contain concrete code/commands; no TBD.
- **Type consistency:** `_maybe_auto_capture(state: str)`, `maybe_auto_capture_baseline(...)` kwargs match the shipped helper signature ([drift_detector.py:1026](phoenix/verification/drift_detector.py:1026)); `cognition_phoenix_version` param name used consistently across `__init__`, `_maybe_auto_capture`, and `get_detector`.
