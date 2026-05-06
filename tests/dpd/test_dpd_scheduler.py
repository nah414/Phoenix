"""Phase 1 DPD self-test.

Architecture v1 Section 2.4 names the vendored DPD engine: trace preservation,
positivity, and RK4 4th-order convergence. The vendored class is
``DPDScheduler`` (architectural drift from spec's ``DPDEngine`` reference --
discovered 2026-05-06; will land in the spec-drift correction commit after
Phase 1 ships).

Phase 1 ships a structural smoke test for ``DPDScheduler``: construct a
scheduler, add a couple of blocks, build the schedule, verify event-list shape.
The deeper trace-preservation / positivity / RK4-convergence tests land when
Phase 3 wires Control through Trinity Core's pipeline.
"""

from __future__ import annotations

import numpy as np

import phoenix  # noqa: F401  -- triggers sys.path injection for vendored modules


def test_dpd_scheduler_construction() -> None:
    """DPDScheduler constructs with default args; n_blocks starts at 0."""
    from synthesis.core.dpd_engine import DPDScheduler

    scheduler = DPDScheduler()
    assert scheduler.n_blocks == 0
    assert scheduler.inter_block_latency == 0.0
    assert scheduler.use_drift_prediction is False


def test_dpd_block_dataclass_shape() -> None:
    """DPDBlock has the architecture v1 Section 2.4 fields."""
    from synthesis.core.dpd_engine import DPDBlock, ProbeType

    # Smallest valid block: 2-dim drive Hamiltonians, weak probe.
    drive_h = np.eye(2, dtype=complex)
    block = DPDBlock(
        drive1_hamiltonian=drive_h,
        drive1_duration=1e-9,
        probe_type=ProbeType.STRONG_PROJECTIVE,
        probe_strength=0.1,
        probe_duration=1e-10,
        probe_observable=None,
        drive2_hamiltonian=None,
        drive2_duration=None,
        adaptive=False,
        label="test-block",
    )
    assert block.drive1_duration == 1e-9
    assert block.probe_strength == 0.1
    assert block.label == "test-block"


def test_dpd_scheduler_add_block_increments_count() -> None:
    """Adding a block to the scheduler bumps n_blocks; clear() resets to 0."""
    from synthesis.core.dpd_engine import DPDBlock, DPDScheduler, ProbeType

    drive_h = np.eye(2, dtype=complex)
    block = DPDBlock(
        drive1_hamiltonian=drive_h,
        drive1_duration=1e-9,
        probe_type=ProbeType.STRONG_PROJECTIVE,
        probe_strength=0.1,
        probe_duration=1e-10,
        probe_observable=None,
        drive2_hamiltonian=None,
        drive2_duration=None,
        adaptive=False,
        label="block-1",
    )
    scheduler = DPDScheduler()
    scheduler.add_block(block)
    assert scheduler.n_blocks == 1
    scheduler.add_block(block)
    assert scheduler.n_blocks == 2
    scheduler.clear()
    assert scheduler.n_blocks == 0


def test_dpd_scheduler_build_schedule_event_shape() -> None:
    """build_schedule() returns timed events covering D1, probe, D2 phases."""
    from synthesis.core.dpd_engine import DPDBlock, DPDScheduler, ProbeType

    drive_h = np.eye(2, dtype=complex)
    block = DPDBlock(
        drive1_hamiltonian=drive_h,
        drive1_duration=2e-9,
        probe_type=ProbeType.STRONG_PROJECTIVE,
        probe_strength=0.5,
        probe_duration=1e-10,
        probe_observable=None,
        drive2_hamiltonian=drive_h,
        drive2_duration=2e-9,
        adaptive=False,
        label="full-block",
    )
    scheduler = DPDScheduler()
    scheduler.add_block(block)
    events = scheduler.build_schedule()

    # Three events for one full block: D1 drive, probe, D2 drive.
    assert len(events) == 3
    assert events[0]["type"] == "drive" and events[0]["phase"] == "D1"
    assert events[1]["type"] == "probe"
    assert events[2]["type"] == "drive" and events[2]["phase"] == "D2"

    # Times should monotonically increase.
    times = [event["t_start"] for event in events]
    assert times == sorted(times)
