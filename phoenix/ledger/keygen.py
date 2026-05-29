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

    if not force:
        for p in (identity_path, recipient_path):
            if p.exists():
                raise KeyGenPathConflict(
                    f"keygen: file already exists at {p}; refusing to "
                    f"overwrite. Pass force=True to replace it, or use a "
                    f"different name.",
                    existing_path=p,
                )

    try:
        recipient_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise KeyGenWriteError(
            f"keygen: cannot create recipients directory at {recipient_path.parent}: {exc}"
        ) from exc

    try:
        import pyrage  # noqa: PLC0415  -- lazy by design
    except ImportError as exc:
        raise ImportError(
            "pyrage is not installed; install via `pip install phoenix-middleware[encryption-age]`."
        ) from exc

    try:
        identity = pyrage.x25519.Identity.generate()
    except Exception as exc:
        raise KeyGenWriteError(f"keygen: pyrage Identity.generate() failed: {exc}") from exc

    identity_text = str(identity)
    recipient_text = str(identity.to_public())

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
