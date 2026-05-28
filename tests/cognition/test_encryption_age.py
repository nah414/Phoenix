"""Tests for :class:`AgePromptEncryptor` (Phase 13.x.6).

Coverage:

- Round-trip: encrypt(decrypt(x)) == x for typical canonical-form JSON.
- Multi-recipient encryption: blob can be decrypted by any of the
  configured identities (lossless key-rotation primitive).
- Tamper detection: modifying ciphertext bytes → AgeDecryptError.
- Wrong identity: decrypting with a non-matching identity →
  AgeDecryptError with identity_fingerprint attached.
- Identity-file permission validation (POSIX): refuses to load
  0o644 identity files; clear error message.
- Identity / recipient malformed: typed AgeKeyLoadError on bad inputs.
- Audit emission: encrypt + decrypt + decrypt-failure all fire
  structured events with fingerprint metadata.
- Module-import without pyrage installed: succeeds (lazy import).
- Default-layout convenience: ``encryptor_from_default_layout()``
  reads from the conventional Phoenix runtime keys directory.

**GPU SAFETY:** This test file uses a fake ``pyrage`` module
installed via ``sys.modules`` monkeypatch (same pattern as the
Phase 13 adapter tests). No real crypto is performed; the fake
performs reversible byte permutation that round-trips and includes
a simple authentication-tag analog for tamper detection. No ML
imports, no network, no GPU.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from phoenix.ledger.encryption_age import (
    AgeDecryptError,
    AgeKeyLoadError,
    AgeKeyPermissionError,
    AgePromptEncryptor,
    _fingerprint,
    default_keys_dir,
    encryptor_from_default_layout,
)


# ---------------------------------------------------------------------------
# Fake pyrage module.
#
# We mimic pyrage's API surface (Identity.from_str, Recipient.from_str,
# encrypt, decrypt) with deterministic XOR-style "encryption" that
# round-trips properly + detects tamper via a 4-byte trailer.
# This is purely for testing the wrapper code path, NOT a security
# primitive — the real pyrage uses ChaCha20-Poly1305.


_FAKE_TAG = b"PXTG"  # 4-byte fake authentication tag


class _FakeIdentity:
    def __init__(self, secret: str) -> None:
        self.secret = secret
        # Derive a fake public key from the secret (sha256 truncated).
        import hashlib

        self._pub = "age1" + hashlib.sha256(secret.encode()).hexdigest()[:30]

    def to_public(self) -> _FakeRecipient:
        return _FakeRecipient(self._pub)


class _FakeRecipient:
    def __init__(self, pub_str: str) -> None:
        self.pub = pub_str

    def __str__(self) -> str:
        return self.pub


def _fake_encrypt(plaintext: bytes, recipients: list[Any]) -> bytes:
    """Reversible XOR with a deterministic stream + recipient header
    + 4-byte tag. Not crypto; structurally faithful to pyrage's
    'bytes-in, bytes-out, recipients-as-list' contract.
    """
    # Header: recipient count + each recipient's pub string length-prefixed.
    header_parts: list[bytes] = [len(recipients).to_bytes(2, "big")]
    for r in recipients:
        pub_bytes = str(r).encode("utf-8")
        header_parts.append(len(pub_bytes).to_bytes(2, "big"))
        header_parts.append(pub_bytes)
    header = b"".join(header_parts)

    # XOR plaintext with cycling 0x42 stream.
    body = bytes(b ^ 0x42 for b in plaintext)
    return header + body + _FAKE_TAG


def _fake_decrypt(ciphertext: bytes, identities: list[Any]) -> bytes:
    """Inverse of _fake_encrypt + validates header / tag / identity match."""
    if not ciphertext.endswith(_FAKE_TAG):
        raise ValueError("authentication tag missing or invalid (fake-tamper)")
    body_and_header = ciphertext[: -len(_FAKE_TAG)]

    # Parse header: recipient count.
    if len(body_and_header) < 2:
        raise ValueError("ciphertext too short to contain header")
    n_recipients = int.from_bytes(body_and_header[:2], "big")
    pos = 2
    recipient_pubs: list[str] = []
    for _ in range(n_recipients):
        if pos + 2 > len(body_and_header):
            raise ValueError("malformed recipient length prefix")
        rlen = int.from_bytes(body_and_header[pos : pos + 2], "big")
        pos += 2
        recipient_pubs.append(body_and_header[pos : pos + rlen].decode("utf-8"))
        pos += rlen

    # Verify the identity matches at least one recipient.
    identity_pubs = {ident.to_public().pub for ident in identities}
    if not (identity_pubs & set(recipient_pubs)):
        raise ValueError(f"no identity matches any of the {len(recipient_pubs)} recipients")

    body = body_and_header[pos:]
    return bytes(b ^ 0x42 for b in body)


@pytest.fixture
def fake_pyrage(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Install a fake ``pyrage`` module that mirrors the real API surface.

    Returned as ``Any`` because mypy can't track dynamic attribute
    assignment on ``types.ModuleType``; the real pyrage has the same
    surface in practice.
    """
    fake: Any = types.ModuleType("pyrage")
    fake_x25519: Any = types.ModuleType("pyrage.x25519")

    fake_x25519.Identity = MagicMock()
    fake_x25519.Identity.from_str = lambda s: _FakeIdentity(s)
    fake_x25519.Recipient = MagicMock()
    fake_x25519.Recipient.from_str = lambda s: _FakeRecipient(s)

    fake.x25519 = fake_x25519
    fake.encrypt = _fake_encrypt
    fake.decrypt = _fake_decrypt

    monkeypatch.setitem(sys.modules, "pyrage", fake)
    monkeypatch.setitem(sys.modules, "pyrage.x25519", fake_x25519)
    return fake


# ---------------------------------------------------------------------------
# Key-file fixtures.


def _write_identity(tmp_path: Path, *, secret: str = "TESTKEY-1") -> Path:
    p = tmp_path / "identity.txt"
    p.write_text(f"AGE-SECRET-KEY-{secret}\n", encoding="utf-8")
    # Set tight permissions (POSIX); ignored on Windows.
    if sys.platform != "win32":
        os.chmod(p, 0o600)
    return p


def _write_recipient(tmp_path: Path, *, name: str = "primary", secret: str = "TESTKEY-1") -> Path:
    """Write a recipient file whose pub key matches an identity built
    from the same ``secret``."""
    p = tmp_path / f"{name}.pub"
    fake_id = _FakeIdentity(f"AGE-SECRET-KEY-{secret}")
    p.write_text(str(fake_id.to_public()) + "\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Round-trip + multi-recipient tests.


class TestRoundTrip:
    def test_encrypt_decrypt_roundtrip(self, fake_pyrage: Any, tmp_path: Path) -> None:
        identity = _write_identity(tmp_path)
        recipient = _write_recipient(tmp_path)
        enc = AgePromptEncryptor(
            identity_path=identity,
            recipient_paths=[recipient],
        )

        canonical = '{"messages":[{"content":"hi","role":"user"}],"system":null}'
        ciphertext = enc.encrypt(canonical)
        assert isinstance(ciphertext, bytes)
        assert ciphertext != canonical.encode("utf-8")  # actually encrypted

        plaintext = enc.decrypt(ciphertext)
        assert plaintext == canonical

    def test_multi_recipient_either_identity_decrypts(
        self, fake_pyrage: Any, tmp_path: Path
    ) -> None:
        """Encrypt to {A, B}; either identity A or B decrypts (rotation overlap)."""
        identity_a = tmp_path / "identity_a.txt"
        identity_a.write_text("AGE-SECRET-KEY-ALPHA\n", encoding="utf-8")
        identity_b = tmp_path / "identity_b.txt"
        identity_b.write_text("AGE-SECRET-KEY-BETA\n", encoding="utf-8")
        if sys.platform != "win32":
            os.chmod(identity_a, 0o600)
            os.chmod(identity_b, 0o600)

        recipient_a = _write_recipient(tmp_path, name="recA", secret="ALPHA")
        recipient_b = _write_recipient(tmp_path, name="recB", secret="BETA")

        # Encrypt with identity A's view; recipients are {A, B}.
        enc_with_a = AgePromptEncryptor(
            identity_path=identity_a,
            recipient_paths=[recipient_a, recipient_b],
        )
        canonical = '{"messages":[],"system":"x"}'
        ciphertext = enc_with_a.encrypt(canonical)

        # Identity B can also decrypt the same blob (rotation overlap).
        enc_with_b = AgePromptEncryptor(
            identity_path=identity_b,
            recipient_paths=[recipient_a, recipient_b],
        )
        plaintext = enc_with_b.decrypt(ciphertext)
        assert plaintext == canonical


# ---------------------------------------------------------------------------
# Tamper / wrong-key detection.


class TestTamperDetection:
    def test_tampered_ciphertext_raises(self, fake_pyrage: Any, tmp_path: Path) -> None:
        identity = _write_identity(tmp_path)
        recipient = _write_recipient(tmp_path)
        enc = AgePromptEncryptor(
            identity_path=identity,
            recipient_paths=[recipient],
        )
        ciphertext = enc.encrypt("hello")
        # Truncate the authentication tag (last 4 bytes of fake format).
        tampered = ciphertext[:-4]
        with pytest.raises(AgeDecryptError) as exc:
            enc.decrypt(tampered)
        assert exc.value.identity_fingerprint == enc.identity_fingerprint

    def test_wrong_identity_raises(self, fake_pyrage: Any, tmp_path: Path) -> None:
        """Encrypt to recipient A; try to decrypt with identity B."""
        identity_a = tmp_path / "identity_a.txt"
        identity_a.write_text("AGE-SECRET-KEY-ALPHA\n", encoding="utf-8")
        if sys.platform != "win32":
            os.chmod(identity_a, 0o600)

        identity_b = tmp_path / "identity_b.txt"
        identity_b.write_text("AGE-SECRET-KEY-BETA\n", encoding="utf-8")
        if sys.platform != "win32":
            os.chmod(identity_b, 0o600)

        recipient_a = _write_recipient(tmp_path, name="recA", secret="ALPHA")
        recipient_b = _write_recipient(tmp_path, name="recB", secret="BETA")

        enc_a = AgePromptEncryptor(identity_path=identity_a, recipient_paths=[recipient_a])
        enc_b = AgePromptEncryptor(identity_path=identity_b, recipient_paths=[recipient_b])
        ciphertext = enc_a.encrypt("secret payload")

        with pytest.raises(AgeDecryptError) as exc:
            enc_b.decrypt(ciphertext)
        assert exc.value.identity_fingerprint == enc_b.identity_fingerprint
        assert exc.value.identity_fingerprint != enc_a.identity_fingerprint


# ---------------------------------------------------------------------------
# Permission validation (POSIX only; skipped on Windows).


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission check")
class TestPermissionValidation:
    def test_loose_permissions_raises(self, fake_pyrage: Any, tmp_path: Path) -> None:
        identity = tmp_path / "identity.txt"
        identity.write_text("AGE-SECRET-KEY-TEST\n", encoding="utf-8")
        os.chmod(identity, 0o644)  # too permissive
        recipient = _write_recipient(tmp_path)
        with pytest.raises(AgeKeyPermissionError) as exc:
            AgePromptEncryptor(identity_path=identity, recipient_paths=[recipient])
        assert "chmod 0600" in str(exc.value)


# ---------------------------------------------------------------------------
# Key-load error paths.


class TestKeyLoadErrors:
    def test_missing_identity_file_raises(self, fake_pyrage: Any, tmp_path: Path) -> None:
        # POSIX: missing file = stat fails inside permission check.
        # Windows: permission check is skipped; the read fails instead.
        # Both paths surface AgeKeyLoadError.
        recipient = _write_recipient(tmp_path)
        with pytest.raises(AgeKeyLoadError):
            AgePromptEncryptor(
                identity_path=tmp_path / "nope.txt",
                recipient_paths=[recipient],
            )

    def test_empty_identity_file_raises(self, fake_pyrage: Any, tmp_path: Path) -> None:
        identity = tmp_path / "identity.txt"
        identity.write_text("", encoding="utf-8")
        if sys.platform != "win32":
            os.chmod(identity, 0o600)
        recipient = _write_recipient(tmp_path)
        with pytest.raises(AgeKeyLoadError, match="contains no AGE-SECRET-KEY"):
            AgePromptEncryptor(identity_path=identity, recipient_paths=[recipient])

    def test_malformed_identity_raises(self, fake_pyrage: Any, tmp_path: Path) -> None:
        identity = tmp_path / "identity.txt"
        identity.write_text("not-an-age-key\n", encoding="utf-8")
        if sys.platform != "win32":
            os.chmod(identity, 0o600)
        recipient = _write_recipient(tmp_path)
        with pytest.raises(AgeKeyLoadError, match="non-identity line"):
            AgePromptEncryptor(identity_path=identity, recipient_paths=[recipient])

    def test_identity_with_age_keygen_comment_header_parses(
        self, fake_pyrage: Any, tmp_path: Path
    ) -> None:
        """Codex review (P1, 2026-05-28): ``age-keygen -o identity.txt`` writes
        ``# created:`` and ``# public key:`` comment headers before the
        secret-key line. The loader must skip comments/blanks and accept the
        secret-key line — otherwise the default setup flow documented in
        phoenix/ledger/README.md fails at startup with AgeKeyLoadError.
        """
        identity = tmp_path / "identity.txt"
        identity.write_text(
            "# created: 2026-05-28T12:00:00Z\n"
            "# public key: age1examplepubkey\n"
            "AGE-SECRET-KEY-COMMENTHEADERTEST\n",
            encoding="utf-8",
        )
        if sys.platform != "win32":
            os.chmod(identity, 0o600)
        recipient = _write_recipient(tmp_path)
        # Should construct cleanly with no raise.
        enc = AgePromptEncryptor(identity_path=identity, recipient_paths=[recipient])
        assert enc.identity_fingerprint == _fingerprint(
            _FakeIdentity("AGE-SECRET-KEY-COMMENTHEADERTEST")._pub
        )

    def test_identity_with_multiple_secret_key_lines_raises(
        self, fake_pyrage: Any, tmp_path: Path
    ) -> None:
        """Defensive: a well-formed age identity file contains exactly one
        secret-key line. Two or more indicates corruption or a concatenated
        identity set — reject rather than silently use the first.
        """
        identity = tmp_path / "identity.txt"
        identity.write_text(
            "AGE-SECRET-KEY-ONE\nAGE-SECRET-KEY-TWO\n",
            encoding="utf-8",
        )
        if sys.platform != "win32":
            os.chmod(identity, 0o600)
        recipient = _write_recipient(tmp_path)
        with pytest.raises(AgeKeyLoadError, match="multiple AGE-SECRET-KEY"):
            AgePromptEncryptor(identity_path=identity, recipient_paths=[recipient])

    def test_empty_recipient_paths_list_raises(self, fake_pyrage: Any, tmp_path: Path) -> None:
        identity = _write_identity(tmp_path)
        with pytest.raises(AgeKeyLoadError, match="at least one recipient_path"):
            AgePromptEncryptor(identity_path=identity, recipient_paths=[])

    def test_malformed_recipient_raises(self, fake_pyrage: Any, tmp_path: Path) -> None:
        identity = _write_identity(tmp_path)
        bad_recipient = tmp_path / "bad.pub"
        bad_recipient.write_text("not-a-recipient\n", encoding="utf-8")
        with pytest.raises(AgeKeyLoadError, match="non-recipient line"):
            AgePromptEncryptor(identity_path=identity, recipient_paths=[bad_recipient])


# ---------------------------------------------------------------------------
# Audit emission.


class TestAuditEmission:
    def test_encrypt_emits_audit_event(
        self,
        fake_pyrage: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Encrypt fires cognition.prompt.encrypted audit with fingerprints."""
        from phoenix.audit import get_emitter, reset_emitter

        reset_emitter()
        events: list[Any] = []
        emitter = get_emitter()
        original_emit = emitter.emit

        def _capturing_emit(ev: Any) -> None:
            events.append(ev)
            original_emit(ev)

        monkeypatch.setattr(emitter, "emit", _capturing_emit)

        identity = _write_identity(tmp_path)
        recipient = _write_recipient(tmp_path)
        enc = AgePromptEncryptor(identity_path=identity, recipient_paths=[recipient])
        enc.encrypt("canonical-form-text")

        encrypted_events = [e for e in events if e.event_type == "cognition.prompt.encrypted"]
        assert len(encrypted_events) == 1
        ev = encrypted_events[0]
        assert ev.layer == "ledger.encryption"
        assert ev.actor_id == "system.encryptor"
        assert "recipient_fingerprints" in ev.parameters
        assert ev.parameters["plaintext_bytes"] == len("canonical-form-text")
        reset_emitter()

    def test_decrypt_failure_emits_audit(
        self,
        fake_pyrage: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from phoenix.audit import get_emitter, reset_emitter

        reset_emitter()
        events: list[Any] = []
        emitter = get_emitter()
        original_emit = emitter.emit

        def _capturing_emit(ev: Any) -> None:
            events.append(ev)
            original_emit(ev)

        monkeypatch.setattr(emitter, "emit", _capturing_emit)

        identity = _write_identity(tmp_path)
        recipient = _write_recipient(tmp_path)
        enc = AgePromptEncryptor(identity_path=identity, recipient_paths=[recipient])
        # Tampered ciphertext.
        with pytest.raises(AgeDecryptError):
            enc.decrypt(b"not-a-valid-blob")

        failed = [e for e in events if e.event_type == "cognition.prompt.decrypt.failed"]
        assert len(failed) == 1
        assert "identity_fingerprint" in failed[0].parameters
        reset_emitter()


# ---------------------------------------------------------------------------
# Public attributes + helpers.


class TestPublicSurface:
    def test_identity_fingerprint_property(self, fake_pyrage: Any, tmp_path: Path) -> None:
        identity = _write_identity(tmp_path)
        recipient = _write_recipient(tmp_path)
        enc = AgePromptEncryptor(identity_path=identity, recipient_paths=[recipient])
        fp = enc.identity_fingerprint
        assert len(fp) == 16  # 16 hex chars
        assert all(c in "0123456789abcdef" for c in fp)

    def test_recipient_fingerprints_property(self, fake_pyrage: Any, tmp_path: Path) -> None:
        identity = _write_identity(tmp_path)
        recipient = _write_recipient(tmp_path)
        enc = AgePromptEncryptor(identity_path=identity, recipient_paths=[recipient])
        fps = enc.recipient_fingerprints
        assert len(fps) == 1
        assert all(len(fp) == 16 for fp in fps)

    def test_fingerprint_helper_deterministic(self) -> None:
        """Same input string → same fingerprint, always."""
        assert _fingerprint("age1abc") == _fingerprint("age1abc")
        assert _fingerprint("age1abc") != _fingerprint("age1def")

    def test_default_keys_dir_default_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PHOENIX_ENCRYPTION_KEYS_DIR", raising=False)
        d = default_keys_dir()
        # Path ends with .phoenix/runtime/encryption_keys.
        parts = d.parts
        assert ".phoenix" in parts
        assert "runtime" in parts
        assert parts[-1] == "encryption_keys"

    def test_default_keys_dir_env_override(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("PHOENIX_ENCRYPTION_KEYS_DIR", str(tmp_path))
        d = default_keys_dir()
        assert d == tmp_path.resolve()


# ---------------------------------------------------------------------------
# Default-layout convenience constructor.


class TestDefaultLayout:
    def test_constructs_from_default_layout(
        self,
        fake_pyrage: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Lay out the conventional dir + read it via the convenience func."""
        monkeypatch.setenv("PHOENIX_ENCRYPTION_KEYS_DIR", str(tmp_path))
        identity = tmp_path / "identity.txt"
        identity.write_text("AGE-SECRET-KEY-CONV\n", encoding="utf-8")
        if sys.platform != "win32":
            os.chmod(identity, 0o600)
        recipients_dir = tmp_path / "recipients"
        recipients_dir.mkdir()
        rec_file = recipients_dir / "primary.pub"
        fake_id = _FakeIdentity("AGE-SECRET-KEY-CONV")
        rec_file.write_text(str(fake_id.to_public()) + "\n", encoding="utf-8")

        enc = encryptor_from_default_layout()
        assert isinstance(enc, AgePromptEncryptor)
        ct = enc.encrypt("hello")
        assert enc.decrypt(ct) == "hello"

    def test_missing_identity_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PHOENIX_ENCRYPTION_KEYS_DIR", str(tmp_path))
        with pytest.raises(AgeKeyLoadError, match="identity file not found"):
            encryptor_from_default_layout()

    def test_missing_recipients_dir_raises(
        self, fake_pyrage: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PHOENIX_ENCRYPTION_KEYS_DIR", str(tmp_path))
        (tmp_path / "identity.txt").write_text("AGE-SECRET-KEY-X\n", encoding="utf-8")
        if sys.platform != "win32":
            os.chmod(tmp_path / "identity.txt", 0o600)
        with pytest.raises(AgeKeyLoadError, match="recipients directory not found"):
            encryptor_from_default_layout()

    def test_empty_recipients_dir_raises(
        self, fake_pyrage: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PHOENIX_ENCRYPTION_KEYS_DIR", str(tmp_path))
        (tmp_path / "identity.txt").write_text("AGE-SECRET-KEY-X\n", encoding="utf-8")
        if sys.platform != "win32":
            os.chmod(tmp_path / "identity.txt", 0o600)
        (tmp_path / "recipients").mkdir()
        with pytest.raises(AgeKeyLoadError, match="no \\*\\.pub recipient files"):
            encryptor_from_default_layout()
