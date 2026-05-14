"""Phase 9 Step 2 -- adapter loader + validator + registry tests.

Covers the three pieces shipped at Step 2:

1. :class:`AdapterRegistry` -- thread-safe in-process registry
   with register/unregister/get/list + a per-adapter validation
   history ring buffer.
2. :func:`run_round_trip_validation` -- inference-time round-trip
   suite; identity adapter passes; a deliberately-broken stub
   adapter fails with the canonical inputs it broke.
3. :func:`load_adapter` -- end-to-end spec → import → validate
   → register pipeline. Failure short-circuits before
   registration so a bad adapter never enters the registry.

Step 1 tests (``test_adapters_step1.py``) cover the Protocol +
sandbox + identity adapter foundation; Step 2 builds on top.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix.adapters import (
    AdapterAlreadyRegistered,
    AdapterError,
    AdapterNotLoaded,
    AdapterRecord,
    AdapterRegistry,
    AdapterValidationError,
    IdentityAdapter,
    LoRAAdapter,
    ValidationHistoryEntry,
    get_registry,
    load_adapter,
    make_identity_adapter,
    reset_registry,
    run_round_trip_validation,
    validation_inputs,
)


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fresh_registry() -> Iterator[AdapterRegistry]:
    """Each test gets a freshly-constructed singleton.

    Without this, a test that registers "identity" would leak the
    record into the next test's ``register()`` call and trip
    :class:`AdapterAlreadyRegistered`.
    """
    reset_registry()
    yield get_registry()
    reset_registry()


# ---------------------------------------------------------------------
# 1. AdapterRegistry CRUD
# ---------------------------------------------------------------------


def test_registry_starts_empty(fresh_registry: AdapterRegistry) -> None:
    assert fresh_registry.list_adapters() == []


def test_registry_register_returns_record(
    fresh_registry: AdapterRegistry,
) -> None:
    adapter = IdentityAdapter()
    record = fresh_registry.register(adapter)
    assert isinstance(record, AdapterRecord)
    assert record.adapter is adapter
    assert record.loaded_at_unix > 0.0


def test_registry_get_returns_same_record(
    fresh_registry: AdapterRegistry,
) -> None:
    adapter = make_identity_adapter(name="echo")
    registered = fresh_registry.register(adapter)
    fetched = fresh_registry.get("echo")
    assert fetched is registered


def test_registry_get_raises_when_missing(
    fresh_registry: AdapterRegistry,
) -> None:
    with pytest.raises(AdapterNotLoaded) as excinfo:
        fresh_registry.get("ghost")
    assert excinfo.value.adapter_id == "ghost"


def test_registry_register_rejects_duplicate(
    fresh_registry: AdapterRegistry,
) -> None:
    fresh_registry.register(make_identity_adapter(name="dup"))
    with pytest.raises(AdapterAlreadyRegistered) as excinfo:
        fresh_registry.register(make_identity_adapter(name="dup"))
    assert excinfo.value.adapter_name == "dup"


def test_registry_unregister_removes(
    fresh_registry: AdapterRegistry,
) -> None:
    fresh_registry.register(make_identity_adapter(name="ghost"))
    fresh_registry.unregister("ghost")
    with pytest.raises(AdapterNotLoaded):
        fresh_registry.get("ghost")


def test_registry_unregister_raises_when_missing(
    fresh_registry: AdapterRegistry,
) -> None:
    with pytest.raises(AdapterNotLoaded):
        fresh_registry.unregister("never-loaded")


def test_registry_list_returns_snapshot(
    fresh_registry: AdapterRegistry,
) -> None:
    fresh_registry.register(make_identity_adapter(name="one"))
    fresh_registry.register(make_identity_adapter(name="two"))
    names = {r.adapter.name for r in fresh_registry.list_adapters()}
    assert names == {"one", "two"}


def test_registry_record_to_dict_exposes_public_metadata(
    fresh_registry: AdapterRegistry,
) -> None:
    record = fresh_registry.register(make_identity_adapter(name="echo"))
    payload = record.to_dict()
    # REST + admin responses depend on this shape
    assert payload["adapter_id"] == "echo"
    assert payload["name"] == "echo"
    assert payload["version"] == "1.0.0"
    assert "identity" in payload["capabilities"]
    assert len(payload["fingerprint"]) == 64
    assert payload["validation_history_length"] == 0


def test_registry_clear_drops_everything(
    fresh_registry: AdapterRegistry,
) -> None:
    fresh_registry.register(make_identity_adapter(name="a"))
    fresh_registry.register(make_identity_adapter(name="b"))
    fresh_registry.clear()
    assert fresh_registry.list_adapters() == []


def test_get_registry_is_singleton() -> None:
    """``get_registry()`` returns the same instance across calls."""
    a = get_registry()
    b = get_registry()
    assert a is b


def test_reset_registry_replaces_singleton() -> None:
    """After reset, the next get_registry() returns a fresh instance."""
    a = get_registry()
    a.register(make_identity_adapter(name="ephemeral"))
    reset_registry()
    b = get_registry()
    assert a is not b
    assert b.list_adapters() == []


# ---------------------------------------------------------------------
# 2. History ring buffer
# ---------------------------------------------------------------------


def test_history_starts_empty(fresh_registry: AdapterRegistry) -> None:
    fresh_registry.register(make_identity_adapter(name="echo"))
    assert fresh_registry.history_snapshot("echo") == []


def test_history_append_grows_snapshot(
    fresh_registry: AdapterRegistry,
) -> None:
    fresh_registry.register(make_identity_adapter(name="echo"))
    entry = ValidationHistoryEntry(
        timestamp_unix=1.0, passed=True, failed_cases=[], wall_clock_ms=2.0
    )
    fresh_registry.append_history("echo", entry)
    snapshot = fresh_registry.history_snapshot("echo")
    assert len(snapshot) == 1
    assert snapshot[0].passed is True


def test_history_snapshot_raises_when_adapter_missing(
    fresh_registry: AdapterRegistry,
) -> None:
    with pytest.raises(AdapterNotLoaded):
        fresh_registry.history_snapshot("ghost")


def test_history_append_silently_noops_when_adapter_missing(
    fresh_registry: AdapterRegistry,
) -> None:
    """Race-avoidance: validator's append shouldn't crash if the
    adapter was unloaded between validation start and append.
    """
    entry = ValidationHistoryEntry(timestamp_unix=1.0, passed=True)
    # Adapter "phantom" was never registered -- append must not raise.
    fresh_registry.append_history("phantom", entry)


def test_history_to_dict_round_trip() -> None:
    entry = ValidationHistoryEntry(
        timestamp_unix=1.5,
        passed=False,
        failed_cases=["x", "abc"],
        wall_clock_ms=12.34,
    )
    payload = entry.to_dict()
    assert payload == {
        "timestamp_unix": 1.5,
        "passed": False,
        "failed_cases": ["x", "abc"],
        "wall_clock_ms": 12.34,
    }


# ---------------------------------------------------------------------
# 3. Thread safety
# ---------------------------------------------------------------------


def test_registry_thread_safe_register(
    fresh_registry: AdapterRegistry,
) -> None:
    """Concurrent registers from N threads land N distinct adapters.

    The registry's RLock means no two threads ever observe a
    half-registered record. Each thread uses a unique name so
    none of them race over the same slot.
    """
    errors: list[BaseException] = []

    def worker(idx: int) -> None:
        try:
            fresh_registry.register(make_identity_adapter(name=f"thread-{idx}"))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent registers raised: {errors!r}"
    names = {r.adapter.name for r in fresh_registry.list_adapters()}
    assert names == {f"thread-{i}" for i in range(8)}


# ---------------------------------------------------------------------
# 4. Validator -- happy path
# ---------------------------------------------------------------------


def test_validator_passes_identity_adapter() -> None:
    """Identity adapter satisfies the round-trip suite trivially.

    This is the contract Step 2's loader depends on -- the
    reference adapter must be loadable without LoRA weights, so
    its validator pass is the canonical happy-path fixture.
    """
    entry = run_round_trip_validation(IdentityAdapter())
    assert entry.passed is True
    assert entry.failed_cases == []
    assert entry.wall_clock_ms >= 0.0
    assert entry.timestamp_unix > 0.0


def test_validation_inputs_is_nonempty_tuple() -> None:
    """The canonical-input set must be a non-empty immutable tuple."""
    inputs = validation_inputs()
    assert isinstance(inputs, tuple)
    assert len(inputs) >= 3
    assert all(isinstance(s, str) for s in inputs)


# ---------------------------------------------------------------------
# 5. Validator -- broken-adapter paths
# ---------------------------------------------------------------------


@dataclass
class _DropsWhitespaceAdapter:
    """A deliberately-broken stub: encode_to_grammar strips whitespace.

    This breaks the "hello world" and " leading-trailing " canonical
    inputs because the decoded value differs from the original.
    """

    name: str = "broken-strip"
    version: str = "0.0.0"
    base_model_fingerprint: str = "test"
    capabilities: list[str] = field(default_factory=lambda: ["test"])

    def encode_to_grammar(self, natural_language: str) -> str:
        return natural_language.strip().replace(" ", "")

    def decode_from_grammar(self, tokens: str) -> str:
        return tokens

    def fingerprint(self) -> str:
        return "broken-strip-fingerprint"


@dataclass
class _RaisesAdapter:
    """A deliberately-broken stub: encode raises on every call."""

    name: str = "broken-raises"
    version: str = "0.0.0"
    base_model_fingerprint: str = "test"
    capabilities: list[str] = field(default_factory=lambda: ["test"])

    def encode_to_grammar(self, natural_language: str) -> str:
        raise RuntimeError(f"broken encoder, input={natural_language!r}")

    def decode_from_grammar(self, tokens: str) -> str:
        return tokens

    def fingerprint(self) -> str:
        return "broken-raises-fingerprint"


def test_validator_marks_failing_cases() -> None:
    """A broken adapter reports the specific inputs it dropped.

    The broken adapter strips whitespace, so the canonical inputs
    that contain whitespace ("hello world", " leading-trailing ")
    must appear in ``failed_cases``; the others pass cleanly.
    """
    entry = run_round_trip_validation(_DropsWhitespaceAdapter())
    assert entry.passed is False
    assert "hello world" in entry.failed_cases
    assert " leading-trailing " in entry.failed_cases
    # The single-token inputs without whitespace still round-trip
    assert "x" not in entry.failed_cases
    assert "abc" not in entry.failed_cases


def test_validator_treats_exceptions_as_failures() -> None:
    """If encode/decode raises, the validator records the failure
    without propagating the exception. The loader decides whether
    to refuse.
    """
    entry = run_round_trip_validation(_RaisesAdapter())
    assert entry.passed is False
    # Every canonical input raised -> every input is in failed_cases
    assert set(entry.failed_cases) == set(validation_inputs())


# ---------------------------------------------------------------------
# 6. Loader -- spec parsing
# ---------------------------------------------------------------------


def test_load_adapter_loads_identity_via_module_spec(
    fresh_registry: AdapterRegistry,
) -> None:
    """End-to-end: spec -> import -> validate -> register."""
    record = load_adapter("phoenix.adapters.identity_adapter:make_identity_adapter")
    assert isinstance(record, AdapterRecord)
    assert record.adapter.name == "identity"
    assert isinstance(record.adapter, LoRAAdapter)
    # Registry now contains it
    assert fresh_registry.get("identity") is record
    # History ring captured the validation
    history = fresh_registry.history_snapshot("identity")
    assert len(history) == 1
    assert history[0].passed is True


def test_load_adapter_rejects_empty_spec() -> None:
    with pytest.raises(AdapterError):
        load_adapter("")


def test_load_adapter_rejects_spec_without_colon() -> None:
    with pytest.raises(AdapterError) as excinfo:
        load_adapter("phoenix.adapters.identity_adapter")
    assert "module.path:callable" in str(excinfo.value)


def test_load_adapter_rejects_filesystem_path() -> None:
    """Phase 9 v1 doesn't ship file-path imports."""
    with pytest.raises(NotImplementedError):
        load_adapter("/tmp/my_adapter.py")
    with pytest.raises(NotImplementedError):
        load_adapter("./my_adapter.py")
    with pytest.raises(NotImplementedError):
        load_adapter("C:/path/adapter.py")


def test_load_adapter_rejects_missing_module() -> None:
    with pytest.raises(AdapterError) as excinfo:
        load_adapter("nonexistent.module.path:thing")
    assert "Cannot import" in str(excinfo.value)


def test_load_adapter_rejects_missing_callable() -> None:
    with pytest.raises(AdapterError) as excinfo:
        load_adapter("phoenix.adapters.identity_adapter:not_a_thing")
    assert "not_a_thing" in str(excinfo.value)


def test_load_adapter_rejects_non_callable_target() -> None:
    with pytest.raises(AdapterError) as excinfo:
        # _IDENTITY_FINGERPRINT_SEED is a string constant, not callable
        load_adapter("phoenix.adapters.identity_adapter:_IDENTITY_FINGERPRINT_SEED")
    assert "not callable" in str(excinfo.value)


# ---------------------------------------------------------------------
# 7. Loader -- validation gating
# ---------------------------------------------------------------------


def _make_broken_factory_module() -> None:
    """No-op; placeholder so the next test's module spec resolves."""


# We register a broken-adapter factory on the test module so
# load_adapter's importlib path can find it.
def _broken_adapter_factory() -> _DropsWhitespaceAdapter:
    return _DropsWhitespaceAdapter()


def test_load_adapter_refuses_failing_validation(
    fresh_registry: AdapterRegistry,
) -> None:
    """If validation fails, the adapter is NOT registered.

    The loader raises ``AdapterValidationError`` and leaves the
    registry untouched -- a broken adapter never sees real traffic.
    """
    with pytest.raises(AdapterValidationError) as excinfo:
        load_adapter("tests.integration.test_adapters_step2:_broken_adapter_factory")
    assert excinfo.value.adapter_name == "broken-strip"
    assert "hello world" in excinfo.value.failed_cases
    # Registry stayed empty -- the broken adapter never registered
    assert fresh_registry.list_adapters() == []


def test_load_adapter_with_validate_false_skips_round_trip(
    fresh_registry: AdapterRegistry,
) -> None:
    """``validate=False`` registers without exercising the validator.

    Reserved for the test path that needs to populate the registry
    without paying the validation cost; production callers always
    pass ``validate=True`` (the default).
    """
    record = load_adapter(
        "tests.integration.test_adapters_step2:_broken_adapter_factory",
        validate=False,
    )
    assert record.adapter.name == "broken-strip"
    # No history entry was appended (validation was skipped)
    assert fresh_registry.history_snapshot("broken-strip") == []
