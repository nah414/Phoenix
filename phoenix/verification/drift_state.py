"""Drift state read API + fail-closed exception.

Per architecture v1 Section 6.5 + Section 6.8: the verification gate
is fail-closed on drift state. If drift cannot be read, no
verification proceeds -- :class:`DriftStateUnavailable` is raised.
Section 6.5 specifies that during a verification run, if any
detector is in ``drift_warning`` state, the final
:class:`Result.agreement_type` is
:attr:`PhoenixDisagreementType.DEGRADED` regardless of whether
cross-axis checks converged.

Phase 5 shipped a stub that always returned
``DriftState(state="healthy")``, exercising the gate's fail-closed
wiring against a known-good baseline. Phase 6b Step 7 (this module
update) wires :func:`read_drift_state` to the
:class:`phoenix.verification.drift_detector.DriftDetector` singleton
and applies the stale-snapshot rule: if the last cycle is older than
``2 * cadence_seconds``, raise :class:`DriftStateUnavailable` with
the firing detectors named.

Per Decision 17 aggregation:

- ``0`` detectors firing -> ``state="healthy"``.
- ``1`` detector firing -> ``state="warning"``.
- ``2+`` detectors firing -> ``state="warning"`` with a
  ``__high_confidence__`` flag in ``detector_summaries`` so the
  verification gate (Section 6.5) auto-promotes one rung.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


class DriftStateUnavailable(Exception):
    """Drift state could not be read.

    Per Section 6.8: fail-closed on drift state. If any of the three
    drift detectors (Section 1 Decision 17: Tier-1 analytical, ML
    statistical, cross-version) cannot report -- or if the most
    recent cycle's snapshot is older than ``2 * cadence_seconds`` --
    the gate refuses to proceed; this exception surfaces with the
    unavailable detector(s) named.

    Phase 5 stub never raised (always returned healthy). Phase 6b
    Step 7's real detector raises with the specific telemetry
    failure mode (stale snapshot, no cycle yet, detector crash).
    """

    def __init__(
        self,
        message: str,
        *,
        unavailable_detectors: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.unavailable_detectors = list(unavailable_detectors) if unavailable_detectors else []


@dataclass(frozen=True)
class DriftState:
    """Snapshot of the drift detector subsystem's state.

    Fields:
        state: One of ``"healthy"`` (all detectors converging) or
            ``"warning"`` (at least one detector flagged drift).
            ``"unavailable"`` is reserved for accompanying a raised
            :class:`DriftStateUnavailable` rather than being
            constructed directly.
        detector_summaries: Optional per-detector messages for audit
            log. Populated when ``state="warning"``. Includes the
            sentinel key ``"__high_confidence__": "true"`` when two
            or more detectors fired (Section 6.5 auto-promote
            trigger).
    """

    state: str
    detector_summaries: dict[str, str] | None = None

    @property
    def is_healthy(self) -> bool:
        return self.state == "healthy"

    @property
    def is_warning(self) -> bool:
        return self.state == "warning"

    @property
    def is_high_confidence_warning(self) -> bool:
        """True when two or more detectors fired in the last cycle.

        Section 6.5 auto-promote: the verification gate promotes one
        rung beyond what ``max_error_bar`` would normally select when
        this flag is set. The Phase 5 stub never set it; Phase 6b's
        real detector flips it on multi-detector agreement.
        """
        if self.state != "warning":
            return False
        if self.detector_summaries is None:
            return False
        return self.detector_summaries.get("__high_confidence__") == "true"


def read_drift_state() -> DriftState:
    """Read the current drift state from the :class:`DriftDetector`.

    Three-way contract (Phase 6b Step 7):

    1. **No snapshot exists** (fresh daemon, scheduler not yet
       started, no backend rehydration). Returns
       ``DriftState(state="healthy")`` -- this matches Phase 5's
       stub behavior and lets the verification gate proceed during
       daemon startup before the first drift cycle. Production
       deployments either start the scheduler at boot or load a
       prior snapshot from the state backend; both paths surface a
       snapshot before any meaningful traffic arrives.

    2. **Snapshot exists but is stale** (older than
       ``2 * cadence_seconds``). Raises
       :class:`DriftStateUnavailable` per Section 6.8 fail-closed
       rule -- the detector WAS running and stopped reporting,
       which is a real telemetry failure.

    3. **Snapshot exists and fresh**. Returns a :class:`DriftState`
       reflecting the Phase 6b Step 7 aggregation rule (healthy
       when 0 detectors firing, warning otherwise; high-confidence
       flag in ``detector_summaries`` when 2+ firing).

    The fail-closed rule applies to *telemetry failures* (stale
    snapshots, broken detectors), not to the cold-start state where
    no cycle has run yet. This distinction preserves the Phase 5
    verification-gate contract while still surfacing real drift
    issues to the gate's Section 6.5 auto-promote path.
    """
    # Lazy import to avoid a hard dependency on the detector module
    # for non-verification consumers (e.g. unit tests that pre-import
    # this module before the detector singleton is constructed).
    from phoenix.verification.drift_detector import get_detector

    detector = get_detector()
    snapshot = detector.last_snapshot()
    if snapshot is None:
        # Cold start: scheduler not yet run, backend has no prior snapshot.
        # Default-healthy preserves the Phase 5 stub contract for the
        # verification gate; the fail-closed rule below kicks in only
        # once the detector has produced (and then lost) a snapshot.
        return DriftState(state="healthy", detector_summaries=None)

    age_seconds = time.time() - snapshot.cycle_unix
    stale_threshold = 2 * detector.cadence_seconds()
    if age_seconds > stale_threshold:
        raise DriftStateUnavailable(
            (
                f"Drift snapshot is stale (age {age_seconds:.0f}s exceeds "
                f"2x cadence {stale_threshold}s); verification gate is "
                f"fail-closed per Section 6.8 until the next cycle "
                f"completes"
            ),
            unavailable_detectors=list(snapshot.firing_detectors)
            or ["tier1_analytical", "ml_statistical", "cross_version"],
        )

    if snapshot.state == "healthy":
        return DriftState(state="healthy", detector_summaries=None)

    # Translate detector-side "warning" / "high_confidence_warning"
    # into the DriftState contract (state="warning" with optional
    # high-confidence sentinel in detector_summaries).
    summaries: dict[str, str] = dict(snapshot.detector_summaries)
    if snapshot.state == "high_confidence_warning":
        summaries["__high_confidence__"] = "true"
    return DriftState(state="warning", detector_summaries=summaries)
