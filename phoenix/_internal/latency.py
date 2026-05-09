"""Latency-tier enum and the typed exception raised when a non-routable tier is requested.

Per architecture v1.1 follow-up (Section 1, post-Decision-28 paragraph, locked
2026-05-08): three tiers are encoded as a single enum so the Router, scheduler,
and front-door scheduling logic can route by tier rather than string-comparison
or hardcoded Decision references.

v1 routes only ``BATCH_REALTIME``. ``STREAMING_REALTIME`` and
``PERCEPTION_REALTIME`` are defined-but-not-routable so v1's pipeline accepts
the tier as a parameter from day one and raises :class:`LatencyTierNotImplemented`
for non-routable tiers. This means the perception extension at Phase 12+ does
not have to retroactively add an enum value or churn callers.
"""

from __future__ import annotations

from enum import Enum


class LatencyTier(Enum):
    """Latency tier each Phoenix solve commits to.

    Values are stable strings used in audit-log entries, ledger entries, and
    front-door routing decisions. Adding a tier in v1.x or v2 appends to this
    enum; existing values never change.
    """

    BATCH_REALTIME = "batch_realtime"
    """v1 (Section 1 Decision 26): 10-100 ms loops on local hardware (NPU/GPU/CPU);
    100 ms - 1 s for cloud-orchestrated. Phoenix v1's default tier."""

    STREAMING_REALTIME = "streaming_realtime"
    """v2 (Section 1 Decision 28): sub-millisecond loops, standing-computation
    API, continuous probe-drive feedback. Defined-but-not-routable in v1; v1
    raises :class:`LatencyTierNotImplemented` if a request specifies this tier."""

    PERCEPTION_REALTIME = "perception_realtime"
    """v1.1 perception phase (Section 11.14.7, locked 2026-05-08): sub-100 ms
    hard real-time per sensor frame end-to-end. Routed only by the perception
    harness extension at Phase 12+. v1 raises :class:`LatencyTierNotImplemented`
    for non-perception tasks specifying this tier."""


class LatencyTierNotImplemented(Exception):
    """Raised when a requested latency tier is defined in the enum but not
    routable in the current Phoenix release.

    The exception carries the requested ``tier`` so audit logs and ops dashboards
    can record exactly which tier was blocked. The exception message names the
    release that will support the tier.
    """

    def __init__(self, tier: LatencyTier, reason: str) -> None:
        super().__init__(reason)
        self.tier = tier
