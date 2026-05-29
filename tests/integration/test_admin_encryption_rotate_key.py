"""Integration tests for ``POST /v1/admin/encryption/rotate-key`` (Phase 13.x.7).

Mirrors :mod:`tests.integration.test_admin_step9_endpoints` for runtime
isolation (per-test fresh runtime + singleton resets). Uses the same
fake-``pyrage`` pattern as :mod:`tests.cognition.test_keygen` so no
real crypto runs.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import types
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix.api.routes import app


# ---------------------------------------------------------------------------
# Fake pyrage (same shape as test_keygen.py).


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
# Per-test isolated runtime (mirrors test_admin_step9_endpoints).


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


def _alice_header() -> str:
    """Non-admin actor header (alice has is_admin=False and
    can_rotate_encryption_key=False by default)."""
    from phoenix.identity.bootstrap import mint_bootstrap_actor

    alice = mint_bootstrap_actor("alice")
    payload = alice.to_payload()
    return "Phoenix-Actor " + base64.b64encode(json.dumps(payload).encode()).decode("ascii")


# ---------------------------------------------------------------------------
# Tests.


class TestRotateKeyEndpoint:
    def test_happy_path_returns_200(
        self,
        fake_pyrage: Any,
        isolated_runtime: Path,
    ) -> None:
        keys_dir = Path(os.environ["PHOENIX_ENCRYPTION_KEYS_DIR"])
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/encryption/rotate-key",
                json={"name": "rotation-2026-05-28"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Paths land in the configured keys dir under the expected layout.
        assert body["identity_path"].endswith("identity-rotation-2026-05-28.txt")
        assert body["recipient_path"].endswith("rotation-2026-05-28.pub")
        # Fingerprints are 16 lowercase hex chars and match each other
        # (keygen primitive uses the recipient text for both sides).
        assert len(body["identity_fingerprint"]) == 16
        assert all(c in "0123456789abcdef" for c in body["identity_fingerprint"])
        assert body["identity_fingerprint"] == body["recipient_fingerprint"]
        # next_step tells the caller about the daemon restart requirement.
        assert "restart" in body["next_step"].lower()
        # And the files actually exist on disk.
        assert (keys_dir / "identity-rotation-2026-05-28.txt").is_file()
        assert (keys_dir / "recipients" / "rotation-2026-05-28.pub").is_file()

    def test_default_name_when_omitted(
        self,
        fake_pyrage: Any,
        isolated_runtime: Path,
    ) -> None:
        keys_dir = Path(os.environ["PHOENIX_ENCRYPTION_KEYS_DIR"])
        expected_name = f"rotation-{date.today().isoformat()}"
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/encryption/rotate-key",
                json={},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["identity_path"].endswith(f"identity-{expected_name}.txt")
        assert body["recipient_path"].endswith(f"{expected_name}.pub")
        assert (keys_dir / f"identity-{expected_name}.txt").is_file()

    def test_conflict_returns_409(
        self,
        fake_pyrage: Any,
        isolated_runtime: Path,
    ) -> None:
        with TestClient(app) as client:
            first = client.post(
                "/v1/admin/encryption/rotate-key",
                json={"name": "dup"},
            )
            assert first.status_code == 200, first.text
            second = client.post(
                "/v1/admin/encryption/rotate-key",
                json={"name": "dup", "force": False},
            )
        assert second.status_code == 409, second.text
        assert "force" in second.text.lower() or "conflict" in second.text.lower()

    def test_force_overrides_conflict(
        self,
        fake_pyrage: Any,
        isolated_runtime: Path,
    ) -> None:
        keys_dir = Path(os.environ["PHOENIX_ENCRYPTION_KEYS_DIR"])
        with TestClient(app) as client:
            first = client.post(
                "/v1/admin/encryption/rotate-key",
                json={"name": "force-test"},
            )
            assert first.status_code == 200, first.text
            first_secret = (keys_dir / "identity-force-test.txt").read_text()
            second = client.post(
                "/v1/admin/encryption/rotate-key",
                json={"name": "force-test", "force": True},
            )
        assert second.status_code == 200, second.text
        second_secret = (keys_dir / "identity-force-test.txt").read_text()
        # Force-overwrite produces a fresh secret.
        assert first_secret != second_secret

    def test_unauthorized_returns_403(
        self,
        fake_pyrage: Any,
        isolated_runtime: Path,
    ) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/encryption/rotate-key",
                json={"name": "alice-attempt"},
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403, resp.text
