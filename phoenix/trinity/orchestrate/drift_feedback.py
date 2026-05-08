"""Drift-signal emitter (Phase 3 buffer-only).

Per architecture v1 Section 2.5: ``drift_feedback`` emits drift signals
from the just-completed solve so future routing decisions and drift
detection benefit from the latest empirical fidelity/latency. The two
consumers are deferred:

- **Router intelligence layer (Section 4.6)** -- drains the buffer on
  schedule and updates per-provider fidelity/latency estimates. Lands
  with the Router subsystem at Phase 4.
- **Drift detector (Section 6.5)** -- consumes drift signals to detect
  calibration drift; raises :class:`DriftStateUnavailable` per Section
  6.8 when the state backend is partitioned. Lands with the audit/ledger
  subsystem at Phase 7.

Phase 3 ships a buffer-only emitter: signals append to a module-level
list; ops can drain manually via :func:`drain_buffer`. The buffer is
unbounded (R6 in the Phase 3 plan's risk register) -- Phase 4 will add a
ring-buffer with overflow tagging.

Greenfield Phoenix code per Decision 37; nothing vendored.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DriftSignal:
    """One drift signal emitted at the end of a solve.

    Fields are stable across releases; new fields append to the end.

    Fields:
        request_id: Echoes :attr:`PhysicsTask.request_id` for ledger join.
        provider_id: The provider this signal characterizes.
        measured_fidelity: Fidelity reported by the just-completed solve
            (from :attr:`KPIBundle.fidelity`).
        measured_latency_us: Latency reported by the just-completed solve
            (from :attr:`KPIBundle.latency_us`).
        timestamp_utc: ISO 8601 UTC timestamp of the emission.
        solver_id: Echo of :attr:`CandidateAnswer.solver_id` for the run.
    """

    request_id: str
    provider_id: str
    measured_fidelity: float
    measured_latency_us: float
    timestamp_utc: str
    solver_id: str


# Module-level buffer; Phase 3 unbounded by design (see R6 in the Phase 3
# plan's risk register). Phase 4's Router intelligence drains it; Phase 7
# adds the ring-buffer with overflow tagging.
_BUFFER: list[DriftSignal] = []


def emit_drift_signal(signal: DriftSignal) -> None:
    """Append a drift signal to the in-memory buffer.

    Fire-and-forget per Section 2.5; the Orchestrate engine calls this
    at the end of every solve. Phase 4's Router intelligence layer drains
    the buffer on a schedule; Phase 7's drift detector also consumes
    drained signals.
    """
    _BUFFER.append(signal)


def drain_buffer() -> list[DriftSignal]:
    """Read-and-clear the in-memory buffer.

    Phase 4's Router intelligence layer calls this on its scheduled tick.
    Phase 3 ops can call it manually if memory grows. Returns a snapshot
    of the buffer's contents at the moment of the call; subsequent calls
    return only signals emitted since the last drain.
    """
    drained = list(_BUFFER)
    _BUFFER.clear()
    return drained


def buffer_size() -> int:
    """Current buffer occupancy. Phase 3 ops/test helper."""
    return len(_BUFFER)
