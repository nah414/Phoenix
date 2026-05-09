"""Bootstrap-actor mint for development convenience.

Per architecture v1 Section 7.3 + the locked Phase 6a scope decision
(2026-05-08): when the front door receives a request without an
``Authorization: Phoenix-Actor`` header AND the install's master key
keystore is present, auto-mint a bootstrap actor (defaults to
``"adam"``). This preserves the dev-mode "just call POST /v1/tasks" UX
while enforcing real Actor verification when a header IS present.

The bootstrap-actor flow is intended for:

- Dev / test / single-user installs on the install owner's machine.
- Tier-1 benchmark scripts that exercise the pipeline.
- The smoke-test ``python -m phoenix.api`` daemon.

It is NOT intended for:

- Multi-tenant deployments (Phoenix Cloud overrides via the cloud-
  seams ``HttpAuthExtractor`` per Decision 35).
- Org-enrolled installs with HKDF subkeys (Section 7.6 -- Phase 6b
  scope).
- Actor verification of signed payloads from external clients (the
  full :func:`actor.Actor.from_signed_payload` path).

When a real signed Actor header is present, :func:`extract_actor`
parses it and verifies via :func:`actor.Actor.from_signed_payload`.
When absent, falls back to the bootstrap mint via
:func:`mint_bootstrap_actor`.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING

from actor.actor import Actor

from phoenix.identity.keystore import (
    KeystoreError,
    get_install_fingerprint,
    load_or_generate_master_key,
)

if TYPE_CHECKING:
    pass


# Bootstrap actor identity. Section 7.3 names "adam" and "ash" as the
# v1 install owners; Phase 6a defaults to "adam" since the bootstrap
# flow runs on the install owner's machine by definition.
BOOTSTRAP_ACTOR_NAME = "adam"


class IdentityError(Exception):
    """Failed to extract or mint an Actor.

    Distinct from KeystoreError (I/O failure) and Actor signature
    verification errors (the vendored Actor's TypeError /
    PermissionError). Phase 6a maps to HTTP 401 at the front door.
    """


def mint_bootstrap_actor(name: str = BOOTSTRAP_ACTOR_NAME) -> Actor:
    """Create a fresh bootstrap actor for the install owner.

    Reads the master key from the keystore (first-call generates it),
    computes the install fingerprint, and signs a fresh
    :class:`actor.Actor` payload via ``Actor.sign``. The resulting
    Actor is valid for 5 minutes per the vendored
    ``SIGNATURE_VALIDITY_SECONDS`` window.

    Raises :class:`IdentityError` when the keystore can't be read or
    written (Phase 6a maps to HTTP 500 since the install is
    fundamentally broken without a keystore).
    """
    try:
        master_key = load_or_generate_master_key()
        fingerprint = get_install_fingerprint()
    except KeystoreError as exc:
        raise IdentityError(
            f"Cannot mint bootstrap actor: keystore unavailable "
            f"({exc.path}). The Phoenix install is broken; restore "
            f"the keystore directory or reinstall."
        ) from exc

    return Actor.sign(name, master_key=master_key, fingerprint=fingerprint)


def extract_actor_from_header(authorization_header: str) -> Actor:
    """Parse a signed Actor from an Authorization header.

    Expected format: ``Phoenix-Actor <base64-payload>`` where the
    payload is the base64-encoded JSON of
    :meth:`actor.Actor.to_payload`'s output.

    Raises :class:`IdentityError` when:
    - The header doesn't start with ``Phoenix-Actor``.
    - The base64 / JSON parse fails.
    - The vendored ``Actor.from_signed_payload`` rejects the signature
      (HMAC mismatch, stale timestamp window, etc.) -- the caller's
      :class:`TypeError` / :class:`PermissionError` is wrapped in
      :class:`IdentityError`.
    """
    if not authorization_header.startswith("Phoenix-Actor "):
        raise IdentityError(
            "Authorization header must start with 'Phoenix-Actor ' "
            "for Phoenix Actor verification."
        )
    encoded = authorization_header[len("Phoenix-Actor ") :].strip()
    if not encoded:
        raise IdentityError("Authorization header missing payload.")

    try:
        decoded = base64.b64decode(encoded.encode("ascii"), validate=True)
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise IdentityError(
            f"Authorization header payload is not valid base64 JSON: {exc}"
        ) from exc

    try:
        master_key = load_or_generate_master_key()
    except KeystoreError as exc:
        raise IdentityError(f"Cannot verify Actor: keystore unavailable ({exc.path}).") from exc

    try:
        actor = Actor.from_signed_payload(payload, master_key=master_key)
    except (TypeError, PermissionError, ValueError) as exc:
        raise IdentityError(f"Actor signature verification failed: {exc}") from exc

    if not actor.is_valid_now():
        raise IdentityError(
            f"Actor signature is outside the valid timestamp window "
            f"(issued at {actor.issued_at}; vendored 5-minute window)."
        )

    return actor


def extract_or_bootstrap(authorization_header: str | None) -> tuple[Actor, bool]:
    """Top-level helper for the front door.

    Returns ``(actor, was_bootstrapped)``. When the header is present,
    parses + verifies it; when absent, mints a bootstrap actor.

    The ``was_bootstrapped`` flag lands in audit logs so ops can tell
    bootstrap-mode requests from real signed-Actor requests at a
    glance.
    """
    if authorization_header:
        return extract_actor_from_header(authorization_header), False
    return mint_bootstrap_actor(), True
