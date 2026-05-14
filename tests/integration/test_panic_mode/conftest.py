"""Shared fixtures for panic-mode tests (Phase 11).

Per architecture v1 Section 10.7 acceptance: the panic-mode tests
exercise each subsystem's fail-closed contract by FORCING the
subsystem into an unavailable state, then asserting Phoenix raises
the typed error rather than silently degrading.

The fixtures here compose:

- ``isolated_runtime`` -- per-test fresh runtime (env-vars pinned
  to tmp_path, all singletons reset). Matches the Phase 8/9/10
  pattern.
- ``kill_state_backend`` -- context manager that closes the state
  backend singleton; operations after enter raise
  :class:`StateBackendUnavailable`.
- ``kill_drift_state`` -- context manager that monkeypatches
  :func:`read_drift_state` to raise :class:`DriftStateUnavailable`.
- ``kill_queue`` -- context manager that monkeypatches the queue
  publish path to raise :class:`QueueUnavailable`.

Each context manager restores cleanly on exit so tests don't leak
the broken state into subsequent tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

import phoenix  # noqa: F401  -- triggers sys.path injection


# ---------------------------------------------------------------------
# Per-test isolated runtime
# ---------------------------------------------------------------------


@pytest.fixture
def isolated_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[Path]:
    """Per-test fresh Phoenix runtime with all singletons reset."""
    runtime = tmp_path / "phoenix_runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PHOENIX_SQLITE_DB_PATH", str(runtime / "state.db"))
    monkeypatch.setenv("PHOENIX_AUDIT_DIR", str(runtime / "audit"))
    monkeypatch.setenv("PHOENIX_KILL_SWITCH_PATH", str(runtime / "kill_switch.json"))
    monkeypatch.setenv("PHOENIX_KEYSTORE_DIR", str(runtime / "keystore"))
    (runtime / "keystore").mkdir(parents=True, exist_ok=True)

    from phoenix._internal.cloud_seams import reset_seams
    from phoenix.audit import reset_emitter
    from phoenix.ledger import reset_ledger
    from phoenix.safety import kill_switch as ks_module
    from phoenix.safety import permissions as permissions_module
    from phoenix.safety.rate_limiter import get_limiter
    from phoenix.state import reset_state_backend

    reset_emitter()
    reset_ledger()
    reset_state_backend()
    ks_module._STORE = None
    get_limiter().reset_all()
    permissions_module._REGISTRY = None
    reset_seams()
    try:
        yield runtime
    finally:
        reset_emitter()
        reset_ledger()
        reset_state_backend()
        ks_module._STORE = None
        get_limiter().reset_all()
        permissions_module._REGISTRY = None
        reset_seams()


# ---------------------------------------------------------------------
# Subsystem kill context managers
# ---------------------------------------------------------------------


@contextmanager
def kill_state_backend() -> Iterator[None]:
    """Close the state backend singleton; subsequent operations raise.

    The Phase 11 ``_require_conn`` path raises
    :class:`StateBackendUnavailable` when the connection has been
    closed. This context manager closes the current backend, yields
    (so the test body exercises the failure path), then resets the
    singleton so the next test starts clean.
    """
    from phoenix.state import get_state_backend, reset_state_backend

    backend = get_state_backend()
    backend.close()
    try:
        yield
    finally:
        reset_state_backend()


@contextmanager
def kill_drift_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Monkeypatch :func:`read_drift_state` to raise.

    Per Section 6.8 + Phase 5: the verification gate reads drift
    state at solve-start and fails-closed on
    :class:`DriftStateUnavailable`. This context manager simulates
    a drift-detector outage by raising the typed error on any read.
    """
    from phoenix.verification.drift_state import DriftStateUnavailable

    def _raising_read() -> Any:
        raise DriftStateUnavailable(
            "synthetic drift-state outage for panic-mode test",
            unavailable_detectors=["tier1_analytical"],
        )

    # The gate imports `read_drift_state` into its own namespace
    # (Phase 8 Step 5 monkeypatching gotcha) -- patch BOTH the
    # source module AND the gate's local binding.
    monkeypatch.setattr("phoenix.verification.drift_state.read_drift_state", _raising_read)
    monkeypatch.setattr("phoenix.verification.gate.read_drift_state", _raising_read)
    yield


@contextmanager
def kill_queue(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Monkeypatch the NATS-backed queue's publish path to raise.

    Simulates a NATS JetStream connection loss. The §10.7 panic
    test asserts that operations consuming the queue surface
    :class:`QueueUnavailable` rather than leaking driver-level
    errors or silently falling back to an in-memory broker.

    For tests that don't actually instantiate a NATS client, this
    context manager monkeypatches the TaskQueue.publish_submit
    method directly on the class so any instance hits the typed
    error on first call.
    """
    from phoenix.queue.errors import QueueUnavailable
    from phoenix.queue.task_queue import TaskQueue

    async def _raising_publish(self: Any, latency_tier: str, payload: bytes) -> None:
        raise QueueUnavailable(
            "synthetic NATS connection loss for panic-mode test",
            subject=f"phoenix.tasks.submit.{latency_tier}",
        )

    monkeypatch.setattr(TaskQueue, "publish_submit", _raising_publish)
    yield


__all__ = [
    "isolated_runtime",
    "kill_drift_state",
    "kill_queue",
    "kill_state_backend",
]
