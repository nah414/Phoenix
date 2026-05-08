"""Trinity Core Solver subsystem -- engine adapter.

Wraps the vendored ``EquationSolver`` registry (``synthesis.equations.registry``)
into Phoenix's :class:`PhysicsTask` -> :class:`CandidateAnswer` flow per
architecture v1 Section 2.3.

Phase 2 scope:

- :func:`_get_registry` lazy-loads + caches the vendored registry instance.
- :func:`pick_solver` dispatches a :class:`PhysicsTask` via the vendored
  ``HamiltonianClassifier`` (``registry.classify``) -- each registered solver
  reports ``can_handle(ctx) -> (bool, confidence)`` and the registry picks
  the highest-confidence match. ``task.metadata["regime_hint"]`` overrides
  the classifier when present.
- :func:`run_solver` invokes the dispatched solver at a specified grid
  resolution and returns a typed :class:`SolverRunResult` (eigenvalues,
  wall-clock, classifier rationale).
- Frontier-physics regime gate raises :class:`FrontierPhysicsRefused` when
  Wheeler-DeWitt / Gravitational Decoherence / Semiclassical Gravity is
  dispatched without ``task.tolerance.frontier_physics``. Per architecture
  Section 1 Decision 7. Phase 2 ships the engine-boundary check; Phase 6
  wires the full Actor-based capability check at the safety gate (the
  defense-in-depth layer above this one).
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from phoenix.trinity.data_model import PhysicsTask

if TYPE_CHECKING:
    from synthesis.equations.base import (
        EquationRegime,
        EquationSolver,
    )
    from synthesis.equations.registry import EquationRegistry


# Frontier-physics regime set per architecture v1 Section 1 Decision 7.
# Listed by enum-name strings rather than imported enum values to avoid a
# vendor-import-at-module-load on fresh clones (where vendor/ is not yet
# populated). The engine resolves regime names at call time when the
# vendored registry is loaded.
FRONTIER_REGIME_NAMES: frozenset[str] = frozenset(
    {
        "WHEELER_DEWITT",
        "GRAVITATIONAL_DECOHERENCE",
        "SEMICLASSICAL_GRAVITY",
    }
)


# ---------------------------------------------------------------------------
# Typed errors


class SolverEngineError(Exception):
    """Base for Trinity Core Solver engine failures."""


class NoEligibleSolverError(SolverEngineError):
    """The registry could not produce a solver for this :class:`PhysicsTask`.

    Raised when:
    - ``task.metadata["regime_hint"]`` is invalid or missing from the registry.
    - The classifier selects a regime but ``get_solver`` returns ``None``.

    Per Section 2.3, the vendored registry's ``classify`` falls back to
    ``NON_RELATIVISTIC_TD`` when no specialized solver matches, so this
    exception fires only when the fallback solver is itself absent (i.e.
    the registry is broken or partial).
    """


class FrontierPhysicsRefused(SolverEngineError):
    """A frontier-physics regime was dispatched without permission.

    Per architecture v1 Section 1 Decision 7: Wheeler-DeWitt, Gravitational
    Decoherence, and Semiclassical Gravity solvers are gated by an explicit
    capability flag. Phase 2 ships the engine-boundary check; Phase 6 adds
    the safety-gate check that fires earlier in the pipeline.
    """

    def __init__(self, regime_name: str, reason: str) -> None:
        super().__init__(reason)
        self.regime_name = regime_name


# ---------------------------------------------------------------------------
# Typed result


@dataclass(frozen=True)
class SolverRunResult:
    """One solver invocation's structured output.

    Phase 2 Step 4's cross-precision wobble logic compares two
    :class:`SolverRunResult` instances (low-grid vs high-grid) to compute
    ``error_bar_solver``.
    """

    regime: EquationRegime
    solver_class_name: str
    n_grid_points: int
    eigenvalues: list[float]
    energy: float | None
    wall_clock_ms: float
    classifier_confidence: float
    classifier_reasoning: str


# ---------------------------------------------------------------------------
# Registry lazy load + cache

_registry_cache: EquationRegistry | None = None


def _get_registry() -> EquationRegistry:
    """Lazy-load + cache the vendored equation-solver registry.

    ``auto_register_all`` returns the vendored module-level singleton; repeat
    calls are idempotent. We cache the reference at this module's level so
    the function-level lookup happens once per Phoenix process.
    """
    global _registry_cache
    if _registry_cache is None:
        from synthesis.equations.registry import auto_register_all

        _registry_cache = auto_register_all()
    return _registry_cache


# ---------------------------------------------------------------------------
# Public engine API


def pick_solver(
    task: PhysicsTask,
) -> tuple[EquationSolver, EquationRegime, float, str]:
    """Dispatch a :class:`PhysicsTask` to the right vendored solver.

    Behavior:

    1. If ``task.metadata["regime_hint"]`` is present, treat it as an
       explicit override -- resolve the string to an :class:`EquationRegime`
       and call ``registry.get_solver`` directly. Confidence reported as 1.0.
    2. Otherwise, call ``registry.classify(physics_context)`` -- the vendored
       classifier asks each solver's ``can_handle`` method and picks the
       highest-confidence match.
    3. If the dispatched regime is in the frontier-physics set
       (Wheeler-DeWitt / Gravitational Decoherence / Semiclassical Gravity)
       AND ``task.tolerance.frontier_physics`` is False, raise
       :class:`FrontierPhysicsRefused`.

    Returns ``(solver, regime, confidence, reasoning)`` -- a 4-tuple suitable
    for both :func:`run_solver` and ``SolverProvenance`` construction.
    """
    registry = _get_registry()

    regime_hint = task.metadata.get("regime_hint") if task.metadata else None
    if regime_hint is not None:
        from synthesis.equations.base import EquationRegime as _EquationRegime

        if isinstance(regime_hint, str):
            try:
                regime = _EquationRegime[regime_hint]
            except KeyError as exc:
                raise NoEligibleSolverError(
                    f"regime_hint {regime_hint!r} is not a valid EquationRegime "
                    f"(valid names: {[r.name for r in _EquationRegime]})"
                ) from exc
        else:
            regime = regime_hint
        solver = registry.get_solver(regime)
        if solver is None:
            raise NoEligibleSolverError(
                f"regime_hint {regime.name} requested but no solver "
                f"registered for it (registry has "
                f"{[r.name for r in registry.available_regimes()]})"
            )
        confidence = 1.0
        reasoning = f"Selected by regime_hint override: {regime.name}"
    else:
        regime, confidence, reasoning = registry.classify(task.physics_context)
        solver = registry.get_solver(regime)
        if solver is None:
            raise NoEligibleSolverError(
                f"Registry classified regime {regime.name} but get_solver returned None"
            )

    if regime.name in FRONTIER_REGIME_NAMES and not task.tolerance.frontier_physics:
        raise FrontierPhysicsRefused(
            regime_name=regime.name,
            reason=(
                f"Regime {regime.name} requires frontier_physics permission "
                f"(architecture Section 1 Decision 7). Set "
                f"task.tolerance.frontier_physics=True to opt in. Phase 6 "
                f"will wire the safety-gate Actor-based capability check above this layer."
            ),
        )

    return solver, regime, confidence, reasoning


def run_solver(
    task: PhysicsTask,
    n_grid: int,
    n_states: int = 5,
) -> SolverRunResult:
    """Run the dispatched solver at the specified grid resolution.

    The vendored ``PhysicsContext`` carries ``metadata["n_grid_points"]``
    that drives the solver's grid construction. To run at a different grid,
    we make a fresh context via :func:`dataclasses.replace` with overridden
    metadata; the original ``task.physics_context`` is not mutated.

    Wall-clock time is captured around ``solve_stationary`` for solver-side
    performance measurement (lands in :class:`SolverProvenance`).
    """
    solver, regime, confidence, reasoning = pick_solver(task)

    ctx = task.physics_context
    overridden_metadata = {**ctx.metadata, "n_grid_points": n_grid}
    ctx_grid = dataclasses.replace(ctx, metadata=overridden_metadata)

    t_start = time.perf_counter()
    result = solver.solve_stationary(ctx_grid, n_states=n_states)
    wall_clock_ms = (time.perf_counter() - t_start) * 1000.0

    eigenvalues_raw = result.eigenvalues
    eigenvalues = [float(e) for e in eigenvalues_raw] if eigenvalues_raw is not None else []
    energy = float(result.energy) if result.energy is not None else None

    return SolverRunResult(
        regime=regime,
        solver_class_name=type(solver).__name__,
        n_grid_points=n_grid,
        eigenvalues=eigenvalues,
        energy=energy,
        wall_clock_ms=wall_clock_ms,
        classifier_confidence=confidence,
        classifier_reasoning=reasoning,
    )
