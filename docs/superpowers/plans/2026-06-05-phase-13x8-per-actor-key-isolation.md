# Phase 13.x.8 Per-Actor Key Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship per-actor encryption *plumbing* — a fail-closed per-actor encryptor registry, per-actor keygen routing (CLI + admin endpoint + enumeration), and decrypt-routing — keyed by `actor.name`, with the per-actor identifier stored in `payload_json` (no SQL column, no migration). The ENCRYPTED_OPT_IN *write path* stays deferred per 13-D2.

**Architecture:** New `phoenix/ledger/encryption_actors.py` mirrors the proven `encryption.py` singleton registry, keyed by `actor_name`, with a `threading.Lock` (request-time mutation) and **fail-closed-by-directory-presence** semantics (a present `actors/<name>/` directory means "configured for isolation" — a load failure raises rather than silently downgrading to the shared key). `encryption_age.py` gets a behavior-preserving refactor (extract `encryptor_from_keys_dir`) so the shared and per-actor layout helpers share one builder. The decrypt path reads the actor identifier from `payload_json`; CLI/admin gain per-actor key provisioning under `actors/<name>/`.

**Tech Stack:** Python 3.11-3.13, pytest, FastAPI, pyrage (age X25519), existing Phoenix StateBackend / ActorPermissions / admin-router / keygen patterns.

**Spec:** `docs/superpowers/specs/2026-06-05-phase-13x8-per-actor-key-isolation-design-v2.md` (committed `d3a78f9`). Supersedes the v1 (`0d2d9ab`).

**Branch:** `phase-13x-per-actor-keys`.

**Locked decisions (from spec v2 §6, post-review):**
- Key on `actor.name` (Actor has no `id`).
- Identifier in `payload_json` (`prompt_encryption_actor_id`); NO column, NO migration.
- Fail-CLOSED by directory presence (dir present + broken keys → raise; dir absent → shared fallback).
- `threading.Lock` on the request-time per-actor cache.
- `_ACTOR_NAME_RE` validation + path-under-root guard (traversal).
- `AgeKeyPermissionError` handled in the fail-closed branch.
- No new permission flag (reuse `can_rotate_encryption_key`; enumeration gates on `is_admin`).
- Write path + forensic-attribution audit DEFERRED (out of scope).

---

## Task 0: Pre-flight + working branch

**Files:** Read-only state checks + create branch.

- [ ] **Step 1: Verify working tree clean (modulo known stray)**

Run: `git status --short`
Expected: only `?? "C\357\200\272temp_section4.txt"`. Surface BLOCKED on anything else.

- [ ] **Step 2: Verify on main + synced**

Run: `git fetch origin && git status -b --short`
Expected: `## main...origin/main` (no ahead/behind). Main is at `d3a78f9` (the v2 spec).

- [ ] **Step 3: Confirm main CI green**

Run: `gh run list --branch main --limit 1 --json status,conclusion --jq '.[] | "status=\(.status) conclusion=\(.conclusion)"'`
Expected: most recent completed run = `success` (a freshly-triggered in_progress run from the v2-spec push is acceptable; check the prior completed run).

- [ ] **Step 4: Create + check out branch**

Run:
```bash
git checkout -b phase-13x-per-actor-keys
git status -b --short
```
Expected: `## phase-13x-per-actor-keys`.

- [ ] **Step 5: No commit.**

---

## Task 1: Refactor `encryption_age.py` — extract `encryptor_from_keys_dir`

**Files:**
- Modify: `phoenix/ledger/encryption_age.py`
- Test: existing `tests/cognition/test_encryption_age.py` (no new test; refactor must not change behavior)

Behavior-preserving extraction so the per-actor helper (Task 2) reuses the layout-building logic instead of duplicating it.

- [ ] **Step 1: Read the current helper**

Run: `grep -n "def encryptor_from_default_layout\|def default_keys_dir" phoenix/ledger/encryption_age.py`
Then read the full `encryptor_from_default_layout` body (it reads `default_keys_dir()/identity.txt` + `recipients/*.pub` and constructs `AgePromptEncryptor(identity_path=..., recipient_paths=...)`).

- [ ] **Step 2: Add `encryptor_from_keys_dir` and have `encryptor_from_default_layout` delegate**

In `phoenix/ledger/encryption_age.py`, add a new function immediately ABOVE `encryptor_from_default_layout`:

```python
def encryptor_from_keys_dir(keys_dir: Path) -> AgePromptEncryptor:
    """Construct an :class:`AgePromptEncryptor` from an arbitrary keys dir.

    Reads ``identity.txt`` + every ``*.pub`` under ``recipients/`` from
    ``keys_dir``. Shared by :func:`encryptor_from_default_layout`
    (keys_dir = :func:`default_keys_dir`) and the Phase 13.x.8 per-actor
    layout (keys_dir = ``default_keys_dir()/'actors'/<actor_name>``).

    Raises:
        AgeKeyLoadError: directory structure not as expected.
        AgeKeyPermissionError: identity file too permissive (POSIX).
    """
    identity_path = keys_dir / "identity.txt"
    recipients_dir = keys_dir / "recipients"
    if not identity_path.is_file():
        raise AgeKeyLoadError(
            f"encryption_age: identity file not found at {identity_path}. "
            f"Generate via `phoenix admin generate-encryption-key` "
            f"(or `phoenix/ledger/keygen.py::generate_age_keypair()` programmatically)."
        )
    if not recipients_dir.is_dir():
        raise AgeKeyLoadError(
            f"encryption_age: recipients directory not found at {recipients_dir}."
        )
    recipient_paths = sorted(recipients_dir.glob("*.pub"))
    if not recipient_paths:
        raise AgeKeyLoadError(
            f"encryption_age: no *.pub recipient files found in {recipients_dir}."
        )
    return AgePromptEncryptor(
        identity_path=identity_path,
        recipient_paths=recipient_paths,
    )
```

Then REPLACE the body of `encryptor_from_default_layout` so it delegates:

```python
def encryptor_from_default_layout() -> AgePromptEncryptor:
    """Construct an :class:`AgePromptEncryptor` from the conventional layout.

    Convenience constructor for daemon startup:

    .. code-block:: python

        from phoenix.ledger.encryption import set_prompt_encryptor
        from phoenix.ledger.encryption_age import encryptor_from_default_layout

        set_prompt_encryptor(encryptor_from_default_layout())

    Reads ``identity.txt`` + every ``*.pub`` under ``recipients/`` from
    the directory returned by :func:`default_keys_dir`.

    Raises:
        AgeKeyLoadError: if the directory structure isn't as expected.
        AgeKeyPermissionError: if the identity file is too permissive.
    """
    return encryptor_from_keys_dir(default_keys_dir())
```

Add `encryptor_from_keys_dir` to the module's `__all__` list.

- [ ] **Step 3: Run the existing encryption_age tests → expect no regression**

Run: `pytest tests/cognition/test_encryption_age.py -v --no-header 2>&1 | tail -5`
Expected: same passed count as before (the refactor is behavior-preserving; existing tests exercise `encryptor_from_default_layout` which now delegates).

- [ ] **Step 4: mypy + commit**

Run: `mypy phoenix/ledger/encryption_age.py --strict 2>&1 | tail -2`
Expected: `Success`.

```bash
git add phoenix/ledger/encryption_age.py
git commit -m "phase 13.x.8 step 1: extract encryptor_from_keys_dir (behavior-preserving)"
```

---

## Task 2: New `phoenix/ledger/encryption_actors.py` — registry + fail-closed resolution

**Files:**
- Create: `phoenix/ledger/encryption_actors.py`
- Create test: `tests/cognition/test_encryption_actors.py`

The core module. Mirrors `encryption.py`'s singleton registry, keyed by `actor_name`, with a `threading.Lock` and fail-closed-by-directory-presence resolution.

- [ ] **Step 1: Write the failing tests**

Create `tests/cognition/test_encryption_actors.py`:

```python
"""Tests for ``phoenix.ledger.encryption_actors`` (Phase 13.x.8).

GPU SAFETY: uses a fake pyrage module via sys.modules monkeypatch
(same pattern as test_encryption_age.py / test_keygen.py). No real
crypto.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from phoenix.ledger.encryption import PromptEncryptor, reset_prompt_encryptor, set_prompt_encryptor
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
# Fake pyrage (mirrors test_keygen.py).


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
    reset_prompt_encryptor_for_actor()  # clear all per-actor cache
    reset_prompt_encryptor()
    yield
    reset_prompt_encryptor_for_actor()
    reset_prompt_encryptor()


def _write_actor_keys(
    keys_root: Path, actor_name: str, *, identity_mode: int = 0o600
) -> None:
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
        # No actor dir on disk → falls back to shared (NullPromptEncryptor default).
        result = get_prompt_encryptor_for_actor("adam")
        assert result is not sentinel

    def test_reset_all(self) -> None:
        set_prompt_encryptor_for_actor("adam", _SentinelEncryptor("a"))  # type: ignore[arg-type]
        set_prompt_encryptor_for_actor("ash", _SentinelEncryptor("b"))  # type: ignore[arg-type]
        reset_prompt_encryptor_for_actor(None)
        assert get_prompt_encryptor_for_actor("adam") is not None  # shared fallback, no crash


class TestFallbackWhenDirAbsent:
    def test_absent_actor_dir_falls_back_to_shared(self) -> None:
        """No actors/<name>/ → returns the shared encryptor (back-compat)."""
        shared = _SentinelEncryptor("SHARED")
        set_prompt_encryptor(shared)  # type: ignore[arg-type]
        assert get_prompt_encryptor_for_actor("nobody") is shared


class TestDiskLoadAndCache:
    def test_loads_from_disk_when_dir_present(
        self, fake_pyrage: Any, tmp_path: Path
    ) -> None:
        _write_actor_keys(tmp_path, "adam")
        enc = get_prompt_encryptor_for_actor("adam")
        # Loaded an AgePromptEncryptor, NOT the shared fallback.
        from phoenix.ledger.encryption_age import AgePromptEncryptor

        assert isinstance(enc, AgePromptEncryptor)

    def test_second_call_is_cached(self, fake_pyrage: Any, tmp_path: Path) -> None:
        _write_actor_keys(tmp_path, "adam")
        first = get_prompt_encryptor_for_actor("adam")
        second = get_prompt_encryptor_for_actor("adam")
        assert first is second  # cache hit returns the same instance


class TestFailClosed:
    def test_dir_present_but_identity_missing_raises(
        self, fake_pyrage: Any, tmp_path: Path
    ) -> None:
        """Directory exists but identity.txt absent → FAIL CLOSED, not shared."""
        # Create the actor dir + recipients but NO identity.txt.
        actor_dir = tmp_path / "actors" / "adam"
        (actor_dir / "recipients").mkdir(parents=True)
        (actor_dir / "recipients" / "primary.pub").write_text("age1xxx\n")
        with pytest.raises(PerActorKeyError):
            get_prompt_encryptor_for_actor("adam")

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission check")
    def test_dir_present_but_loose_permissions_raises(
        self, fake_pyrage: Any, tmp_path: Path
    ) -> None:
        """Directory exists + identity is 0o644 → AgeKeyPermissionError →
        PerActorKeyError (fail-closed), NOT a silent shared downgrade."""
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
        # _ACTOR_NAME_RE is lowercase-only.
        with pytest.raises(PerActorKeyError, match="invalid"):
            get_prompt_encryptor_for_actor("Adam")


class TestEnumeration:
    def test_lists_actors_with_keys(self, fake_pyrage: Any, tmp_path: Path) -> None:
        _write_actor_keys(tmp_path, "adam")
        _write_actor_keys(tmp_path, "ash")
        # A dir with no identity.txt should NOT be listed.
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
```

- [ ] **Step 2: Run tests → expect ImportError**

Run: `pytest tests/cognition/test_encryption_actors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'phoenix.ledger.encryption_actors'`.

- [ ] **Step 3: Create the module**

Create `phoenix/ledger/encryption_actors.py`:

```python
"""Per-actor prompt-encryptor registry (Phase 13.x.8).

Extends the shared :mod:`phoenix.ledger.encryption` registry with
per-actor isolation: each Phoenix actor (keyed by ``actor.name``) can
have their own age identity + recipients under
``<keys_dir>/actors/<actor_name>/``.

**Fail-closed by directory presence.** If ``actors/<actor_name>/``
EXISTS, that actor is configured for isolation — a load failure
(:class:`AgeKeyLoadError` OR :class:`AgeKeyPermissionError`) raises
:class:`PerActorKeyError` rather than silently downgrading to the
shared key. If the directory is ABSENT, the actor falls back to the
shared :func:`phoenix.ledger.encryption.get_prompt_encryptor`
(13.x.6 behavior preserved).

**Thread-safety.** Unlike the shared registry (startup-time mutation
only), this cache mutates at REQUEST time (the rotate-key admin
endpoint evicts entries). It is guarded by a :class:`threading.Lock`.
In-process eviction is correct only while the daemon runs single-worker
(uvicorn with no ``--workers``); revisit cross-process invalidation
when ``--workers`` ships.

**Scope (Phase 13.x.8 = plumbing).** This module provides the
resolution the future ENCRYPTED_OPT_IN writer will call. The writer
itself + startup ``set_prompt_encryptor()`` activation remain deferred
per design decision 13-D2.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from phoenix.ledger.encryption import PromptEncryptor, get_prompt_encryptor
from phoenix.ledger.encryption_age import (
    AgeKeyLoadError,
    AgeKeyPermissionError,
    AgePromptEncryptor,
    default_keys_dir,
    encryptor_from_keys_dir,
)

# Mirror of phoenix.api.routes._ACTOR_NAME_RE. Defined locally (not
# imported) to avoid importing the heavy FastAPI app module into the
# ledger layer. A pin test (test_encryption_actors.py) asserts the two
# patterns stay identical so they cannot silently diverge.
_ACTOR_NAME_RE = re.compile(r"^[a-z0-9_\-]{1,64}$")


class PerActorKeyError(Exception):
    """Raised when an actor is configured for isolation
    (``actors/<name>/`` exists) but its keys cannot be loaded, OR when
    the supplied ``actor_name`` fails validation.

    The point of this typed error is to make a fail-CLOSED state
    distinguishable from the legitimate shared-key fallback: callers
    must never silently treat an isolation-configured actor as
    shared-key.
    """


# ---------------------------------------------------------------------------
# Per-actor registry. GUARDED BY _lock (request-time mutation).

_per_actor_encryptors: dict[str, PromptEncryptor] = {}
_lock = threading.Lock()


def _validate_actor_name(actor_name: str) -> None:
    if not _ACTOR_NAME_RE.match(actor_name):
        raise PerActorKeyError(
            f"invalid actor_name {actor_name!r}: must match {_ACTOR_NAME_RE.pattern}"
        )


def _actor_keys_dir(actor_name: str) -> Path:
    """Resolve <keys_dir>/actors/<actor_name>, asserting it stays under the
    actors/ root (path-traversal guard)."""
    _validate_actor_name(actor_name)
    actors_root = (default_keys_dir() / "actors").resolve()
    candidate = (actors_root / actor_name).resolve()
    if not candidate.is_relative_to(actors_root):
        raise PerActorKeyError(
            f"invalid actor_name {actor_name!r}: resolved path escapes the actors/ root"
        )
    return candidate


def encryptor_from_actor_default_layout(actor_name: str) -> AgePromptEncryptor:
    """Build an :class:`AgePromptEncryptor` rooted at
    ``<keys_dir>/actors/<actor_name>/``.

    Raises:
        PerActorKeyError: ``actor_name`` invalid / escapes root.
        AgeKeyLoadError: directory structure not as expected.
        AgeKeyPermissionError: identity file too permissive (POSIX).
    """
    return encryptor_from_keys_dir(_actor_keys_dir(actor_name))


def get_prompt_encryptor_for_actor(actor_name: str) -> PromptEncryptor:
    """Resolve the encryptor for ``actor_name``. FAIL-CLOSED by directory
    presence.

    Resolution (whole body under ``_lock``):
    1. Cache hit → return cached.
    2. ``actors/<actor_name>/`` EXISTS → load; on
       :class:`AgeKeyLoadError`/:class:`AgeKeyPermissionError` raise
       :class:`PerActorKeyError` (never fall back to shared); cache + return.
    3. Directory ABSENT → :func:`get_prompt_encryptor` (shared fallback).

    Raises:
        PerActorKeyError: invalid name, or dir present but keys broken.
    """
    actor_dir = _actor_keys_dir(actor_name)  # validates name (raises before lock)
    with _lock:
        cached = _per_actor_encryptors.get(actor_name)
        if cached is not None:
            return cached
        if actor_dir.is_dir():
            try:
                encryptor: PromptEncryptor = encryptor_from_keys_dir(actor_dir)
            except (AgeKeyLoadError, AgeKeyPermissionError) as exc:
                raise PerActorKeyError(
                    f"actor {actor_name!r} is configured for isolation "
                    f"(actors/{actor_name}/ exists) but its keys could not be "
                    f"loaded: {exc}. Refusing to fall back to the shared key."
                ) from exc
            _per_actor_encryptors[actor_name] = encryptor
            return encryptor
    # Directory absent → shared fallback (outside the lock; shared registry
    # has its own semantics).
    return get_prompt_encryptor()


def set_prompt_encryptor_for_actor(
    actor_name: str, encryptor: PromptEncryptor
) -> None:
    """Register ``encryptor`` for ``actor_name`` (highest precedence;
    tests + explicit startup wiring)."""
    _validate_actor_name(actor_name)
    with _lock:
        _per_actor_encryptors[actor_name] = encryptor


def reset_prompt_encryptor_for_actor(actor_name: str | None = None) -> None:
    """Evict one actor's cached encryptor, or all when ``actor_name`` is
    None. Called by the rotate-key admin endpoint on success (cache
    invalidation) and by test teardown."""
    with _lock:
        if actor_name is None:
            _per_actor_encryptors.clear()
        else:
            _per_actor_encryptors.pop(actor_name, None)


def list_actors_with_keys() -> list[str]:
    """Return ``actor_name``s that have a valid ``actors/<name>/identity.txt``
    on disk. Used by the enumeration admin endpoint."""
    actors_root = default_keys_dir() / "actors"
    if not actors_root.is_dir():
        return []
    names: list[str] = []
    for child in sorted(actors_root.iterdir()):
        if not child.is_dir():
            continue
        if not _ACTOR_NAME_RE.match(child.name):
            continue
        if (child / "identity.txt").is_file():
            names.append(child.name)
    return names


__all__ = [
    "PerActorKeyError",
    "encryptor_from_actor_default_layout",
    "get_prompt_encryptor_for_actor",
    "list_actors_with_keys",
    "reset_prompt_encryptor_for_actor",
    "set_prompt_encryptor_for_actor",
]
```

- [ ] **Step 4: Run tests → expect all PASS**

Run: `pytest tests/cognition/test_encryption_actors.py -v --no-header 2>&1 | tail -8`
Expected: all tests PASSED (the POSIX-permission test skips on Windows).

- [ ] **Step 5: mypy + commit**

Run: `mypy phoenix/ledger/encryption_actors.py --strict 2>&1 | tail -2`
Expected: `Success`.

```bash
git add phoenix/ledger/encryption_actors.py tests/cognition/test_encryption_actors.py
git commit -m "phase 13.x.8 step 2: encryption_actors registry (fail-closed + lock + traversal guard)"
```

---

## Task 3: Decrypt-routing in `_replay_encrypted`

**Files:**
- Modify: `phoenix/ledger/cognition_replay.py` (`_replay_encrypted`)
- Create test: `tests/cognition/test_replay_per_actor_routing.py`

Route decrypt to the per-actor encryptor when the payload carries `prompt_encryption_actor_id`.

- [ ] **Step 1: Read the current `_replay_encrypted`**

Run: `grep -n "encryptor = get_prompt_encryptor()" phoenix/ledger/cognition_replay.py`
The line `encryptor = get_prompt_encryptor()` (inside `_replay_encrypted`) is the graft point.

- [ ] **Step 2: Write the failing test**

Create `tests/cognition/test_replay_per_actor_routing.py`:

```python
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
```

- [ ] **Step 3: Run → expect FAIL**

Run: `pytest tests/cognition/test_replay_per_actor_routing.py -v`
Expected: FAIL with `AttributeError: module 'phoenix.ledger.cognition_replay' has no attribute '_resolve_decrypt_encryptor'`.

- [ ] **Step 4: Add the routing helper + use it in `_replay_encrypted`**

In `phoenix/ledger/cognition_replay.py`, add a module-level helper (near the other helpers, after the imports):

```python
def _resolve_decrypt_encryptor(payload: dict[str, Any]) -> PromptEncryptor:
    """Phase 13.x.8: pick the encryptor for an ENCRYPTED_OPT_IN replay.

    Reads ``prompt_encryption_actor_id`` from the payload (stored in
    payload_json, NOT a SQL column). Absent/null → shared encryptor
    (back-compat: all pre-13.x.8 rows). Present → that actor's
    per-actor encryptor.
    """
    actor_name = payload.get("prompt_encryption_actor_id")
    if not actor_name:
        return get_prompt_encryptor()
    from phoenix.ledger.encryption_actors import get_prompt_encryptor_for_actor

    return get_prompt_encryptor_for_actor(str(actor_name))
```

Add the `PromptEncryptor` import if not already present:
```python
from phoenix.ledger.encryption import PromptEncryptor, get_prompt_encryptor
```
(verify `get_prompt_encryptor` is already imported; add `PromptEncryptor` to the same import.)

Then in `_replay_encrypted`, REPLACE the line:
```python
    encryptor = get_prompt_encryptor()
```
with:
```python
    encryptor = _resolve_decrypt_encryptor(payload)
```

- [ ] **Step 5: Run tests → expect 3 PASS + no replay regression**

Run: `pytest tests/cognition/test_replay_per_actor_routing.py -v --no-header 2>&1 | tail -5`
Expected: 3 PASSED.

Then: `pytest tests/cognition/test_cognition_replay.py -v --no-header 2>&1 | tail -3`
Expected: no regressions (existing ENCRYPTED_OPT_IN replay tests still pass — absent actor_id → shared encryptor, unchanged behavior).

- [ ] **Step 6: mypy + commit**

Run: `mypy phoenix/ledger/cognition_replay.py --strict 2>&1 | tail -2`
Expected: `Success`.

```bash
git add phoenix/ledger/cognition_replay.py tests/cognition/test_replay_per_actor_routing.py
git commit -m "phase 13.x.8 step 3: _replay_encrypted routes decrypt by payload actor id"
```

---

## Task 4: CLI `--actor` flag

**Files:**
- Modify: `phoenix/cli/commands/admin.py` (the `generate-encryption-key` handler)
- Modify: `phoenix/cli/entry.py` (the subparser registration)
- Create test: `tests/cli/test_admin_generate_encryption_key_actor.py`

- [ ] **Step 1: Survey the existing subcommand**

Run: `grep -n "generate-encryption-key\|_cmd_generate_encryption_key\|def _add_admin_group" phoenix/cli/commands/admin.py phoenix/cli/entry.py`
Read the handler `_cmd_generate_encryption_key` (it calls `generate_age_keypair(keys_dir=..., name=..., force=...)`) and the subparser registration in `entry.py` (the `--name`/`--force`/`--keys-dir` args). 13.x.7 shipped these.

- [ ] **Step 2: Write the failing tests**

Create `tests/cli/test_admin_generate_encryption_key_actor.py`:

```python
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


class TestGenerateEncryptionKeyActor:
    def test_actor_flag_routes_keys_under_actors_subdir(
        self, fake_pyrage: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PHOENIX_ENCRYPTION_KEYS_DIR", str(tmp_path))
        from phoenix.cli.commands.admin import _cmd_generate_encryption_key

        # NOTE: signature is (args, _config, _client, _fmt) per the existing
        # 4-arg admin handler convention; confirm during survey.
        args = argparse.Namespace(
            name="primary", force=False, keys_dir=None, actor="adam"
        )
        rc = _cmd_generate_encryption_key(args, _stub_config(), _stub_client(), None)
        assert rc == 0
        assert (tmp_path / "actors" / "adam" / "identity.txt").is_file()
        assert (tmp_path / "actors" / "adam" / "recipients" / "primary.pub").is_file()

    def test_no_actor_flag_uses_shared_layout(
        self, fake_pyrage: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PHOENIX_ENCRYPTION_KEYS_DIR", str(tmp_path))
        from phoenix.cli.commands.admin import _cmd_generate_encryption_key

        args = argparse.Namespace(
            name="primary", force=False, keys_dir=None, actor=None
        )
        rc = _cmd_generate_encryption_key(args, _stub_config(), _stub_client(), None)
        assert rc == 0
        assert (tmp_path / "identity.txt").is_file()  # shared layout, no actors/

    def test_invalid_actor_name_exits_nonzero(
        self, fake_pyrage: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PHOENIX_ENCRYPTION_KEYS_DIR", str(tmp_path))
        from phoenix.cli.commands.admin import _cmd_generate_encryption_key

        args = argparse.Namespace(
            name="primary", force=False, keys_dir=None, actor="../escape"
        )
        rc = _cmd_generate_encryption_key(args, _stub_config(), _stub_client(), None)
        assert rc == 1
```

(Adapt `_stub_config` / `_stub_client` / handler arity to the actual shapes found in the survey — the 13.x.7 tests `tests/cli/test_admin_generate_encryption_key.py` show the exact constructors.)

- [ ] **Step 3: Run → expect FAIL**

Run: `pytest tests/cli/test_admin_generate_encryption_key_actor.py -v`
Expected: FAIL (handler ignores `actor`, keys land in the shared layout).

- [ ] **Step 4: Add `--actor` handling**

In `phoenix/cli/commands/admin.py`, in `_cmd_generate_encryption_key`, BEFORE the `generate_age_keypair(...)` call, compute the keys_dir from `--actor`:

```python
    actor = getattr(args, "actor", None)
    keys_dir = None
    if getattr(args, "keys_dir", None):
        from pathlib import Path

        keys_dir = Path(args.keys_dir).expanduser().resolve()
    if actor:
        from phoenix.ledger.encryption_actors import _actor_keys_dir, PerActorKeyError

        try:
            keys_dir = _actor_keys_dir(actor)
        except PerActorKeyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
```

Then pass `keys_dir=keys_dir` into the existing `generate_age_keypair(...)` call (replacing whatever keys_dir it currently computes). Ensure `sys` is imported.

In `phoenix/cli/entry.py`, in the `generate-encryption-key` subparser registration (`_add_admin_group`), add the `--actor` argument alongside `--name`/`--force`/`--keys-dir`:

```python
    gen_key.add_argument(
        "--actor",
        default=None,
        help=(
            "Provision keys for a specific actor under actors/<name>/ "
            "(Phase 13.x.8 per-actor isolation). Omit for the shared layout."
        ),
    )
```

- [ ] **Step 5: Run tests → expect 3 PASS**

Run: `pytest tests/cli/test_admin_generate_encryption_key_actor.py -v --no-header 2>&1 | tail -5`
Expected: 3 PASSED.

Then regression: `pytest tests/cli/test_admin_generate_encryption_key.py -v --no-header 2>&1 | tail -3`
Expected: existing 13.x.7 CLI tests still pass.

- [ ] **Step 6: Commit**

```bash
git add phoenix/cli/commands/admin.py phoenix/cli/entry.py tests/cli/test_admin_generate_encryption_key_actor.py
git commit -m "phase 13.x.8 step 4: CLI generate-encryption-key --actor flag"
```

---

## Task 5: Admin rotate-key `actor_name` + enumeration endpoint

**Files:**
- Modify: `phoenix/admin/encryption_admin.py` (rotate-key: optional `actor_name` + per-actor keys_dir + cache eviction; new enumeration endpoint)
- Create test: `tests/integration/test_admin_encryption_actors.py`

- [ ] **Step 1: Survey the rotate-key handler + auth helper**

Run: `grep -n "rotate-key\|RotateKeyPayload\|_admin_authn\|keys_dir=None\|def _rotate" phoenix/admin/encryption_admin.py`
Read the rotate-key handler (the `keys_dir=None` call at ~line 230, the `RotateKeyPayload`, the `_admin_authn` chain). 13.x.7 shipped these.

- [ ] **Step 2: Write the failing tests**

Create `tests/integration/test_admin_encryption_actors.py`:

```python
"""Integration tests for Phase 13.x.8 admin surface: rotate-key actor_name
+ enumeration endpoint."""

from __future__ import annotations

import hashlib
import os
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


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


@pytest.fixture
def isolated_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("PHOENIX_ENCRYPTION_KEYS_DIR", str(tmp_path))
    return tmp_path


def _client() -> TestClient:
    from phoenix.api import app

    # Bootstrap actor 'adam' is admin + has can_rotate_encryption_key by
    # default (no Authorization header → bootstrap adam per the 13.x.7
    # test pattern). Confirm against test_admin_encryption_rotate_key.py.
    return TestClient(app)


def _alice_header() -> dict[str, str]:
    """Non-admin actor header (mirrors test_admin_encryption_rotate_key.py)."""
    from tests.integration.test_admin_encryption_rotate_key import _alice_header as h

    return h()


class TestRotateKeyWithActor:
    def test_rotate_with_actor_name_writes_under_actors(
        self, fake_pyrage: Any, isolated_runtime: Path
    ) -> None:
        client = _client()
        r = client.post(
            "/v1/admin/encryption/rotate-key", json={"actor_name": "adam"}
        )
        assert r.status_code == 200
        assert (isolated_runtime / "actors" / "adam" / "identity.txt").is_file()

    def test_rotate_without_actor_name_uses_shared(
        self, fake_pyrage: Any, isolated_runtime: Path
    ) -> None:
        client = _client()
        r = client.post("/v1/admin/encryption/rotate-key", json={})
        assert r.status_code == 200
        # Shared layout: identity at root, not under actors/.
        assert (isolated_runtime / "identity.txt").is_file()

    def test_rotate_invalid_actor_name_returns_400(
        self, fake_pyrage: Any, isolated_runtime: Path
    ) -> None:
        client = _client()
        r = client.post(
            "/v1/admin/encryption/rotate-key", json={"actor_name": "../escape"}
        )
        assert r.status_code == 400


class TestEnumerationEndpoint:
    def test_lists_actors_with_keys(
        self, fake_pyrage: Any, isolated_runtime: Path
    ) -> None:
        client = _client()
        client.post("/v1/admin/encryption/rotate-key", json={"actor_name": "adam"})
        client.post("/v1/admin/encryption/rotate-key", json={"actor_name": "ash"})
        r = client.get("/v1/admin/encryption/actors")
        assert r.status_code == 200
        assert sorted(r.json()["actors"]) == ["adam", "ash"]

    def test_empty_when_no_actors(
        self, fake_pyrage: Any, isolated_runtime: Path
    ) -> None:
        client = _client()
        r = client.get("/v1/admin/encryption/actors")
        assert r.status_code == 200
        assert r.json()["actors"] == []

    def test_enumeration_requires_admin(
        self, fake_pyrage: Any, isolated_runtime: Path
    ) -> None:
        from phoenix.api import app

        client = TestClient(app)
        client.headers.update(_alice_header())
        r = client.get("/v1/admin/encryption/actors")
        assert r.status_code == 403
```

(Adapt the auth fixtures to the exact shapes in `tests/integration/test_admin_encryption_rotate_key.py` — bootstrap-adam-is-admin default + the `_alice_header` non-admin helper.)

- [ ] **Step 3: Run → expect FAIL**

Run: `pytest tests/integration/test_admin_encryption_actors.py -v`
Expected: FAIL (rotate-key ignores `actor_name`; enumeration endpoint 404).

- [ ] **Step 4: Extend the rotate-key handler + add the enumeration endpoint**

In `phoenix/admin/encryption_admin.py`:

(a) Add `actor_name: str | None = None` to the `RotateKeyPayload` model (alongside the existing fields).

(b) In the rotate handler, after auth + BEFORE the `generate_age_keypair(...)` call, compute the per-actor keys_dir:

```python
    actor_name = payload.actor_name
    rotate_keys_dir = None  # shared layout (existing behavior)
    if actor_name:
        from phoenix.ledger.encryption_actors import _actor_keys_dir, PerActorKeyError

        try:
            rotate_keys_dir = _actor_keys_dir(actor_name)
        except PerActorKeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
```

Change the `generate_age_keypair(keys_dir=None, ...)` call to `generate_age_keypair(keys_dir=rotate_keys_dir, ...)`.

After the success audit emit, add cache eviction:

```python
    if actor_name:
        from phoenix.ledger.encryption_actors import reset_prompt_encryptor_for_actor

        reset_prompt_encryptor_for_actor(actor_name)
```

(c) Add the enumeration endpoint (same router, same `_admin_authn` → `require_admin` chain, but it does NOT need `can_rotate_encryption_key` — just `is_admin`):

```python
@router.get("/actors")
def list_encryption_actors(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """List actor_names with per-actor encryption keys configured.
    Admin-only (is_admin via require_admin). Phase 13.x.8."""
    actor = _admin_authn(authorization, event_prefix="admin.encryption.actors")
    require_admin(actor)
    from phoenix.ledger.encryption_actors import list_actors_with_keys

    return {"actors": list_actors_with_keys()}
```

(Match the exact `_admin_authn` / `require_admin` / `Header` signatures used by the existing rotate-key handler — the survey in Step 1 gives them. The enumeration endpoint reuses the auth chain but omits the `can_rotate_encryption_key` permission check, since listing is a read.)

- [ ] **Step 5: Run tests → expect 6 PASS**

Run: `pytest tests/integration/test_admin_encryption_actors.py -v --no-header 2>&1 | tail -8`
Expected: 6 PASSED.

Then regression: `pytest tests/integration/test_admin_encryption_rotate_key.py -v --no-header 2>&1 | tail -3`
Expected: existing 13.x.7 rotate-key tests still pass.

- [ ] **Step 6: mypy + commit**

Run: `mypy phoenix/admin/encryption_admin.py --strict 2>&1 | tail -2`
Expected: `Success`.

```bash
git add phoenix/admin/encryption_admin.py tests/integration/test_admin_encryption_actors.py
git commit -m "phase 13.x.8 step 5: rotate-key actor_name + enumeration endpoint"
```

---

## Task 6: README ceremony docs + CHANGELOG + full validation

**Files:**
- Modify: `phoenix/ledger/README.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add a per-actor section to the ledger README**

In `phoenix/ledger/README.md`, after the existing "Key rotation" subsection of the encryption ceremony, add:

```markdown
### Per-actor key isolation (Phase 13.x.8)

For multi-tenant / compliance deployments, each actor can have their
own age identity + recipients under `actors/<actor_name>/`:

```bash
phoenix admin generate-encryption-key --actor <actor_name>
# or, in-process:
POST /v1/admin/encryption/rotate-key {"actor_name": "<actor_name>"}
```

`GET /v1/admin/encryption/actors` lists actors with per-actor keys.

**Fail-closed:** once `actors/<actor_name>/` exists, that actor is
"configured for isolation" — if its keys are broken/unreadable, the
encryptor resolution RAISES rather than silently downgrading to the
shared key. An actor with NO per-actor directory falls back to the
shared key (back-compat).

**Scope note (13.x.8 = plumbing):** the per-actor registry +
keygen routing + decrypt-routing are wired and tested, but the
ENCRYPTED_OPT_IN *write path* remains deferred (per 13-D2). Per-actor
isolation is not end-to-end-live until that write path activates;
this phase makes the plumbing ready for that day.
```

- [ ] **Step 2: Add the CHANGELOG entry**

In `CHANGELOG.md`, under `## [1.1.0.dev0] — 2026-05-20`, after the existing 13.x.7 / 13.5 entries, add:

```markdown
### Phase 13.x.8: per-actor key isolation (plumbing) (2026-06-05)

Per-actor encryption plumbing — registry, keygen routing, CLI/admin
surface, and decrypt-routing — keyed by `actor.name`, fail-closed.

**Scope (descoped after adversarial design review, workflow
`wf_432eec7f`):** this ships the per-actor *plumbing* only. The
ENCRYPTED_OPT_IN *write path* does not exist in Phoenix today (a
deliberate 13-D2 deferral: "column shipped, key-mgmt ceremony
deferred to first commercial customer"). 13.x.8 makes per-actor
isolation wired, tested, and ready — but NOT end-to-end-live until
that write path activates. The write path + per-actor audit
attribution remain deferred.

**New module:** `phoenix/ledger/encryption_actors.py` — per-actor
encryptor registry mirroring the shared `encryption.py` singleton,
keyed by `actor.name`, with:
- **Fail-CLOSED by directory presence:** `actors/<name>/` exists →
  a load failure (`AgeKeyLoadError`/`AgeKeyPermissionError`) raises
  `PerActorKeyError`, never silently downgrades to the shared key.
  Directory absent → shared fallback (back-compat).
- `threading.Lock` on the request-time cache (the rotate-key
  endpoint evicts entries). In-process eviction is correct only
  while single-worker; revisit at `--workers`.
- `_ACTOR_NAME_RE` validation + path-under-root guard (traversal).

**Storage:** the per-actor identifier lives inside `payload_json`
(`prompt_encryption_actor_id`), NOT a SQL column — Phoenix backends
persist only 7 base columns and replay reads from payload. **No
migration, no new permission flag, no SQL column.**

**Decrypt-routing:** `_replay_encrypted` reads
`payload.get("prompt_encryption_actor_id")` — absent → shared
encryptor (all current rows); present → per-actor.

**CLI/admin:** `phoenix admin generate-encryption-key --actor <name>`
and `POST /v1/admin/encryption/rotate-key {actor_name}` provision
keys under `actors/<name>/`; the rotate endpoint evicts the per-actor
cache on success. New `GET /v1/admin/encryption/actors` enumeration
(gated on `is_admin`). The rotate-key permission gate
(`can_rotate_encryption_key`) is checked against the authenticated
admin.

**Refactor:** extracted `encryption_age.py::encryptor_from_keys_dir`
(behavior-preserving) so shared + per-actor layouts share one builder.

**Tests added:** ~28 across 4 test files (registry/fail-closed/
traversal, decrypt-routing, CLI --actor, admin endpoints).

**Supersedes the v1 design** (`0d2d9ab`) which mistakenly assumed an
existing write path; see design v2
(`docs/superpowers/specs/2026-06-05-phase-13x8-per-actor-key-isolation-design-v2.md`).
```

- [ ] **Step 3: Full project test suite**

Run: `pytest tests/ --no-header 2>&1 | tail -10`
Expected: all Phase 13.x.8 tests pass; no regressions. Note total + skipped.

- [ ] **Step 4: mypy --strict on touched modules**

Run: `mypy phoenix/ledger/encryption_actors.py phoenix/ledger/encryption_age.py phoenix/ledger/cognition_replay.py phoenix/admin/encryption_admin.py phoenix/cli/commands/admin.py phoenix/cli/entry.py --strict 2>&1 | tail -3`
Expected: `Success: no issues found in 6 source files`.

- [ ] **Step 5: ruff check + format**

Run: `ruff check phoenix/ledger/encryption_actors.py phoenix/ledger/encryption_age.py phoenix/ledger/cognition_replay.py phoenix/admin/encryption_admin.py phoenix/cli/commands/admin.py phoenix/cli/entry.py tests/cognition/test_encryption_actors.py tests/cognition/test_replay_per_actor_routing.py tests/cli/test_admin_generate_encryption_key_actor.py tests/integration/test_admin_encryption_actors.py 2>&1 | tail -2`
Expected: `All checks passed!`.

Run: `ruff format --check <same files> 2>&1 | tail -2`
Expected: `N files already formatted`.

- [ ] **Step 6: Commit**

```bash
git add phoenix/ledger/README.md CHANGELOG.md
git commit -m "phase 13.x.8 step 6: README ceremony + CHANGELOG + full validation"
```

---

## Task 7: Push branch + create PR

- [ ] **Step 1: Push**

Run: `git push -u origin phase-13x-per-actor-keys 2>&1 | tail -3`

- [ ] **Step 2: Create the PR**

Run:
```bash
gh pr create --title "phase 13.x.8: per-actor key isolation (plumbing)" --body "$(cat <<'EOF'
## Summary

Per-actor encryption **plumbing** — registry, keygen routing, CLI/admin surface, and decrypt-routing — keyed by `actor.name`, fail-closed. Output of an adversarial design review (workflow `wf_432eec7f`, 6 reviewers) that found the v1 design assumed an ENCRYPTED_OPT_IN write path which does not exist in Phoenix (deliberate 13-D2 deferral). This PR ships the per-actor plumbing only; the write path + per-actor audit attribution stay deferred.

**Honest scope:** per-actor isolation is wired, tested, and ready — but NOT end-to-end-live until the deferred ENCRYPTED_OPT_IN write path activates.

## What ships

- **`phoenix/ledger/encryption_actors.py`** — per-actor encryptor registry mirroring the shared `encryption.py` singleton. **Fail-CLOSED by directory presence** (`actors/<name>/` exists + broken keys → `PerActorKeyError`, never a silent shared-key downgrade). `threading.Lock` on the request-time cache. `_ACTOR_NAME_RE` validation + path-under-root traversal guard.
- **Decrypt-routing:** `_replay_encrypted` reads `payload.get("prompt_encryption_actor_id")` (stored in `payload_json`, **not** a SQL column). Absent → shared; present → per-actor.
- **CLI:** `generate-encryption-key --actor <name>` → keys under `actors/<name>/`.
- **Admin:** `rotate-key` gains optional `actor_name` (+ cache eviction on success); new `GET /v1/admin/encryption/actors` enumeration (gated on `is_admin`).
- **Refactor:** extracted `encryptor_from_keys_dir` (behavior-preserving) so shared + per-actor layouts share one builder.

## NOT shipped (deferred)

- The ENCRYPTED_OPT_IN **write path** + startup `set_prompt_encryptor()` activation (13-D2).
- **Per-actor audit attribution** (encrypt audit currently `system.encryptor`).
- **No migration, no new permission flag, no SQL column.**

## Security fixes from the review

- v1's fallback **failed open** — a transient key-load failure silently downgraded an isolation-configured actor to shared-key. Now **fail-closed by directory presence**.
- `AgeKeyPermissionError` handled in the fail-closed branch (loose-permission key → typed error, not a crash).
- `actor_name` path-traversal guard (`../primary` refused).
- `threading.Lock` on the request-time cache.

## Tests

~28 new across 4 files: registry/fail-closed/traversal, decrypt-routing, CLI `--actor`, admin endpoints. Full project pytest green; mypy --strict clean; ruff clean.

## Spec / plan

- Design v2: `docs/superpowers/specs/2026-06-05-phase-13x8-per-actor-key-isolation-design-v2.md` (`d3a78f9`, supersedes v1 `0d2d9ab`)
- Plan: `docs/superpowers/plans/2026-06-05-phase-13x8-per-actor-key-isolation.md`
- Design review: workflow `wf_432eec7f` (6 reviewers, 50 findings, verified against the tree)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)" 2>&1 | tail -3
```

- [ ] **Step 3: Check initial CI**

Run: `gh pr checks <pr-number> 2>&1 | head -10`

- [ ] **Step 4: Final summary**

Report PR URL + number, commit chain, test counts (~28 new), CI initial state, and the honest scope framing (plumbing ready, write path deferred).

---

## Self-review

**Spec coverage (v2):**
- §0/§3 write-path deferral — Tasks document it (CHANGELOG Task 6, PR Task 7); no task builds the writer. ✓
- §4.1 directory layout — Tasks 2/4/5 write under `actors/<name>/`. ✓
- §4.2 registry — Task 2. ✓
- §4.3 payload storage, no column/migration — Task 3 reads `payload.get(...)`; no migration task exists (correct). ✓
- §4.5 decrypt-routing — Task 3. ✓
- §4.6 CLI/admin — Tasks 4/5. ✓
- §4.7 no new permission — no permissions task (correct). ✓
- §5.1 fail-closed resolution — Task 2 impl + tests. ✓
- §6 resolved markers — folded into impl (lock, fail-closed, 0o700 via keygen's chmod, no self-serve, mixed-state). NOTE: 0o700 on the actor subdir is created by `generate_age_keypair` via keygen's existing `recipients/` mkdir — verify keygen sets 0o700 on the actor dir; if not, this is a small add in Task 4/5. Flagged for implementer.
- §7 acceptance — Tasks 2-6 map to criteria 1-8. ✓
- §8 file list — matches Task files. ✓

**Placeholder scan:** No TBD/TODO. `<pr-number>` in Task 7 is a runtime field. Several "confirm during survey / adapt to actual shapes" notes are intentional guidance for matching live patterns (CLI handler arity, auth fixtures) — not deferred work.

**Type consistency:**
- `PerActorKeyError` consistent across Tasks 2, 4, 5. ✓
- `get_prompt_encryptor_for_actor` / `set_prompt_encryptor_for_actor` / `reset_prompt_encryptor_for_actor` / `list_actors_with_keys` / `encryptor_from_actor_default_layout` / `_actor_keys_dir` consistent across Tasks 2, 3, 4, 5. ✓
- `encryptor_from_keys_dir` defined Task 1, used Task 2. ✓
- `_resolve_decrypt_encryptor` defined + used Task 3. ✓
- `prompt_encryption_actor_id` payload key consistent Tasks 3, 5, 6. ✓
- `actor_name` (not `actor.id`) consistent throughout. ✓

**One open implementer flag (§6 0o700):** the actor-subdir `0o700` hardening depends on whether `generate_age_keypair` chmods the *directory* it creates (it chmods the *identity file* 0o600). If the actor dir isn't 0o700, add a `chmod(actor_dir, 0o700)` (POSIX, WARN on Windows) in the CLI/endpoint path. Implementer verifies in Task 4/5 and adds the one-liner if needed.

Plan complete.
