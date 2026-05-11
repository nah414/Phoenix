"""Reproducibility-mode helpers (Phase 7 Step 7).

Per architecture v1 Section 1 Decisions 19-21: Phoenix's
:class:`~phoenix.trinity.data_model.PhysicsTask.tolerance.reproducibility_mode`
field selects one of three behaviors:

- ``default`` -- no environment capture; the solve proceeds as in
  Phase 5/6. PERF: 0 overhead.
- ``strict`` -- before the solve runs, the deterministic environment
  is captured + pinned (numpy RNG state, BLAS thread counts,
  PYTHONHASHSEED). The captured snapshot lands in the ledger entry
  so a future replay can restore it bit-exactly. PERF: 15-30%
  wall-clock cost from forcing BLAS to single thread per Decision 20.
- ``replay`` -- strict mode plus the verification gate re-runs the
  deterministic portion of the pipeline immediately after the
  original solve and verifies the result_hash matches. PERF: 2x
  wall-clock per Decision 19. Phase 7 Step 7 lands the env capture +
  ledger-entry recording; Step 8 lands the actual re-run logic in
  :mod:`phoenix.ledger.replay_engine`.

This module deliberately captures only what Phoenix can restore
reliably across Python versions + platforms:

- numpy random state (serialized via :func:`np.random.get_state`)
- ``OMP_NUM_THREADS`` / ``MKL_NUM_THREADS`` / ``OPENBLAS_NUM_THREADS``
  environment variables
- ``PYTHONHASHSEED`` environment variable

What we do NOT capture (per Decision 20 + the 2026-05-11 OPEN-5 lock):

- Hardware floating-point environment (rounding mode, denormals).
  Python's :mod:`fpectl` is removed; getting this portably requires
  platform-specific code we'd have to maintain forever. The replay
  test long-window (Section 10.7) on x86_64 Linux/macOS/Windows
  shows bit-exact match without this, so v1 ships without.
- CPython GC state. Solves don't allocate enough to be GC-sensitive
  in practice; v1.x revisits if a real divergence is observed.
- Process clock. Wall-clock is recorded as informational metadata
  but doesn't enter the deterministic re-execution path.

**Runtime BLAS limitation (documented):** changing
``$OMP_NUM_THREADS`` at runtime via :func:`os.environ` affects
**new** thread pools created after the change but does NOT resize
already-loaded BLAS pools. A truly strict-mode pin requires either
process-restart with the env var set ahead of import, or the
``threadpoolctl`` library to dynamically resize the loaded pool.
v1 documents this limitation in the strict-mode docstring and
records what the *current* env vars say at solve start; v1.x can
add threadpoolctl as an opt-in dependency for tighter pinning.

The captured :class:`EnvSnapshot` is JSON-serializable via
:meth:`EnvSnapshot.to_dict` so it lands cleanly inside a
:class:`SolveEntry.environment_snapshot` field on the ledger.
"""

from __future__ import annotations

import contextlib
import logging
import os
import platform
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# Environment variable names the snapshot captures. Each is optional;
# missing variables serialize as None so the snapshot dict shape is
# stable across captures.
_BLAS_ENV_VARS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")
_HASH_SEED_ENV = "PYTHONHASHSEED"


@dataclass(frozen=True)
class EnvSnapshot:
    """Deterministic-environment capture for replay verification.

    Fields:

    - ``numpy_random_state`` -- serialized output of
      :func:`np.random.get_state` (a tuple). Stored as a JSON-safe
      dict so it round-trips through the ledger's payload_json.
      ``None`` if numpy isn't importable.
    - ``thread_env_vars`` -- dict mapping each captured BLAS env var
      name to its string value at capture time, or ``None`` when the
      var is unset.
    - ``python_hash_seed`` -- value of ``$PYTHONHASHSEED`` at capture
      time, or ``None`` when unset. Affects dict iteration order +
      hash() output for str/bytes/datetime; can break replays of
      code that depends on those.
    - ``platform_info`` -- informational only; not used for restoration.
      Captures Python version + platform string so a replay-divergence
      report can highlight platform mismatches.
    """

    numpy_random_state: dict[str, Any] | None = None
    thread_env_vars: dict[str, str | None] = field(default_factory=dict)
    python_hash_seed: str | None = None
    platform_info: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-safe dict for ledger serialization."""
        return {
            "numpy_random_state": self.numpy_random_state,
            "thread_env_vars": dict(self.thread_env_vars),
            "python_hash_seed": self.python_hash_seed,
            "platform_info": dict(self.platform_info),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EnvSnapshot:
        """Reconstruct from a JSON-decoded dict (the inverse of to_dict)."""
        return cls(
            numpy_random_state=data.get("numpy_random_state"),
            thread_env_vars=dict(data.get("thread_env_vars") or {}),
            python_hash_seed=data.get("python_hash_seed"),
            platform_info=dict(data.get("platform_info") or {}),
        )


def _serialize_numpy_state() -> dict[str, Any] | None:
    """Capture :func:`np.random.get_state` in JSON-safe form.

    The numpy random-state tuple is ``(name, keys, pos, has_gauss,
    cached_gaussian)``. We serialize ``keys`` (uint32 ndarray) to a
    plain list so the snapshot survives a JSON round-trip; restoration
    rebuilds the ndarray from the list.

    Returns ``None`` if numpy isn't importable (Phoenix's vendored
    substrate hard-depends on it, but the helper stays defensive).
    """
    try:
        import numpy as np
    except ImportError:
        return None
    name, keys, pos, has_gauss, cached_gaussian = np.random.get_state()
    return {
        "name": name,
        "keys": [int(k) for k in keys],
        "pos": int(pos),
        "has_gauss": int(has_gauss),
        "cached_gaussian": float(cached_gaussian),
    }


def _restore_numpy_state(state: dict[str, Any] | None) -> None:
    """Restore numpy's global random state from a serialized snapshot.

    No-op when ``state`` is None (the original solve ran without
    numpy, which Phoenix v1 never does, but defensive).
    """
    if state is None:
        return
    try:
        import numpy as np
    except ImportError:
        return
    keys = np.array(state["keys"], dtype=np.uint32)
    np.random.set_state(
        (
            state["name"],
            keys,
            state["pos"],
            state["has_gauss"],
            state["cached_gaussian"],
        )
    )


def capture_environment() -> EnvSnapshot:
    """Capture the current deterministic environment.

    Reads numpy's global random state, the BLAS thread env vars, and
    PYTHONHASHSEED. The snapshot is what Phoenix records in the
    ledger entry's ``environment_snapshot`` field; the replay engine
    (Phase 7 Step 8) reads it back and calls
    :func:`restore_environment` before re-executing the solve.

    Always succeeds; never raises (defensive against numpy not
    being available, etc.).
    """
    thread_vars: dict[str, str | None] = {name: os.environ.get(name) for name in _BLAS_ENV_VARS}
    plat: dict[str, str] = {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
    }
    return EnvSnapshot(
        numpy_random_state=_serialize_numpy_state(),
        thread_env_vars=thread_vars,
        python_hash_seed=os.environ.get(_HASH_SEED_ENV),
        platform_info=plat,
    )


def restore_environment(snap: EnvSnapshot) -> None:
    """Restore the deterministic environment from a snapshot.

    Mutates global state:

    - Calls :func:`np.random.set_state` with the captured RNG state.
    - Writes the captured thread env vars to :data:`os.environ`.
      **Caveat:** this affects new BLAS thread pools created after
      the call but does NOT resize already-loaded pools. See the
      module docstring for the v1.x threadpoolctl alternative.
    - Writes :data:`PYTHONHASHSEED` to :data:`os.environ` (similar
      caveat: it affects new subprocesses, not the running
      interpreter's already-finalized hash randomization).

    The replay engine calls this immediately before re-running the
    deterministic portion of the pipeline.
    """
    _restore_numpy_state(snap.numpy_random_state)
    for name, value in snap.thread_env_vars.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    if snap.python_hash_seed is None:
        os.environ.pop(_HASH_SEED_ENV, None)
    else:
        os.environ[_HASH_SEED_ENV] = snap.python_hash_seed


@contextlib.contextmanager
def deterministic_environment(snap: EnvSnapshot) -> Iterator[None]:
    """Context manager: restore ``snap`` on enter, restore prior env on exit.

    Used by the replay engine to scope environment restoration to
    just the re-execution window. Outside the ``with`` block the
    daemon's environment is unchanged, so a replay request from one
    user doesn't perturb subsequent live solves.

    Usage::

        with deterministic_environment(snap_from_ledger):
            result = solve(task)  # re-runs with captured env
    """
    prior = capture_environment()
    restore_environment(snap)
    try:
        yield
    finally:
        try:
            restore_environment(prior)
        except Exception:
            logger.exception("Failed to restore prior environment on context exit")


def pin_single_thread_blas() -> None:
    """Set BLAS thread env vars to ``"1"`` for strict-mode determinism.

    Per Decision 20: strict reproducibility requires single-thread
    BLAS to eliminate the non-determinism from parallel reductions
    (Kahan summation order isn't fixed when threads race). Phoenix
    sets the env vars but acknowledges they only fully affect new
    pools; v1.x adds threadpoolctl for live pool resizing.

    The strict-mode solve path calls this AFTER
    :func:`capture_environment` so the snapshot in the ledger
    reflects the user's pre-pin env (the replay restores the same
    env, then re-pins to single thread before re-executing).
    """
    for name in _BLAS_ENV_VARS:
        os.environ[name] = "1"


__all__ = [
    "EnvSnapshot",
    "capture_environment",
    "deterministic_environment",
    "pin_single_thread_blas",
    "restore_environment",
]
