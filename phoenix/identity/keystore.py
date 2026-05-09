"""Ed25519 master-key keystore for Phoenix install identity.

Per architecture v1 Section 1 Decision 10 + Section 7.3: Phoenix's
install identity is anchored to an Ed25519 keypair stored in the OS-
native keystore (DPAPI on Windows, Keychain on macOS, libsecret on
Linux). The vendored :class:`actor.Actor` HMAC-signs payloads with a
key derived from this master key.

Phase 6a ships the **filesystem-backed** keystore: the master key is
generated on first run and stored at ``~/.phoenix/runtime/master_key.bin``
with restrictive file permissions (0600 on POSIX). This is the honest
threat model from Section 7.2 -- a malicious local process running as
the same OS user can read this file. v1.x adds OS-keystore bindings
(DPAPI / Keychain / libsecret) to harden against same-OS-user attacks.

The keystore module exposes:

- :func:`load_or_generate_master_key` -- returns the bytes; first call
  generates + persists, subsequent calls load.
- :func:`get_install_fingerprint` -- a stable hex string derived from
  the master key (Section 7.3 ``identity_fingerprint`` field on the
  vendored Actor).
- :class:`KeystoreError` -- raised on permission / I/O failures.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path

# 32 bytes = 256 bits, enough for HMAC-SHA256 + Ed25519 derivation.
_MASTER_KEY_BYTES = 32
_KEYSTORE_DIR_NAME = ".phoenix"
_RUNTIME_SUBDIR = "runtime"
_KEY_FILENAME = "master_key.bin"


class KeystoreError(Exception):
    """Keystore I/O or permission failure.

    Carries the path so audit log readers see where the failure
    happened. Phase 6a maps to HTTP 500 at the front door (the install
    can't sign anything without a master key).
    """

    def __init__(self, message: str, *, path: Path | None = None) -> None:
        super().__init__(message)
        self.path = path


def _keystore_dir() -> Path:
    """Phoenix's per-user runtime directory.

    Phase 6a uses ``~/.phoenix/runtime/`` -- platform-agnostic. v1.x's
    OS-keystore bindings will keep this directory for runtime state
    (kill switch, ActorPermissions JSON) and move the master key to
    DPAPI / Keychain / libsecret.
    """
    home = Path.home()
    return home / _KEYSTORE_DIR_NAME / _RUNTIME_SUBDIR


def _master_key_path() -> Path:
    return _keystore_dir() / _KEY_FILENAME


def load_or_generate_master_key() -> bytes:
    """Return the install's master key bytes.

    First call generates a 32-byte cryptographically-random key and
    persists it. Subsequent calls load from disk. POSIX file mode is
    set to 0600 (read+write owner only) at write time; Windows relies
    on the user's home directory ACLs.

    Raises :class:`KeystoreError` on directory creation or read/write
    permission failures.
    """
    path = _master_key_path()
    if path.exists():
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise KeystoreError(
                f"Failed to read master key at {path}: {exc}",
                path=path,
            ) from exc
        if len(data) != _MASTER_KEY_BYTES:
            raise KeystoreError(
                f"Master key at {path} is malformed "
                f"(expected {_MASTER_KEY_BYTES} bytes, got {len(data)}).",
                path=path,
            )
        return data

    # First-run generation.
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise KeystoreError(
            f"Failed to create keystore directory at {path.parent}: {exc}",
            path=path.parent,
        ) from exc

    key = secrets.token_bytes(_MASTER_KEY_BYTES)
    try:
        path.write_bytes(key)
    except OSError as exc:
        raise KeystoreError(
            f"Failed to write master key at {path}: {exc}",
            path=path,
        ) from exc

    # POSIX-only: tighten permissions to 0600.
    if hasattr(os, "chmod") and os.name != "nt":
        try:
            os.chmod(path, 0o600)
        except OSError:
            # Best-effort; not fatal on filesystems without chmod support.
            pass

    return key


def get_install_fingerprint() -> str:
    """Derive a stable hex fingerprint from the master key.

    Per Section 7.3 the fingerprint is "hex of install Ed25519
    fingerprint". Phase 6a derives a SHA-256 hex digest over the
    master key bytes (truncated to 32 hex chars / 16 bytes for
    readability). v1.x's OS-keystore bindings switch to a real Ed25519
    public-key fingerprint; the wire format on the vendored
    :class:`Actor` accepts any string identifier.
    """
    key = load_or_generate_master_key()
    digest = hashlib.sha256(key).hexdigest()
    return digest[:32]
