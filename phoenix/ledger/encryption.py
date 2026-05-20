"""Prompt-encryption skeleton for ``ENCRYPTED_OPT_IN`` (Phase 13 Step 8).

Per 13-D2 (locked 2026-05-18) + [OPEN: P13-3] default: Phase 13 ships
the **column** and the encrypt/decrypt **Protocol surface**; the
actual customer-key-management ceremony — KMS integration, key
rotation, per-actor key isolation, audit-bound encrypt/decrypt
events — lands at a follow-on phase (TBD; tracked as Phase 13.1 or
folded into Phase 7's broader at-rest-encryption story).

The point of shipping the skeleton now:

1. The migration (Step 8 chunk 3) adds the ``prompt_encrypted``
   ``BLOB NULL`` column to ``ledger_entries`` so the on-disk schema
   is stable — no later migration churn for ops who have already
   stood up the v4 ledger.
2. The Protocol seam :class:`PromptEncryptor` is wired through the
   ledger append + replay paths so the call sites exist; swapping in
   a real impl later is one-line setter in
   :func:`set_prompt_encryptor`.
3. The default :class:`NullPromptEncryptor` raises
   :class:`EncryptedDispositionNotConfigured` on every call. Any
   actor attempting ``prompt_disposition=ENCRYPTED_OPT_IN`` before
   the ceremony lands gets a clear, non-silent failure with a link
   to the open issue.

**SAFETY:** This module performs NO cryptographic operations in its
default state. The :class:`NullPromptEncryptor`'s ``encrypt`` and
``decrypt`` both raise. Production deployments that need
``ENCRYPTED_OPT_IN`` MUST replace the default impl via
:func:`set_prompt_encryptor` during daemon startup, and the
replacement MUST integrate with a real KMS — Phoenix does not ship a
"toy" software-key encryptor because the failure mode of a toy
encryptor (looks-secure-isn't) is worse than the failure mode of the
null encryptor (obviously-not-configured).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Typed error


class EncryptedDispositionNotConfigured(Exception):
    """Raised when a caller attempts ``ENCRYPTED_OPT_IN`` before the
    customer-key-management ceremony has been completed.

    Maps to HTTP 501 (Not Implemented) at the front door. The message
    points the caller to the open tracking issue so ops know which
    follow-on phase wires the ceremony.

    This is deliberately **not** an :class:`MCPError` or any other
    Phase 13 subsystem error — encryption is its own concern with its
    own follow-on phase, and folding it under the cognition errors
    would conflate "cognition disabled" with "encryption not
    configured" at the front door.
    """

    def __init__(
        self,
        message: str | None = None,
    ) -> None:
        super().__init__(
            message
            or (
                "ENCRYPTED_OPT_IN prompt disposition requires a configured "
                "PromptEncryptor (customer-key-management ceremony). Phase 13 "
                "ships the column + Protocol; the ceremony lands at a follow-on "
                "phase. Configure via phoenix.ledger.encryption.set_prompt_encryptor() "
                "during daemon startup."
            )
        )


# ---------------------------------------------------------------------------
# Protocol seam


@runtime_checkable
class PromptEncryptor(Protocol):
    """Encrypt/decrypt the canonical prompt JSON for at-rest storage.

    Phase 13 ships this Protocol surface; the customer-key-management
    ceremony that produces a real impl lands later. Replacement impls
    MUST:

    1. Integrate with a real KMS — no software-only "toy" keys.
    2. Tag each encrypted blob with the key ID / rotation epoch so
       decryption survives key rotation.
    3. Emit an audit event on every encrypt + decrypt op (call site
       in the ledger append/read path threads the actor through).
    4. Be deterministic-decrypt: ``decrypt(encrypt(canonical)) ==
       canonical`` for every supported canonical form.

    The Protocol intentionally does NOT specify a key-id parameter —
    that's the impl's internal contract with its KMS. The caller
    treats the encryptor as opaque bytes-in, bytes-out (with a clear
    error on failure).
    """

    def encrypt(self, canonical_form: str) -> bytes:
        """Encrypt the canonicalized prompt JSON. Returns bytes
        suitable for the ``prompt_encrypted`` BLOB column.

        Implementations must raise an appropriate exception on
        configuration failure (e.g. missing key); :func:`encrypt`
        MUST NOT return ``b""`` to silently signal "not configured".
        """
        ...

    def decrypt(self, encrypted: bytes) -> str:
        """Decrypt a stored blob back to the canonical prompt JSON.

        Must raise on decryption failure (key rotated out, blob
        tampered with, wrong KMS). The replay engine catches the
        error and either falls back to HASH_ONLY semantics (if the
        original disposition was HASH_ONLY before encryption was
        enabled) or surfaces the failure to the operator.
        """
        ...


# ---------------------------------------------------------------------------
# Default null impl


class NullPromptEncryptor:
    """The Phase-13-shipped default; both methods raise.

    See module docstring for why a null encryptor is the right
    default: a "toy" software-key impl would look secure but isn't,
    and that's a worse failure mode than "obviously not configured".

    Tests that exercise the ``ENCRYPTED_OPT_IN`` write path with a
    real-shaped impl should swap in a test double via
    :func:`set_prompt_encryptor` — the null impl is the production
    default, not a test fixture.
    """

    def encrypt(self, canonical_form: str) -> bytes:
        raise EncryptedDispositionNotConfigured()

    def decrypt(self, encrypted: bytes) -> str:
        raise EncryptedDispositionNotConfigured()


# ---------------------------------------------------------------------------
# Module-level singleton accessor


_PROMPT_ENCRYPTOR: PromptEncryptor = NullPromptEncryptor()


def get_prompt_encryptor() -> PromptEncryptor:
    """Return the currently configured :class:`PromptEncryptor`.

    Defaults to :class:`NullPromptEncryptor`. Production deployments
    swap in a real KMS-backed impl via :func:`set_prompt_encryptor`
    during daemon startup.
    """
    return _PROMPT_ENCRYPTOR


def set_prompt_encryptor(encryptor: PromptEncryptor) -> None:
    """Replace the module-level :class:`PromptEncryptor` impl.

    Called by daemon startup (Phase 13.1 / follow-on phase) once the
    customer-key-management ceremony has run. Tests use this to swap
    in a test double; production calls it once during init and never
    again.

    No thread-safety lock: this is a startup-time mutation, not a
    runtime one. Daemon startup is single-threaded; swapping the
    encryptor mid-request would race the read path anyway.
    """
    global _PROMPT_ENCRYPTOR
    _PROMPT_ENCRYPTOR = encryptor


def reset_prompt_encryptor() -> None:
    """Test helper: restore the default :class:`NullPromptEncryptor`.

    Production code never calls this. Tests use it in ``teardown``
    to clear any test-installed encryptor.
    """
    global _PROMPT_ENCRYPTOR
    _PROMPT_ENCRYPTOR = NullPromptEncryptor()


__all__ = [
    "EncryptedDispositionNotConfigured",
    "PromptEncryptor",
    "NullPromptEncryptor",
    "get_prompt_encryptor",
    "set_prompt_encryptor",
    "reset_prompt_encryptor",
]
