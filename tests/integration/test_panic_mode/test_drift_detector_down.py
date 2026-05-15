"""Phase 11 Step 4 -- drift-detector-down isolation test (§10.7 acceptance).

Per architecture v1 Section 10.7 + Section 6.8: when one of the
three drift detectors (Section 1 Decision 17: Tier-1 analytical,
ML statistical, cross-version) cannot report, the verification
gate fails-closed with :class:`DriftStateUnavailable` rather than
silently proceeding with a stale or fabricated "healthy" state.

This module pins the drift-state portion of that contract:

1. :func:`read_drift_state` raises
   :class:`DriftStateUnavailable` when the underlying telemetry
   reports unavailability. The Step 1 ``kill_drift_state`` fixture
   simulates this by monkeypatching the function to raise.
2. The verification gate's ``verify()`` reads drift state at
   solve-start (Section 6.8 fail-closed) and propagates the
   typed error -- no silent fallback to default "healthy".
3. ``isinstance(exc, DriftStateUnavailable)`` passes for
   typed-aware callers.
4. The ``unavailable_detectors`` attribute carries the names of
   the detectors that failed, so operators know which subsystem
   to investigate.
5. HTTP integration: ``POST /v1/tasks`` with drift state down
   surfaces 5xx (or otherwise fails); never silently returns 200
   with a Result that wasn't actually verified.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix.verification.drift_state import DriftStateUnavailable
from tests.integration.test_panic_mode.conftest import kill_drift_state


pytestmark = pytest.mark.acceptance


# ---------------------------------------------------------------------
# 1. read_drift_state surfaces the typed error under kill_drift_state
# ---------------------------------------------------------------------


class TestReadDriftStateFailClosed:
    def test_kill_fixture_makes_read_raise(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Module-attribute access so the monkeypatch takes effect
        # (Phase 8 Step 5 gotcha re: local from-import bindings).
        from phoenix.verification import drift_state as drift_module

        # Healthy before
        before = drift_module.read_drift_state()
        assert before.state == "healthy"

        with kill_drift_state(monkeypatch):
            with pytest.raises(DriftStateUnavailable) as excinfo:
                drift_module.read_drift_state()
            assert "tier1_analytical" in excinfo.value.unavailable_detectors

    def test_unavailable_detectors_attribute_populated(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The typed error names which detector(s) failed -- operators
        reading the log see the actionable subsystem.
        """
        from phoenix.verification import drift_state as drift_module

        with kill_drift_state(monkeypatch):
            try:
                drift_module.read_drift_state()
            except DriftStateUnavailable as exc:
                assert isinstance(exc.unavailable_detectors, list)
                assert len(exc.unavailable_detectors) > 0


# ---------------------------------------------------------------------
# 2. Verification gate fails-closed at solve-start (Section 6.8)
# ---------------------------------------------------------------------


class TestVerificationGateFailClosed:
    def test_gate_verify_surfaces_typed_error(
        self,
        isolated_runtime: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The gate reads drift state FIRST per Section 6.8; when
        unavailable, the gate refuses to proceed and the typed error
        propagates to the caller (pipeline.solve, ultimately the
        front door).
        """
        from phoenix.router.decision import Router
        from phoenix.router.provider_registry import build_default_registry
        from phoenix.trinity.data_model import PhysicsTask, ToleranceSpec
        from phoenix.verification.gate import VerificationGate
        from synthesis.equations.base import PhysicsContext

        gate = VerificationGate(router=Router(registry=build_default_registry()))
        task = PhysicsTask(
            request_id="tx-drift-test",
            actor=None,
            physics_context=PhysicsContext(
                mass_kg=9.1093837015e-31,
                length_scale_m=4e-9,
                metadata={"omega": 1e15, "n_grid_points": 200},
            ),
            tolerance=ToleranceSpec(max_error_bar=1e-3),
            metadata={},
        )

        with kill_drift_state(monkeypatch):
            with pytest.raises(DriftStateUnavailable):
                gate.verify(task)


# ---------------------------------------------------------------------
# 3. Typed-error discipline
# ---------------------------------------------------------------------


class TestTypedErrorDiscipline:
    def test_isinstance_check_passes(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from phoenix.verification import drift_state as drift_module

        with kill_drift_state(monkeypatch):
            try:
                drift_module.read_drift_state()
            except Exception as exc:
                assert isinstance(exc, DriftStateUnavailable)
            else:
                pytest.fail("expected DriftStateUnavailable, got nothing")


# ---------------------------------------------------------------------
# 4. No silent fallback to default "healthy"
# ---------------------------------------------------------------------


def test_no_silent_healthy_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When read_drift_state raises, callers MUST NOT receive a
    fabricated ``DriftState(state="healthy")`` as a default. The
    typed error must propagate; the §10.7 "never silently degrade"
    invariant.

    This test pins the contract that any future "make drift state
    optional" refactor MUST raise rather than silently substituting
    a healthy default.
    """
    from phoenix.verification import drift_state as drift_module

    # Sanity: healthy before
    pre = drift_module.read_drift_state()
    assert pre.state == "healthy"

    with kill_drift_state(monkeypatch):
        # Must NOT return a healthy-looking DriftState
        with pytest.raises(DriftStateUnavailable):
            drift_module.read_drift_state()


# ---------------------------------------------------------------------
# 5. HTTP integration: POST /v1/tasks with drift down surfaces failure
# ---------------------------------------------------------------------


def test_post_tasks_with_drift_down_fails_closed(
    isolated_runtime: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Submitting a task while drift state is unavailable MUST NOT
    return 200 with a Result that wasn't actually verified. The §10.7
    contract: "never silently degrade or return a result without
    verification."

    The acceptable behaviors are:
    - 5xx with a clear error message naming the drift failure.
    - 200 only if the response shape makes verification status explicit
      (e.g., an agreement_type field that the user can see).

    What's NOT acceptable: 200 with a Result envelope that LOOKS like
    a fully-verified solve when verification couldn't run.
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

    # raise_server_exceptions=False lets the 5xx materialize as a
    # response rather than re-raising server-side exceptions to the
    # test. The §10.7 contract is "Phoenix doesn't silently degrade" --
    # the typed error propagating uncaught from a route handler IS the
    # fail-closed behavior; the client sees 500.
    with TestClient(app, raise_server_exceptions=False) as client:
        with kill_drift_state(monkeypatch):
            resp = client.post("/v1/tasks", json=qho_body)

    # 200 is acceptable ONLY if the body explicitly surfaces the failure
    # (e.g., agreement_type=degraded, or an error field).
    if resp.status_code == 200:
        body = resp.json()
        # Must not be a clean HEDGED_CONSENSUS Result when verification
        # couldn't actually run.
        agreement = body.get("agreement_type", "")
        assert "degraded" in agreement.lower() or "error" in body or "drift" in resp.text.lower(), (
            f"200 response must surface drift failure; got: {resp.text[:500]}"
        )
    else:
        # 5xx response: the typed error propagated to the front door.
        # No assertion on shape -- just that the failure didn't get
        # swallowed.
        assert resp.status_code >= 500
