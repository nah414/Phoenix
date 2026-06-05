"""Tests for `phoenix admin generate-encryption-key --actor <name>` (Phase 13.x.8)."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import types
from pathlib import Path
from typing import Any

import pytest


class _FakeIdentity:
    def __init__(self, secret: str) -> None:
        self.secret = secret
        self._pub = "age1" + hashlib.sha256(secret.encode()).hexdigest()[:30]

    @staticmethod
    def generate() -> "_FakeIdentity":
        return _FakeIdentity("AGE-SECRET-KEY-" + os.urandom(6).hex().upper())

    def to_public(self) -> "_FakeRecipient":
        return _FakeRecipient(self._pub)

    def __str__(self) -> str:
        return self.secret


class _FakeRecipient:
    def __init__(self, pub: str) -> None:
        self.pub = pub

    def __str__(self) -> str:
        return self.pub


@pytest.fixture
def fake_pyrage(monkeypatch: pytest.MonkeyPatch) -> Any:
    fake: Any = types.ModuleType("pyrage")
    fake_x25519: Any = types.ModuleType("pyrage.x25519")
    fake_x25519.Identity = _FakeIdentity
    fake_x25519.Recipient = type("R", (), {})
    fake.x25519 = fake_x25519
    monkeypatch.setitem(sys.modules, "pyrage", fake)
    monkeypatch.setitem(sys.modules, "pyrage.x25519", fake_x25519)
    return fake


def _stub_config() -> Any:
    from phoenix.cli.config_loader import CLIConfig

    return CLIConfig()


def _stub_client() -> Any:
    from phoenix.cli.http_client import CLIHTTPClient

    return CLIHTTPClient(base_url="http://localhost:8003", actor_name=None)


def _call(args: argparse.Namespace) -> int:
    """Invoke the handler with the project's live 4-arg handler shape:
    ``(args, config, client, fmt)`` where ``fmt`` is a ``str`` (the
    13.x.7 CLI test passes ``"text"`` here, not ``None``)."""
    from phoenix.cli.commands.admin import _cmd_generate_encryption_key

    return _cmd_generate_encryption_key(args, _stub_config(), _stub_client(), "text")


class TestGenerateEncryptionKeyActor:
    def test_actor_flag_routes_keys_under_actors_subdir(
        self, fake_pyrage: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PHOENIX_ENCRYPTION_KEYS_DIR", str(tmp_path))
        args = argparse.Namespace(name="primary", force=False, keys_dir=None, actor="adam")
        rc = _call(args)
        assert rc == 0
        assert (tmp_path / "actors" / "adam" / "identity.txt").is_file()
        assert (tmp_path / "actors" / "adam" / "recipients" / "primary.pub").is_file()

    def test_no_actor_flag_uses_shared_layout(
        self, fake_pyrage: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PHOENIX_ENCRYPTION_KEYS_DIR", str(tmp_path))
        args = argparse.Namespace(name="primary", force=False, keys_dir=None, actor=None)
        rc = _call(args)
        assert rc == 0
        assert (tmp_path / "identity.txt").is_file()  # shared layout

    def test_invalid_actor_name_exits_nonzero(
        self, fake_pyrage: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PHOENIX_ENCRYPTION_KEYS_DIR", str(tmp_path))
        args = argparse.Namespace(name="primary", force=False, keys_dir=None, actor="../escape")
        rc = _call(args)
        assert rc == 1
