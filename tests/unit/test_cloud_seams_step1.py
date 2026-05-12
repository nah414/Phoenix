"""Phase 10 Step 1 -- cloud_seams Protocol shells + registry tests.

Covers the three Protocol definitions, the BudgetDecision dataclass,
the generic name-keyed CloudSeams registry, the UnknownSeam error,
and the get_seams() / reset_seams() singleton pattern.

The Step 1 stub impls (which raise NotImplementedError until the
real impls land in Steps 4 + 9) are also exercised here so the
"shell is wired in" contract is verified end-to-end.
"""

from __future__ import annotations

import pytest

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix._internal.cloud_seams import (
    AuditLogExporter,
    BudgetDecision,
    CloudSeams,
    HttpAuthExtractor,
    JobBudgetController,
    UnknownSeam,
    get_seams,
    reset_seams,
)


# ---------------------------------------------------------------------
# 1. BudgetDecision dataclass shape
# ---------------------------------------------------------------------


def test_budget_decision_fields() -> None:
    decision = BudgetDecision(
        allowed=True,
        remaining_usd=42.5,
        rationale="under per_solve ceiling",
        ceiling_applied_usd=50.0,
    )
    assert decision.allowed is True
    assert decision.remaining_usd == 42.5
    assert decision.rationale == "under per_solve ceiling"
    assert decision.ceiling_applied_usd == 50.0


def test_budget_decision_is_frozen() -> None:
    """Decisions are immutable so audit logs can't be mutated mid-trace."""
    decision = BudgetDecision(
        allowed=True, remaining_usd=1.0, rationale="", ceiling_applied_usd=1.0
    )
    with pytest.raises(Exception):
        decision.allowed = False  # type: ignore[misc]


# ---------------------------------------------------------------------
# 2. UnknownSeam is a KeyError subclass
# ---------------------------------------------------------------------


def test_unknown_seam_subclasses_key_error() -> None:
    """Callers that wrap dict lookups in `except KeyError` Just Work."""
    err = UnknownSeam("ghost")
    assert isinstance(err, KeyError)


# ---------------------------------------------------------------------
# 3. CloudSeams registry behaviour
# ---------------------------------------------------------------------


class TestCloudSeamsRegistry:
    def test_default_constructor_registers_three_builtin_seams(self) -> None:
        seams = CloudSeams()
        names = seams.names()
        assert "auth" in names
        assert "audit" in names
        assert "budget" in names

    def test_register_replaces_existing_impl(self) -> None:
        seams = CloudSeams()

        class _MyImpl:
            pass

        my = _MyImpl()
        seams.register("audit", my)
        assert seams.get("audit") is my

    def test_register_accepts_unknown_name(self) -> None:
        """Section 10.3.1 extension discipline: unknown names register silently."""
        seams = CloudSeams()

        class _FutureSeam:
            pass

        impl = _FutureSeam()
        seams.register("canonical_library", impl)
        assert seams.get("canonical_library") is impl
        assert "canonical_library" in seams.names()

    def test_register_rejects_empty_name(self) -> None:
        seams = CloudSeams()
        with pytest.raises(ValueError):
            seams.register("", object())

    def test_register_rejects_non_string_name(self) -> None:
        seams = CloudSeams()
        with pytest.raises(ValueError):
            seams.register(None, object())  # type: ignore[arg-type]

    def test_get_unknown_raises_unknown_seam(self) -> None:
        seams = CloudSeams()
        with pytest.raises(UnknownSeam):
            seams.get("never-registered")

    def test_reset_to_defaults_drops_overrides(self) -> None:
        seams = CloudSeams()
        seams.register("canonical_library", object())
        seams.register("audit", object())
        seams.reset_to_defaults()
        # canonical_library override dropped
        assert "canonical_library" not in seams.names()
        # audit returns to the Step 1 stub
        audit = seams.get("audit")
        assert isinstance(audit, AuditLogExporter)


# ---------------------------------------------------------------------
# 4. Singleton lifecycle
# ---------------------------------------------------------------------


class TestSingleton:
    def test_get_seams_is_singleton(self) -> None:
        reset_seams()
        a = get_seams()
        b = get_seams()
        assert a is b
        reset_seams()

    def test_reset_replaces_singleton(self) -> None:
        reset_seams()
        a = get_seams()
        a.register("canonical_library", object())
        reset_seams()
        b = get_seams()
        assert a is not b
        # Fresh singleton has no canonical_library
        assert "canonical_library" not in b.names()


# ---------------------------------------------------------------------
# 5. Step 1 stub impls -- registered AND raise on call
# ---------------------------------------------------------------------


class TestStep1Stubs:
    def test_auth_stub_satisfies_protocol_structurally(self) -> None:
        seams = CloudSeams()
        impl = seams.get("auth")
        # Protocol is @runtime_checkable
        assert isinstance(impl, HttpAuthExtractor)

    def test_audit_stub_satisfies_protocol_structurally(self) -> None:
        seams = CloudSeams()
        impl = seams.get("audit")
        assert isinstance(impl, AuditLogExporter)

    def test_budget_stub_satisfies_protocol_structurally(self) -> None:
        seams = CloudSeams()
        impl = seams.get("budget")
        assert isinstance(impl, JobBudgetController)

    def test_auth_stub_raises_not_implemented(self) -> None:
        seams = CloudSeams()
        impl = seams.get("auth")
        with pytest.raises(NotImplementedError) as excinfo:
            impl.extract_actor(request=object())
        assert "Step 9" in str(excinfo.value)

    def test_audit_export_stub_raises(self) -> None:
        seams = CloudSeams()
        impl = seams.get("audit")
        with pytest.raises(NotImplementedError):
            impl.export(event=object())  # type: ignore[arg-type]

    def test_audit_flush_stub_raises(self) -> None:
        seams = CloudSeams()
        impl = seams.get("audit")
        with pytest.raises(NotImplementedError):
            impl.flush(timeout_s=1.0)

    def test_budget_impl_is_real_local_controller_after_step_4(self) -> None:
        """Step 4 swapped the stub out for LocalJobBudgetController."""
        from phoenix._internal.cloud_seams import LocalJobBudgetController

        seams = CloudSeams()
        impl = seams.get("budget")
        assert isinstance(impl, LocalJobBudgetController)


# ---------------------------------------------------------------------
# 6. Thread safety smoke
# ---------------------------------------------------------------------


def test_register_is_thread_safe() -> None:
    """N concurrent registers of distinct names land N entries."""
    import threading

    seams = CloudSeams()
    errors: list[BaseException] = []

    def _worker(idx: int) -> None:
        try:
            seams.register(f"extension_{idx}", object())
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    names = set(seams.names())
    assert {f"extension_{i}" for i in range(8)}.issubset(names)
