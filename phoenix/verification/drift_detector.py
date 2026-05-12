"""Drift detector orchestrator + three checkers (Phase 6b Step 7).

Per architecture v1 Section 1 Decision 17: Phoenix continuously self-
checks for calibration drift via three independent detectors:

1. **Tier-1 analytical battery** -- runs HO-1, ISW-1, H1S-1, RABI-1,
   SCG-1 against the vendored solver registry, asserting each
   benchmark stays inside its analytical tolerance.

2. **ML statistical** -- thin Phoenix adapter around the vendored
   ``ml/drift_ensemble.py``'s :meth:`predict_scale_separated`
   (locked OPEN-5, 2026-05-10). The adapter accepts a feature
   provider callback; with no provider the checker skips (Phase 6b
   ships the seam, Phase 7+ wires solver-output feature collection).

3. **Cross-version comparison** -- runs the current Phoenix's Tier-1
   battery and compares per-test pass/fail against the previous
   release's recorded results at
   ``~/.phoenix/runtime/calibration_history/<phoenix_version>.json``.
   No-op on the first run (no prior version on disk).

Aggregation rule (per Decision 17):

- ``0`` detectors firing -> ``state="healthy"``.
- ``1`` detector firing -> ``state="warning"``, ``drift_warning``
  flag in :class:`DriftState`.
- ``2+`` detectors firing -> ``state="high_confidence_warning"``,
  the verification gate (Section 6.5) auto-promotes one rung.

**Cadence and lifecycle.** :class:`DriftDetector` owns the cadence
(default 6h per Decision 17, configurable via
``$PHOENIX_DRIFT_CADENCE_HOURS``). The background scheduler is **not
auto-started**: :func:`get_detector` constructs the singleton with
the configured state backend, but the scheduler stays idle until an
explicit :meth:`DriftDetector.start_scheduler` call. The FastAPI
lifespan does **not** start it in Phase 6b; this protects test
environments (where running a full Tier-1 cycle adds 5-7 minutes per
test session) and lets launcher scripts opt in explicitly. Ops
scripts can run individual cycles via :meth:`run_cycle` synchronously.

**Forward-compat callback registration** (per locked OPEN-6,
2026-05-10): :meth:`register_drift_callback` accepts a callback
fired on every cycle snapshot. Phase 6b registers only the
verification gate's auto-promote consumer; Phase 7 adds the router
intelligence's fidelity-rescoring consumer as a second caller.

**Snapshot persistence.** When a :class:`StateBackend` is provided,
:meth:`run_cycle` persists the snapshot via the Phase 6b
:meth:`put_drift_state_snapshot` method, so daemon restart sees the
last cycle's state. Replay (Section 6.5 replay rule) reads the
recorded snapshot rather than the live cycle.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import numpy as np

    from phoenix.state.backend_protocol import StateBackend

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result + snapshot types


@dataclass(frozen=True)
class CheckerResult:
    """Output of a single drift checker's :meth:`run`."""

    name: str
    drifting: bool
    summary: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DriftSnapshot:
    """One drift-cycle snapshot.

    Persisted via :meth:`StateBackend.put_drift_state_snapshot`
    (shape matches the dict keys in the snapshot record); loaded on
    daemon restart so replay sees the same drift state as the
    original run.
    """

    cycle_unix: float
    state: str  # "healthy" | "warning" | "high_confidence_warning"
    firing_detectors: list[str]
    detector_summaries: dict[str, str]
    metadata: dict[str, Any] = field(default_factory=dict)


CallbackType = Callable[[DriftSnapshot], None]


# ---------------------------------------------------------------------------
# Tier-1 analytical benchmarks (inlined per the audit-grade vendoring
# discipline: tests/tier1/test_tier1_benchmarks.py also exercises the
# vendored solvers via the same equations, but the drift detector
# can't depend on tests/ at runtime, so the math is duplicated here.
# Five small benchmarks; the duplication is bounded.)

# Physical constants -- mirror tests/tier1/test_tier1_benchmarks.py.
HBAR = 1.054571817e-34
M_ELECTRON = 9.1093837015e-31
MU_BOHR = 9.2740100783e-24
C_LIGHT = 299792458.0


def _registry() -> Any:
    """Lazy import of the vendored solver registry."""
    from synthesis.equations.registry import auto_register_all

    return auto_register_all()


def _benchmark_ho1() -> tuple[bool, str]:
    """HO-1: QHO ground state vs analytical 0.5 * hbar * omega.

    Returns (passed, summary). Catches all exceptions and reports
    failure with the exception type -- a thrown benchmark is drift.
    """
    try:
        from synthesis.equations.base import EquationRegime, PhysicsContext

        omega = 1e15
        solver = _registry().get_solver(EquationRegime.NON_RELATIVISTIC_TI)
        ctx = PhysicsContext(
            mass_kg=M_ELECTRON,
            length_scale_m=4e-9,
            metadata={"omega": omega, "n_grid_points": 400},
        )
        result = solver.solve_stationary(ctx, n_states=5)
        expected = HBAR * omega * 0.5
        actual = float(result.eigenvalues[0])
        passed = math.isclose(actual, expected, rel_tol=0.01)
        summary = (
            f"HO-1 ground state: expected {expected:.4e}, got {actual:.4e}"
            f" (rel_tol=0.01, {'PASS' if passed else 'FAIL'})"
        )
        return passed, summary
    except Exception as exc:
        return False, f"HO-1 raised {type(exc).__name__}: {exc}"


def _benchmark_isw1() -> tuple[bool, str]:
    """ISW-1: infinite-square-well level ratios E_n/E_1 = n^2."""
    try:
        import numpy as np

        from synthesis.equations.base import EquationRegime, PhysicsContext

        length = 1e-9
        n_grid = 200
        dx = length / n_grid
        diag = 2.0 * HBAR**2 / (2 * M_ELECTRON * dx**2)
        off = -1.0 * HBAR**2 / (2 * M_ELECTRON * dx**2)
        hamiltonian = np.diag(np.full(n_grid, diag))
        hamiltonian += np.diag(np.full(n_grid - 1, off), 1)
        hamiltonian += np.diag(np.full(n_grid - 1, off), -1)

        solver = _registry().get_solver(EquationRegime.NON_RELATIVISTIC_TI)
        ctx = PhysicsContext(
            mass_kg=M_ELECTRON,
            length_scale_m=length,
            custom_hamiltonian=hamiltonian,
            metadata={"omega": 0, "n_grid_points": n_grid},
        )
        result = solver.solve_stationary(ctx, n_states=5)
        e1 = float(result.eigenvalues[0])
        r2 = float(result.eigenvalues[1]) / e1
        r3 = float(result.eigenvalues[2]) / e1
        passed = math.isclose(r2, 4.0, rel_tol=0.01) and math.isclose(r3, 9.0, rel_tol=0.01)
        return passed, (
            f"ISW-1 ratios E2/E1={r2:.4f} (exp 4), E3/E1={r3:.4f} (exp 9) "
            f"({'PASS' if passed else 'FAIL'})"
        )
    except Exception as exc:
        return False, f"ISW-1 raised {type(exc).__name__}: {exc}"


def _benchmark_h1s1() -> tuple[bool, str]:
    """H1S-1: Dirac rest-energy reduction at low velocity."""
    try:
        from synthesis.equations.base import (
            EquationRegime,
            ParticleType,
            PhysicsContext,
        )

        solver = _registry().get_solver(EquationRegime.DIRAC)
        ctx = PhysicsContext(
            mass_kg=M_ELECTRON,
            velocity_over_c=0.5,
            has_spin=True,
            spin_value=0.5,
            particle_type=ParticleType.SPIN_HALF,
            length_scale_m=1e-11,
            metadata={"n_grid_points": 100},
        )
        result = solver.solve_stationary(ctx, n_states=5)
        expected = M_ELECTRON * C_LIGHT**2
        actual = float(result.eigenvalues[0])
        ratio = actual / expected if expected != 0 else float("inf")
        passed = abs(ratio - 1.0) < 0.20
        return passed, (
            f"H1S-1 ratio={ratio:.4f} (target 1.0 +/- 0.20) ({'PASS' if passed else 'FAIL'})"
        )
    except Exception as exc:
        return False, f"H1S-1 raised {type(exc).__name__}: {exc}"


def _benchmark_rabi1() -> tuple[bool, str]:
    """RABI-1: Pauli/Zeeman splitting matches g * mu_B * B."""
    try:
        from synthesis.equations.base import EquationRegime, PhysicsContext

        b_field = 1.0
        solver = _registry().get_solver(EquationRegime.PAULI)
        ctx = PhysicsContext(
            mass_kg=M_ELECTRON,
            has_spin=True,
            spin_value=0.5,
            has_magnetic_field=True,
            magnetic_field_tesla=b_field,
            length_scale_m=4e-9,
            metadata={"omega": 1e15, "n_grid_points": 100},
        )
        result = solver.solve_stationary(ctx, n_states=6)
        e0 = float(result.eigenvalues[0])
        e1 = float(result.eigenvalues[1])
        g_factor = 2.0023
        expected = g_factor * MU_BOHR * b_field
        actual = abs(e1 - e0)
        passed = math.isclose(actual, expected, rel_tol=0.05)
        return passed, (
            f"RABI-1 split: expected {expected:.4e} J, got {actual:.4e} J "
            f"({'PASS' if passed else 'FAIL'})"
        )
    except Exception as exc:
        return False, f"RABI-1 raised {type(exc).__name__}: {exc}"


def _benchmark_scg1() -> tuple[bool, str]:
    """SCG-1: semiclassical-gravity correction stays below 10% for electron."""
    try:
        from synthesis.equations.base import EquationRegime, PhysicsContext

        sc_solver = _registry().get_solver(EquationRegime.SEMICLASSICAL_GRAVITY)
        tise_solver = _registry().get_solver(EquationRegime.NON_RELATIVISTIC_TI)
        ctx = PhysicsContext(
            mass_kg=M_ELECTRON,
            include_gravity=True,
            gravitational_regime="semiclassical",
            length_scale_m=4e-9,
            metadata={"omega": 1e15, "n_grid_points": 200},
        )
        r_sc = sc_solver.solve_stationary(ctx, n_states=3)
        r_tise = tise_solver.solve_stationary(ctx, n_states=3)
        e0_sc = float(r_sc.eigenvalues[0])
        e0_tise = float(r_tise.eigenvalues[0])
        correction = abs(e0_sc - e0_tise) / abs(e0_tise) if e0_tise != 0 else float("inf")
        passed = correction < 0.10
        return passed, (
            f"SCG-1 correction={correction:.4e} (limit 0.10) ({'PASS' if passed else 'FAIL'})"
        )
    except Exception as exc:
        return False, f"SCG-1 raised {type(exc).__name__}: {exc}"


TIER1_BATTERY: dict[str, Callable[[], tuple[bool, str]]] = {
    "HO-1": _benchmark_ho1,
    "ISW-1": _benchmark_isw1,
    "H1S-1": _benchmark_h1s1,
    "RABI-1": _benchmark_rabi1,
    "SCG-1": _benchmark_scg1,
}


def run_tier1_battery() -> dict[str, tuple[bool, str]]:
    """Run all 5 Tier-1 benchmarks; return {name: (passed, summary)}.

    **PERF:** each benchmark constructs a vendored solver and runs an
    eigendecomposition on a ~100-400 point grid; combined runtime is
    seconds, not minutes. The 5-7 minute figure from Decision 17 is
    for the full statistical drift cycle, not Tier-1 alone.

    **SAFETY:** never raises -- each benchmark catches exceptions and
    reports them as drift (failed=True). The drift detector treats
    benchmark crashes as drift, which is the correct fail-closed
    behavior for a calibration check.
    """
    return {name: fn() for name, fn in TIER1_BATTERY.items()}


# ---------------------------------------------------------------------------
# Checker protocol + concrete implementations.


class DriftCheckerProtocol(Protocol):
    """Structural contract a drift checker satisfies.

    Each checker has a stable ``name`` (string used in firing-detector
    lists) and a synchronous :meth:`run` that returns a
    :class:`CheckerResult`. Checkers must not raise -- they should
    catch exceptions internally and report them as drift with the
    error captured in the result summary.
    """

    name: str

    def run(self) -> CheckerResult: ...


class Tier1AnalyticalChecker:
    """Detector 1 of 3: runs the vendored Tier-1 analytical battery.

    Drift criterion: any of the 5 benchmarks fails its analytical
    tolerance check. The :attr:`last_results` attribute exposes the
    per-benchmark pass/fail dict for :class:`CrossVersionChecker` to
    consume in the same cycle.
    """

    name = "tier1_analytical"

    def __init__(self, runner: Callable[[], dict[str, tuple[bool, str]]] | None = None) -> None:
        self._runner = runner if runner is not None else run_tier1_battery
        self._last_results: dict[str, bool] = {}

    def run(self) -> CheckerResult:
        battery = self._runner()
        passed = {name: result[0] for name, result in battery.items()}
        summaries = {name: result[1] for name, result in battery.items()}
        self._last_results = dict(passed)

        n_failed = sum(1 for v in passed.values() if not v)
        drifting = n_failed > 0
        failed_names = [name for name, ok in passed.items() if not ok]
        if drifting:
            summary = (
                f"{n_failed}/{len(passed)} Tier-1 benchmarks failed: {', '.join(failed_names)}"
            )
        else:
            summary = f"all {len(passed)} Tier-1 benchmarks passed"
        return CheckerResult(
            name=self.name,
            drifting=drifting,
            summary=summary,
            metadata={"per_test": passed, "per_test_summaries": summaries},
        )

    def last_results(self) -> dict[str, bool]:
        """Return the last :meth:`run`'s per-benchmark pass/fail.

        Returns an empty dict if :meth:`run` has not yet been called
        in this process. :class:`CrossVersionChecker` consumes this
        via the injection point in :class:`DriftDetector`.
        """
        return dict(self._last_results)


class MLStatisticalChecker:
    """Detector 2 of 3: ML statistical drift on solver-output features.

    Phase 6b Step 7 ships the seam + the vendored DriftEnsemble
    adapter; concrete feature collection from solver outputs lands
    in Phase 7. The checker accepts a ``feature_provider`` callable
    that returns the latest feature buffer; if the provider is
    ``None`` or returns an empty array, the checker reports
    ``drifting=False`` with a "no features" summary.

    Per locked OPEN-5 (2026-05-10): vendor ``ml/drift_ensemble.py``
    unchanged from ``C:\\frank-data\\``, wrap with a thin Phoenix
    adapter. This checker imports the vendored
    :class:`DriftEnsemble` lazily and uses only
    :meth:`predict_scale_separated`, which works with stock numpy
    (no sklearn or torch required).
    """

    name = "ml_statistical"

    def __init__(
        self,
        feature_provider: Callable[[], np.ndarray | None] | None = None,
        *,
        drift_snr_threshold: float = 1.0,
    ) -> None:
        self._feature_provider = feature_provider
        self._threshold = drift_snr_threshold

    def run(self) -> CheckerResult:
        if self._feature_provider is None:
            return CheckerResult(
                name=self.name,
                drifting=False,
                summary="no feature provider configured (Phase 7+ wires this)",
                metadata={"skipped": True, "reason": "no_provider"},
            )
        try:
            features = self._feature_provider()
        except Exception as exc:
            return CheckerResult(
                name=self.name,
                drifting=True,
                summary=f"feature provider raised {type(exc).__name__}: {exc}",
                metadata={"error": True},
            )
        if features is None or getattr(features, "size", 0) == 0:
            return CheckerResult(
                name=self.name,
                drifting=False,
                summary="no features available yet",
                metadata={"skipped": True, "reason": "empty_features"},
            )

        try:
            # Vendored module per OPEN-5; path resolved via Phoenix's
            # sys.path injection in phoenix/__init__.py.
            from ml.drift_ensemble import DriftEnsemble
        except ImportError as exc:
            return CheckerResult(
                name=self.name,
                drifting=False,
                summary=f"DriftEnsemble unavailable ({exc})",
                metadata={"skipped": True, "reason": "ml_unavailable"},
            )

        try:
            ensemble = DriftEnsemble()
            result = ensemble.predict_scale_separated(features)
        except Exception as exc:
            return CheckerResult(
                name=self.name,
                drifting=True,
                summary=f"predict_scale_separated raised {type(exc).__name__}: {exc}",
                metadata={"error": True},
            )

        drift_snr = float(result.get("drift_snr", 0.0))
        drifting = drift_snr >= self._threshold
        return CheckerResult(
            name=self.name,
            drifting=drifting,
            summary=(
                f"drift_snr={drift_snr:.4f} (threshold={self._threshold}), "
                f"stationary={result.get('stationary')}"
            ),
            metadata={"predict_scale_separated": result},
        )


class CrossVersionChecker:
    """Detector 3 of 3: compare current Tier-1 results vs prior release.

    Drift criterion: any benchmark that passed in the prior release
    now fails. Pure regressions are drift; new failures of
    historically-failing benchmarks are not (already-known drift).
    No-op on first run (no prior version on disk).

    History layout: ``~/.phoenix/runtime/calibration_history/<phoenix_version>.json``
    -- one file per Phoenix release containing the Tier-1 results
    dict ``{name: passed}``. The checker writes the current run's
    file on successful :meth:`run` (so the next release's first run
    has the prior version's results to compare against).
    """

    name = "cross_version"

    def __init__(
        self,
        current_version: str,
        current_results_provider: Callable[[], dict[str, bool]],
        *,
        history_dir: Path | None = None,
    ) -> None:
        self._version = current_version
        self._results_provider = current_results_provider
        self._history_dir = (
            history_dir
            if history_dir is not None
            else Path.home() / ".phoenix" / "runtime" / "calibration_history"
        )

    def run(self) -> CheckerResult:
        current = self._results_provider() or {}
        if not current:
            return CheckerResult(
                name=self.name,
                drifting=False,
                summary="no current Tier-1 results yet (Tier1AnalyticalChecker must run first)",
                metadata={"skipped": True, "reason": "no_current"},
            )

        prior_path, prior_version, prior_results = self._read_prior_results()
        if prior_results is None:
            self._write_current(current)
            return CheckerResult(
                name=self.name,
                drifting=False,
                summary=f"first run for version {self._version!r}; no prior version on disk",
                metadata={
                    "skipped": True,
                    "reason": "no_prior",
                    "wrote_current": True,
                },
            )

        regressions = [
            name
            for name, passed_now in current.items()
            if not passed_now and prior_results.get(name, False)
        ]
        drifting = bool(regressions)
        # Always persist the current run's results so the next release sees them.
        self._write_current(current)
        if drifting:
            summary = (
                f"{len(regressions)} regression(s) vs prior version "
                f"{prior_version!r}: {', '.join(regressions)}"
            )
        else:
            summary = (
                f"no regressions vs prior version {prior_version!r} ({len(current)} benchmarks)"
            )
        return CheckerResult(
            name=self.name,
            drifting=drifting,
            summary=summary,
            metadata={
                "prior_version": prior_version,
                "regressions": regressions,
                "wrote_current": True,
            },
        )

    def _read_prior_results(
        self,
    ) -> tuple[Path | None, str | None, dict[str, bool] | None]:
        """Find the most recent prior version's results file, if any."""
        if not self._history_dir.exists():
            return None, None, None
        candidates: list[tuple[float, Path, str]] = []
        for entry in self._history_dir.iterdir():
            if not entry.is_file() or entry.suffix != ".json":
                continue
            version = entry.stem
            if version == self._version:
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            candidates.append((mtime, entry, version))
        if not candidates:
            return None, None, None
        candidates.sort(reverse=True)
        _, path, version = candidates[0]
        try:
            with path.open(encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError):
            return path, version, None
        if not isinstance(raw, dict):
            return path, version, None
        # Coerce values to bool defensively.
        parsed = {str(k): bool(v) for k, v in raw.items()}
        return path, version, parsed

    def _write_current(self, results: dict[str, bool]) -> None:
        try:
            self._history_dir.mkdir(parents=True, exist_ok=True)
            target = self._history_dir / f"{self._version}.json"
            tmp = target.with_suffix(".json.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, sort_keys=True)
            tmp.replace(target)
        except OSError:
            logger.exception("Failed to write calibration history for %s", self._version)


# ---------------------------------------------------------------------------
# Orchestrator


class DriftDetector:
    """Drift-detector orchestrator (Phase 6b Step 7).

    Composes three checkers (Tier-1 analytical, ML statistical,
    cross-version) and an optional :class:`StateBackend` for snapshot
    persistence. Cycles can be triggered:

    - **Manually**: call :meth:`run_cycle` synchronously. Returns the
      resulting :class:`DriftSnapshot`.
    - **Scheduled**: call :meth:`start_scheduler` to launch a daemon
      thread that runs cycles every ``cadence_seconds`` (default 6h
      per Decision 17, overrideable via
      ``$PHOENIX_DRIFT_CADENCE_HOURS``). The Phase 6b daemon does NOT
      auto-start this -- launcher scripts opt in.

    The :meth:`register_drift_callback` seam (per locked OPEN-6,
    2026-05-10) lets Phase 6b register the verification gate's
    auto-promote consumer; Phase 7 adds the router intelligence's
    fidelity-rescoring consumer as a second registered callback.

    Snapshot persistence: when a :class:`StateBackend` is configured,
    each successful :meth:`run_cycle` calls
    :meth:`put_drift_state_snapshot` with the snapshot dict. On
    construction, the detector reads the prior snapshot (if any) so
    :meth:`last_snapshot` returns sensible data immediately on
    daemon restart (before the first new cycle runs).
    """

    def __init__(
        self,
        *,
        state_backend: StateBackend | None = None,
        cadence_seconds: int | None = None,
        checkers: list[DriftCheckerProtocol] | None = None,
        phoenix_release: str = "1.0.0.dev9",
        feature_provider: Callable[[], np.ndarray | None] | None = None,
    ) -> None:
        self._backend = state_backend
        self._cadence = (
            cadence_seconds if cadence_seconds is not None else _resolve_cadence_seconds()
        )
        if checkers is not None:
            self._checkers: list[DriftCheckerProtocol] = list(checkers)
        else:
            tier1 = Tier1AnalyticalChecker()
            ml = MLStatisticalChecker(feature_provider=feature_provider)
            cross = CrossVersionChecker(
                current_version=phoenix_release,
                current_results_provider=tier1.last_results,
            )
            self._checkers = [tier1, ml, cross]
        self._callbacks: list[CallbackType] = []
        self._lock = threading.RLock()
        self._last_snapshot: DriftSnapshot | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # Hydrate from backend if available.
        if self._backend is not None:
            try:
                stored = self._backend.get_drift_state_snapshot()
            except Exception:
                logger.exception("Failed to read drift snapshot from state backend")
                stored = None
            if stored is not None:
                self._last_snapshot = _snapshot_from_record(stored)

    def cadence_seconds(self) -> int:
        return self._cadence

    def register_drift_callback(self, callback: CallbackType) -> None:
        """Register a callback fired on every cycle snapshot.

        Phase 6b registers only the verification gate (Section 6.5
        auto-promote). Phase 7 adds the router intelligence
        (Section 4.6 fidelity rescoring) as a second caller.
        Callbacks fire synchronously after snapshot persistence;
        exceptions are caught and logged so a misbehaving callback
        doesn't block the next one.
        """
        with self._lock:
            self._callbacks.append(callback)

    def last_snapshot(self) -> DriftSnapshot | None:
        """Return the most recent :class:`DriftSnapshot`, or ``None``
        if no cycle has run yet AND no prior snapshot was loaded from
        the backend on construction."""
        with self._lock:
            return self._last_snapshot

    def run_cycle(self) -> DriftSnapshot:
        """Run all checkers synchronously and return the snapshot.

        Per Decision 17 the aggregation rule is:

        - 0 firing -> ``state="healthy"``
        - 1 firing -> ``state="warning"``
        - 2+ firing -> ``state="high_confidence_warning"``

        Exceptions raised by individual checkers are caught and
        recorded as firing (fail-closed). The snapshot is persisted
        via the state backend (if configured) and registered
        callbacks are fired in registration order.
        """
        cycle_start = time.time()
        results: list[CheckerResult] = []
        for checker in self._checkers:
            try:
                result = checker.run()
            except Exception as exc:
                logger.exception("Drift checker %r crashed", checker.name)
                result = CheckerResult(
                    name=checker.name,
                    drifting=True,
                    summary=f"checker error: {type(exc).__name__}: {exc}",
                    metadata={"error": True},
                )
            results.append(result)

        firing = [r.name for r in results if r.drifting]
        if not firing:
            state = "healthy"
        elif len(firing) == 1:
            state = "warning"
        else:
            state = "high_confidence_warning"

        snapshot = DriftSnapshot(
            cycle_unix=cycle_start,
            state=state,
            firing_detectors=firing,
            detector_summaries={r.name: r.summary for r in results},
            metadata={r.name: dict(r.metadata) for r in results},
        )

        with self._lock:
            self._last_snapshot = snapshot
            callbacks = list(self._callbacks)

        if self._backend is not None:
            try:
                self._backend.put_drift_state_snapshot(_snapshot_to_record(snapshot))
            except Exception:
                logger.exception("Failed to persist drift snapshot to state backend")

        for cb in callbacks:
            try:
                cb(snapshot)
            except Exception:
                logger.exception("Drift callback raised")

        return snapshot

    def start_scheduler(self) -> None:
        """Start the background cadence thread. Idempotent.

        The first cycle runs immediately on startup UNLESS
        ``$PHOENIX_SKIP_STARTUP_DRIFT_CYCLE=1`` is set (dev mode).
        Subsequent cycles fire every ``cadence_seconds``.
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_scheduler,
                name="phoenix-drift-detector",
                daemon=True,
            )
            self._thread.start()

    def stop_scheduler(self, timeout_seconds: float = 2.0) -> None:
        """Signal the scheduler thread to stop; join up to
        ``timeout_seconds``. Idempotent."""
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout_seconds)

    def _run_scheduler(self) -> None:
        skip_startup = os.environ.get("PHOENIX_SKIP_STARTUP_DRIFT_CYCLE") == "1"
        if not skip_startup:
            try:
                self.run_cycle()
            except Exception:
                logger.exception("Drift startup cycle failed")
        while not self._stop_event.is_set():
            if self._stop_event.wait(timeout=self._cadence):
                return
            try:
                self.run_cycle()
            except Exception:
                logger.exception("Scheduled drift cycle failed")


def _resolve_cadence_seconds() -> int:
    """Read ``$PHOENIX_DRIFT_CADENCE_HOURS`` or fall back to Decision 17
    default (6h)."""
    raw = os.environ.get("PHOENIX_DRIFT_CADENCE_HOURS")
    if not raw:
        return 6 * 60 * 60
    try:
        hours = float(raw)
    except ValueError:
        logger.warning("Invalid PHOENIX_DRIFT_CADENCE_HOURS=%r; falling back to 6h", raw)
        return 6 * 60 * 60
    if hours <= 0:
        logger.warning("Non-positive PHOENIX_DRIFT_CADENCE_HOURS=%s; falling back to 6h", hours)
        return 6 * 60 * 60
    return int(hours * 3600)


def _snapshot_to_record(snapshot: DriftSnapshot) -> dict[str, Any]:
    """Convert :class:`DriftSnapshot` to the dict shape stored via
    :meth:`StateBackend.put_drift_state_snapshot` per Phase 6b
    Step 1's Protocol contract."""
    return {
        "cycle_unix": snapshot.cycle_unix,
        "state": snapshot.state,
        "firing_detectors": list(snapshot.firing_detectors),
        "detector_summaries": dict(snapshot.detector_summaries),
    }


def _snapshot_from_record(record: dict[str, Any]) -> DriftSnapshot:
    """Inverse of :func:`_snapshot_to_record`."""
    return DriftSnapshot(
        cycle_unix=float(record.get("cycle_unix", 0.0)),
        state=str(record.get("state", "healthy")),
        firing_detectors=list(record.get("firing_detectors", [])),
        detector_summaries=dict(record.get("detector_summaries", {})),
        metadata={},
    )


# ---------------------------------------------------------------------------
# Module-level singleton.

_DETECTOR: DriftDetector | None = None
_DETECTOR_LOCK = threading.Lock()


def get_detector() -> DriftDetector:
    """Return the singleton :class:`DriftDetector`.

    Constructs lazily with the configured state backend (via
    :func:`phoenix.state.get_state_backend`). The scheduler is NOT
    started -- callers opt in via :meth:`DriftDetector.start_scheduler`.
    """
    global _DETECTOR
    with _DETECTOR_LOCK:
        if _DETECTOR is None:
            try:
                from phoenix.state import get_state_backend

                backend: StateBackend | None = get_state_backend()
            except Exception:
                logger.exception(
                    "DriftDetector constructed without state backend (state-factory unavailable)"
                )
                backend = None
            _DETECTOR = DriftDetector(state_backend=backend)
        return _DETECTOR


def reset_detector() -> None:
    """Stop the scheduler (if running) and clear the singleton.

    Tests use this for isolation; the FastAPI lifespan shutdown can
    call it for clean teardown.
    """
    global _DETECTOR
    with _DETECTOR_LOCK:
        if _DETECTOR is not None:
            try:
                _DETECTOR.stop_scheduler()
            except Exception:
                logger.exception("Failed to stop drift scheduler on reset")
        _DETECTOR = None
