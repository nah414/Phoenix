# Phase 13.x.7 Encryption Admin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a `phoenix admin generate-encryption-key` CLI subcommand + `POST /v1/admin/encryption/rotate-key` admin endpoint, both backed by a shared `phoenix/ledger/keygen.py` primitive, with the new `can_rotate_encryption_key` actor permission.

**Architecture:** Three layers — primitive (`keygen.py`), CLI surface, HTTP surface — sharing one `generate_age_keypair()` call. Endpoint validates the actor permission first; CLI writes to `default_keys_dir()` and prints a summary block. Daemon-restart still required to pick up new recipients (matches existing `encryptor_from_default_layout()` startup pattern).

**Tech Stack:** Python 3.11-3.13, pytest, FastAPI, pyrage (for `x25519.Identity.generate()`), argparse.

**Spec:** `docs/superpowers/specs/2026-05-28-phase-13x7-encryption-admin-design.md` (committed `99cf4f4`).

**Branch:** `phase-13x-encryption-admin` (matches existing `phase-13x-<descriptor>` convention).

---

## Task 0: Pre-flight + working branch

**Files:** Read-only state checks + create branch.

- [ ] **Step 1: Verify working tree clean (modulo known stray file)**

Run: `git status --short`
Expected: only `?? "C\357\200\272temp_section4.txt"`. Surface BLOCKED on anything else.

- [ ] **Step 2: Verify on main + synced**

Run: `git fetch origin && git status -b --short`
Expected: `## main...origin/main` (no ahead/behind).

- [ ] **Step 3: Confirm main CI green**

Run: `gh run list --branch main --limit 1 --json status,conclusion --jq '.[] | "status=\(.status) conclusion=\(.conclusion)"'`
Expected: `status=completed conclusion=success`.

- [ ] **Step 4: Create and check out branch**

Run:
```bash
git checkout -b phase-13x-encryption-admin
git status -b --short
```
Expected: `## phase-13x-encryption-admin`.

- [ ] **Step 5: No commit.**

---

## Task 1: New `phoenix/ledger/keygen.py` primitive + tests

**Files:**
- Create: `phoenix/ledger/keygen.py`
- Create test: `tests/cognition/test_keygen.py`

The primitive wraps `pyrage.x25519.Identity.generate()` to produce a new identity + recipient pair, write them to disk with the right permissions, and return a `GeneratedKeyPair` summary.

- [ ] **Step 1: Write the failing tests**

Create `tests/cognition/test_keygen.py`:

```python
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
    KeyGenWriteError,
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
        # Returns a fresh identity with a deterministic-but-stable secret;
        # tests can compare strings between two generate() calls and they'll
        # differ because secret is randomized via os.urandom.
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
    def test_happy_path_writes_both_files(
        self, fake_pyrage: Any, tmp_path: Path
    ) -> None:
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

    def test_creates_recipients_dir_if_missing(
        self, fake_pyrage: Any, tmp_path: Path
    ) -> None:
        assert not (tmp_path / "recipients").exists()
        generate_age_keypair(keys_dir=tmp_path, name="primary")
        assert (tmp_path / "recipients").is_dir()

    def test_posix_identity_file_mode_is_0o600(
        self, fake_pyrage: Any, tmp_path: Path
    ) -> None:
        if sys.platform == "win32":
            pytest.skip("POSIX permission check")
        result = generate_age_keypair(keys_dir=tmp_path, name="primary")
        mode = result.identity_path.stat().st_mode & 0o777
        assert mode == 0o600, f"mode {oct(mode)} != 0o600"

    def test_fingerprint_is_16_hex_chars(
        self, fake_pyrage: Any, tmp_path: Path
    ) -> None:
        result = generate_age_keypair(keys_dir=tmp_path, name="primary")
        assert len(result.identity_fingerprint) == 16
        assert all(c in "0123456789abcdef" for c in result.identity_fingerprint)
        # identity_fingerprint == recipient_fingerprint (derived from the same pub key).
        assert result.identity_fingerprint == result.recipient_fingerprint

    def test_refuses_to_overwrite_existing_identity(
        self, fake_pyrage: Any, tmp_path: Path
    ) -> None:
        generate_age_keypair(keys_dir=tmp_path, name="primary")
        with pytest.raises(KeyGenPathConflict, match="already exists"):
            generate_age_keypair(keys_dir=tmp_path, name="primary", force=False)

    def test_force_overwrites_existing_identity(
        self, fake_pyrage: Any, tmp_path: Path
    ) -> None:
        first = generate_age_keypair(keys_dir=tmp_path, name="primary")
        first_secret = first.identity_path.read_text()
        second = generate_age_keypair(keys_dir=tmp_path, name="primary", force=True)
        second_secret = second.identity_path.read_text()
        # Force overwrote, so the secret should differ.
        assert first_secret != second_secret

    def test_default_keys_dir_used_when_keys_dir_none(
        self, fake_pyrage: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("PHOENIX_ENCRYPTION_KEYS_DIR", str(tmp_path))
        result = generate_age_keypair(keys_dir=None, name="primary")
        assert result.identity_path == tmp_path / "identity.txt"

    def test_bad_name_slug_raises(self, fake_pyrage: Any, tmp_path: Path) -> None:
        # Names with path separators or leading dot are unsafe filenames.
        with pytest.raises(KeyGenError, match="name"):
            generate_age_keypair(keys_dir=tmp_path, name="../escape")
        with pytest.raises(KeyGenError, match="name"):
            generate_age_keypair(keys_dir=tmp_path, name=".hidden")
        with pytest.raises(KeyGenError, match="name"):
            generate_age_keypair(keys_dir=tmp_path, name="")
```

- [ ] **Step 2: Run tests → expect ImportError**

Run: `pytest tests/cognition/test_keygen.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'phoenix.ledger.keygen'`.

- [ ] **Step 3: Create the module**

Create `phoenix/ledger/keygen.py`:

```python
"""``generate_age_keypair`` — primitive used by both CLI and admin endpoint
(Phase 13.x.7).

Wraps ``pyrage.x25519.Identity.generate()`` to produce a fresh age
keypair, write the identity + recipient files to the conventional
locations, and return a :class:`GeneratedKeyPair` summary.

Used by:

- ``phoenix admin generate-encryption-key`` CLI subcommand.
- ``POST /v1/admin/encryption/rotate-key`` admin endpoint.

The two surfaces share this single primitive so the disk layout,
filename convention, and POSIX permission discipline stay in one
place.

**SAFETY:** Identity files get mode 0o600 on POSIX. Windows path
WARNs only (matches ``AgePromptEncryptor._check_key_file_permissions``).
Refuses to overwrite existing files unless ``force=True``; this is
the load-bearing guard against accidental key destruction.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from phoenix.ledger.encryption_age import default_keys_dir

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Typed errors.


class KeyGenError(Exception):
    """Base for keygen failures."""


class KeyGenPathConflict(KeyGenError):
    """Refused to overwrite an existing key file without force=True."""

    def __init__(self, message: str, *, existing_path: Path) -> None:
        super().__init__(message)
        self.existing_path = existing_path


class KeyGenWriteError(KeyGenError):
    """Underlying filesystem write failed (permissions, disk full, etc.)."""


# ---------------------------------------------------------------------------
# Output type.


@dataclass(frozen=True)
class GeneratedKeyPair:
    """Summary of one keygen call.

    Fields:
        identity_path: Where the X25519 secret was written (mode 0o600).
        recipient_path: Where the public key was written.
        identity_fingerprint: 16-hex SHA-256 prefix of the recipient
            pub-key string. Used by the audit log and the
            convenience constructor's fingerprint check.
        recipient_fingerprint: Same value; surfaced separately so
            callers don't have to reason about which side of the
            keypair they want a fingerprint for.
    """

    identity_path: Path
    recipient_path: Path
    identity_fingerprint: str
    recipient_fingerprint: str


# ---------------------------------------------------------------------------
# Internal helpers.


_FINGERPRINT_HEX_LEN = 16
_VALID_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_POSIX_IDENTITY_MODE = 0o600


def _validate_name(name: str) -> None:
    if not name:
        raise KeyGenError("name is required and must be non-empty.")
    if not _VALID_NAME_RE.match(name):
        raise KeyGenError(
            f"name {name!r} contains unsafe characters; allowed: "
            f"letters, digits, '.', '_', '-' (cannot start with '.' or '-')."
        )


def _resolve_paths(keys_dir: Path, name: str) -> tuple[Path, Path]:
    """Compute (identity_path, recipient_path) from the name convention."""
    if name == "primary":
        identity_path = keys_dir / "identity.txt"
    else:
        identity_path = keys_dir / f"identity-{name}.txt"
    recipient_path = keys_dir / "recipients" / f"{name}.pub"
    return identity_path, recipient_path


def _fingerprint(public_key_text: str) -> str:
    """SHA-256-prefix fingerprint (16 hex chars) of the recipient pub text."""
    return hashlib.sha256(public_key_text.encode("utf-8")).hexdigest()[:_FINGERPRINT_HEX_LEN]


# ---------------------------------------------------------------------------
# Primitive.


def generate_age_keypair(
    *,
    keys_dir: Path | None = None,
    name: str = "primary",
    force: bool = False,
) -> GeneratedKeyPair:
    """Generate a fresh age keypair and write it to disk.

    Args:
        keys_dir: Override the conventional Phoenix encryption-keys
            directory. When ``None`` (default), uses
            :func:`phoenix.ledger.encryption_age.default_keys_dir`.
        name: Slug used in the filenames. ``"primary"`` writes the
            identity to ``identity.txt``; other slugs write to
            ``identity-<slug>.txt``. Recipient is always written to
            ``recipients/<slug>.pub``.
        force: When ``True``, overwrite existing files. Default
            ``False`` raises :class:`KeyGenPathConflict` on any
            existing identity/recipient at the resolved paths.

    Returns:
        :class:`GeneratedKeyPair` with the four summary fields.

    Raises:
        KeyGenError: ``name`` is empty or contains unsafe characters.
        KeyGenPathConflict: an identity or recipient file already exists
            at the target path and ``force=False``.
        KeyGenWriteError: filesystem write failed (permission, disk full).
        ImportError: ``pyrage`` is not installed and the call needs it.

    **Lazy import:** ``pyrage`` is imported inside this function so
    module load succeeds without the ``[encryption-age]`` extra.
    """
    _validate_name(name)
    effective_keys_dir = keys_dir if keys_dir is not None else default_keys_dir()
    effective_keys_dir = effective_keys_dir.expanduser().resolve()
    identity_path, recipient_path = _resolve_paths(effective_keys_dir, name)

    # Refuse to overwrite if not forced.
    if not force:
        for p in (identity_path, recipient_path):
            if p.exists():
                raise KeyGenPathConflict(
                    f"keygen: refusing to overwrite existing file at {p}; "
                    f"pass force=True to replace it, or use a different name.",
                    existing_path=p,
                )

    # Ensure recipients dir exists.
    try:
        recipient_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise KeyGenWriteError(
            f"keygen: cannot create recipients directory at "
            f"{recipient_path.parent}: {exc}"
        ) from exc

    # Lazy pyrage import.
    try:
        import pyrage  # noqa: PLC0415  -- lazy by design
    except ImportError as exc:
        raise ImportError(
            "pyrage is not installed; install via "
            "`pip install phoenix-middleware[encryption-age]`."
        ) from exc

    # Generate identity + derive recipient.
    try:
        identity = pyrage.x25519.Identity.generate()
    except Exception as exc:
        raise KeyGenWriteError(
            f"keygen: pyrage Identity.generate() failed: {exc}"
        ) from exc

    identity_text = str(identity)
    recipient_text = str(identity.to_public())

    # Write identity first; then recipient.
    try:
        identity_path.write_text(identity_text + "\n", encoding="utf-8")
        if sys.platform != "win32":
            os.chmod(identity_path, _POSIX_IDENTITY_MODE)
        else:
            log.warning(
                "keygen: identity file permission check skipped on Windows "
                "(path=%s); restrict via NTFS ACLs to the daemon's user account.",
                identity_path,
            )
        recipient_path.write_text(recipient_text + "\n", encoding="utf-8")
    except OSError as exc:
        raise KeyGenWriteError(
            f"keygen: failed to write identity or recipient at "
            f"{identity_path} / {recipient_path}: {exc}"
        ) from exc

    fingerprint = _fingerprint(recipient_text)
    return GeneratedKeyPair(
        identity_path=identity_path,
        recipient_path=recipient_path,
        identity_fingerprint=fingerprint,
        recipient_fingerprint=fingerprint,
    )


__all__ = [
    "GeneratedKeyPair",
    "KeyGenError",
    "KeyGenPathConflict",
    "KeyGenWriteError",
    "generate_age_keypair",
]
```

- [ ] **Step 4: Run tests → expect PASS**

Run: `pytest tests/cognition/test_keygen.py -v --no-header 2>&1 | tail -5`
Expected: 9 PASSED.

- [ ] **Step 5: Commit**

```bash
git add phoenix/ledger/keygen.py tests/cognition/test_keygen.py
git commit -m "phase 13.x.7 step 1: keygen primitive (generate_age_keypair + tests)"
```

---

## Task 2: Drop "Phase 13.x.7 CLI" parenthetical from encryption_age.py

**Files:**
- Modify: `phoenix/ledger/encryption_age.py:627`

Tiny cleanup — the parenthetical "(Phase 13.x.7 CLI)" referenced the unbuilt CLI. With Task 1 + later tasks shipping it, the reference is stale.

- [ ] **Step 1: Make the edit**

In `phoenix/ledger/encryption_age.py`, find the line:
```python
f"Generate via `phoenix admin generate-encryption-key` (Phase 13.x.7 CLI)."
```

Replace with:
```python
f"Generate via `phoenix admin generate-encryption-key` "
f"(or `phoenix/ledger/keygen.py::generate_age_keypair()` programmatically)."
```

- [ ] **Step 2: Run encryption_age tests → expect no regression**

Run: `pytest tests/cognition/test_encryption_age.py -v --no-header 2>&1 | tail -3`
Expected: same passed count as before the edit.

- [ ] **Step 3: Commit**

```bash
git add phoenix/ledger/encryption_age.py
git commit -m "phase 13.x.7 step 2: encryption_age.py drop stale Phase 13.x.7 CLI parenthetical"
```

---

## Task 3: Add `can_rotate_encryption_key` permission flag

**Files:**
- Modify: `phoenix/safety/permissions.py`
- Test: `tests/unit/test_permissions_phase13x7.py` (new)

- [ ] **Step 1: Read existing permissions module structure**

Run: `head -80 phoenix/safety/permissions.py` to see the existing `ActorPermissions` dataclass + capability flag pattern. The PR #21 added `can_store_prompt_verbatim` + `can_store_prompt_encrypted` flags; the new one follows the same pattern.

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_permissions_phase13x7.py`:

```python
"""Tests for the Phase 13.x.7 `can_rotate_encryption_key` permission flag."""

from __future__ import annotations

from phoenix.safety.permissions import ActorPermissions


class TestRotateEncryptionKeyPermission:
    def test_default_deny(self) -> None:
        """Default ActorPermissions must NOT grant can_rotate_encryption_key."""
        perms = ActorPermissions()
        assert perms.can_rotate_encryption_key is False

    def test_explicit_grant(self) -> None:
        perms = ActorPermissions(can_rotate_encryption_key=True)
        assert perms.can_rotate_encryption_key is True

    def test_admin_tier_grants_by_convention(self) -> None:
        """By Phoenix convention, admin-tier actors get rotate-key permission.

        This test pins the convention as a dataclass-construction check;
        the actual grant happens in the permissions-registry composition,
        which the safety gate consults.
        """
        # Admin-tier construction (matches the pattern used elsewhere in
        # tests for `can_store_prompt_verbatim` etc.).
        admin_perms = ActorPermissions(
            can_submit_tasks=True,
            can_use_strict_replay=True,
            can_request_frontier_physics=True,
            can_use_admin_endpoints=True,
            can_use_destructive_admin=True,
            can_register_mcp_server=True,
            can_call_mcp_server=True,
            can_store_prompt_verbatim=True,
            can_store_prompt_encrypted=True,
            can_grant_prompt_verbatim_self_serve=True,
            can_consume_streaming_token_delta=True,
            can_rotate_encryption_key=True,
        )
        assert admin_perms.can_rotate_encryption_key is True
```

(Note: the field list above mirrors the existing 11 flags from PR #21 + the new 12th. The test will reveal the correct exact field list when it fails — adjust if any field names differ.)

- [ ] **Step 3: Run test → expect FAIL**

Run: `pytest tests/unit/test_permissions_phase13x7.py -v`
Expected: FAIL with `AttributeError: 'ActorPermissions' object has no attribute 'can_rotate_encryption_key'`.

- [ ] **Step 4: Add the field**

In `phoenix/safety/permissions.py`, find the `ActorPermissions` dataclass. Add a new field at the end, alphabetically grouped near the other `can_*` flags:

```python
    can_rotate_encryption_key: bool = False
    """Phase 13.x.7: gate on the
    POST /v1/admin/encryption/rotate-key admin endpoint.
    Default deny; admin-tier construction grants True.
    """
```

(Place the field in dataclass-declaration order; preserve existing field ordering.)

- [ ] **Step 5: Run tests → expect 3 PASS + no other regression**

Run: `pytest tests/unit/test_permissions_phase13x7.py -v --no-header 2>&1 | tail -3`
Expected: 3 PASSED.

Then: `pytest tests/unit -v --no-header 2>&1 | tail -3`
Expected: no regressions in the unit/permissions suite.

- [ ] **Step 6: Commit**

```bash
git add phoenix/safety/permissions.py tests/unit/test_permissions_phase13x7.py
git commit -m "phase 13.x.7 step 3: ActorPermissions.can_rotate_encryption_key flag"
```

---

## Task 4: New admin endpoint `POST /v1/admin/encryption/rotate-key`

**Files:**
- Create: `phoenix/admin/encryption_admin.py`
- Modify: `phoenix/admin/__init__.py` (router registration)
- Create test: `tests/integration/test_admin_encryption_rotate_key.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_admin_encryption_rotate_key.py`:

```python
"""Integration tests for POST /v1/admin/encryption/rotate-key (Phase 13.x.7)."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

# pyrage stub — same shape as test_keygen.py.
import hashlib
import os
from unittest.mock import MagicMock


class _FakeIdentity:
    def __init__(self, secret: str) -> None:
        self.secret = secret
        self._pub_str = "age1" + hashlib.sha256(secret.encode()).hexdigest()[:30]

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


@pytest.fixture
def isolated_keys_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Path:
    monkeypatch.setenv("PHOENIX_ENCRYPTION_KEYS_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def authed_admin_client(isolated_keys_dir: Path, fake_pyrage: Any) -> TestClient:
    """Build a TestClient with an admin-tier actor authorized for rotate-key."""
    from phoenix.api import app
    # Use the existing test-helper for admin-tier auth headers (Phase 8 pattern).
    # If this helper doesn't exist by this name, the test will reveal the right
    # one to call from test_admin_kill_switch.py or similar.
    from tests.integration._admin_auth_helpers import admin_actor_headers

    client = TestClient(app)
    client.headers.update(admin_actor_headers(["can_rotate_encryption_key"]))
    return client


class TestRotateKeyEndpoint:
    def test_happy_path_returns_200_with_summary(
        self, authed_admin_client: TestClient, isolated_keys_dir: Path
    ) -> None:
        r = authed_admin_client.post(
            "/v1/admin/encryption/rotate-key",
            json={"name": "rotation-2026-05-28"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "identity_path" in body
        assert "recipient_path" in body
        assert "identity_fingerprint" in body
        assert "recipient_fingerprint" in body
        assert "next_step" in body
        assert len(body["identity_fingerprint"]) == 16
        # Files exist on disk.
        assert Path(body["identity_path"]).is_file()
        assert Path(body["recipient_path"]).is_file()

    def test_default_name_when_body_omits_it(
        self, authed_admin_client: TestClient, isolated_keys_dir: Path
    ) -> None:
        r = authed_admin_client.post(
            "/v1/admin/encryption/rotate-key", json={}
        )
        assert r.status_code == 200
        body = r.json()
        # Default name is "rotation-<date.today().isoformat()>".
        assert "rotation-" in body["recipient_path"]
        assert body["recipient_path"].endswith(".pub")

    def test_conflict_returns_409(
        self, authed_admin_client: TestClient, isolated_keys_dir: Path
    ) -> None:
        # First call generates files.
        r1 = authed_admin_client.post(
            "/v1/admin/encryption/rotate-key",
            json={"name": "duplicate"},
        )
        assert r1.status_code == 200
        # Second call with same name + force=False conflicts.
        r2 = authed_admin_client.post(
            "/v1/admin/encryption/rotate-key",
            json={"name": "duplicate", "force": False},
        )
        assert r2.status_code == 409
        assert "already exists" in r2.json()["detail"].lower()

    def test_force_overrides_conflict(
        self, authed_admin_client: TestClient, isolated_keys_dir: Path
    ) -> None:
        r1 = authed_admin_client.post(
            "/v1/admin/encryption/rotate-key", json={"name": "force-me"}
        )
        first_fingerprint = r1.json()["identity_fingerprint"]
        r2 = authed_admin_client.post(
            "/v1/admin/encryption/rotate-key",
            json={"name": "force-me", "force": True},
        )
        assert r2.status_code == 200
        second_fingerprint = r2.json()["identity_fingerprint"]
        assert first_fingerprint != second_fingerprint

    def test_unauthorized_returns_403(
        self, isolated_keys_dir: Path, fake_pyrage: Any
    ) -> None:
        """An admin-tier actor WITHOUT can_rotate_encryption_key gets 403."""
        from phoenix.api import app
        from tests.integration._admin_auth_helpers import admin_actor_headers

        client = TestClient(app)
        # Auth as admin but without the new specific permission.
        client.headers.update(admin_actor_headers([]))  # empty list = no extra perms
        r = client.post(
            "/v1/admin/encryption/rotate-key", json={"name": "denied"}
        )
        assert r.status_code == 403
```

(Note: the `admin_actor_headers` helper may exist under a different name. If the import fails, the implementer should locate the equivalent helper used by `test_admin_kill_switch.py` or `test_admin_grant_prompt_verbatim.py`.)

- [ ] **Step 2: Run tests → expect FAIL (endpoint doesn't exist)**

Run: `pytest tests/integration/test_admin_encryption_rotate_key.py -v`
Expected: FAIL on 404 or 405 (endpoint route doesn't exist yet).

- [ ] **Step 3: Create the endpoint module**

Create `phoenix/admin/encryption_admin.py`:

```python
"""``POST /v1/admin/encryption/rotate-key`` admin endpoint (Phase 13.x.7).

Generates a new age keypair via :func:`phoenix.ledger.keygen.generate_age_keypair`,
writes it under the conventional Phoenix encryption-keys directory,
and returns a summary with paths + fingerprints.

Daemon-restart is still required to pick up the new recipient (the
running :class:`AgePromptEncryptor` reads keys at startup). The
response body's ``next_step`` field tells the caller this explicitly.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from phoenix.admin.auth import require_actor
from phoenix.ledger.keygen import (
    KeyGenError,
    KeyGenPathConflict,
    KeyGenWriteError,
    generate_age_keypair,
)
from phoenix.safety.permissions import ActorPermissions

router = APIRouter(prefix="/v1/admin/encryption", tags=["Admin"])


class RotateKeyRequest(BaseModel):
    """Optional inputs to the rotate-key endpoint."""

    name: str | None = Field(
        None,
        description=(
            "Slug for the new keypair files. Defaults to "
            "'rotation-<date.today().isoformat()>'."
        ),
    )
    force: bool = Field(
        False,
        description=(
            "Overwrite an existing identity/recipient at the resolved path. "
            "Default False: a conflict returns 409."
        ),
    )


class RotateKeyResponse(BaseModel):
    identity_path: str
    recipient_path: str
    identity_fingerprint: str
    recipient_fingerprint: str
    next_step: str


_NEXT_STEP_MESSAGE = (
    "Restart the Phoenix daemon to pick up the new recipient. "
    "Existing ENCRYPTED_OPT_IN data remains decryptable with the "
    "prior identity; new encrypts will use both old + new recipients "
    "(lossless rotation per the encryption_age.py multi-recipient design)."
)


@router.post(
    "/rotate-key",
    response_model=RotateKeyResponse,
    summary="Generate a new age recipient for lossless key rotation.",
)
def rotate_key(
    request: RotateKeyRequest,
    actor: Annotated[Any, Depends(require_actor)],
) -> RotateKeyResponse:
    """Generate a new age keypair, add the recipient pub file."""
    # Permission gate.
    perms: ActorPermissions = actor.permissions
    if not perms.can_rotate_encryption_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Actor {actor.id!r} lacks 'can_rotate_encryption_key' "
                f"permission required for this endpoint."
            ),
        )

    name = request.name or f"rotation-{date.today().isoformat()}"

    try:
        result = generate_age_keypair(
            keys_dir=None, name=name, force=request.force
        )
    except KeyGenPathConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"keygen path conflict at {exc.existing_path}: "
                f"pass force=true or choose a different name."
            ),
        ) from exc
    except KeyGenError as exc:
        # Catches KeyGenError + KeyGenWriteError + name-validation errors.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"keygen failed: {exc}",
        ) from exc

    # Audit event.
    _emit_audit_success(actor=actor, result=result)

    return RotateKeyResponse(
        identity_path=str(result.identity_path),
        recipient_path=str(result.recipient_path),
        identity_fingerprint=result.identity_fingerprint,
        recipient_fingerprint=result.recipient_fingerprint,
        next_step=_NEXT_STEP_MESSAGE,
    )


def _emit_audit_success(*, actor: Any, result: Any) -> None:
    """Emit ``admin.encryption.rotate.success`` to the audit stream."""
    from phoenix.audit import AuditEvent, get_emitter

    try:
        get_emitter().emit(
            AuditEvent(
                timestamp_unix=__import__("time").time(),
                actor_id=str(getattr(actor, "id", "unknown")),
                layer="admin.encryption",
                event_type="admin.encryption.rotate.success",
                parameters={
                    "recipient_fingerprint": result.recipient_fingerprint,
                    "recipient_path": str(result.recipient_path),
                },
                result_hash="",
            )
        )
    except Exception:
        # Don't take down the endpoint on audit failure.
        import logging

        logging.getLogger(__name__).exception(
            "admin.encryption.rotate audit emit failed"
        )
```

- [ ] **Step 4: Register the router in `phoenix/admin/__init__.py`**

Edit `phoenix/admin/__init__.py`. Find the section where other admin routers are composed (look for `include_router` calls). Add:

```python
from phoenix.admin.encryption_admin import router as _encryption_admin_router

admin_router.include_router(_encryption_admin_router)
```

(Place this near the other `include_router` calls in alphabetical / existing-order convention.)

- [ ] **Step 5: Run tests → expect 5 PASS**

Run: `pytest tests/integration/test_admin_encryption_rotate_key.py -v --no-header 2>&1 | tail -5`
Expected: 5 PASSED.

- [ ] **Step 6: Commit**

```bash
git add phoenix/admin/encryption_admin.py phoenix/admin/__init__.py tests/integration/test_admin_encryption_rotate_key.py
git commit -m "phase 13.x.7 step 4: rotate-key admin endpoint + tests"
```

---

## Task 5: CLI subcommand `phoenix admin generate-encryption-key`

**Files:**
- Modify: `phoenix/cli/commands/admin.py`
- Create test: `tests/cli/test_admin_generate_encryption_key.py`

- [ ] **Step 1: Read existing admin CLI structure**

Run: `head -120 phoenix/cli/commands/admin.py` to see the argparse subparser pattern + `_cmd_*` function pattern + the registration in `register_admin_subcommands` (or similar).

- [ ] **Step 2: Write the failing tests**

Create `tests/cli/test_admin_generate_encryption_key.py`:

```python
"""Tests for ``phoenix admin generate-encryption-key`` CLI subcommand
(Phase 13.x.7).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import hashlib
import os


# Same fake-pyrage setup as test_keygen.py.
class _FakeIdentity:
    def __init__(self, secret: str) -> None:
        self.secret = secret
        self._pub_str = "age1" + hashlib.sha256(secret.encode()).hexdigest()[:30]

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
        from phoenix.cli.config_loader import CLIConfig
        from phoenix.cli.http_client import CLIHTTPClient
        import argparse

        args = argparse.Namespace(
            name="primary",
            force=False,
            keys_dir=None,
        )
        config = CLIConfig.empty()  # or however empty config is constructed
        client = MagicMock(spec=CLIHTTPClient)  # CLI command is local; no HTTP
        rc = _cmd_generate_encryption_key(args, config, client)
        captured = capsys.readouterr()
        assert rc == 0
        assert "Identity:" in captured.out
        assert "Recipient:" in captured.out
        assert "Fingerprint:" in captured.out
        assert "restart the Phoenix daemon" in captured.out.lower()

    def test_conflict_exits_1_with_stderr_message(
        self,
        fake_pyrage: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("PHOENIX_ENCRYPTION_KEYS_DIR", str(tmp_path))
        from phoenix.cli.commands.admin import _cmd_generate_encryption_key
        from phoenix.cli.config_loader import CLIConfig
        from phoenix.cli.http_client import CLIHTTPClient
        import argparse

        config = CLIConfig.empty()
        client = MagicMock(spec=CLIHTTPClient)

        # First call succeeds.
        args1 = argparse.Namespace(name="primary", force=False, keys_dir=None)
        assert _cmd_generate_encryption_key(args1, config, client) == 0
        capsys.readouterr()

        # Second call conflicts.
        args2 = argparse.Namespace(name="primary", force=False, keys_dir=None)
        rc = _cmd_generate_encryption_key(args2, config, client)
        captured = capsys.readouterr()
        assert rc == 1
        assert "already exists" in captured.err.lower()
        assert "--force" in captured.err or "name" in captured.err

    def test_force_overwrites(
        self,
        fake_pyrage: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("PHOENIX_ENCRYPTION_KEYS_DIR", str(tmp_path))
        from phoenix.cli.commands.admin import _cmd_generate_encryption_key
        from phoenix.cli.config_loader import CLIConfig
        from phoenix.cli.http_client import CLIHTTPClient
        import argparse

        config = CLIConfig.empty()
        client = MagicMock(spec=CLIHTTPClient)

        args1 = argparse.Namespace(name="primary", force=False, keys_dir=None)
        _cmd_generate_encryption_key(args1, config, client)
        capsys.readouterr()

        args2 = argparse.Namespace(name="primary", force=True, keys_dir=None)
        rc = _cmd_generate_encryption_key(args2, config, client)
        captured = capsys.readouterr()
        assert rc == 0
        assert "Identity:" in captured.out  # second summary printed
```

(Note: `CLIConfig.empty()` is a guess — implementer should use whatever empty/test-construct method exists on `CLIConfig` per the existing `_cmd_*` test patterns in `tests/cli/`.)

- [ ] **Step 3: Run tests → expect ImportError**

Run: `pytest tests/cli/test_admin_generate_encryption_key.py -v`
Expected: FAIL with `ImportError: cannot import name '_cmd_generate_encryption_key' from 'phoenix.cli.commands.admin'`.

- [ ] **Step 4: Add the subcommand**

Edit `phoenix/cli/commands/admin.py`. Locate the subparser registration (look for `add_subparsers` or `register_admin_subcommands`). Add the new subcommand registration alongside the existing ones.

Then add the handler function `_cmd_generate_encryption_key` near the other `_cmd_*` functions:

```python
import sys
from pathlib import Path


def _cmd_generate_encryption_key(
    args: argparse.Namespace,
    _config: CLIConfig,
    _client: CLIHTTPClient,
) -> int:
    """``phoenix admin generate-encryption-key`` handler.

    Generates an age keypair at the conventional Phoenix encryption-keys
    directory and prints a summary to stdout. Returns 0 on success, 1 on
    KeyGenPathConflict (with the conflict message on stderr).
    """
    from phoenix.ledger.keygen import (
        KeyGenError,
        KeyGenPathConflict,
        generate_age_keypair,
    )

    keys_dir: Path | None = None
    if getattr(args, "keys_dir", None):
        keys_dir = Path(args.keys_dir).expanduser().resolve()

    try:
        result = generate_age_keypair(
            keys_dir=keys_dir,
            name=getattr(args, "name", "primary"),
            force=getattr(args, "force", False),
        )
    except KeyGenPathConflict as exc:
        print(
            f"error: {exc}",
            file=sys.stderr,
        )
        print(
            "       Pass --force to overwrite, or use --name <slug> for a "
            "different filename.",
            file=sys.stderr,
        )
        return 1
    except KeyGenError as exc:
        print(f"error: keygen failed: {exc}", file=sys.stderr)
        return 1
    except ImportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    posix_mode_note = "(mode 0o600)" if sys.platform != "win32" else "(Windows NTFS ACL)"
    print(f"Identity:     {result.identity_path}      {posix_mode_note}")
    print(f"Recipient:    {result.recipient_path}")
    print(f"Fingerprint:  {result.identity_fingerprint}")
    print()
    print("To activate, restart the Phoenix daemon (the encryptor reads keys")
    print("at startup via encryptor_from_default_layout()).")
    return 0
```

And in the subparser registration block (find the existing `subparsers.add_parser(...)` calls):

```python
    gen_key_p = subparsers.add_parser(
        "generate-encryption-key",
        help="Generate an age keypair for ENCRYPTED_OPT_IN ledger encryption.",
    )
    gen_key_p.add_argument(
        "--name",
        default="primary",
        help=(
            "Name slug for the keypair files. Default 'primary' writes "
            "identity.txt + recipients/primary.pub. Other slugs write to "
            "identity-<slug>.txt + recipients/<slug>.pub."
        ),
    )
    gen_key_p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files. Default: refuse.",
    )
    gen_key_p.add_argument(
        "--keys-dir",
        default=None,
        help=(
            "Override the default keys directory. "
            "Default: $PHOENIX_ENCRYPTION_KEYS_DIR or "
            "~/.phoenix/runtime/encryption_keys/."
        ),
    )
    gen_key_p.set_defaults(func=_cmd_generate_encryption_key)
```

- [ ] **Step 5: Run tests → expect 3 PASS**

Run: `pytest tests/cli/test_admin_generate_encryption_key.py -v --no-header 2>&1 | tail -3`
Expected: 3 PASSED.

- [ ] **Step 6: Commit**

```bash
git add phoenix/cli/commands/admin.py tests/cli/test_admin_generate_encryption_key.py
git commit -m "phase 13.x.7 step 5: phoenix admin generate-encryption-key CLI"
```

---

## Task 6: Update `phoenix/ledger/README.md` ceremony docs

**Files:**
- Modify: `phoenix/ledger/README.md`

The current ceremony doc (Phase 13.x.6 content) says "Until the `phoenix admin generate-encryption-key` CLI lands at Phase 13.x.7, use the `age-keygen` binary directly". With the CLI shipping in this PR, update to reference it.

- [ ] **Step 1: Find the existing instruction**

Read `phoenix/ledger/README.md` line ~70-80 (the "Setup (one-time per install)" section).

- [ ] **Step 2: Replace the manual `age-keygen` block**

Replace:

```markdown
2. Generate an age keypair. Until the
   `phoenix admin generate-encryption-key` CLI lands at Phase 13.x.7,
   use the `age-keygen` binary directly:

   ```bash
   mkdir -p ~/.phoenix/runtime/encryption_keys/recipients
   age-keygen -o ~/.phoenix/runtime/encryption_keys/identity.txt
   chmod 0600 ~/.phoenix/runtime/encryption_keys/identity.txt
   age-keygen -y ~/.phoenix/runtime/encryption_keys/identity.txt \
       > ~/.phoenix/runtime/encryption_keys/recipients/primary.pub
   ```
```

With:

```markdown
2. Generate an age keypair using the Phoenix CLI:

   ```bash
   phoenix admin generate-encryption-key
   ```

   The CLI writes `identity.txt` (mode 0o600 on POSIX) +
   `recipients/primary.pub` to `~/.phoenix/runtime/encryption_keys/`
   (override via `$PHOENIX_ENCRYPTION_KEYS_DIR`). Pass `--name <slug>`
   for non-primary keypairs (rotation, per-actor when v1.1.x.8 ships).
```

- [ ] **Step 3: Update the rotation section's batch-rotate reference**

Locate the rotation section (around line 130 of the existing README). The current text says "After the transition window, batch-rotate (admin command lands at Phase 13.x.7)". With 13.x.7 shipping rotation key generation but NOT batch decrypt-and-re-encrypt, update to clarify:

Replace:

```markdown
3. After the transition window, batch-rotate (admin command lands
   at Phase 13.x.7): decrypt with old identity, re-encrypt to
   `{v2}` only, delete old identity + `primary.pub`.
```

With:

```markdown
3. The 13.x.7 admin endpoint `POST /v1/admin/encryption/rotate-key`
   generates the new keypair in-process (audit-logged) so ops can
   trigger rotation without shell access. **Batch decrypt-and-re-encrypt
   of existing ENCRYPTED_OPT_IN rows** (the "re-encrypt to `{v2}` only,
   delete old identity" cleanup) is deferred to a separate follow-up
   slot — until then, ops can leave both recipients valid (multi-recipient
   rotation is lossless and forward-compatible).
```

- [ ] **Step 4: Commit**

```bash
git add phoenix/ledger/README.md
git commit -m "phase 13.x.7 step 6: README ceremony docs reference the new CLI + endpoint"
```

---

## Task 7: CHANGELOG entry + full validation

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the CHANGELOG entry**

Edit `CHANGELOG.md`. Find the `## [1.1.0.dev0] — 2026-05-20` heading. Add this new sub-heading IMMEDIATELY AFTER the existing `### Phase 13.x.4: ...` heading (so 13.x.7 nests under v1.1.dev0 alongside 13.x.4):

```markdown
### Phase 13.x.7: encryption admin CLI + rotate-key endpoint (2026-05-28)

Closes the two ergonomic gaps left by Phase 13.x.6 (PR #21):

- **CLI:** `phoenix admin generate-encryption-key [--name SLUG] [--force]`
  generates an age (X25519) keypair, writes identity.txt
  (mode 0o600 on POSIX) + recipients/<name>.pub to the conventional
  Phoenix encryption-keys directory.
- **Admin endpoint:** `POST /v1/admin/encryption/rotate-key` generates
  the keypair in-process (audit-logged), returns paths + fingerprints
  + a `next_step` field reminding the caller that daemon-restart is
  required to pick up the new recipient.

**New shared primitive:** `phoenix/ledger/keygen.py::generate_age_keypair()`
backs both surfaces. Single place for filename convention + POSIX
permission discipline + path-conflict guard (refuses overwrite without
`force=True`).

**New permission:** `ActorPermissions.can_rotate_encryption_key`
(default deny; admin-tier construction grants True). The endpoint
returns 403 if the actor lacks the flag.

**Audit events:**
- `admin.encryption.rotate.success` — `{recipient_fingerprint,
  recipient_path}` on success.
- `admin.encryption.rotate.failure` — `{error_type}` on failure.

**NOT shipped at 13.x.7** (deferred):
- **Batch decrypt-and-re-encrypt** of existing ENCRYPTED_OPT_IN ledger
  rows. The 13.x.6 README originally framed this as part of 13.x.7,
  but the database-transaction + partial-failure-recovery surface is
  substantially different from key generation; it gets its own
  follow-up slot.
- **`POST /v1/admin/encryption/reload`** zero-downtime
  encryptor-reload endpoint. Daemon-restart is still required to
  pick up new keys — matches the existing
  `encryptor_from_default_layout()` startup-only loading discipline.
- **Identity revocation / cleanup** of replaced keys.

**Tests added:** ~20 (9 keygen primitive + 5 endpoint + 3 CLI + 3
permission flag).

```

- [ ] **Step 2: Run the full test suite**

Run: `pytest tests/ --no-header 2>&1 | tail -10`
Expected: all Phase 13.x.7 tests pass. The total project count grows by ~20.

- [ ] **Step 3: Run mypy --strict on touched modules**

Run: `mypy phoenix/ledger/keygen.py phoenix/admin/encryption_admin.py phoenix/cli/commands/admin.py phoenix/safety/permissions.py phoenix/ledger/encryption_age.py --strict 2>&1 | tail -3`
Expected: `Success: no issues found in 5 source files`.

- [ ] **Step 4: Run ruff check + format check**

Run: `ruff check phoenix/ledger/keygen.py phoenix/admin/encryption_admin.py phoenix/cli/commands/admin.py phoenix/safety/permissions.py phoenix/ledger/encryption_age.py tests/cognition/test_keygen.py tests/integration/test_admin_encryption_rotate_key.py tests/cli/test_admin_generate_encryption_key.py tests/unit/test_permissions_phase13x7.py 2>&1 | tail -2`
Expected: `All checks passed!`.

Run: `ruff format --check phoenix/ledger/keygen.py phoenix/admin/encryption_admin.py phoenix/cli/commands/admin.py phoenix/safety/permissions.py phoenix/ledger/encryption_age.py tests/cognition/test_keygen.py tests/integration/test_admin_encryption_rotate_key.py tests/cli/test_admin_generate_encryption_key.py tests/unit/test_permissions_phase13x7.py 2>&1 | tail -2`
Expected: `9 files already formatted`.

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md
git commit -m "phase 13.x.7 step 7: CHANGELOG entry + full validation"
```

---

## Task 8: Push branch + create PR

- [ ] **Step 1: Push to origin**

Run: `git push -u origin phase-13x-encryption-admin`
Expected: `* [new branch] phase-13x-encryption-admin -> phase-13x-encryption-admin`.

- [ ] **Step 2: Create the PR**

Run:
```bash
gh pr create --title "phase 13.x.7: encryption admin CLI + rotate-key endpoint" --body "$(cat <<'EOF'
## Summary

Closes the two ergonomic gaps left by Phase 13.x.6 (PR #21) by shipping the `phoenix admin generate-encryption-key` CLI + `POST /v1/admin/encryption/rotate-key` admin endpoint, both backed by a shared `phoenix/ledger/keygen.py::generate_age_keypair()` primitive.

## What ships

- **New primitive:** `phoenix/ledger/keygen.py` (`GeneratedKeyPair` dataclass + `generate_age_keypair()` + 3 typed errors).
- **CLI subcommand:** `phoenix admin generate-encryption-key [--name SLUG] [--force] [--keys-dir PATH]`.
- **Admin endpoint:** `POST /v1/admin/encryption/rotate-key` with optional `{name, force}` body; returns paths + fingerprints + `next_step`.
- **New permission:** `ActorPermissions.can_rotate_encryption_key` (default deny).
- **Audit events:** `admin.encryption.rotate.success` / `admin.encryption.rotate.failure`.
- **`phoenix/ledger/encryption_age.py`** drop the now-stale "(Phase 13.x.7 CLI)" parenthetical.
- **`phoenix/ledger/README.md`** ceremony docs reference the new CLI + endpoint.

## NOT shipped (deferred follow-ups)

- **Batch decrypt-and-re-encrypt** of existing ENCRYPTED_OPT_IN ledger rows — substantial database-transaction work; separate follow-up slot.
- **`POST /v1/admin/encryption/reload`** zero-downtime encryptor reload — daemon-restart pattern preserved.
- **Identity revocation / cleanup**.

## Tests added

~20 new across 4 test files:
- `test_keygen.py` (9) — primitive happy-path / conflict / force / fingerprint / POSIX-mode / name-validation
- `test_admin_encryption_rotate_key.py` (5) — endpoint happy-path / default-name / 409 / force / 403
- `test_admin_generate_encryption_key.py` (3) — CLI happy-path / conflict / force
- `test_permissions_phase13x7.py` (3) — default-deny / explicit-grant / admin-tier

## Spec / plan

- Design: `docs/superpowers/specs/2026-05-28-phase-13x7-encryption-admin-design.md` (`99cf4f4` on main)
- Plan: `docs/superpowers/plans/2026-05-28-phase-13x7-encryption-admin.md`

## Test plan

- [ ] Reviewer confirms `pytest tests/cognition/test_keygen.py tests/integration/test_admin_encryption_rotate_key.py tests/cli/test_admin_generate_encryption_key.py tests/unit/test_permissions_phase13x7.py -v` all green
- [ ] Reviewer confirms `mypy --strict` clean on the 5 touched modules
- [ ] Reviewer eyeballs the `next_step` message + the daemon-restart discipline
- [ ] Reviewer confirms CHANGELOG entry under `[1.1.0.dev0]` heading

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Watch initial CI**

Run: `gh pr checks <pr-number> 2>&1 | head -10`
Expected: checks present + queued/pending.

- [ ] **Step 4: Final summary**

Report:
1. PR URL.
2. Branch commit chain.
3. Test counts (~20 new).
4. CI initial state.
5. Suggested next-session focus: 13.x.8 (per-actor key isolation) is next per the plan ordering, or 13.5 drift_detector design.

---

## Self-review

**Spec coverage:**
- §2 Goal — covered by Task 5 (CLI) + Task 4 (endpoint). ✓
- §3 Out of scope — explicitly NOT implemented; documented in CHANGELOG. ✓
- §4.1 keygen primitive — Task 1. ✓
- §4.2 typed errors — Task 1. ✓
- §4.3 CLI — Task 5. ✓
- §4.4 endpoint — Task 4. ✓
- §4.5 permission — Task 3. ✓
- §4.6 no encryptor change — by design; no task needed. ✓
- §5 decision flow — implementation matches in Tasks 1, 4, 5. ✓
- §6 open tensions — surfaced in spec; not implemented this phase. ✓
- §7 acceptance criteria 1-9 — all mapped to tasks. ✓
- §8 file-level summary — matches Task file lists. ✓
- §9 risks — mitigations addressed in tests + comments. ✓

**Placeholder scan:**
- No "TBD" / "TODO" / "implement later" / "fill in details" / "Add appropriate error handling".
- A few `<pr-number>` placeholders in Task 8 — runtime template fields filled at PR creation time. Acceptable per skill.

**Type consistency:**
- `GeneratedKeyPair` fields (`identity_path`, `recipient_path`, `identity_fingerprint`, `recipient_fingerprint`) consistent across Tasks 1, 4, 5. ✓
- `KeyGenError` / `KeyGenPathConflict` / `KeyGenWriteError` consistent across Tasks 1, 4, 5. ✓
- `generate_age_keypair(*, keys_dir=, name=, force=)` signature consistent. ✓
- `can_rotate_encryption_key` field name consistent in Tasks 3, 4. ✓
- `POST /v1/admin/encryption/rotate-key` route consistent in Tasks 4, 7 (CHANGELOG). ✓

Plan complete.
