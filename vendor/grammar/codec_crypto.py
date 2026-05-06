#!/usr/bin/env python3
"""
Dr. Frank & Eddy v6.4.1 -- Sanskrit Codec Crypto (Phase SEC.1)

HKDF key derivation + AES-GCM-256 sealing for sanskrit codec output.

Design (see BUILDGUIDE_SANSKRIT_SECURITY.md):
  - Master secret is the install's Ed25519 private key (the same keypair
    the messaging subsystem uses as its BB84 peer identity, but the
    codec consumes only the local private bytes -- no quantum entropy,
    no peer exchange). Stored in Windows keyring, DPAPI-protected via
    the `keyring` library.
  - Two sub-keys derived per call via HKDF-SHA256 with distinct info strings:
      * permutation_seed (32 bytes): seeds the deterministic glyph shuffle
      * aead_key (32 bytes): AES-GCM-256 wrapping key for compressed blobs
  - Keys are NEVER cached. Every encode/decode call unwraps the master,
    derives the sub-keys, uses them for exactly one operation, then
    zeroizes all three buffers. Performance: ~2ms per call.
  - AAD binds each ciphertext to (codec_version, install_id, permutation_version).
  - Install id fingerprint = first 8 bytes of SHA-256(Ed25519 public key).
    Public information, not secret; safe to put in AAD.

This module is kept small and crypto-focused. No codec rule logic here.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass
from typing import Callable, Optional

import keyring
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


# Must match messaging.identity constants; keeping them duplicated here
# to avoid importing the whole messaging package when the codec runs
# (messaging pulls in sqlite, transport layer, etc. -- heavyweight for a
# crypto helper).
_KEYRING_SERVICE = "DrFrankAndEddy"
_KEYRING_PRIVATE_KEY_ID = "identity_ed25519_private"
_IDENTITY_JSON_PATH = "C:/frank-data/messaging/identity.json"


# HKDF domain-separation strings. Bump the version suffix if the derivation
# semantics ever change; old ciphertexts remain readable via the matching
# old info string.
_INFO_PERMUTATION_V1 = b"codec/permutation/v1"
_INFO_AEAD_V1 = b"codec/aead/v1"

# AES-GCM standard: 12-byte nonce, 16-byte auth tag. Don't change without
# re-reading the NIST spec; 12 bytes is the recommended nonce length and
# swapping to 16 enables collision risk at large call volumes.
_AEAD_NONCE_LEN = 12

# Debug-only plaintext escape hatch. Set FRANK_CODEC_ALLOW_PLAINTEXT=1 to
# disable sealing. Production installs must NOT set this; checked via
# environment read, not cached, so flipping the env in-process takes effect.
_PLAINTEXT_ENV_FLAG = "FRANK_CODEC_ALLOW_PLAINTEXT"


class IdentityNotInitialisedError(RuntimeError):
    """Raised when derive_keys() is called before the install Ed25519
    identity exists (file at _IDENTITY_JSON_PATH + private key in the
    DPAPI-backed keyring)."""


class SealError(RuntimeError):
    """Wraps cryptography.exceptions.InvalidTag + related decrypt failures."""


@dataclass(frozen=True)
class InstallIdentity:
    """Public install identity. Safe to log, include in AAD, share externally."""
    public_key_bytes: bytes   # 32 bytes, raw Ed25519 public key
    fingerprint: bytes        # 8 bytes, first bytes of SHA-256(public_key_bytes)

    @property
    def fingerprint_hex(self) -> str:
        return self.fingerprint.hex()


def zeroize(buf: bytearray) -> None:
    """Best-effort zero of a mutable byte buffer. Python's memory model
    doesn't strictly guarantee no copies, but this clears the object we
    hold and reduces the window where live key material exists."""
    for i in range(len(buf)):
        buf[i] = 0


def plaintext_mode_enabled() -> bool:
    """True only when the debug env flag is explicitly set. Defaults to
    False so production callers always seal."""
    return os.environ.get(_PLAINTEXT_ENV_FLAG) == "1"


def load_install_identity() -> InstallIdentity:
    """Load the public half of the install's Ed25519 identity.

    Reads from messaging/identity.json. Does NOT touch the private key
    (that stays in keyring/DPAPI). Safe to call from any context.

    The identity file lives under messaging/ historically because it was
    first introduced for the BB84 peer-discovery layer. The codec
    re-uses the same Ed25519 keypair as its sealing identity, but does
    not engage with BB84 messaging in any way.

    Raises IdentityNotInitialisedError if no identity file exists yet --
    caller can then either abort or trigger IdentityManager.load_or_create.
    """
    import json
    from pathlib import Path

    p = Path(_IDENTITY_JSON_PATH)
    if not p.exists():
        raise IdentityNotInitialisedError(
            f"No install Ed25519 identity at {p}. Run "
            f"messaging.identity.IdentityManager().load_or_create() to "
            f"generate one before using the sealed codec."
        )

    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)

    pub_b64 = data.get("public_key")
    if not pub_b64:
        raise IdentityNotInitialisedError(
            f"Identity file {p} missing 'public_key' field."
        )

    pub_bytes = base64.b64decode(pub_b64)
    fp = hashlib.sha256(pub_bytes).digest()[:8]
    return InstallIdentity(public_key_bytes=pub_bytes, fingerprint=fp)


def _load_master_secret_from_keyring() -> bytearray:
    """Unwrap the install Ed25519 private key via keyring (DPAPI on
    Windows).

    Returns 32 bytes of raw private-key material as a mutable bytearray so
    the caller can zeroize it. If keyring lookup fails, raises.

    This is the only function in this module that touches secret material;
    everything else consumes bytes or bytearrays it's given.
    """
    stored = keyring.get_password(_KEYRING_SERVICE, _KEYRING_PRIVATE_KEY_ID)
    if stored is None:
        raise IdentityNotInitialisedError(
            "No install Ed25519 private key in keyring. Run "
            "messaging.identity.IdentityManager().load_or_create() to "
            "initialise."
        )
    raw = base64.b64decode(stored)
    if len(raw) != 32:
        raise IdentityNotInitialisedError(
            f"Install Ed25519 private key has unexpected length "
            f"{len(raw)} (want 32)."
        )
    # Copy into a bytearray we control so the caller can zeroize.
    buf = bytearray(raw)
    # Immediately best-effort clear the decoded bytes object too.
    # (Python may have already copied; still worth asking.)
    del raw
    del stored
    return buf


def derive_keys_from_master(
    master_secret: bytes,
    install_id_fingerprint: bytes,
) -> tuple[bytearray, bytearray]:
    """Low-level HKDF derivation. Takes the master secret as a parameter
    so unit tests can exercise the crypto without touching keyring.

    Returns (permutation_seed, aead_key) as bytearrays; caller is
    responsible for zeroizing both when done.

    Pure function: same inputs -> same outputs, always.
    """
    if len(master_secret) != 32:
        raise ValueError(f"master_secret must be 32 bytes, got {len(master_secret)}")
    if len(install_id_fingerprint) != 8:
        raise ValueError(
            f"install_id_fingerprint must be 8 bytes, got {len(install_id_fingerprint)}"
        )

    # Salt = SHA-256 of the fingerprint. Deterministic per install.
    salt = hashlib.sha256(install_id_fingerprint).digest()

    # The `cryptography` library returns `bytes`; we wrap in bytearray so
    # the caller can overwrite.
    perm_seed_bytes = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=_INFO_PERMUTATION_V1,
    ).derive(bytes(master_secret))

    aead_key_bytes = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=_INFO_AEAD_V1,
    ).derive(bytes(master_secret))

    return bytearray(perm_seed_bytes), bytearray(aead_key_bytes)


def derive_keys(
    identity: Optional[InstallIdentity] = None,
    master_loader: Callable[[], bytearray] = _load_master_secret_from_keyring,
) -> tuple[bytearray, bytearray, InstallIdentity]:
    """High-level: load master via DPAPI, derive sub-keys, zeroize master.

    Returns (permutation_seed, aead_key, install_identity). The caller must
    zeroize the two key bytearrays when done with them.

    The `master_loader` callable exists as a test seam. Production code
    should never pass a custom loader.
    """
    if identity is None:
        identity = load_install_identity()
    master = master_loader()
    try:
        perm, aead = derive_keys_from_master(bytes(master), identity.fingerprint)
    finally:
        zeroize(master)
    return perm, aead, identity


def build_aad(
    codec_version: str,
    install_fingerprint: bytes,
    permutation_version: str = "v1",
) -> bytes:
    """Deterministic AAD for AES-GCM. Binds ciphertext to codec + install.

    Format: <codec_version>|<fp_hex>|<permutation_version> as UTF-8.
    Version bumps must bump the AAD schema too, so old ciphertexts fail
    decryption under a new codec version (forces migration).
    """
    return f"{codec_version}|{install_fingerprint.hex()}|{permutation_version}".encode("utf-8")


def seal(plaintext: bytes, aead_key: bytes, aad: bytes) -> bytes:
    """AES-GCM-256 encrypt. Returns nonce || ciphertext || tag.

    Nonce is 12 random bytes per call; at our call volume (< 2^32 calls
    per install lifetime) the collision probability is negligible.
    """
    if len(aead_key) != 32:
        raise ValueError(f"aead_key must be 32 bytes, got {len(aead_key)}")
    aesgcm = AESGCM(bytes(aead_key))
    nonce = secrets.token_bytes(_AEAD_NONCE_LEN)
    ct_and_tag = aesgcm.encrypt(nonce, plaintext, aad)
    return nonce + ct_and_tag


def unseal(blob: bytes, aead_key: bytes, aad: bytes) -> bytes:
    """AES-GCM-256 decrypt. Raises SealError on auth failure (wrong key,
    tampered ciphertext, mismatched AAD, or truncated blob)."""
    if len(aead_key) != 32:
        raise ValueError(f"aead_key must be 32 bytes, got {len(aead_key)}")
    if len(blob) < _AEAD_NONCE_LEN + 16:  # nonce + minimum 16-byte tag
        raise SealError("sealed blob too short to contain nonce + tag")

    nonce = blob[:_AEAD_NONCE_LEN]
    ct_and_tag = blob[_AEAD_NONCE_LEN:]
    aesgcm = AESGCM(bytes(aead_key))
    try:
        return aesgcm.decrypt(nonce, ct_and_tag, aad)
    except Exception as e:
        # Do not leak cryptography's InvalidTag details to callers; it's
        # the same answer for "wrong key" / "tampered" / "wrong AAD".
        raise SealError("authentication failed") from e


__all__ = [
    "IdentityNotInitialisedError",
    "SealError",
    "InstallIdentity",
    "zeroize",
    "plaintext_mode_enabled",
    "load_install_identity",
    "derive_keys_from_master",
    "derive_keys",
    "build_aad",
    "seal",
    "unseal",
]
