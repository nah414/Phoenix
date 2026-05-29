"""Tests for ``phoenix.ledger.keygen`` (Phase 13.x.7).

GPU SAFETY: Uses a fake ``pyrage`` module installed via sys.modules
monkeypatch (same pattern as ``test_encryption_age.py``). No real
crypto is performed; the fake produces structurally-valid
identity/recipient strings via SHA-256-truncated derivation.
"""

from __future__ import annotations

import hashlib
import os
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from phoenix.ledger.keygen import (
    GeneratedKeyPair,
    KeyGenError,
    KeyGenPathConflict,
    generate_age_keypair,
)


# ---------------------------------------------------------------------------
# Fake pyrage.x25519.Identity.generate(): same shape as PR #21's test stubs.


class _FakeIdentity:
    """Mirrors pyrage.x25519.Identity: from_str + to_public + str()."""

    def __init__(self, secret: str) -> None:
        self.secret = secret
        self._pub_str = "age1" + hashlib.sha256(secret.encode()).hexdigest()[:30]

    @classmethod
    def from_str(cls, s: str) -> _FakeIdentity:
        return cls(s)

    @staticmethod
    def generate() -> _FakeIdentity:
        secret = "AGE-SECRET-KEY-" + os.urandom(8).hex().upper()
        return _FakeIdentity(secret)

    def to_public(self) -> _FakeRecipient:
        return _FakeRecipient(self._pub_str)

    def __str__(self) -> str:
        return self.secret


class _FakeRecipient:
    def __init__(self, pub_str: str) -> None:
        self.pub = pub_str

    def __str__(self) -> str:
        return self.pub


@pytest.fixture
def fake_pyrage(monkeypatch: pytest.MonkeyPatch) -> Any:
    fake: Any = types.ModuleType("pyrage")
    fake_x25519: Any = types.ModuleType("pyrage.x25519")
    fake_x25519.Identity = _FakeIdentity
    fake_x25519.Recipient = MagicMock()
    fake.x25519 = fake_x25519
    monkeypatch.setitem(sys.modules, "pyrage", fake)
    monkeypatch.setitem(sys.modules, "pyrage.x25519", fake_x25519)
    return fake


class TestGenerateAgeKeyPair:
    def test_happy_path_writes_both_files(self, fake_pyrage: Any, tmp_path: Path) -> None:
        result = generate_age_keypair(keys_dir=tmp_path, name="primary")
        assert isinstance(result, GeneratedKeyPair)
        assert result.identity_path == tmp_path / "identity.txt"
        assert result.recipient_path == tmp_path / "recipients" / "primary.pub"
        assert result.identity_path.is_file()
        assert result.recipient_path.is_file()
        assert result.identity_path.read_text().startswith("AGE-SECRET-KEY-")
        assert result.recipient_path.read_text().startswith("age1")

    def test_named_keypair_uses_identity_suffixed_filename(
        self, fake_pyrage: Any, tmp_path: Path
    ) -> None:
        result = generate_age_keypair(keys_dir=tmp_path, name="rotation-v2")
        assert result.identity_path == tmp_path / "identity-rotation-v2.txt"
        assert result.recipient_path == tmp_path / "recipients" / "rotation-v2.pub"

    def test_creates_recipients_dir_if_missing(self, fake_pyrage: Any, tmp_path: Path) -> None:
        assert not (tmp_path / "recipients").exists()
        generate_age_keypair(keys_dir=tmp_path, name="primary")
        assert (tmp_path / "recipients").is_dir()

    def test_posix_identity_file_mode_is_0o600(self, fake_pyrage: Any, tmp_path: Path) -> None:
        if sys.platform == "win32":
            pytest.skip("POSIX permission check")
        result = generate_age_keypair(keys_dir=tmp_path, name="primary")
        mode = result.identity_path.stat().st_mode & 0o777
        assert mode == 0o600, f"mode {oct(mode)} != 0o600"

    def test_fingerprint_is_16_hex_chars(self, fake_pyrage: Any, tmp_path: Path) -> None:
        result = generate_age_keypair(keys_dir=tmp_path, name="primary")
        assert len(result.identity_fingerprint) == 16
        assert all(c in "0123456789abcdef" for c in result.identity_fingerprint)
        assert result.identity_fingerprint == result.recipient_fingerprint

    def test_refuses_to_overwrite_existing_identity(self, fake_pyrage: Any, tmp_path: Path) -> None:
        generate_age_keypair(keys_dir=tmp_path, name="primary")
        with pytest.raises(KeyGenPathConflict, match="already exists"):
            generate_age_keypair(keys_dir=tmp_path, name="primary", force=False)

    def test_force_overwrites_existing_identity(self, fake_pyrage: Any, tmp_path: Path) -> None:
        first = generate_age_keypair(keys_dir=tmp_path, name="primary")
        first_secret = first.identity_path.read_text()
        second = generate_age_keypair(keys_dir=tmp_path, name="primary", force=True)
        second_secret = second.identity_path.read_text()
        assert first_secret != second_secret

    def test_default_keys_dir_used_when_keys_dir_none(
        self, fake_pyrage: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("PHOENIX_ENCRYPTION_KEYS_DIR", str(tmp_path))
        result = generate_age_keypair(keys_dir=None, name="primary")
        assert result.identity_path == tmp_path / "identity.txt"

    def test_bad_name_slug_raises(self, fake_pyrage: Any, tmp_path: Path) -> None:
        with pytest.raises(KeyGenError, match="name"):
            generate_age_keypair(keys_dir=tmp_path, name="../escape")
        with pytest.raises(KeyGenError, match="name"):
            generate_age_keypair(keys_dir=tmp_path, name=".hidden")
        with pytest.raises(KeyGenError, match="name"):
            generate_age_keypair(keys_dir=tmp_path, name="")
