"""Integration tests for Phase 13.x.8 admin surface: rotate-key ``actor_name``
+ the ``GET /v1/admin/encryption/actors`` enumeration endpoint.

Fixtures mirror :mod:`tests.integration.test_admin_encryption_rotate_key`
(the 13.x.7 reference): a fake-``pyrage`` shim so no real crypto runs, and
a per-test isolated runtime that points every Phoenix path at a tmp dir and
resets the process-global singletons. The non-admin ``_alice_header`` helper
is reused from that module verbatim.
"""

from __future__ import annotations

import hashlib
import os
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix.api.routes import app

# Non-admin actor header (alice: is_admin=False, can_rotate_encryption_key
# =False). Reused verbatim from the 13.x.7 rotate-key integration test.
from tests.integration.test_admin_encryption_rotate_key import _alice_header


# ---------------------------------------------------------------------------
# Fake pyrage (same shape as test_admin_encryption_rotate_key / test_keygen).


class _FakeIdentity:
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


# ---------------------------------------------------------------------------
# Per-test isolated runtime (mirrors test_admin_encryption_rotate_key).


@pytest.fixture
def isolated_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[Path]:
    runtime = tmp_path / "phoenix_runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PHOENIX_SQLITE_DB_PATH", str(runtime / "state.db"))
    monkeypatch.setenv("PHOENIX_AUDIT_DIR", str(runtime / "audit"))
    monkeypatch.setenv("PHOENIX_KILL_SWITCH_PATH", str(runtime / "kill_switch.json"))
    monkeypatch.setenv("PHOENIX_KEYSTORE_DIR", str(runtime / "keystore"))
    (runtime / "keystore").mkdir(parents=True, exist_ok=True)

    keys_dir = runtime / "encryption_keys"
    keys_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PHOENIX_ENCRYPTION_KEYS_DIR", str(keys_dir))

    from phoenix._internal.cloud_seams import reset_seams
    from phoenix.audit import reset_emitter
    from phoenix.ledger import reset_ledger
    from phoenix.ledger.encryption_actors import reset_prompt_encryptor_for_actor
    from phoenix.safety import kill_switch as ks_module
    from phoenix.safety import permissions as permissions_module
    from phoenix.safety.rate_limiter import get_limiter
    from phoenix.state import reset_state_backend

    reset_emitter()
    reset_ledger()
    reset_state_backend()
    ks_module._STORE = None
    get_limiter().reset_all()
    permissions_module._REGISTRY = None
    reset_seams()
    reset_prompt_encryptor_for_actor()  # 13.x.8: clear per-actor cache
    try:
        yield runtime
    finally:
        reset_emitter()
        reset_ledger()
        reset_state_backend()
        ks_module._STORE = None
        get_limiter().reset_all()
        permissions_module._REGISTRY = None
        reset_seams()
        reset_prompt_encryptor_for_actor()


# ---------------------------------------------------------------------------
# Tests.


class TestRotateKeyWithActor:
    def test_rotate_with_actor_name_writes_under_actors(
        self, fake_pyrage: Any, isolated_runtime: Path
    ) -> None:
        keys_dir = Path(os.environ["PHOENIX_ENCRYPTION_KEYS_DIR"])
        with TestClient(app) as client:
            r = client.post("/v1/admin/encryption/rotate-key", json={"actor_name": "adam"})
        assert r.status_code == 200, r.text
        assert (keys_dir / "actors" / "adam" / "identity.txt").is_file()

    def test_rotate_without_actor_name_uses_shared(
        self, fake_pyrage: Any, isolated_runtime: Path
    ) -> None:
        keys_dir = Path(os.environ["PHOENIX_ENCRYPTION_KEYS_DIR"])
        with TestClient(app) as client:
            r = client.post("/v1/admin/encryption/rotate-key", json={})
        assert r.status_code == 200, r.text
        # Shared layout: the default name is ``rotation-<date>`` so the
        # identity lands at ``identity-rotation-<date>.txt`` and NOT under
        # an actors/ subtree.
        assert not (keys_dir / "actors").exists()

    def test_rotate_invalid_actor_name_returns_400(
        self, fake_pyrage: Any, isolated_runtime: Path
    ) -> None:
        with TestClient(app) as client:
            r = client.post("/v1/admin/encryption/rotate-key", json={"actor_name": "../escape"})
        assert r.status_code == 400, r.text


class TestEnumerationEndpoint:
    def test_lists_actors_with_keys(self, fake_pyrage: Any, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            client.post("/v1/admin/encryption/rotate-key", json={"actor_name": "adam"})
            client.post("/v1/admin/encryption/rotate-key", json={"actor_name": "ash"})
            r = client.get("/v1/admin/encryption/actors")
        assert r.status_code == 200, r.text
        assert sorted(r.json()["actors"]) == ["adam", "ash"]

    def test_empty_when_no_actors(self, fake_pyrage: Any, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            r = client.get("/v1/admin/encryption/actors")
        assert r.status_code == 200, r.text
        assert r.json()["actors"] == []

    def test_enumeration_requires_admin(self, fake_pyrage: Any, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            r = client.get(
                "/v1/admin/encryption/actors",
                headers={"Authorization": _alice_header()},
            )
        assert r.status_code == 403, r.text
