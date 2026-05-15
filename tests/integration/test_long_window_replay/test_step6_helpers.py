"""Phase 11 Step 6 -- pins the long-window replay helper contracts.

Two helpers ship at Step 6 (used by Step 7's canonical bit-exact
verification test):

1. :func:`monkeypatch_clock` -- fast-forward ``time.time()`` and
   related wall-clock reads to a chosen ``target_unix``.
2. :func:`build_strict_mode_qho_fixture` -- submit a deterministic
   strict-mode QHO solve and return the :class:`FixtureSolve`
   bundle (task_id + result + result_hash).

This module verifies the contracts each helper exposes. Step 7
then uses them together to assert the §10.7 long-window replay
invariant.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

import phoenix  # noqa: F401  -- triggers sys.path injection
from tests.integration.test_long_window_replay.clock_advance import (
    monkeypatch_clock,
)
from tests.integration.test_long_window_replay.fixture_solve_entry import (
    FixtureSolve,
    build_strict_mode_qho_fixture,
)


pytestmark = pytest.mark.acceptance


# ---------------------------------------------------------------------
# 1. monkeypatch_clock contract
# ---------------------------------------------------------------------


class TestMonkeypatchClock:
    def test_time_returns_target_unix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        target = 1_900_000_000.0  # ~2030-03-17 UTC
        with monkeypatch_clock(monkeypatch, target_unix=target):
            assert time.time() == target

    def test_time_is_deterministic_during_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Repeated reads return the SAME value -- no drift mid-test."""
        target = 1_900_000_000.0
        with monkeypatch_clock(monkeypatch, target_unix=target):
            a = time.time()
            b = time.time()
            c = time.time()
        assert a == b == c == target

    def test_time_ns_matches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        target = 1_900_000_000.0
        with monkeypatch_clock(monkeypatch, target_unix=target):
            assert time.time_ns() == int(target * 1_000_000_000)

    def test_time_restored_after_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """monkeypatch's automatic teardown restores time.time()."""
        real_before = time.time()
        target = 1_900_000_000.0
        with monkeypatch_clock(monkeypatch, target_unix=target):
            assert time.time() == target
        real_after = time.time()
        # Real time is back; difference from before is sub-second
        assert abs(real_after - real_before) < 5.0
        assert real_after != target

    def test_monotonic_not_patched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """time.monotonic() is NOT affected -- duration measurements
        keep working during the test.
        """
        target = 1_900_000_000.0
        with monkeypatch_clock(monkeypatch, target_unix=target):
            m1 = time.monotonic()
            # Sleep just above one Windows pre-3.13 scheduler tick (~15.6 ms)
            # so the test holds on Windows py3.11/3.12 where time.monotonic()
            # resolution is coarse. Python 3.13 moved Windows monotonic to
            # QueryPerformanceCounter (sub-µs); Linux + macOS have always had
            # sub-µs monotonic. 20 ms gives every platform headroom while
            # keeping the test fast.
            time.sleep(0.02)
            m2 = time.monotonic()
        assert m2 > m1  # monotonic still ticking; advance applied to wall-clock only


# ---------------------------------------------------------------------
# 2. build_strict_mode_qho_fixture contract
# ---------------------------------------------------------------------


class TestBuildStrictModeQhoFixture:
    def test_returns_fixture_solve_with_task_id(self, isolated_runtime: Path) -> None:
        fixture = build_strict_mode_qho_fixture(isolated_runtime)
        assert isinstance(fixture, FixtureSolve)
        assert fixture.task_id.startswith("req_")

    def test_omega_ledger_entry_id_populated(self, isolated_runtime: Path) -> None:
        """The fixture captures the ledger entry's UUID for the solve.
        The replay engine fetches the original ``result_hash`` from
        the entry internally; the fixture only needs the ID to wire
        the replay call (POST /v1/tasks/{id}/replay).
        """
        fixture = build_strict_mode_qho_fixture(isolated_runtime)
        # UUID4-shaped: 36 chars with hyphens
        assert len(fixture.omega_ledger_entry_id) == 36
        assert fixture.omega_ledger_entry_id.count("-") == 4

    def test_solve_timestamp_recent(self, isolated_runtime: Path) -> None:
        """``solve_unix_timestamp`` records when the fixture was built;
        it's the anchor for Step 7's clock advance.
        """
        before = time.time()
        fixture = build_strict_mode_qho_fixture(isolated_runtime)
        after = time.time()
        assert before <= fixture.solve_unix_timestamp <= after

    def test_result_numeric_fields_pinned(self, isolated_runtime: Path) -> None:
        """Two builds of the same fixture produce the same numeric
        outputs -- the determinism contract.
        """
        a = build_strict_mode_qho_fixture(isolated_runtime)
        # Reset the ledger between builds so we get a fresh task_id.
        # (The state backend has been writing audit events; reset to
        # ensure the second fixture build composes cleanly.)
        from phoenix.audit import reset_emitter
        from phoenix.ledger import reset_ledger
        from phoenix.safety import kill_switch as ks_module
        from phoenix.state import reset_state_backend
        from phoenix.trinity import reproducibility_context

        reset_emitter()
        reset_ledger()
        reset_state_backend()
        ks_module._STORE = None
        reproducibility_context.clear_all()

        b = build_strict_mode_qho_fixture(isolated_runtime)

        # task_id is per-request (req_<uuid>), so it differs; the
        # NUMERIC outputs should match because the task spec is pinned.
        assert a.task_id != b.task_id
        assert a.result_value == b.result_value
        assert a.error_bar == b.error_bar
        assert a.sigma == b.sigma
        # agreement_type is also a function of the result, so matches.
        assert a.agreement_type == b.agreement_type
