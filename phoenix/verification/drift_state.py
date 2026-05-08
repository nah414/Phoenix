"""Drift state read API + fail-closed exception (Phase 5 stub).

Per architecture v1 Section 6.5 + 6.8: the verification gate is fail-closed
on drift state. If drift cannot be read, no verification proceeds --
:class:`DriftStateUnavailable` is raised. Section 6.5 specifies that
during a verification run, if any detector is in ``drift_warning`` state,
the final :class:`Result.agreement_type` is :attr:`PhoenixDisagreementType.DEGRADED`
regardless of whether cross-axis checks converged.

Phase 5 ships a stub implementation per the locked scope decision
(2026-05-08): :func:`read_drift_state` always returns
:class:`DriftState(state="healthy")`, exercising the gate's fail-closed
wiring against a known-good baseline. Phase 7's drift detector replaces
this stub with real telemetry from three detectors (Tier-1 analytical
battery, ML statistical, cross-version) per Section 1 Decision 17.

The exception class :class:`DriftStateUnavailable` is real -- the gate
wraps :func:`read_drift_state` calls and raises this when reads fail.
Phase 7's real detector will throw this on telemetry-source unavailability.
"""

from __future__ import annotations

from dataclasses import dataclass


class DriftStateUnavailable(Exception):
    """Drift state could not be read.

    Per Section 6.8: fail-closed on drift state. If any of the three
    drift detectors (Section 1 Decision 17: Tier-1 analytical, ML
    statistical, cross-version) cannot report, the gate refuses to
    proceed; this exception surfaces with the unavailable detector(s)
    named.

    Phase 5 stub never raises (always returns healthy). Phase 7's real
    detector raises with the specific telemetry failure mode.
    """

    def __init__(self, message: str, *, unavailable_detectors: list[str] | None = None) -> None:
        super().__init__(message)
        self.unavailable_detectors = list(unavailable_detectors) if unavailable_detectors else []


@dataclass(frozen=True)
class DriftState:
    """Snapshot of the drift detector subsystem's state.

    Fields:
        state: One of ``"healthy"`` (all detectors converging),
            ``"warning"`` (at least one detector flagged drift), or
            ``"unavailable"`` (read failed; should accompany a raised
            :class:`DriftStateUnavailable` rather than being constructed
            normally).
        detector_summaries: Optional per-detector messages for audit log.
            Phase 7's real detector populates; Phase 5 stub leaves empty.
    """

    state: str
    detector_summaries: dict[str, str] | None = None

    @property
    def is_healthy(self) -> bool:
        return self.state == "healthy"

    @property
    def is_warning(self) -> bool:
        return self.state == "warning"


def read_drift_state() -> DriftState:
    """Read the current drift state. Phase 5 stub; Phase 7 wires real reads.

    Phase 5 always returns :class:`DriftState(state="healthy")`. The gate's
    fail-closed wiring still calls this function and respects
    :class:`DriftStateUnavailable` when raised; Phase 5's tests can
    monkey-patch this to exercise both branches.
    """
    return DriftState(state="healthy", detector_summaries=None)
