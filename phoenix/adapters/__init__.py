"""Phoenix LoRA adapter subsystem (Phase 9).

Per architecture v1 Section 2.7 + Section 3.5: Phoenix v1 ships
the **interface** for loading LoRA adapters, plus a reference
identity adapter, plus the inference-time validator and subprocess
sandbox. Users bring their own LoRA weights per Section 1
Decision 8 ("v1 capability, not v1 content").

Phase 9 Step 1 ships:
- :class:`LoRAAdapter` Protocol
- :class:`IdentityAdapter` reference (echo adapter, no weights)
- :class:`AdapterError` family
- :func:`run_in_sandbox` / :func:`call_adapter_method` (subprocess
  + timeout + restricted env + tempdir per locked OPEN-2)

Step 2 ships the loader + validator + in-process registry.
Steps 3-4 wire the REST + admin endpoints.
"""

from phoenix.adapters.errors import (
    AdapterAlreadyRegistered,
    AdapterError,
    AdapterNotLoaded,
    AdapterTimeoutError,
    AdapterValidationError,
    AdapterVersionMismatch,
)
from phoenix.adapters.identity_adapter import IdentityAdapter, make_identity_adapter
from phoenix.adapters.loader import load_adapter
from phoenix.adapters.protocol import LoRAAdapter
from phoenix.adapters.registry import (
    AdapterRecord,
    AdapterRegistry,
    ValidationHistoryEntry,
    get_registry,
    reset_registry,
)
from phoenix.adapters.sandbox import SandboxResult, call_adapter_method, run_in_sandbox
from phoenix.adapters.validator import run_round_trip_validation, validation_inputs

__all__ = [
    "AdapterAlreadyRegistered",
    "AdapterError",
    "AdapterNotLoaded",
    "AdapterRecord",
    "AdapterRegistry",
    "AdapterTimeoutError",
    "AdapterValidationError",
    "AdapterVersionMismatch",
    "IdentityAdapter",
    "LoRAAdapter",
    "SandboxResult",
    "ValidationHistoryEntry",
    "call_adapter_method",
    "get_registry",
    "load_adapter",
    "make_identity_adapter",
    "reset_registry",
    "run_in_sandbox",
    "run_round_trip_validation",
    "validation_inputs",
]
