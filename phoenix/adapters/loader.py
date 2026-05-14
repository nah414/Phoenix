"""Adapter loader orchestration (Phase 9 Step 2).

Per architecture v1 Section 2.7: the loader is the single
chokepoint that takes a *spec string* and produces a
registered, validated :class:`LoRAAdapter` instance. Phase 9
Step 3's REST endpoint ``POST /v1/adapters`` is a thin wrapper
over :func:`load_adapter`.

**Spec format.** Phase 9 v1 accepts module-style specs of the
form ``"<dotted.module.path>:<callable_name>"``. The loader
imports the module, looks up the callable, calls it with no
arguments, and expects a :class:`LoRAAdapter` back. The
identity adapter test-vehicle is loadable as
``"phoenix.adapters.identity_adapter:make_identity_adapter"``.

File-path specs (e.g., ``"/path/to/my_adapter.py"``) raise
:class:`NotImplementedError` -- Phase 9 ships the *capability*
(per Decision 8: "v1 capability, not v1 content"), not the
filesystem-discovery story. v1.x can layer file-path imports
without breaking the spec format.

**The load pipeline:**

1. Parse the spec.
2. Import the module and resolve the callable.
3. Instantiate the adapter.
4. Type-check against the :class:`LoRAAdapter` Protocol.
5. Run inference-time validation (round-trip).
6. Register in the in-process registry.
7. Append the validation entry to the per-adapter history ring.

Any failure short-circuits the pipeline -- the adapter is NOT
registered if validation fails. The registry remains in the
state it was in before the load attempt.
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING

from phoenix.adapters.errors import (
    AdapterError,
    AdapterValidationError,
)
from phoenix.adapters.protocol import LoRAAdapter
from phoenix.adapters.registry import AdapterRecord, get_registry
from phoenix.adapters.validator import run_round_trip_validation

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def load_adapter(
    spec: str,
    *,
    validate: bool = True,
    sandbox_timeout_seconds: float = 5.0,
) -> AdapterRecord:
    """Resolve ``spec``, validate, register, and return the record.

    Parameters:
      - ``spec``: module-style ``"module.path:callable"`` string.
        The callable must take no args and return a
        :class:`LoRAAdapter`.
      - ``validate``: if False, skip the round-trip suite.
        Reserved for tests that exercise the registry without
        going through validation; production callers always
        pass ``True``.
      - ``sandbox_timeout_seconds``: per-call timeout for each
        round-trip invocation through the sandbox.

    Raises:
      - :class:`AdapterError` -- spec doesn't resolve or callable
        doesn't return a Protocol-shaped object.
      - :class:`AdapterValidationError` -- validation found one or
        more failing round-trips.
      - :class:`AdapterAlreadyRegistered` -- an adapter with the
        same ``name`` is already in the registry.
      - :class:`NotImplementedError` -- file-path spec form.
    """
    adapter = _resolve_spec(spec)

    if validate:
        entry = run_round_trip_validation(
            adapter,
            sandbox_timeout_seconds=sandbox_timeout_seconds,
        )
        if not entry.passed:
            raise AdapterValidationError(
                adapter_name=adapter.name,
                failed_cases=entry.failed_cases,
            )
    else:
        entry = None

    registry = get_registry()
    record = registry.register(adapter)
    if entry is not None:
        registry.append_history(adapter.name, entry)

    logger.info(
        "Loaded adapter %r from %r (validated=%s)",
        adapter.name,
        spec,
        validate,
    )
    return record


def _resolve_spec(spec: str) -> LoRAAdapter:
    """Parse + import + instantiate the adapter referenced by ``spec``.

    Phase 9 v1 supports module-style specs only.
    """
    if not spec or not isinstance(spec, str):
        raise AdapterError(f"Adapter spec must be a non-empty string; got {spec!r}")

    # Disallow obvious filesystem paths -- they're the v1.x path.
    looks_like_path = (
        spec.startswith(("/", "./", "../"))
        or (len(spec) >= 2 and spec[1] == ":")  # Windows drive letter
        or spec.endswith(".py")
    )
    if looks_like_path:
        raise NotImplementedError(
            f"File-path adapter specs are not supported in Phase 9 v1; "
            f"use 'module.path:callable' form instead. Got: {spec!r}"
        )

    if ":" not in spec:
        raise AdapterError(f"Adapter spec must be 'module.path:callable'; got {spec!r}")

    module_path, _, callable_name = spec.partition(":")
    if not module_path or not callable_name:
        raise AdapterError(f"Adapter spec must be 'module.path:callable'; got {spec!r}")

    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise AdapterError(f"Cannot import adapter module {module_path!r}: {exc}") from exc

    factory = getattr(module, callable_name, None)
    if factory is None:
        raise AdapterError(f"Module {module_path!r} has no attribute {callable_name!r}")
    if not callable(factory):
        raise AdapterError(
            f"{module_path}:{callable_name} is not callable ({type(factory).__name__})"
        )

    try:
        adapter = factory()
    except Exception as exc:
        raise AdapterError(f"Factory {spec!r} raised on construction: {exc!r}") from exc

    if not isinstance(adapter, LoRAAdapter):
        raise AdapterError(
            f"Factory {spec!r} returned a non-LoRAAdapter instance "
            f"(type={type(adapter).__name__}); the result must "
            f"structurally satisfy phoenix.adapters.protocol.LoRAAdapter."
        )

    return adapter


__all__ = ["load_adapter"]
