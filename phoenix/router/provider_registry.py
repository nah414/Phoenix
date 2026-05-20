"""Provider registry: per-provider health + metadata for routing decisions.

Per architecture v1 Section 4.3: Phoenix maintains a registry of every
:class:`BaseProviderClient` impl available in this install, with health
state, queue-depth estimate, last-calibration timestamp, and historical
reliability. The Router's Stage 3 (provider health filter) consults this
registry to drop unhealthy providers before ranking.

Phase 4 ships:

- :class:`ProviderHealth` enum (HEALTHY / DEGRADED / OFFLINE) per Section 4.3.
- :class:`ProviderEntry` -- one registered provider's full snapshot.
- :class:`ProviderRegistry` -- the lookup + iteration surface.

Phase 4 does NOT yet ship live telemetry polling (Source B per Section 4.6
-- live per-qubit T1/T2, per-gate error rates, queue depth from provider
APIs) or ledger-backed historical reliability (Source C). Phase 4's
intelligence layer (Step 5) uses Source A only -- static
:class:`HardwareParams` from the vendored ``synthesis/core/hardware_backends.py``.
Sources B + C land at Phase 7 with the Omega Ledger.

Phase 4 default registry (constructed by :func:`build_default_registry`):
the existing :class:`LocalClassicalSimulator` plus the three Phase 4
stubs (:class:`IBMQuantumStub`, :class:`BraketStub`, :class:`IonQStub`).
Tests can construct alternate registries to exercise specific scenarios
(all-healthy, IBM degraded, all-offline-except-local-sim).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phoenix.trinity.orchestrate.provider_client import BaseProviderClient


class ProviderHealth(Enum):
    """Coarse provider health classification per Section 4.3."""

    HEALTHY = "healthy"
    """Provider is accepting submissions; live telemetry within thresholds."""

    DEGRADED = "degraded"
    """Provider responsive but flagged: calibration drift > threshold,
    queue depth elevated, or recent failure within the failover quarantine
    window. Stage 3 drops degraded providers from the candidate pool by
    default; ops can override per-task via ``preferred_providers`` at the
    user policy layer."""

    OFFLINE = "offline"
    """Provider unreachable / credentials expired / health check failing
    for the past N minutes. Stage 3 always drops; no override path at the
    routing layer (the user has to wait for the provider to come back)."""


@dataclass
class ProviderEntry:
    """One registered provider's snapshot.

    Mutable (not frozen): the registry is the per-process state-of-the-world.
    Failover protocol (Step 8) updates :attr:`health` and
    :attr:`degraded_until` as providers fail and recover.

    Fields:
        client: The :class:`BaseProviderClient` instance.
        health: Current :class:`ProviderHealth` classification.
        degraded_until: When ``health == DEGRADED`` due to a transient
            failover quarantine, this is the UTC timestamp at which the
            provider auto-promotes back to ``HEALTHY``. ``None`` for
            permanent degradation (manual recovery required).
        queue_depth_estimate: Last-known queue depth; ``None`` if no live
            telemetry yet (Phase 4 default; Source B at Phase 7).
        last_calibration_utc: ISO 8601 of last reported calibration.
            ``None`` if unknown (Phase 4 default).
        registered_at_utc: ISO 8601 of when this entry joined the registry.
    """

    client: BaseProviderClient
    health: ProviderHealth = ProviderHealth.HEALTHY
    degraded_until: str | None = None
    queue_depth_estimate: int | None = None
    last_calibration_utc: str | None = None
    registered_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def provider_id(self) -> str:
        """Convenience accessor for the underlying client's provider_id."""
        return self.client.provider_id


class ProviderRegistry:
    """Per-process registry of available providers.

    Mutable; the Router and the failover protocol both modify it as
    providers fail / recover. Tests construct fresh registries; ops can
    inspect the registry via the dev-ops backdoor (Phase 8 wires
    ``/v1/admin/providers``).

    Phase 4 keeps this in-memory only; Phase 7 adds persistent backing
    via the state backend so health survives daemon restarts.
    """

    def __init__(self) -> None:
        self._entries: dict[str, ProviderEntry] = {}

    def register(self, entry: ProviderEntry) -> None:
        """Register a provider. Idempotent on ``provider_id`` -- a
        re-register replaces the existing entry (used by health updates).
        """
        self._entries[entry.provider_id] = entry

    def deregister(self, provider_id: str) -> None:
        """Remove a provider from the registry.

        Raises :class:`KeyError` if the provider isn't registered.
        """
        del self._entries[provider_id]

    def get(self, provider_id: str) -> ProviderEntry | None:
        """Look up by provider_id. Returns ``None`` when not registered."""
        return self._entries.get(provider_id)

    def all_entries(self) -> list[ProviderEntry]:
        """Snapshot of all registered providers in insertion order."""
        return list(self._entries.values())

    def healthy_entries(self) -> list[ProviderEntry]:
        """Snapshot of entries with ``health == HEALTHY``. Stage 3
        consumes this for filter."""
        return [e for e in self._entries.values() if e.health is ProviderHealth.HEALTHY]

    def mark_degraded(
        self,
        provider_id: str,
        *,
        degraded_until_utc: str | None = None,
    ) -> None:
        """Mark a provider degraded. Used by the failover protocol's
        exponential-backoff quarantine. Pass ``degraded_until_utc=None``
        for permanent degradation (manual recovery required)."""
        entry = self._entries.get(provider_id)
        if entry is None:
            raise KeyError(f"Provider {provider_id!r} not registered")
        entry.health = ProviderHealth.DEGRADED
        entry.degraded_until = degraded_until_utc

    def mark_offline(self, provider_id: str) -> None:
        """Mark a provider offline."""
        entry = self._entries.get(provider_id)
        if entry is None:
            raise KeyError(f"Provider {provider_id!r} not registered")
        entry.health = ProviderHealth.OFFLINE
        entry.degraded_until = None

    def mark_healthy(self, provider_id: str) -> None:
        """Restore a provider to HEALTHY. Used after the quarantine
        window expires or after a manual recovery."""
        entry = self._entries.get(provider_id)
        if entry is None:
            raise KeyError(f"Provider {provider_id!r} not registered")
        entry.health = ProviderHealth.HEALTHY
        entry.degraded_until = None

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, provider_id: object) -> bool:
        return isinstance(provider_id, str) and provider_id in self._entries


def build_default_registry() -> ProviderRegistry:
    """Construct the Phase 4 default registry.

    Registers four providers:

    - :class:`LocalClassicalSimulator` -- the only Phase 3 fully-wired
      adapter; always healthy and available; Stage 1 modality filter
      accepts it for ``"simulation"`` technology only.
    - :class:`IBMQuantumStub` -- superconducting, healthy by default,
      raises NotImplementedError on submit.
    - :class:`BraketStub` -- superconducting, healthy by default.
    - :class:`IonQStub` -- trapped_ion, healthy by default.

    Tests can construct alternate registries (e.g. all-stubs-degraded
    to force the failover protocol's all-alternates-exhausted path).
    """
    from phoenix.providers.classical.local_simulator import LocalClassicalSimulator
    from phoenix.providers.quantum import BraketStub, IBMQuantumStub, IonQStub

    registry = ProviderRegistry()
    registry.register(ProviderEntry(client=LocalClassicalSimulator()))
    registry.register(ProviderEntry(client=IBMQuantumStub()))
    registry.register(ProviderEntry(client=BraketStub()))
    registry.register(ProviderEntry(client=IonQStub()))
    return registry
