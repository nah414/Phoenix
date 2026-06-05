"""``AgePromptEncryptor`` — age-based concrete :class:`PromptEncryptor` impl.

Phase 13.x.6 (2026-05-23) — the customer-key-management ceremony.

This module replaces the Phase 13 default :class:`NullPromptEncryptor`
with a real cryptographic implementation backed by **age**
(<https://age-encryption.org>), Filippo Valsorda's modern file
encryption format. The Python binding used here is
`pyrage <https://github.com/woodruffw/pyrage>`_ — Rust bindings,
~5-10× faster than the pure-Python ``age`` package, stable API.

**Cryptographic primitives** (selected by age, not by Phoenix):

- Key agreement: X25519
- Symmetric AEAD: ChaCha20-Poly1305
- KDF: scrypt for passphrase recipients (not used in Phoenix; we use
  X25519 key pairs only)

**Threat model — what this protects against:**

- **Offline attacker with filesystem access to the encrypted blobs**
  (e.g., a stolen ``ledger_entries`` database dump): cannot read
  prompt contents without the identity (private key).
- **Remote attacker with no keys**: cannot read prompt contents.
- **Ciphertext tampering**: ChaCha20-Poly1305's authentication tag
  detects any modification; decrypt raises rather than returning
  garbage plaintext.
- **Wrong-key attacks**: age's recipient-stanza format ensures only
  the matching identity can decrypt; trying with the wrong identity
  raises a clean error.

**Threat model — what this does NOT protect against:**

- **In-process attacker with daemon memory access**: prompts are
  in-memory in plaintext during encryption; an attacker who can read
  the Phoenix daemon's heap can read the plaintext directly.
- **OS-level keylogger or compromised init**: can intercept prompts
  before they reach the encryption layer.
- **Identity-file theft**: an attacker who steals
  ``~/.phoenix/runtime/encryption_keys/identity.txt`` can decrypt
  every blob ever encrypted to the corresponding recipient. File
  permissions (validated by this module) raise the bar here but
  don't eliminate the risk.
- **Coercion / legal compulsion of the key holder**: out of scope
  for cryptographic controls.

For deployments needing tamper-evident **external** audit (e.g.,
regulated industries where Phoenix's own ledger isn't a sufficient
witness), a v1.2.x ``AwsKmsPromptEncryptor`` / ``VaultPromptEncryptor``
plugs into the same :class:`PromptEncryptor` Protocol via
:func:`set_prompt_encryptor`. The age impl is the local-default
reference; the cloud-KMS impls are enterprise add-ons.

**Key rotation story (age-native):**

age natively supports multi-recipient encryption. To rotate keys
without re-encrypting existing data:

1. Generate a new identity + recipient pair.
2. Add the new recipient's pub key to ``recipient_paths`` (don't
   remove the old one yet).
3. New encrypts go to ``{old_recipient, new_recipient}`` — either
   identity can decrypt them.
4. Old encrypts remain decryptable with the old identity.
5. After the transition window, batch-rotate by reading old entries,
   decrypting with old identity, re-encrypting to the new recipient
   only, then deleting the old identity file.

The batch-rotate operation itself is a v1.1.x admin command
(Phase 13.x.7); this module ships the multi-recipient encrypt
primitive that makes the rotation safe.

**SAFETY contract (load-bearing):**

- Identity files MUST have ``0o600`` permissions on POSIX systems.
  This module **refuses to load** an identity file with looser
  permissions; the error message includes the exact ``chmod 0600``
  command to fix.
- Identity contents are NEVER logged, never serialized to audit
  events, never included in error messages. Audit events carry only
  the SHA-256 fingerprint of the recipient pub keys.
- Failed decrypts are NOT retried. A failed decrypt indicates
  tampering or wrong key; retry would be wrong-shaped.
- ``pyrage`` is imported lazily on first call so module import
  succeeds without the ``[encryption-age]`` extra installed.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phoenix.ledger.encryption import PromptEncryptor  # noqa: F401  -- Protocol re-export

if TYPE_CHECKING:
    pass


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Typed errors.


class AgeEncryptionError(Exception):
    """Base class for AgePromptEncryptor failures."""


class AgeKeyPermissionError(AgeEncryptionError):
    """Identity file permissions are too permissive (POSIX only).

    Phoenix refuses to load identity files with permissions wider
    than ``0o600`` (owner read/write only) on POSIX systems. This
    catches a common ops mistake where an admin checks an identity
    into a shared directory or copies one with the wrong umask.

    The error message includes the exact ``chmod 0600`` command to
    fix. The module does NOT provide an opt-out — loosening this
    check would silently undermine the encryption story.
    """


class AgeKeyLoadError(AgeEncryptionError):
    """Could not parse an age identity or recipient file.

    Common causes: file is empty; file contains a non-X25519 key
    (passphrase or SSH keys are unsupported by Phoenix's age
    integration); file was truncated mid-write.
    """


class AgeDecryptError(AgeEncryptionError):
    """Decryption failed.

    Carries ``identity_fingerprint`` (a 16-hex-char SHA-256 prefix of
    the identity that attempted the decrypt) so forensic analysis can
    correlate the failed decrypt against audit log entries that
    recorded the encrypt operation's recipient set. Common causes:

    - Ciphertext was tampered with at rest (AEAD authentication tag
      mismatch).
    - The identity loaded doesn't match any recipient the blob was
      encrypted to (e.g., admin tried to decrypt with the new
      identity but the blob was encrypted only to the old recipient).
    - File-level corruption of the blob (storage media failure).

    Phoenix does NOT retry decrypts. The caller (typically the
    replay engine) surfaces this as a typed error to the admin
    endpoint.

    Sourcery review (2026-05-28): the prior docstring promised a
    ``recipient_fingerprints_tried`` list attribute that the class
    never exposed — decryption operates with one identity at a time,
    so the meaningful audit handle is the identity, not a recipient
    set. Cross-referencing the encrypt-side recipient fingerprints is
    done at the audit-log layer (the ``cognition.prompt.encrypted``
    event carries them).
    """

    def __init__(self, message: str, *, identity_fingerprint: str) -> None:
        super().__init__(message)
        self.identity_fingerprint = identity_fingerprint


# ---------------------------------------------------------------------------
# Internal helpers.


_POSIX_KEY_MODE_MAX = 0o600
_FINGERPRINT_HEX_LEN = 16  # truncated SHA-256 prefix for audit correlation


def _fingerprint(public_key_text: str) -> str:
    """SHA-256-prefix fingerprint of an age recipient public key string.

    Returns the first 16 hex chars of the SHA-256 of the *text* of
    the public key (the ``age1...`` string). 16 hex = 64 bits of
    collision resistance, plenty for audit correlation (we're not
    asking it to be cryptographic; just stable).

    Why hash the text rather than the raw 32-byte public key bytes?
    Convenience: the text form is what ops sees in key files and
    audit logs; hashing the same string ops sees gives a fingerprint
    they can independently reproduce with ``sha256sum`` if needed.
    """
    return hashlib.sha256(public_key_text.encode("utf-8")).hexdigest()[:_FINGERPRINT_HEX_LEN]


def _check_key_file_permissions(path: Path) -> None:
    """Refuse to load an identity file with insecure permissions.

    POSIX-only check. On Windows the equivalent (ACL inspection) is
    a v1.1.x improvement — the ``os.stat`` mode bits there are not
    meaningfully predictive of access control. We don't fail on
    Windows; we WARN via the log. This matches how other Phoenix
    secret-bearing files (e.g., ``master_key.bin`` in the
    Phase 6a keystore) handle the same gap.
    """
    if sys.platform == "win32":
        log.warning(
            "encryption_age: identity file permission check skipped on Windows "
            "(path=%s); use NTFS ACLs to restrict to the daemon's user account.",
            path,
        )
        return
    try:
        mode = path.stat().st_mode & 0o777
    except OSError as exc:
        raise AgeKeyLoadError(
            f"encryption_age: cannot stat identity file at {path}: {exc}"
        ) from exc
    if mode & ~_POSIX_KEY_MODE_MAX:
        raise AgeKeyPermissionError(
            f"encryption_age: identity file at {path} has mode {oct(mode)} "
            f"(too permissive). Phoenix requires mode 0o600 (owner read/write). "
            f"Fix with: chmod 0600 {path}"
        )


def _emit_audit(
    *,
    event_type: str,
    parameters: dict[str, Any],
) -> None:
    """Emit a structured audit event for encrypt/decrypt operations.

    Audit events go through ``phoenix.audit.get_emitter()`` so they
    land in the Omega Ledger's JSONL sink (and OpenTelemetry exporter
    when configured). The encryptor's ``actor_id`` is
    ``"system.encryptor"`` because the encryption op happens at the
    ledger-append seam without per-request actor context; per-actor
    audit attribution is a v1.1.x improvement that requires threading
    actor context through the Protocol.

    Wrapped in try/except so a misbehaving audit emitter does NOT
    take down the encryption hot path. ``log.exception`` captures
    the failure for ops debugging.
    """
    from phoenix.audit import AuditEvent, get_emitter

    try:
        get_emitter().emit(
            AuditEvent(
                timestamp_unix=time.time(),
                actor_id="system.encryptor",
                layer="ledger.encryption",
                event_type=event_type,
                parameters=parameters,
                result_hash="",
            )
        )
    except Exception:
        log.exception("encryption_age: audit emit failed (event_type=%s)", event_type)


# ---------------------------------------------------------------------------
# AgePromptEncryptor.


@dataclass(frozen=True)
class _LoadedKeys:
    """Internal: the parsed age identity + recipients with fingerprints."""

    identity: Any  # pyrage.x25519.Identity — kept as Any to avoid hard import dep
    identity_fingerprint: str
    recipients: list[Any]  # list[pyrage.x25519.Recipient]
    recipient_fingerprints: list[str] = field(default_factory=list)


class AgePromptEncryptor:
    """age-based implementation of :class:`PromptEncryptor`.

    Construct with paths to one identity file (X25519 private key)
    and one or more recipient files (X25519 public keys). The
    encryptor reads + parses the files once at construction time so
    every encrypt/decrypt avoids disk I/O.

    Args:
        identity_path: Path to the age identity file (private key).
            The file must contain a single line beginning with
            ``AGE-SECRET-KEY-`` (X25519 only; passphrase and SSH
            identities are unsupported by Phoenix's integration).
            On POSIX systems the file must have mode ``0o600``.
        recipient_paths: List of paths to age recipient files (public
            keys). Each file contains one or more lines beginning
            with ``age1``. Multi-recipient encryption is supported
            for lossless key rotation — see module docstring.

    Raises:
        AgeKeyPermissionError: identity file permissions too loose
            (POSIX only).
        AgeKeyLoadError: file not found, malformed key, or empty file.
        ImportError: ``pyrage`` not installed. Install via
            ``pip install phoenix-middleware[encryption-age]``.

    **Lazy SDK import:** ``pyrage`` is imported on first construction
    so module import succeeds without the extra installed. The
    typed import error includes the install command.
    """

    def __init__(
        self,
        *,
        identity_path: Path,
        recipient_paths: list[Path],
    ) -> None:
        if not recipient_paths:
            raise AgeKeyLoadError(
                "encryption_age: at least one recipient_path is required "
                "(multi-recipient encryption supports key rotation)."
            )

        _check_key_file_permissions(identity_path)

        try:
            import pyrage
        except ImportError as exc:
            raise ImportError(
                "pyrage is not installed; install via "
                "`pip install phoenix-middleware[encryption-age]`."
            ) from exc

        # Load identity.
        #
        # Codex review (P1, 2026-05-28): ``age-keygen -o identity.txt`` writes
        # comment headers (``# created: ...`` + ``# public key: ...``) before
        # the secret-key line, so a blob-level ``startswith("AGE-SECRET-KEY-")``
        # check rejects valid default-layout identities and breaks the setup
        # flow documented in phoenix/ledger/README.md. We parse line-by-line —
        # mirroring the recipient parser below — and accept the first non-blank,
        # non-comment line as the secret-key candidate. Multiple secret-key
        # lines are an error (real age identities contain exactly one).
        try:
            identity_text_full = identity_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise AgeKeyLoadError(
                f"encryption_age: cannot read identity file at {identity_path}: {exc}"
            ) from exc
        secret_key_line: str | None = None
        for raw_line in identity_text_full.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if not line.startswith("AGE-SECRET-KEY-"):
                raise AgeKeyLoadError(
                    f"encryption_age: identity file at {identity_path} contains a "
                    f"non-identity line (expected 'AGE-SECRET-KEY-...'); got: "
                    f"{line[:32]}... Phoenix's age integration does not support "
                    f"passphrase or SSH identities."
                )
            if secret_key_line is not None:
                raise AgeKeyLoadError(
                    f"encryption_age: identity file at {identity_path} contains "
                    f"multiple AGE-SECRET-KEY- lines; expected exactly one."
                )
            secret_key_line = line
        if secret_key_line is None:
            raise AgeKeyLoadError(
                f"encryption_age: identity file at {identity_path} contains no "
                f"AGE-SECRET-KEY- line (comments and blank lines are allowed but "
                f"a secret-key line is required). Phoenix's age integration does "
                f"not support passphrase or SSH identities."
            )
        try:
            identity = pyrage.x25519.Identity.from_str(secret_key_line)
        except Exception as exc:
            raise AgeKeyLoadError(
                f"encryption_age: pyrage failed to parse identity at {identity_path}: {exc}"
            ) from exc

        # Fingerprint the identity via its derived public key.
        # pyrage exposes identity.to_public() → Recipient, str() → "age1..."
        try:
            identity_pub_text = str(identity.to_public())
        except Exception as exc:
            raise AgeKeyLoadError(
                f"encryption_age: could not derive public key from identity at "
                f"{identity_path}: {exc}"
            ) from exc
        identity_fingerprint = _fingerprint(identity_pub_text)

        # Load recipients.
        recipients: list[Any] = []
        recipient_fingerprints: list[str] = []
        for rpath in recipient_paths:
            try:
                rtext_full = rpath.read_text(encoding="utf-8")
            except OSError as exc:
                raise AgeKeyLoadError(
                    f"encryption_age: cannot read recipient file at {rpath}: {exc}"
                ) from exc
            # A recipient file may contain multiple "age1..." lines (e.g., one
            # per intended decryptor). Skip blank + comment lines.
            for raw_line in rtext_full.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if not line.startswith("age1"):
                    raise AgeKeyLoadError(
                        f"encryption_age: recipient file at {rpath} contains a "
                        f"non-recipient line (expected lines starting with 'age1'); "
                        f"got: {line[:32]}..."
                    )
                try:
                    rec = pyrage.x25519.Recipient.from_str(line)
                except Exception as exc:
                    raise AgeKeyLoadError(
                        f"encryption_age: pyrage failed to parse recipient line in {rpath}: {exc}"
                    ) from exc
                recipients.append(rec)
                recipient_fingerprints.append(_fingerprint(line))

        if not recipients:
            raise AgeKeyLoadError(
                f"encryption_age: no valid recipients found across "
                f"{len(recipient_paths)} recipient file(s); at least one is required."
            )

        self._keys = _LoadedKeys(
            identity=identity,
            identity_fingerprint=identity_fingerprint,
            recipients=recipients,
            recipient_fingerprints=recipient_fingerprints,
        )
        self._pyrage = pyrage

    def encrypt(self, canonical_form: str) -> bytes:
        """Encrypt ``canonical_form`` to all configured recipients.

        The output is age's binary container format (header listing
        the recipient stanzas + ChaCha20-Poly1305 ciphertext +
        authentication tag). The format is self-describing — age can
        read it back without external metadata.

        Emits ``cognition.prompt.encrypted`` audit event with the
        list of recipient fingerprints used.

        Args:
            canonical_form: The canonicalized prompt JSON
                (output of :func:`phoenix.ledger.prompt_disposition.
                canonicalize_prompt`).

        Returns:
            age-encrypted bytes suitable for the ``prompt_encrypted``
            BLOB column.

        Raises:
            AgeEncryptionError: pyrage encrypt call failed (rare —
                pyrage validates inputs at recipient-load time).
        """
        plaintext_bytes = canonical_form.encode("utf-8")
        try:
            ciphertext = self._pyrage.encrypt(plaintext_bytes, self._keys.recipients)
        except Exception as exc:
            _emit_audit(
                event_type="cognition.prompt.encrypt.failed",
                parameters={
                    "recipient_fingerprints": list(self._keys.recipient_fingerprints),
                    "error_type": type(exc).__name__,
                },
            )
            raise AgeEncryptionError(f"encryption_age: pyrage encrypt failed: {exc}") from exc

        _emit_audit(
            event_type="cognition.prompt.encrypted",
            parameters={
                "recipient_fingerprints": list(self._keys.recipient_fingerprints),
                "plaintext_bytes": len(plaintext_bytes),
                "ciphertext_bytes": len(ciphertext),
            },
        )
        return bytes(ciphertext)

    def decrypt(self, encrypted: bytes) -> str:
        """Decrypt ``encrypted`` using the configured identity.

        Emits ``cognition.prompt.decrypted`` on success or
        ``cognition.prompt.decrypt.failed`` on AEAD failure / wrong
        key. **Does not retry** — a failed decrypt is structural
        (tampered ciphertext or wrong identity), not transient.

        Args:
            encrypted: age-format ciphertext bytes (output of a
                previous :meth:`encrypt` call, possibly with
                different recipients per key-rotation).

        Returns:
            The original canonical prompt JSON string.

        Raises:
            AgeDecryptError: AEAD authentication failed, wrong
                identity, or ciphertext malformed. Carries
                ``identity_fingerprint`` for forensic correlation.
        """
        try:
            plaintext_bytes = self._pyrage.decrypt(encrypted, [self._keys.identity])
        except Exception as exc:
            _emit_audit(
                event_type="cognition.prompt.decrypt.failed",
                parameters={
                    "identity_fingerprint": self._keys.identity_fingerprint,
                    "ciphertext_bytes": len(encrypted),
                    "error_type": type(exc).__name__,
                },
            )
            raise AgeDecryptError(
                f"encryption_age: pyrage decrypt failed (identity_fingerprint="
                f"{self._keys.identity_fingerprint}): {exc}",
                identity_fingerprint=self._keys.identity_fingerprint,
            ) from exc

        try:
            # bytes(...) coerces pyrage's possibly-Any return to mypy-bytes;
            # str(...) on the decode handles pyrage's untyped surface.
            plaintext = bytes(plaintext_bytes).decode("utf-8")
        except UnicodeDecodeError as exc:
            _emit_audit(
                event_type="cognition.prompt.decrypt.failed",
                parameters={
                    "identity_fingerprint": self._keys.identity_fingerprint,
                    "ciphertext_bytes": len(encrypted),
                    "error_type": "UnicodeDecodeError",
                },
            )
            raise AgeDecryptError(
                f"encryption_age: decrypted plaintext is not valid UTF-8 "
                f"(identity_fingerprint={self._keys.identity_fingerprint}): {exc}",
                identity_fingerprint=self._keys.identity_fingerprint,
            ) from exc

        _emit_audit(
            event_type="cognition.prompt.decrypted",
            parameters={
                "identity_fingerprint": self._keys.identity_fingerprint,
                "ciphertext_bytes": len(encrypted),
                "plaintext_bytes": len(plaintext_bytes),
            },
        )
        return plaintext

    @property
    def identity_fingerprint(self) -> str:
        """16-hex-char SHA-256 prefix of the identity's public key.

        Exposed for ops correlation: an admin can compare this
        against the ``identity_fingerprint`` recorded in decrypt
        audit events to verify the correct identity is loaded.
        """
        return self._keys.identity_fingerprint

    @property
    def recipient_fingerprints(self) -> list[str]:
        """List of 16-hex-char fingerprints of configured recipients.

        Order matches construction order. New encrypts go to ALL of
        these (multi-recipient encryption); decrypts use the loaded
        identity (whose fingerprint should match one of these for
        the encrypt-and-decrypt-with-same-keys case, or NOT match
        them for the post-rotation case).
        """
        return list(self._keys.recipient_fingerprints)


# ---------------------------------------------------------------------------
# Convenience helper for the typical Phoenix runtime layout.


_DEFAULT_KEYS_DIR_ENV = "PHOENIX_ENCRYPTION_KEYS_DIR"
_DEFAULT_KEYS_DIR_REL = Path(".phoenix") / "runtime" / "encryption_keys"


def default_keys_dir() -> Path:
    """Return the conventional Phoenix encryption-keys directory.

    Override via ``$PHOENIX_ENCRYPTION_KEYS_DIR``; defaults to
    ``~/.phoenix/runtime/encryption_keys/``. Matches the runtime-
    directory convention used elsewhere in Phase 6a / Phase 6b.

    The directory layout:

    .. code-block:: text

        encryption_keys/
        ├── identity.txt           # X25519 private key (mode 0o600)
        └── recipients/
            ├── primary.pub        # X25519 public key
            └── rotation.pub       # additional recipients for key rotation
    """
    env = os.environ.get(_DEFAULT_KEYS_DIR_ENV)
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / _DEFAULT_KEYS_DIR_REL).resolve()


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


def encryptor_from_default_layout() -> AgePromptEncryptor:
    """Construct an :class:`AgePromptEncryptor` from the conventional layout.

    Convenience constructor for daemon startup:

    .. code-block:: python

        from phoenix.ledger.encryption import set_prompt_encryptor
        from phoenix.ledger.encryption_age import encryptor_from_default_layout

        set_prompt_encryptor(encryptor_from_default_layout())

    Reads ``identity.txt`` + every ``*.pub`` under ``recipients/``
    from the directory returned by :func:`default_keys_dir`.

    Raises:
        AgeKeyLoadError: if the directory structure isn't as expected.
        AgeKeyPermissionError: if the identity file is too permissive.
    """
    return encryptor_from_keys_dir(default_keys_dir())


__all__ = [
    "AgeDecryptError",
    "AgeEncryptionError",
    "AgeKeyLoadError",
    "AgeKeyPermissionError",
    "AgePromptEncryptor",
    "default_keys_dir",
    "encryptor_from_default_layout",
    "encryptor_from_keys_dir",
]
