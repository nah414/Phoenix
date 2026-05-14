"""Phase 9 Step 1 -- adapter scaffolding tests.

Covers the four scaffold pieces shipped at Phase 9 Step 1:

1. :class:`LoRAAdapter` Protocol -- structural shape check via
   :func:`isinstance` against :class:`IdentityAdapter`.
2. :class:`IdentityAdapter` round-trip -- encode/decode are
   pass-through; fingerprint is constant across processes.
3. :class:`AdapterError` family -- typed errors carry the
   fields the routes layer needs (``adapter_name``,
   ``failed_cases``, ``timeout_seconds``).
4. :func:`run_in_sandbox` -- subprocess timeout enforcement,
   restricted env passthrough, tempdir cleanup.

This is the verification gate for Step 1; Step 2 ships the
loader + validator + registry tests in
``test_adapters_step2.py``.
"""

from __future__ import annotations

import os
import sys

import pytest

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix.adapters import (
    AdapterAlreadyRegistered,
    AdapterError,
    AdapterNotLoaded,
    AdapterTimeoutError,
    AdapterValidationError,
    AdapterVersionMismatch,
    IdentityAdapter,
    LoRAAdapter,
    SandboxResult,
    call_adapter_method,
    make_identity_adapter,
    run_in_sandbox,
)


# ---------------------------------------------------------------------
# 1. Protocol structural shape
# ---------------------------------------------------------------------


def test_identity_adapter_satisfies_lora_protocol() -> None:
    """:class:`IdentityAdapter` must structurally satisfy the Protocol.

    The Protocol is @runtime_checkable so isinstance works for any
    duck-typed implementation. This is the gate Step 2's loader
    relies on to accept "bring your own LoRA" implementations.
    """
    adapter = IdentityAdapter()
    assert isinstance(adapter, LoRAAdapter)


def test_identity_adapter_has_required_fields() -> None:
    """The Protocol fields are populated with sensible defaults."""
    adapter = IdentityAdapter()
    assert adapter.name == "identity"
    assert adapter.version == "1.0.0"
    assert adapter.base_model_fingerprint.startswith("phoenix.identity_adapter")
    assert "identity" in adapter.capabilities


def test_make_identity_adapter_factory_overrides_name() -> None:
    """The factory builds named instances for registry collision tests."""
    a = make_identity_adapter(name="identity-a")
    b = make_identity_adapter(name="identity-b", capabilities=["echo", "test"])
    assert a.name == "identity-a"
    assert b.name == "identity-b"
    assert b.capabilities == ["echo", "test"]


# ---------------------------------------------------------------------
# 2. Identity adapter round-trip + fingerprint stability
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase",
    [
        "x",
        "abc",
        "hello world",
        "non-ascii: üñî",
        " leading-trailing ",
        "",
    ],
)
def test_identity_adapter_round_trip(phrase: str) -> None:
    """decode(encode(x)) == x for every input we care about.

    Step 2's validator depends on this: a freshly-loaded identity
    adapter must satisfy the round-trip contract trivially so the
    validator's happy path test has a known-good fixture.
    """
    adapter = IdentityAdapter()
    encoded = adapter.encode_to_grammar(phrase)
    decoded = adapter.decode_from_grammar(encoded)
    assert decoded == phrase


def test_identity_fingerprint_is_constant_across_instances() -> None:
    """Two IdentityAdapter() instances produce the same fingerprint.

    Replay-mode verification (Phase 7 Step 8) compares the loaded
    adapter's fingerprint against the recorded ledger entry. A
    "no-state" reference adapter MUST return a byte-stable identifier
    so that comparison is deterministic.
    """
    a = IdentityAdapter()
    b = IdentityAdapter()
    assert a.fingerprint() == b.fingerprint()
    # Stable hex SHA-256
    assert len(a.fingerprint()) == 64
    assert all(c in "0123456789abcdef" for c in a.fingerprint())


# ---------------------------------------------------------------------
# 3. Typed error family
# ---------------------------------------------------------------------


def test_adapter_error_hierarchy() -> None:
    """All typed errors share the AdapterError base.

    The routes layer relies on a single ``except AdapterError``
    block to catch the whole family before mapping to HTTP codes.
    """
    for cls in (
        AdapterValidationError,
        AdapterTimeoutError,
        AdapterVersionMismatch,
        AdapterNotLoaded,
        AdapterAlreadyRegistered,
    ):
        assert issubclass(cls, AdapterError)


def test_adapter_validation_error_carries_failed_cases() -> None:
    """Validation errors surface which canonical inputs round-tripped wrong."""
    err = AdapterValidationError(
        adapter_name="broken",
        failed_cases=["abc", "xyz"],
    )
    assert err.adapter_name == "broken"
    assert err.failed_cases == ["abc", "xyz"]
    assert "broken" in str(err)
    assert "abc" in str(err)


def test_adapter_timeout_error_carries_timeout_seconds() -> None:
    """Timeout errors carry the configured limit so logs name a number."""
    err = AdapterTimeoutError(adapter_name="slow", timeout_seconds=5.0)
    assert err.adapter_name == "slow"
    assert err.timeout_seconds == 5.0
    assert "5.0" in str(err)


def test_adapter_not_loaded_error_carries_adapter_id() -> None:
    err = AdapterNotLoaded("ghost")
    assert err.adapter_id == "ghost"
    assert "ghost" in str(err)


def test_adapter_already_registered_error_carries_name() -> None:
    err = AdapterAlreadyRegistered("dup")
    assert err.adapter_name == "dup"
    assert "dup" in str(err)


# ---------------------------------------------------------------------
# 4. Subprocess sandbox
# ---------------------------------------------------------------------


def test_run_in_sandbox_returns_stdout_for_short_command() -> None:
    """Happy path: a fast subprocess returns its stdout."""
    result = run_in_sandbox(
        adapter_name="hello",
        argv=[sys.executable, "-c", "print('hi')"],
        timeout_seconds=10.0,
    )
    assert isinstance(result, SandboxResult)
    assert result.returncode == 0
    assert "hi" in result.stdout
    assert result.wall_clock_seconds >= 0.0


def test_run_in_sandbox_raises_on_timeout() -> None:
    """A subprocess that exceeds timeout raises AdapterTimeoutError.

    This is the runaway-adapter trap. The subprocess gets killed
    and the typed error propagates up to the routes layer for
    HTTP 504 mapping.
    """
    with pytest.raises(AdapterTimeoutError) as excinfo:
        run_in_sandbox(
            adapter_name="hanger",
            argv=[
                sys.executable,
                "-c",
                "import time; time.sleep(30)",
            ],
            timeout_seconds=0.5,
        )
    assert excinfo.value.adapter_name == "hanger"
    assert excinfo.value.timeout_seconds == 0.5


def test_run_in_sandbox_blocks_phoenix_env_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``PHOENIX_*`` env vars must not leak into the subprocess.

    Locked OPEN-2 documents the sandbox env policy: PHOENIX_* is
    explicitly blocked so a runaway adapter can't introspect
    Phoenix runtime state (e.g., PHOENIX_REPRODUCIBILITY_MODE).
    """
    monkeypatch.setenv("PHOENIX_TEST_SECRET", "must-not-leak")
    # Use a portable probe: print PHOENIX_TEST_SECRET if set; nothing otherwise.
    result = run_in_sandbox(
        adapter_name="env-probe",
        argv=[
            sys.executable,
            "-c",
            "import os; print(os.environ.get('PHOENIX_TEST_SECRET', 'BLOCKED'))",
        ],
        timeout_seconds=10.0,
    )
    assert result.returncode == 0
    assert "BLOCKED" in result.stdout
    assert "must-not-leak" not in result.stdout


def test_run_in_sandbox_passes_path_through() -> None:
    """``PATH`` MUST pass through so the subprocess can find tools.

    The allow-list lets PATH through deliberately; the threat model
    (runaway adapter, not OS escalation) doesn't require stripping it.
    """
    result = run_in_sandbox(
        adapter_name="path-probe",
        argv=[
            sys.executable,
            "-c",
            "import os; print('HAS_PATH' if os.environ.get('PATH') else 'NO_PATH')",
        ],
        timeout_seconds=10.0,
    )
    assert result.returncode == 0
    assert "HAS_PATH" in result.stdout


def test_call_adapter_method_dispatches_to_in_process_target() -> None:
    """The in-process path invokes the adapter method directly.

    Identity adapter (no executable to launch) and unit-test stubs
    use this path. Real external adapters would ship a CLI and
    be invoked via :func:`run_in_sandbox`.
    """
    adapter = IdentityAdapter()
    out = call_adapter_method(
        adapter=adapter,
        method="encode_to_grammar",
        arg="hello",
        timeout_seconds=1.0,
    )
    assert out == "hello"


def test_call_adapter_method_rejects_missing_attribute() -> None:
    """Calling a non-existent method raises AttributeError cleanly."""
    adapter = IdentityAdapter()
    with pytest.raises(AttributeError):
        call_adapter_method(
            adapter=adapter,
            method="nonexistent_method",
            arg="hello",
            timeout_seconds=1.0,
        )


def test_run_in_sandbox_cleans_up_tempdir_on_success() -> None:
    """Sandbox tempdir must be removed after a normal completion."""
    # Capture the tempdir name by having the subprocess print os.getcwd()
    result = run_in_sandbox(
        adapter_name="cleanup",
        argv=[
            sys.executable,
            "-c",
            "import os; print(os.getcwd())",
        ],
        timeout_seconds=10.0,
    )
    cwd_printed = result.stdout.strip()
    # The tempdir was the cwd; it should be gone now.
    assert cwd_printed != ""
    assert not os.path.exists(cwd_printed)
