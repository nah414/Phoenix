"""Typed errors for the LoRA adapter subsystem (Phase 9 Step 1).

Per architecture v1 Section 2.7 + 3.5: the LoRA hot-swap interface
raises typed exceptions which the routes layer maps to HTTP status
codes:

- :class:`AdapterValidationError` -> 503 (inference-time round-trip
  failed; refused to register).
- :class:`AdapterTimeoutError` -> 504 (subprocess exceeded the
  configured timeout).
- :class:`AdapterVersionMismatch` -> 412 (replay sees a different
  adapter fingerprint than the recorded ledger entry).
- :class:`AdapterNotLoaded` -> 404 (operation on an unloaded
  adapter).
- :class:`AdapterAlreadyRegistered` -> 409 (re-registering a name
  that's already in the registry).

All inherit from :class:`AdapterError` so a single
``except AdapterError`` in the routes layer catches the family.
"""

from __future__ import annotations


class AdapterError(Exception):
    """Base class for adapter subsystem errors."""


class AdapterValidationError(AdapterError):
    """Inference-time validation round-trip failed.

    Raised by :class:`~phoenix.adapters.validator.AdapterValidator`
    when ``decode(encode(canonical))`` does not match the canonical
    input for one or more validation cases. Maps to HTTP 503.

    The ``failed_cases`` attribute lists the canonical inputs that
    didn't round-trip cleanly so callers can surface the specific
    failure in the error response body.
    """

    def __init__(self, *, adapter_name: str, failed_cases: list[str]) -> None:
        self.adapter_name = adapter_name
        self.failed_cases = list(failed_cases)
        super().__init__(
            f"Adapter {adapter_name!r} failed inference-time validation; "
            f"{len(failed_cases)} canonical case(s) did not round-trip: "
            f"{failed_cases}"
        )


class AdapterTimeoutError(AdapterError):
    """Adapter subprocess exceeded the configured timeout.

    Raised by :class:`~phoenix.adapters.sandbox.AdapterSandbox` when
    the subprocess running an adapter call (encode or decode) exceeds
    ``timeout_seconds``. Maps to HTTP 504. The runaway subprocess is
    terminated before the exception propagates.
    """

    def __init__(self, *, adapter_name: str, timeout_seconds: float) -> None:
        self.adapter_name = adapter_name
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"Adapter {adapter_name!r} exceeded {timeout_seconds:.1f}s timeout; "
            f"subprocess terminated."
        )


class AdapterVersionMismatch(AdapterError):
    """Loaded adapter's fingerprint differs from the recorded value.

    Raised by the replay engine (Phase 7 Step 8) when ``strict`` or
    ``replay`` mode encounters a ledger entry whose recorded adapter
    fingerprint doesn't match the currently-loaded adapter. The
    replay refuses to proceed rather than silently using a different
    version. Maps to HTTP 412.
    """


class AdapterNotLoaded(AdapterError):
    """Operation targeted an adapter that's not in the registry.

    Raised by force-revalidate / round-trip-history / unload when
    the named adapter_id has no corresponding registry entry. Maps
    to HTTP 404.
    """

    def __init__(self, adapter_id: str) -> None:
        self.adapter_id = adapter_id
        super().__init__(
            f"Adapter {adapter_id!r} is not loaded; POST /v1/adapters first to register."
        )


class AdapterAlreadyRegistered(AdapterError):
    """An adapter with the requested name is already in the registry.

    Re-registering would silently replace the prior adapter; we
    prefer to surface the collision explicitly so callers can
    DELETE the old one first if that's intentional. Maps to HTTP 409.
    """

    def __init__(self, adapter_name: str) -> None:
        self.adapter_name = adapter_name
        super().__init__(
            f"Adapter {adapter_name!r} is already registered; "
            f"DELETE /v1/adapters/{adapter_name} first to replace."
        )


__all__ = [
    "AdapterAlreadyRegistered",
    "AdapterError",
    "AdapterNotLoaded",
    "AdapterTimeoutError",
    "AdapterValidationError",
    "AdapterVersionMismatch",
]
