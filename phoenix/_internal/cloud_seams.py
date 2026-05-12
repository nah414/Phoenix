"""Phoenix Cloud abstraction seams (Phase 10 Step 1, Section 10.3.1).

Per architecture v1 Section 10.3.1: Phoenix v1 ships three thin
``typing.Protocol`` definitions that let Phoenix Cloud (a future
*separate* product) wrap Phoenix-the-middleware with multi-tenant
hosting + the commercial bundle (enterprise SSO, audit-log retention
SLA, white-glove drift recalibration, dedicated provider rate
contracts) without modifying core Phoenix.

**The three v1 seams:**

- ``auth`` -- :class:`HttpAuthExtractor`. Where the front door reads
  the Actor from a request. Default impl reads the
  ``X-Phoenix-Actor`` / ``Authorization: Phoenix-Actor`` header.
  Phoenix Cloud's impl reads a tenant-scoped session cookie.
- ``audit`` -- :class:`AuditLogExporter`. Where structured audit
  events flow on emit. Default impl appends to the local JSONL
  writer + the OTel adapter. Phoenix Cloud's impl additionally
  writes to a tamper-evident retention store.
- ``budget`` -- :class:`JobBudgetController`. Where per-job cost
  ceilings are enforced. Default impl reads/writes the local
  ``solve_cost_ledger`` table and enforces Section 4.7's defaults
  (per-solve, per-actor-24h, per-org-24h ceilings). Phoenix Cloud's
  impl additionally consults the tenant's commercial contract.

The :class:`CloudSeams` registry is **generic name-keyed**, not
hardcoded with three named slots. v1.x extensions register
additional seams (e.g., ``canonical_library`` for the perception
harness extension) without modifying core. The core module knows
nothing about future seam names.

**Safety invariant:** Seam Protocols deliberately do NOT expose
mutation surfaces beyond what each seam owns. A Phoenix Cloud
implementation cannot inject a fake Actor for a different tenant
via :class:`HttpAuthExtractor` -- the returned Actor is still
HMAC-verified at Section 7's safety gate. Similarly,
:class:`JobBudgetController` cannot *suppress* safety-gate denials;
it can only deny solves that would otherwise be allowed. Every new
seam must preserve this defense-in-depth.

**Step 1 ships shells:** the Protocols, the registry, and stub
default impls that raise :class:`NotImplementedError` when called.
Step 4 lands the real ``LocalJobBudgetController``; Step 9 lands
the real ``LocalHttpAuthExtractor`` + ``LocalAuditLogExporter``.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from actor.actor import Actor

    from phoenix.audit import AuditEvent


# ---------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------


class UnknownSeam(KeyError):
    """Raised by :meth:`CloudSeams.get` when ``name`` isn't registered.

    Subclass of :class:`KeyError` so callers that wrap dict-shaped
    lookups don't need to special-case this error.
    """


# ---------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class BudgetDecision:
    """Outcome of :meth:`JobBudgetController.check_solve_budget`.

    Fields:
      - ``allowed`` (bool) -- whether the solve may proceed.
      - ``remaining_usd`` (float) -- spend headroom under the tightest
        ceiling that applied (0.0 when denied; positive when allowed).
      - ``rationale`` (str) -- human-readable explanation. When denied,
        names WHICH ceiling tripped (``"per_solve"`` |
        ``"per_actor_24h"`` | ``"per_org_24h"``).
      - ``ceiling_applied_usd`` (float) -- the actual ceiling value the
        decision used (after admin overrides + env-var overrides
        resolved). Surfaced into routing provenance so the user sees
        what budget was actually in effect.
    """

    allowed: bool
    remaining_usd: float
    rationale: str
    ceiling_applied_usd: float


# ---------------------------------------------------------------------
# Seam Protocols
# ---------------------------------------------------------------------


@runtime_checkable
class HttpAuthExtractor(Protocol):
    """Seam 1 -- extract the verified Actor from transport metadata.

    Called once per HTTP request, before Section 7's safety gate runs.
    Returns a verified Actor; raises :class:`AuthError` on missing or
    invalid credentials.

    Phase 9 v1 default impl wraps :func:`phoenix.identity.bootstrap
    .extract_or_bootstrap` against the standard
    ``Authorization: Phoenix-Actor <payload>`` header.
    """

    def extract_actor(self, request: Any) -> "Actor":
        """Return a verified Actor from transport metadata.

        ``request`` is the FastAPI/Starlette ``Request`` object;
        typed as ``Any`` here to keep the seam Protocol decoupled
        from FastAPI specifics.
        """
        ...


@runtime_checkable
class AuditLogExporter(Protocol):
    """Seam 2 -- persist a structured audit event.

    ``export`` MUST be fire-and-forget non-blocking and MUST NOT raise
    to the caller (errors go to Phoenix's internal error log instead).
    ``flush`` is called on graceful shutdown and on kill-switch engage.
    """

    def export(self, event: "AuditEvent") -> None:
        """Persist an audit event. Non-blocking, never raises."""
        ...

    def flush(self, timeout_s: float) -> bool:
        """Block until pending events are durably written, or timeout.

        Returns True if all events flushed, False if timeout reached.
        """
        ...


@runtime_checkable
class JobBudgetController(Protocol):
    """Seam 3 -- check + record per-job resource budgets.

    The default impl enforces Section 4.7's defaults (per-solve,
    per-actor-24h, per-org-24h ceilings). Phoenix Cloud's impl
    additionally consults the tenant's commercial contract and emits
    billing events.

    **Safety:** the seam can only DENY solves that would otherwise be
    allowed by the safety gate. It CANNOT bypass safety-gate denials.
    """

    def check_solve_budget(
        self,
        actor: "Actor",
        estimated_cost_usd: float,
        reproducibility_mode: str,
    ) -> BudgetDecision:
        """Decide whether to allow the solve. Called by the Router."""
        ...

    def record_solve_cost(
        self,
        actor: "Actor",
        request_id: str,
        actual_cost_usd: float,
        provenance: dict[str, Any],
    ) -> None:
        """Record measured solve cost. Called post-solve from Orchestrate."""
        ...


# ---------------------------------------------------------------------
# Step 1 stub implementations
# ---------------------------------------------------------------------


_STEP_1_STUB_MESSAGE = (
    "Phoenix v1 Phase 10 Step 1 ships the cloud_seams shells only; "
    "the real default impl lands in a later step. "
    "Either wait for that step to land or register a custom impl via "
    "phoenix._internal.cloud_seams.get_seams().register(name, impl)."
)


class _Step1AuthStub:
    """Stub :class:`HttpAuthExtractor` -- raises until Step 9."""

    def extract_actor(self, request: Any) -> "Actor":
        raise NotImplementedError(
            f"LocalHttpAuthExtractor: {_STEP_1_STUB_MESSAGE} (Real impl lands in Phase 10 Step 9.)"
        )


class _Step1AuditStub:
    """Stub :class:`AuditLogExporter` -- raises until Step 9."""

    def export(self, event: "AuditEvent") -> None:
        raise NotImplementedError(
            f"LocalAuditLogExporter: {_STEP_1_STUB_MESSAGE} (Real impl lands in Phase 10 Step 9.)"
        )

    def flush(self, timeout_s: float) -> bool:
        raise NotImplementedError(
            f"LocalAuditLogExporter: {_STEP_1_STUB_MESSAGE} (Real impl lands in Phase 10 Step 9.)"
        )


class _Step1BudgetStub:
    """Stub :class:`JobBudgetController` -- raises until Step 4."""

    def check_solve_budget(
        self,
        actor: "Actor",
        estimated_cost_usd: float,
        reproducibility_mode: str,
    ) -> BudgetDecision:
        raise NotImplementedError(
            f"LocalJobBudgetController: {_STEP_1_STUB_MESSAGE} "
            "(Real impl lands in Phase 10 Step 4.)"
        )

    def record_solve_cost(
        self,
        actor: "Actor",
        request_id: str,
        actual_cost_usd: float,
        provenance: dict[str, Any],
    ) -> None:
        raise NotImplementedError(
            f"LocalJobBudgetController: {_STEP_1_STUB_MESSAGE} "
            "(Real impl lands in Phase 10 Step 4.)"
        )


# ---------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------


_BUILTIN_SEAM_NAMES = frozenset({"auth", "audit", "budget"})


class CloudSeams:
    """Generic name-keyed registry of cloud seam Protocol implementations.

    Default constructor registers Phoenix v1's three seams
    (``"auth"``, ``"audit"``, ``"budget"``) with Step 1's stub impls.
    Steps 4 + 9 swap the stubs out for real default impls. Phoenix
    Cloud's process startup overrides specific seams via
    :meth:`register` before opening the front door.

    Generic-by-design (not hardcoded with three named slots) so v1.x
    extensions can register additional seams without modifying core.
    The perception harness extension (v1.1) plans an optional
    ``canonical_library`` seam for retention-SLA-bearing canonical
    example libraries.
    """

    def __init__(self) -> None:
        self._impls: dict[str, Any] = {}
        self._lock = threading.RLock()
        # Phase 10 Step 1: register stubs. Steps 4 + 9 replace.
        self._impls["auth"] = _Step1AuthStub()
        self._impls["audit"] = _Step1AuditStub()
        self._impls["budget"] = _Step1BudgetStub()

    def register(self, name: str, impl: Any) -> None:
        """Replace (or register) the implementation for ``name``.

        Phoenix Cloud calls this once per seam at process startup
        before the front door opens. Calling :meth:`register` with an
        unknown name succeeds silently per Section 10.3.1's
        "extension discipline" -- consumers that don't know the seam
        exists won't call :meth:`get`.
        """
        if not isinstance(name, str) or not name:
            raise ValueError(f"Seam name must be a non-empty string; got {name!r}")
        with self._lock:
            self._impls[name] = impl

    def get(self, name: str) -> Any:
        """Return the active impl for ``name``.

        Raises :class:`UnknownSeam` if ``name`` was never registered.
        Phoenix code calls this lazily, on demand (not at import
        time) so the registry can be swapped before first use.
        """
        with self._lock:
            if name not in self._impls:
                raise UnknownSeam(
                    f"No implementation registered for seam {name!r}; "
                    f"known seams: {sorted(self._impls)}"
                )
            return self._impls[name]

    def names(self) -> list[str]:
        """Return registered seam names (for dev-ops introspection)."""
        with self._lock:
            return sorted(self._impls.keys())

    def reset_to_defaults(self) -> None:
        """Drop all registrations and re-register Step 1 stubs.

        Test-isolation helper. Does NOT replace the singleton itself;
        use :func:`reset_seams` for that.
        """
        with self._lock:
            self._impls.clear()
            self._impls["auth"] = _Step1AuthStub()
            self._impls["audit"] = _Step1AuditStub()
            self._impls["budget"] = _Step1BudgetStub()


# ---------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------


_SEAMS: CloudSeams | None = None
_SEAMS_LOCK = threading.Lock()


def get_seams() -> CloudSeams:
    """Return the lazily-constructed singleton :class:`CloudSeams`."""
    global _SEAMS
    with _SEAMS_LOCK:
        if _SEAMS is None:
            _SEAMS = CloudSeams()
        return _SEAMS


def reset_seams() -> None:
    """Drop the singleton. Test-isolation helper."""
    global _SEAMS
    with _SEAMS_LOCK:
        _SEAMS = None


__all__ = [
    "AuditLogExporter",
    "BudgetDecision",
    "CloudSeams",
    "HttpAuthExtractor",
    "JobBudgetController",
    "UnknownSeam",
    "get_seams",
    "reset_seams",
]
