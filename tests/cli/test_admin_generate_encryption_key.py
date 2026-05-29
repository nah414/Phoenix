"""Tests for ``phoenix admin generate-encryption-key`` CLI subcommand
(Phase 13.x.7).

The CLI subcommand wraps :func:`phoenix.ledger.keygen.generate_age_keypair`
locally (no daemon round-trip). These tests exercise the handler
directly with a fake ``pyrage`` so no real crypto runs.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from phoenix.cli.config_loader import CLIConfig
from phoenix.cli.http_client import CLIHTTPClient


# ---------------------------------------------------------------------------
# Fake pyrage.x25519.Identity.generate(): same shape as test_keygen.py.


class _FakeIdentity:
    def __init__(self, secret: str) -> None:
        self.secret = secret
        self._pub_str = "age1" + hashlib.sha256(secret.encode()).hexdigest()[:30]

    @classmethod
    def from_str(cls, s: str) -> "_FakeIdentity":
        return cls(s)

    @staticmethod
    def generate() -> "_FakeIdentity":
        secret = "AGE-SECRET-KEY-" + os.urandom(8).hex().upper()
        return _FakeIdentity(secret)

    def to_public(self) -> "_FakeRecipient":
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


def _stub_config() -> CLIConfig:
    """Construct a minimal CLIConfig — the CLI handler doesn't read it."""
    return CLIConfig()


def _stub_client() -> CLIHTTPClient:
    """Construct a stub CLIHTTPClient — the CLI handler doesn't call it."""
    return CLIHTTPClient(base_url="http://localhost:8000", actor_name=None)


# ---------------------------------------------------------------------------
# Tests.


class TestGenerateEncryptionKeyCLI:
    def test_happy_path_writes_summary(
        self,
        fake_pyrage: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("PHOENIX_ENCRYPTION_KEYS_DIR", str(tmp_path))
        from phoenix.cli.commands.admin import _cmd_generate_encryption_key

        args = argparse.Namespace(name="primary", force=False, keys_dir=None)
        rc = _cmd_generate_encryption_key(args, _stub_config(), _stub_client(), "text")
        captured = capsys.readouterr()

        assert rc == 0, f"expected rc=0, got {rc}; stderr={captured.err!r}"
        assert "Identity:" in captured.out
        assert "Recipient:" in captured.out
        assert "Fingerprint:" in captured.out
        assert "restart" in captured.out.lower()
        # And the files actually got written.
        assert (tmp_path / "identity.txt").is_file()
        assert (tmp_path / "recipients" / "primary.pub").is_file()

    def test_conflict_exits_1_with_stderr_message(
        self,
        fake_pyrage: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("PHOENIX_ENCRYPTION_KEYS_DIR", str(tmp_path))
        from phoenix.cli.commands.admin import _cmd_generate_encryption_key

        args = argparse.Namespace(name="primary", force=False, keys_dir=None)
        # First call: succeeds.
        rc1 = _cmd_generate_encryption_key(args, _stub_config(), _stub_client(), "text")
        assert rc1 == 0
        capsys.readouterr()  # drain the first invocation's output

        # Second call (force=False): conflicts.
        rc2 = _cmd_generate_encryption_key(args, _stub_config(), _stub_client(), "text")
        captured = capsys.readouterr()

        assert rc2 == 1, f"expected rc=1 on conflict, got {rc2}"
        assert "already exists" in captured.err.lower()
        assert "--force" in captured.err

    def test_force_overwrites(
        self,
        fake_pyrage: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("PHOENIX_ENCRYPTION_KEYS_DIR", str(tmp_path))
        from phoenix.cli.commands.admin import _cmd_generate_encryption_key

        # First invocation lays down the keypair.
        args1 = argparse.Namespace(name="primary", force=False, keys_dir=None)
        rc1 = _cmd_generate_encryption_key(args1, _stub_config(), _stub_client(), "text")
        assert rc1 == 0
        first_identity = (tmp_path / "identity.txt").read_text()
        capsys.readouterr()  # drain

        # Second invocation with force=True must overwrite + return 0.
        args2 = argparse.Namespace(name="primary", force=True, keys_dir=None)
        rc2 = _cmd_generate_encryption_key(args2, _stub_config(), _stub_client(), "text")
        captured = capsys.readouterr()

        assert rc2 == 0, f"expected rc=0 with force, got {rc2}; stderr={captured.err!r}"
        assert "Identity:" in captured.out
        assert "Fingerprint:" in captured.out
        # The key on disk must have been replaced (fresh generate each call).
        second_identity = (tmp_path / "identity.txt").read_text()
        assert second_identity != first_identity, (
            "force=True should regenerate the identity, but it was unchanged"
        )
