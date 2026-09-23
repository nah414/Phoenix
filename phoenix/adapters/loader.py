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

**Module allowlist (PHX-FU3, 2026-09-18).** The loader imports only
modules under:

- Phoenix's own adapter package, :data:`BUILTIN_ADAPTER_NAMESPACE`
  (``phoenix.adapters``) -- except the subsystem's own machinery
  (this loader, the registry, sandbox, validator, protocol, errors
  and the package ``__init__``), which is never loadable; and
- the namespaces listed in :data:`ADAPTER_ALLOWLIST_ENV`
  (``PHOENIX_ADAPTER_ALLOWLIST``): comma-separated dotted prefixes,
  read from the daemon's environment on every load. An entry admits
  that module and its submodules (``acme.lora`` admits
  ``acme.lora.v6`` but not ``acme.lorax``).

Any other module is refused with :class:`AdapterSpecNotAllowed`
*before* it is imported. After import, the factory itself must be
defined in an allowlisted module, so an allowlisted module cannot
lend out a callable it merely re-exports (``from os import ...``).

**The load pipeline:**

1. Parse the spec and check the module against the allowlist.
2. Import the module and resolve the callable (whose defining
   module must also be allowlisted).
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
import os
from typing import TYPE_CHECKING

from phoenix.adapters.errors import (
    AdapterError,
    AdapterSpecNotAllowed,
    AdapterValidationError,
)
from phoenix.adapters.protocol import LoRAAdapter
from phoenix.adapters.registry import AdapterRecord, get_registry
from phoenix.adapters.validator import run_round_trip_validation

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

#: Phoenix's own adapter package. Adapters shipped with Phoenix (the
#: identity adapter today) live under it and are always loadable.
BUILTIN_ADAPTER_NAMESPACE = "phoenix.adapters"

#: Daemon environment variable listing extra loadable namespaces:
#: comma-separated dotted module prefixes, e.g. ``acme.lora,my_adapters``.
ADAPTER_ALLOWLIST_ENV = "PHOENIX_ADAPTER_ALLOWLIST"

#: The adapter subsystem's own machinery. These modules sit under
#: :data:`BUILTIN_ADAPTER_NAMESPACE` but are not adapters, and some of
#: their zero-argument callables have side effects (``reset_registry``
#: empties the registry), so no allowlist entry makes them loadable.
_SUBSYSTEM_MODULES = frozenset(
    {
        "phoenix.adapters",
        "phoenix.adapters.errors",
        "phoenix.adapters.loader",
        "phoenix.adapters.protocol",
        "phoenix.adapters.registry",
        "phoenix.adapters.sandbox",
        "phoenix.adapters.validator",
    }
)


def _is_dotted_identifier(path: str) -> bool:
    """True for ``a.b.c`` where every component is a Python identifier."""
    return all(part.isidentifier() for part in path.split("."))


def allowed_adapter_namespaces() -> tuple[str, ...]:
    """Return the namespaces adapter specs may import from.

    :data:`BUILTIN_ADAPTER_NAMESPACE` first, then each valid entry of
    :data:`ADAPTER_ALLOWLIST_ENV` (read on every call, so the daemon's
    environment is the single source). Malformed entries are logged
    and ignored -- they never widen the allowlist.
    """
    namespaces = [BUILTIN_ADAPTER_NAMESPACE]
    for raw_entry in os.environ.get(ADAPTER_ALLOWLIST_ENV, "").split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        if not _is_dotted_identifier(entry):
            logger.warning(
                "Ignoring %s entry %r: not a dotted module prefix",
                ADAPTER_ALLOWLIST_ENV,
                entry,
            )
            continue
        if entry not in namespaces:
            namespaces.append(entry)
    return tuple(namespaces)


def _module_is_allowed(module_name: str, namespaces: tuple[str, ...]) -> bool:
    """True when ``module_name`` is, or sits under, an allowlisted namespace."""
    if module_name in _SUBSYSTEM_MODULES:
        return False
    return any(module_name == ns or module_name.startswith(ns + ".") for ns in namespaces)


def _not_allowed(
    module_path: str, reason: str, namespaces: tuple[str, ...]
) -> AdapterSpecNotAllowed:
    return AdapterSpecNotAllowed(
        module_path=module_path,
        message=(
            f"{reason}. Adapters load only from {', '.join(namespaces)} "
            f"(the adapter subsystem's own modules excluded); an operator "
            f"can allow more namespaces with {ADAPTER_ALLOWLIST_ENV} "
            f"(comma-separated dotted prefixes) in the daemon's environment."
        ),
    )


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
      - :class:`AdapterSpecNotAllowed` -- the module (or the
        factory's defining module) is outside the allowlist; nothing
        outside it is imported or called.
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
    if not _is_dotted_identifier(module_path) or not callable_name.isidentifier():
        raise AdapterError(
            f"Adapter spec must be 'module.path:callable' made of Python "
            f"identifiers (no relative or dotted callable parts); got {spec!r}"
        )

    # The allowlist gate runs BEFORE any import: importing a module runs
    # its top-level code, so a refused module must never be imported.
    namespaces = allowed_adapter_namespaces()
    if not _module_is_allowed(module_path, namespaces):
        raise _not_allowed(
            module_path,
            f"Adapter module {module_path!r} is not on the adapter allowlist",
            namespaces,
        )

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

    # An allowlisted module may re-export callables defined elsewhere
    # (``from os import getcwd``); only code defined in an allowlisted
    # module is ever called.
    defined_in = getattr(factory, "__module__", None)
    if not isinstance(defined_in, str) or not _module_is_allowed(defined_in, namespaces):
        raise _not_allowed(
            module_path,
            f"{module_path}:{callable_name} is defined in {defined_in!r}, "
            f"which is not on the adapter allowlist",
            namespaces,
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


__all__ = [
    "ADAPTER_ALLOWLIST_ENV",
    "BUILTIN_ADAPTER_NAMESPACE",
    "allowed_adapter_namespaces",
    "load_adapter",
]
