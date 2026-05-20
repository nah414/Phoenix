"""Tests for :mod:`phoenix.ledger.encryption` (Phase 13 Step 8).

Per 13-D2 + [OPEN: P13-3] default: Phase 13 ships the column + the
encrypt/decrypt Protocol surface; the customer-key-management
ceremony lands later. These tests verify:

- The default :class:`NullPromptEncryptor` raises
  :class:`EncryptedDispositionNotConfigured` on both encrypt and
  decrypt (the load-bearing "not configured" failure mode).
- The singleton-replacement setter
  (:func:`set_prompt_encryptor`) works for swapping in a real impl
  later (or a test double now).
- :func:`reset_prompt_encryptor` restores the null default
  (test-isolation helper).
- The Protocol is :func:`runtime_checkable` so an impl that
  structurally satisfies it registers as :class:`PromptEncryptor`.
"""

from __future__ import annotations

import pytest

from phoenix.ledger.encryption import (
    EncryptedDispositionNotConfigured,
    NullPromptEncryptor,
    PromptEncryptor,
    get_prompt_encryptor,
    reset_prompt_encryptor,
    set_prompt_encryptor,
)


@pytest.fixture(autouse=True)
def restore_default_encryptor() -> None:
    """Reset the module-level singleton after every test."""
    yield
    reset_prompt_encryptor()


def test_default_encryptor_is_null_impl() -> None:
    """Fresh process state: the encryptor is the NullPromptEncryptor."""
    enc = get_prompt_encryptor()
    assert isinstance(enc, NullPromptEncryptor)


def test_null_encryptor_raises_on_encrypt() -> None:
    enc = NullPromptEncryptor()
    with pytest.raises(EncryptedDispositionNotConfigured):
        enc.encrypt("any canonical form")


def test_null_encryptor_raises_on_decrypt() -> None:
    enc = NullPromptEncryptor()
    with pytest.raises(EncryptedDispositionNotConfigured):
        enc.decrypt(b"any blob")


def test_encrypted_disposition_not_configured_default_message() -> None:
    """The default error message points to the configuration site."""
    err = EncryptedDispositionNotConfigured()
    msg = str(err)
    assert "ENCRYPTED_OPT_IN" in msg
    assert "set_prompt_encryptor" in msg


def test_encrypted_disposition_not_configured_custom_message() -> None:
    err = EncryptedDispositionNotConfigured("custom reason")
    assert str(err) == "custom reason"


def test_set_prompt_encryptor_swaps_singleton() -> None:
    """Installing a real-shaped encryptor is one call."""

    class _TestEncryptor:
        def encrypt(self, canonical_form: str) -> bytes:
            return canonical_form.encode("utf-8")[::-1]

        def decrypt(self, encrypted: bytes) -> str:
            return encrypted[::-1].decode("utf-8")

    set_prompt_encryptor(_TestEncryptor())
    enc = get_prompt_encryptor()
    assert not isinstance(enc, NullPromptEncryptor)
    assert enc.encrypt("hello") == b"olleh"
    assert enc.decrypt(b"olleh") == "hello"


def test_reset_restores_null_default() -> None:
    """After reset, the singleton is the NullPromptEncryptor again."""

    class _Dummy:
        def encrypt(self, canonical_form: str) -> bytes:  # pragma: no cover
            return b""

        def decrypt(self, encrypted: bytes) -> str:  # pragma: no cover
            return ""

    set_prompt_encryptor(_Dummy())
    assert not isinstance(get_prompt_encryptor(), NullPromptEncryptor)
    reset_prompt_encryptor()
    assert isinstance(get_prompt_encryptor(), NullPromptEncryptor)


def test_protocol_runtime_checkable() -> None:
    """A class structurally satisfying PromptEncryptor passes isinstance check."""

    class _Structural:
        def encrypt(self, canonical_form: str) -> bytes:
            return b""

        def decrypt(self, encrypted: bytes) -> str:
            return ""

    assert isinstance(_Structural(), PromptEncryptor)


def test_null_encryptor_satisfies_protocol() -> None:
    """The null impl itself satisfies the Protocol (returns errors, but shape matches)."""
    assert isinstance(NullPromptEncryptor(), PromptEncryptor)
