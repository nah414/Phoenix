"""In-process adapter registry (Phase 9 Step 2).

The registry is the single source of truth for which LoRA adapters
are currently loaded in this Phoenix process. The REST adapter
endpoints (Phase 9 Step 3) + the admin force-revalidate endpoint
(Phase 9 Step 4) + the task-grammar layer's capability-routing
(Phase 9+) all read from this registry.

Thread-safe via an internal :class:`threading.RLock`. The registry
is process-lifetime only -- adapter state does NOT survive daemon
restart (the user has to POST /v1/adapters again on startup). v1
ships this as the chosen design; v1.x can add a persistence layer
(state backend) if a real need emerges.

**Round-trip history:** every validator run appends a result to the
adapter's per-adapter ring buffer for
:func:`GET /v1/admin/adapters/{id}/round-trip-history`. Default cap
50 entries per adapter.
"""

from __future__ import annotations

import collections
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from phoenix.adapters.errors import AdapterAlreadyRegistered, AdapterNotLoaded

if TYPE_CHECKING:
    from phoenix.adapters.protocol import LoRAAdapter


_DEFAULT_HISTORY_SIZE = 50


@dataclass
class ValidationHistoryEntry:
    """One entry in an adapter's round-trip-history ring buffer.

    Fields:
      - ``timestamp_unix`` (float): when the validation ran.
      - ``passed`` (bool): overall pass/fail.
      - ``failed_cases`` (list[str]): canonical inputs that didn't
        round-trip. Empty list when passed.
      - ``wall_clock_ms`` (float): how long the suite took.
    """

    timestamp_unix: float
    passed: bool
    failed_cases: list[str] = field(default_factory=list)
    wall_clock_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp_unix": self.timestamp_unix,
            "passed": self.passed,
            "failed_cases": list(self.failed_cases),
            "wall_clock_ms": self.wall_clock_ms,
        }


@dataclass
class AdapterRecord:
    """Registry record for a loaded adapter.

    Combines the live :class:`LoRAAdapter` instance with the
    metadata + history Phoenix needs to expose via REST/admin.
    """

    adapter: LoRAAdapter
    loaded_at_unix: float
    history: collections.deque[ValidationHistoryEntry] = field(
        default_factory=lambda: collections.deque(maxlen=_DEFAULT_HISTORY_SIZE)
    )

    def to_dict(self) -> dict[str, Any]:
        """Public metadata shape for REST + admin responses."""
        return {
            "adapter_id": self.adapter.name,
            "name": self.adapter.name,
            "version": self.adapter.version,
            "base_model_fingerprint": self.adapter.base_model_fingerprint,
            "capabilities": list(self.adapter.capabilities),
            "fingerprint": self.adapter.fingerprint(),
            "loaded_at_unix": self.loaded_at_unix,
            "validation_history_length": len(self.history),
        }


class AdapterRegistry:
    """Thread-safe in-process registry of loaded LoRA adapters."""

    def __init__(self) -> None:
        self._adapters: dict[str, AdapterRecord] = {}
        self._lock = threading.RLock()

    def register(self, adapter: LoRAAdapter) -> AdapterRecord:
        """Add ``adapter`` to the registry.

        Raises :class:`AdapterAlreadyRegistered` if an adapter with
        the same name is already present. Re-registering would
        silently replace the prior adapter; we prefer to surface
        the collision so callers DELETE first when that's
        intentional.
        """
        with self._lock:
            if adapter.name in self._adapters:
                raise AdapterAlreadyRegistered(adapter.name)
            record = AdapterRecord(adapter=adapter, loaded_at_unix=time.time())
            self._adapters[adapter.name] = record
            return record

    def unregister(self, adapter_id: str) -> None:
        """Remove ``adapter_id`` from the registry.

        Raises :class:`AdapterNotLoaded` if the id is unknown.
        """
        with self._lock:
            if adapter_id not in self._adapters:
                raise AdapterNotLoaded(adapter_id)
            del self._adapters[adapter_id]

    def get(self, adapter_id: str) -> AdapterRecord:
        """Look up an adapter by id. Raises :class:`AdapterNotLoaded`."""
        with self._lock:
            record = self._adapters.get(adapter_id)
            if record is None:
                raise AdapterNotLoaded(adapter_id)
            return record

    def list_adapters(self) -> list[AdapterRecord]:
        """Return all registered adapter records (snapshot)."""
        with self._lock:
            return list(self._adapters.values())

    def append_history(
        self,
        adapter_id: str,
        entry: ValidationHistoryEntry,
    ) -> None:
        """Append a validation result to the adapter's history ring.

        Silently no-ops if the adapter has been unloaded between
        the validation call and this append (race avoidance).
        """
        with self._lock:
            record = self._adapters.get(adapter_id)
            if record is not None:
                record.history.append(entry)

    def history_snapshot(self, adapter_id: str) -> list[ValidationHistoryEntry]:
        """Return a copy of the adapter's history.

        Raises :class:`AdapterNotLoaded` if the adapter isn't
        registered.
        """
        with self._lock:
            record = self._adapters.get(adapter_id)
            if record is None:
                raise AdapterNotLoaded(adapter_id)
            return list(record.history)

    def clear(self) -> None:
        """Drop all adapters. Test-isolation helper."""
        with self._lock:
            self._adapters.clear()


# Module-level singleton (matches Phase 6b/7/8 factory pattern).
_REGISTRY: AdapterRegistry | None = None
_REGISTRY_LOCK = threading.Lock()


def get_registry() -> AdapterRegistry:
    """Return the lazily-constructed singleton :class:`AdapterRegistry`."""
    global _REGISTRY
    with _REGISTRY_LOCK:
        if _REGISTRY is None:
            _REGISTRY = AdapterRegistry()
        return _REGISTRY


def reset_registry() -> None:
    """Drop the singleton. Test-isolation helper."""
    global _REGISTRY
    with _REGISTRY_LOCK:
        _REGISTRY = None


__all__ = [
    "AdapterRecord",
    "AdapterRegistry",
    "ValidationHistoryEntry",
    "get_registry",
    "reset_registry",
]
