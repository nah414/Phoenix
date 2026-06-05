"""Tests for ``phoenix.ledger.encryption_actors`` (Phase 13.x.8).

GPU SAFETY: uses a fake pyrage module via sys.modules monkeypatch
(same pattern as test_encryption_age.py / test_keygen.py). No real
crypto.
"""

from __future__ import annotations

import hashlib
import os
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from phoenix.ledger.encryption import reset_prompt_encryptor, set_prompt_encryptor
from phoenix.ledger.encryption_actors import (
    PerActorKeyError,
    _ACTOR_NAME_RE,
    encryptor_from_actor_default_layout,
    get_prompt_encryptor_for_actor,
    list_actors_with_keys,
    reset_prompt_encryptor_for_actor,
    set_prompt_encryptor_for_actor,
)


# ---------------------------------------------------------------------------
# Fake pyrage (mirrors test_keygen.py / test_encryption_age.py).


class _FakeIdentity:
    def __init__(self, secret: str) -> None:
        self.secret = secret
        self._pub = "age1" + hashlib.sha256(secret.encode()).hexdigest()[:30]

    @classmethod
    def from_str(cls, s: str) -> "_FakeIdentity":
        return cls(s)

    def to_public(self) -> "_FakeRecipient":
        return _FakeRecipient(self._pub)

    def __str__(self) -> str:
        return self.secret


class _FakeRecipient:
    def __init__(self, pub: str) -> None:
        self.pub = pub

    @classmethod
    def from_str(cls, s: str) -> "_FakeRecipient":
        return cls(s)

    def __str__(self) -> str:
        return self.pub


@pytest.fixture
def fake_pyrage(monkeypatch: pytest.MonkeyPatch) -> Any:
    fake: Any = types.ModuleType("pyrage")
    fake_x25519: Any = types.ModuleType("pyrage.x25519")
    fake_x25519.Identity = _FakeIdentity
    fake_x25519.Recipient = _FakeRecipient
    fake.x25519 = fake_x25519
    fake.encrypt = lambda data, recipients: b"CT:" + bytes(data)
    fake.decrypt = lambda blob, identities: bytes(blob)[3:]
    monkeypatch.setitem(sys.modules, "pyrage", fake)
    monkeypatch.setitem(sys.modules, "pyrage.x25519", fake_x25519)
    return fake


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """Isolate keys dir + reset both registries before and after each test."""
    monkeypatch.setenv("PHOENIX_ENCRYPTION_KEYS_DIR", str(tmp_path))
    reset_prompt_encryptor_for_actor()
    reset_prompt_encryptor()
    yield
    reset_prompt_encryptor_for_actor()
    reset_prompt_encryptor()


def _write_actor_keys(keys_root: Path, actor_name: str, *, identity_mode: int = 0o600) -> None:
    """Lay down actors/<name>/identity.txt + recipients/primary.pub."""
    actor_dir = keys_root / "actors" / actor_name
    (actor_dir / "recipients").mkdir(parents=True, exist_ok=True)
    identity = actor_dir / "identity.txt"
    identity.write_text("AGE-SECRET-KEY-" + actor_name.upper() + "\n", encoding="utf-8")
    if sys.platform != "win32":
        os.chmod(identity, identity_mode)
    fake_id = _FakeIdentity("AGE-SECRET-KEY-" + actor_name.upper())
    (actor_dir / "recipients" / "primary.pub").write_text(
        str(fake_id.to_public()) + "\n", encoding="utf-8"
    )


class _SentinelEncryptor:
    """A trivial PromptEncryptor test double to prove identity routing."""

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def encrypt(self, canonical_form: str) -> bytes:
        return f"{self.tag}:{canonical_form}".encode()

    def decrypt(self, encrypted: bytes) -> str:
        return encrypted.decode().split(":", 1)[1]


class TestActorNameRegexPin:
    def test_mirrors_routes_actor_name_re(self) -> None:
        """The local _ACTOR_NAME_RE must stay identical to routes.py's.

        Pinned so the two cannot silently diverge (the regex is mirrored
        locally to avoid importing the heavy FastAPI app module).
        """
        from phoenix.api.routes import _ACTOR_NAME_RE as routes_re

        assert _ACTOR_NAME_RE.pattern == routes_re.pattern


class TestSetGetReset:
    def test_set_then_get_returns_custom(self) -> None:
        sentinel = _SentinelEncryptor("adam")
        set_prompt_encryptor_for_actor("adam", sentinel)  # type: ignore[arg-type]
        assert get_prompt_encryptor_for_actor("adam") is sentinel

    def test_reset_one_actor(self, fake_pyrage: Any, tmp_path: Path) -> None:
        sentinel = _SentinelEncryptor("adam")
        set_prompt_encryptor_for_actor("adam", sentinel)  # type: ignore[arg-type]
        reset_prompt_encryptor_for_actor("adam")
        result = get_prompt_encryptor_for_actor("adam")
        assert result is not sentinel

    def test_reset_all(self) -> None:
        set_prompt_encryptor_for_actor("adam", _SentinelEncryptor("a"))  # type: ignore[arg-type]
        set_prompt_encryptor_for_actor("ash", _SentinelEncryptor("b"))  # type: ignore[arg-type]
        reset_prompt_encryptor_for_actor(None)
        assert get_prompt_encryptor_for_actor("adam") is not None


class TestFallbackWhenDirAbsent:
    def test_absent_actor_dir_falls_back_to_shared(self) -> None:
        shared = _SentinelEncryptor("SHARED")
        set_prompt_encryptor(shared)  # type: ignore[arg-type]
        assert get_prompt_encryptor_for_actor("nobody") is shared


class TestDiskLoadAndCache:
    def test_loads_from_disk_when_dir_present(self, fake_pyrage: Any, tmp_path: Path) -> None:
        _write_actor_keys(tmp_path, "adam")
        enc = get_prompt_encryptor_for_actor("adam")
        from phoenix.ledger.encryption_age import AgePromptEncryptor

        assert isinstance(enc, AgePromptEncryptor)

    def test_second_call_is_cached(self, fake_pyrage: Any, tmp_path: Path) -> None:
        _write_actor_keys(tmp_path, "adam")
        first = get_prompt_encryptor_for_actor("adam")
        second = get_prompt_encryptor_for_actor("adam")
        assert first is second


class TestFailClosed:
    def test_dir_present_but_identity_missing_raises(
        self, fake_pyrage: Any, tmp_path: Path
    ) -> None:
        actor_dir = tmp_path / "actors" / "adam"
        (actor_dir / "recipients").mkdir(parents=True)
        (actor_dir / "recipients" / "primary.pub").write_text("age1xxx\n")
        with pytest.raises(PerActorKeyError):
            get_prompt_encryptor_for_actor("adam")

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission check")
    def test_dir_present_but_loose_permissions_raises(
        self, fake_pyrage: Any, tmp_path: Path
    ) -> None:
        _write_actor_keys(tmp_path, "adam", identity_mode=0o644)
        with pytest.raises(PerActorKeyError):
            get_prompt_encryptor_for_actor("adam")


class TestPathTraversal:
    def test_dotdot_actor_name_refused(self) -> None:
        with pytest.raises(PerActorKeyError, match="invalid"):
            get_prompt_encryptor_for_actor("../primary")

    def test_slash_actor_name_refused(self) -> None:
        with pytest.raises(PerActorKeyError, match="invalid"):
            get_prompt_encryptor_for_actor("a/b")

    def test_uppercase_refused(self) -> None:
        with pytest.raises(PerActorKeyError, match="invalid"):
            get_prompt_encryptor_for_actor("Adam")


class TestEnumeration:
    def test_lists_actors_with_keys(self, fake_pyrage: Any, tmp_path: Path) -> None:
        _write_actor_keys(tmp_path, "adam")
        _write_actor_keys(tmp_path, "ash")
        (tmp_path / "actors" / "broken" / "recipients").mkdir(parents=True)
        actors = list_actors_with_keys()
        assert sorted(actors) == ["adam", "ash"]

    def test_empty_when_no_actors_dir(self, tmp_path: Path) -> None:
        assert list_actors_with_keys() == []


class TestEncryptorFromActorLayout:
    def test_builds_from_actor_subdir(self, fake_pyrage: Any, tmp_path: Path) -> None:
        _write_actor_keys(tmp_path, "adam")
        from phoenix.ledger.encryption_age import AgePromptEncryptor

        enc = encryptor_from_actor_default_layout("adam")
        assert isinstance(enc, AgePromptEncryptor)

    def test_invalid_name_raises(self) -> None:
        with pytest.raises(PerActorKeyError, match="invalid"):
            encryptor_from_actor_default_layout("../escape")
