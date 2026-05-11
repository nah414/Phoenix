"""Drift-alert emitter bridge (Phase 6b Step 8).

Wires :class:`phoenix.verification.drift_detector.DriftDetector`'s
:meth:`register_drift_callback` into :class:`phoenix.api.event_broker`'s
:meth:`emit`. Each call to :meth:`DriftDetector.run_cycle` produces
exactly one :class:`DriftSnapshot`; this bridge emits a ``drift.alert``
event onto the broker **only on state transitions** (so a sequence of
five healthy cycles produces one alert at most, not five).

Per architecture v1 Section 5.3 line 995: the
``/v1/ws/calibration/drift`` WebSocket endpoint subscribes to this
channel; the handler lives in :mod:`phoenix.api.routes`.

Per locked open-item 4 (2026-05-10) JetStream consumer mode for the
``phoenix.drift.alerts`` subject: durable with 10-minute ``MAX_AGE``
so a briefly-disconnected ops dashboard catches up. The in-memory
broker has no retention concept; the per-task-buffer cap (1000
events) bounds memory growth. When ``$PHOENIX_EVENT_BROKER=nats``,
the constant ``DRIFT_ALERTS_CHANNEL`` is used as the per-buffer key
in the broker, mirroring how task IDs key per-task buffers.

The transition-detection logic lives in :class:`_DriftAlertEmitter`;
it is module-private because the public API is :func:`install_drift_alert_emitter`.
The emitter is a stateful callback (it remembers the prior state);
the install function registers it on the detector. Tests construct
emitters directly to exercise transition logic without TestClient
overhead.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from phoenix.api.event_broker import BrokerProtocol
    from phoenix.verification.drift_detector import DriftDetector, DriftSnapshot

logger = logging.getLogger(__name__)


DRIFT_ALERTS_CHANNEL = "phoenix.drift.alerts"
"""Per-buffer key the broker uses for drift alerts.

Matches the architecture's NATS subject name (line 995) so the
NATS-backed broker subscribes on the same string both inside Phoenix
and from external observers (ops dashboards subscribing directly to
NATS without going through Phoenix's WebSocket layer).
"""

DRIFT_ALERT_EVENT_TYPE = "drift.alert"
"""The ``type`` discriminator on emitted :class:`TaskEvent` instances."""


class _DriftAlertEmitter:
    """Stateful callback that emits broker events on drift state
    transitions.

    Constructed with a :class:`BrokerProtocol` instance; ``__call__``
    accepts a :class:`DriftSnapshot` and emits a ``drift.alert``
    event ONLY when the snapshot's state differs from the previously-
    seen state. The first snapshot establishes the baseline (no
    initial-state alert -- ops dashboards on cold start should poll
    a separate ``/v1/calibration/status`` REST endpoint for the
    current state; Phase 6b ships only the transition stream).

    Thread-safe: a single lock guards the ``_previous_state`` slot
    so multiple cycles arriving on overlapping threads don't race.
    """

    def __init__(self, broker: BrokerProtocol) -> None:
        self._broker = broker
        self._previous_state: str | None = None
        self._lock = threading.Lock()

    def __call__(self, snapshot: DriftSnapshot) -> None:
        """Drift callback. Emits ``drift.alert`` only on transitions."""
        with self._lock:
            from_state = self._previous_state
            if from_state == snapshot.state:
                return
            self._previous_state = snapshot.state
        if from_state is None:
            # First snapshot of this process: establish baseline; do
            # not emit. Subsequent cycles fire on real transitions.
            return
        payload: dict[str, Any] = {
            "from_state": from_state,
            "to_state": snapshot.state,
            "firing_detectors": list(snapshot.firing_detectors),
            "detector_summaries": dict(snapshot.detector_summaries),
        }
        try:
            self._broker.emit(DRIFT_ALERTS_CHANNEL, DRIFT_ALERT_EVENT_TYPE, payload)
        except Exception:
            # Broker write failure must not propagate into the drift
            # detector's run-cycle loop -- log and move on.
            logger.exception(
                "Drift alert emit failed for transition %s -> %s", from_state, snapshot.state
            )


def install_drift_alert_emitter(
    detector: DriftDetector,
    broker: BrokerProtocol,
) -> _DriftAlertEmitter:
    """Register a drift-alert emitter on the detector.

    Returns the emitter so callers can hold a reference (typically
    unused; lifespan code just discards it). The emitter is
    registered via :meth:`DriftDetector.register_drift_callback` per
    the locked Phase 6b open-item 6 (2026-05-10) callback seam.

    Idempotency is the caller's responsibility: calling this twice
    registers two emitters on the same detector, each tracking its
    own prior state. The FastAPI lifespan calls this once at
    startup; tests reset the detector singleton between scenarios
    so a fresh emitter is registered on each test's detector.
    """
    emitter = _DriftAlertEmitter(broker)
    detector.register_drift_callback(emitter)
    return emitter
