"""Tests for Phase 13.x.8 per-actor decrypt-routing in _replay_encrypted.

Hand-constructs ENCRYPTED_OPT_IN payloads (no live writer produces
them yet — write path deferred per 13-D2) and verifies the decrypt
path routes to the shared encryptor when prompt_encryption_actor_id
is absent, and to the per-actor encryptor when present.
"""

from __future__ import annotations

from typing import Any

import pytest

from phoenix.ledger import cognition_replay as cr
from phoenix.ledger.encryption import reset_prompt_encryptor, set_prompt_encryptor
from phoenix.ledger.encryption_actors import (
    reset_prompt_encryptor_for_actor,
    set_prompt_encryptor_for_actor,
)


class _TagEncryptor:
    """Decrypt returns a fixed canonical form so we can assert WHICH
    encryptor was used."""

    def __init__(self, canonical: str) -> None:
        self._canonical = canonical

    def encrypt(self, canonical_form: str) -> bytes:
        return b"ct"

    def decrypt(self, encrypted: bytes) -> str:
        return self._canonical


@pytest.fixture(autouse=True)
def _reset() -> Any:
    reset_prompt_encryptor()
    reset_prompt_encryptor_for_actor()
    yield
    reset_prompt_encryptor()
    reset_prompt_encryptor_for_actor()


def test_resolve_encryptor_absent_id_uses_shared() -> None:
    """The routing helper returns the shared encryptor when the payload
    has no prompt_encryption_actor_id."""
    shared = _TagEncryptor("SHARED")
    set_prompt_encryptor(shared)  # type: ignore[arg-type]
    enc = cr._resolve_decrypt_encryptor({"prompt_encrypted": "x"})
    assert enc.decrypt(b"") == "SHARED"


def test_resolve_encryptor_present_id_uses_per_actor() -> None:
    """With prompt_encryption_actor_id set, routing returns that actor's
    encryptor."""
    shared = _TagEncryptor("SHARED")
    per_actor = _TagEncryptor("ADAM")
    set_prompt_encryptor(shared)  # type: ignore[arg-type]
    set_prompt_encryptor_for_actor("adam", per_actor)  # type: ignore[arg-type]
    enc = cr._resolve_decrypt_encryptor(
        {"prompt_encrypted": "x", "prompt_encryption_actor_id": "adam"}
    )
    assert enc.decrypt(b"") == "ADAM"


def test_resolve_encryptor_null_id_uses_shared() -> None:
    """An explicit null prompt_encryption_actor_id → shared."""
    shared = _TagEncryptor("SHARED")
    set_prompt_encryptor(shared)  # type: ignore[arg-type]
    enc = cr._resolve_decrypt_encryptor(
        {"prompt_encrypted": "x", "prompt_encryption_actor_id": None}
    )
    assert enc.decrypt(b"") == "SHARED"
