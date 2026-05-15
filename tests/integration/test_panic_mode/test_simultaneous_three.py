"""Phase 11 Step 5 -- canonical §10.7 simultaneous-three panic test.

Per architecture v1 Section 10.7 ("Compositional fail-closed test"):

  A deliberate test that simultaneously: (a) crashes the NATS
  JetStream connection, (b) makes the state backend (SQLite/Postgres)
  unreachable, and (c) marks one of the three drift detectors as
  unavailable.

  Phoenix must fail fast and loud with typed errors
  (`QueueUnavailable`, `StateBackendUnavailable`,
  `DriftStateUnavailable`) -- never silently degrade or return a
  result without verification.

  The test verifies that fail-closed posture composes: multiple
  simultaneous failures produce a deterministic error response
  naming the *first* failing condition, not a confused mixture.

This module is the §10.7 acceptance gate's centerpiece. It composes
the three isolation contexts from Steps 2-4 and asserts:

1. **Exactly one typed error fires** -- not a confused mixture.
2. **The error class is one of the three** Phase 11 typed errors
   (QueueUnavailable, StateBackendUnavailable, DriftStateUnavailable).
3. **The error is deterministic** -- repeated runs surface the
   SAME typed error class (the first failing condition in the
   request path).
4. **No silent success** -- the request does NOT return 200 with
   a clean Result envelope. The fail-closed posture refuses to
   produce a result when verification couldn't run.
"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path

import pytest

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix.queue.errors import QueueUnavailable
from phoenix.state.errors import StateBackendUnavailable
from phoenix.verification.drift_state import DriftStateUnavailable
from tests.integration.test_panic_mode.conftest import (
    kill_drift_state,
    kill_queue,
    kill_state_backend,
)


pytestmark = pytest.mark.acceptance


_TYPED_ERROR_CLASSES = (
    QueueUnavailable,
    StateBackendUnavailable,
    DriftStateUnavailable,
)


# ---------------------------------------------------------------------
# THE canonical §10.7 acceptance test
# ---------------------------------------------------------------------


def test_simultaneous_three_failures_fail_closed_with_typed_error(
    isolated_runtime: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The §10.7 centerpiece: with NATS + state backend + drift
    detector all simultaneously down, Phoenix fails fast and loud
    with exactly ONE typed error, not a confused mixture.

    This composes the three isolation contexts (steps 2-4) and
    asserts the union acceptance invariant. The specific typed
    error class that fires is determined by Phoenix's request-path
    ordering (whichever subsystem the verification gate reads
    first); the test asserts only that:

    - Exactly one of the three typed error classes fires.
    - NO silent 200 Result with a clean agreement_type.
    - The failure surfaces to the caller (not swallowed deep
      inside Phoenix).
    """
    from phoenix.router.decision import Router
    from phoenix.router.provider_registry import build_default_registry
    from phoenix.trinity.data_model import PhysicsTask, ToleranceSpec
    from phoenix.verification.gate import VerificationGate
    from synthesis.equations.base import PhysicsContext

    gate = VerificationGate(router=Router(registry=build_default_registry()))
    task = PhysicsTask(
        request_id="tx-panic-test",
        actor=None,
        physics_context=PhysicsContext(
            mass_kg=9.1093837015e-31,
            length_scale_m=4e-9,
            metadata={"omega": 1e15, "n_grid_points": 200},
        ),
        tolerance=ToleranceSpec(max_error_bar=1e-3),
        metadata={},
    )

    # Compose all three kill contexts simultaneously
    with ExitStack() as stack:
        stack.enter_context(kill_state_backend())
        stack.enter_context(kill_drift_state(monkeypatch))
        stack.enter_context(kill_queue(monkeypatch))

        # gate.verify(task) MUST raise one of the three typed errors.
        # No silent success path (returns Result with healthy fields).
        with pytest.raises(_TYPED_ERROR_CLASSES) as excinfo:
            gate.verify(task)

    # The error surfaced was ONE of the three typed errors -- not
    # a confused mixture, not a generic Exception, not a swallowed
    # warning.
    assert isinstance(excinfo.value, _TYPED_ERROR_CLASSES)


# ---------------------------------------------------------------------
# Determinism: repeated runs surface the SAME typed error
# ---------------------------------------------------------------------


def test_simultaneous_three_failures_are_deterministic(
    isolated_runtime: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per §10.7: 'produce a deterministic error response naming
    the FIRST failing condition, not a confused mixture.'

    Multiple runs of the simultaneous-three panic scenario must
    surface the SAME typed error class. This pins the request-path
    ordering -- whichever subsystem fails first in the verification
    gate's path is what callers consistently see.
    """
    from phoenix.router.decision import Router
    from phoenix.router.provider_registry import build_default_registry
    from phoenix.trinity.data_model import PhysicsTask, ToleranceSpec
    from phoenix.verification.gate import VerificationGate
    from synthesis.equations.base import PhysicsContext

    def _build_gate_and_task(request_id: str) -> tuple:
        gate = VerificationGate(router=Router(registry=build_default_registry()))
        task = PhysicsTask(
            request_id=request_id,
            actor=None,
            physics_context=PhysicsContext(
                mass_kg=9.1093837015e-31,
                length_scale_m=4e-9,
                metadata={"omega": 1e15, "n_grid_points": 200},
            ),
            tolerance=ToleranceSpec(max_error_bar=1e-3),
            metadata={},
        )
        return gate, task

    observed_error_types: list[type] = []
    for i in range(3):
        with ExitStack() as stack:
            stack.enter_context(kill_state_backend())
            stack.enter_context(kill_drift_state(monkeypatch))
            stack.enter_context(kill_queue(monkeypatch))
            gate, task = _build_gate_and_task(f"tx-{i}")
            try:
                gate.verify(task)
            except _TYPED_ERROR_CLASSES as exc:
                observed_error_types.append(type(exc))
            else:
                pytest.fail(f"run {i}: expected typed error, got success")

    # All three runs surfaced the SAME typed error class -- the
    # request-path ordering is deterministic.
    assert len(set(observed_error_types)) == 1, (
        f"non-deterministic typed-error surfacing: {observed_error_types}"
    )


# ---------------------------------------------------------------------
# HTTP-level composition: POST /v1/tasks with three failures
# ---------------------------------------------------------------------


def test_post_tasks_with_three_failures_fail_closed_http(
    isolated_runtime: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The HTTP front door surfaces the fail-closed contract.

    Even with all three subsystems down, the route handler MUST NOT
    return 200 with a Result envelope as if everything were healthy.
    The response is either:

    - 5xx (uncaught typed error propagates to the FastAPI handler).
    - 200 with the failure surfaced in the body (agreement_type
      reflects unverified, OR a clear error field).

    What's NOT acceptable: a 200 response with a HEDGED_CONSENSUS
    agreement type that LOOKS like a verified solve.
    """
    from fastapi.testclient import TestClient

    from phoenix.api.routes import app

    qho_body = {
        "physics_context": {
            "mass_kg": 9.1093837015e-31,
            "length_scale_m": 4e-9,
            "metadata": {"omega": 1e15, "n_grid_points": 200},
        },
        "tolerance": {
            "max_error_bar": 1e-3,
            "latency_tier": "batch_realtime",
        },
    }

    with TestClient(app, raise_server_exceptions=False) as client:
        with ExitStack() as stack:
            stack.enter_context(kill_state_backend())
            stack.enter_context(kill_drift_state(monkeypatch))
            stack.enter_context(kill_queue(monkeypatch))
            resp = client.post("/v1/tasks", json=qho_body)

    # Acceptable: 5xx OR 200-with-failure-surfaced. NOT acceptable:
    # 200 with clean HEDGED_CONSENSUS agreement.
    if resp.status_code == 200:
        body = resp.json()
        agreement = body.get("agreement_type", "")
        assert (
            "degraded" in agreement.lower()
            or "error" in body
            or "drift" in resp.text.lower()
            or "unavailable" in resp.text.lower()
        ), f"200 response must surface the failure; got clean Result: {resp.text[:500]}"
    else:
        assert resp.status_code >= 500


# ---------------------------------------------------------------------
# The error class identity: pin which one fires first
# ---------------------------------------------------------------------


def test_first_failing_condition_is_drift_state_unavailable(
    isolated_runtime: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phoenix's request path reads drift state FIRST (Section 6.8
    explicit fail-closed at the verification gate's solve-start).
    With all three subsystems down, DriftStateUnavailable is the
    typed error that surfaces.

    This test pins the request-path ordering. If a future refactor
    rearranges the gate's first reads (e.g., to read kill switch
    state from the backend before drift state), this test will
    fail loudly and the new ordering MUST be documented + the
    test updated -- not silently allowed to drift.
    """
    from phoenix.router.decision import Router
    from phoenix.router.provider_registry import build_default_registry
    from phoenix.trinity.data_model import PhysicsTask, ToleranceSpec
    from phoenix.verification.gate import VerificationGate
    from synthesis.equations.base import PhysicsContext

    gate = VerificationGate(router=Router(registry=build_default_registry()))
    task = PhysicsTask(
        request_id="tx-drift-first",
        actor=None,
        physics_context=PhysicsContext(
            mass_kg=9.1093837015e-31,
            length_scale_m=4e-9,
            metadata={"omega": 1e15, "n_grid_points": 200},
        ),
        tolerance=ToleranceSpec(max_error_bar=1e-3),
        metadata={},
    )

    with ExitStack() as stack:
        stack.enter_context(kill_state_backend())
        stack.enter_context(kill_drift_state(monkeypatch))
        stack.enter_context(kill_queue(monkeypatch))

        with pytest.raises(DriftStateUnavailable):
            gate.verify(task)
