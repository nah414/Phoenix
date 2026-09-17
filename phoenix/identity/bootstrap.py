"""Actor identity at the front door: signed ``Phoenix-Actor`` headers only.

Every authenticated REST route resolves its caller with
:func:`require_actor`. It parses the
``Authorization: Phoenix-Actor <base64 JSON>`` header and verifies the
vendored :class:`actor.Actor` HMAC over ``name|fingerprint|issued_at``
plus the 300-second freshness window (architecture v1 Section 7.2). A
request with no header, an empty header, or a header that fails
verification raises :class:`IdentityError`, which every route maps to
HTTP 401.

**Security change (2026-09-16).** Until this date a request *without* an
``Authorization`` header was silently given a freshly minted ``adam``
bootstrap actor (the Phase 6a "locked scope" Decision 4, carried into
Phase 6b Decision 7). ``adam`` holds every admin flag by default
(Section 7.3), so anything that could reach the port was treated as the
install owner: a Docker container published on ``0.0.0.0``, a tailnet
peer, or a browser page using CSRF or DNS rebinding against
``127.0.0.1`` (CWE-306). That fallback is removed. There is no
header-less path and no environment flag that restores one.

**How the install owner authenticates now.** By signing, as Section 9.4
describes: a client running as the same OS user reads the existing
master key and signs. :func:`sign_local_actor_header` does exactly that
and never creates a key. The ``phoenix`` CLI and ``phoenix mcp serve``
call it on every request for the actor the operator configured
(``--actor`` or ``default_actor`` in ``~/.phoenix/config.yaml``); with no
actor configured they send no header, so nobody is signed as ``adam``
implicitly. ``phoenix identity header`` prints a header for curl or the
Swagger UI.

:func:`mint_bootstrap_actor` remains the in-process signer: the ledger
replay engine rebuilds actors with it, and tests use it to build real
signed actors. Nothing on the request path calls it for an unsigned
caller.
"""

from __future__ import annotations

import base64
import json

from actor.actor import Actor

from phoenix.identity.keystore import (
    KeystoreError,
    fingerprint_for_key,
    get_install_fingerprint,
    load_master_key,
    load_or_generate_master_key,
)

# Bootstrap actor identity. Section 7.3 names "adam" and "ash" as the
# v1 install owners. Only the in-process signer (:func:`mint_bootstrap_actor`)
# defaults to it; signing clients must be told which actor to sign as.
BOOTSTRAP_ACTOR_NAME = "adam"

# Authorization scheme for a signed Actor payload.
ACTOR_AUTH_SCHEME = "Phoenix-Actor"
_SCHEME_PREFIX = ACTOR_AUTH_SCHEME + " "


class IdentityError(Exception):
    """Failed to extract, verify, or sign an Actor.

    Distinct from KeystoreError (I/O failure) and Actor signature
    verification errors (the vendored Actor's TypeError /
    PermissionError). The front door maps it to HTTP 401.
    """


def mint_bootstrap_actor(name: str = BOOTSTRAP_ACTOR_NAME) -> Actor:
    """Sign a fresh Actor for ``name`` with this install's master key.

    In-process signer for trusted code (the ledger replay engine,
    tests). Reads the master key from the keystore (first call
    generates it), computes the install fingerprint, and signs via
    ``Actor.sign``. The resulting Actor is valid for 5 minutes per the
    vendored ``SIGNATURE_VALIDITY_SECONDS`` window.

    Never call this for an HTTP caller that did not present a signed
    header: that is the removed header-less bootstrap.

    Raises :class:`IdentityError` when the keystore can't be read or
    written.
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


def actor_authorization_header(actor: Actor) -> str:
    """Serialize a signed Actor as an ``Authorization`` header value."""
    payload = json.dumps(actor.to_payload()).encode("utf-8")
    return _SCHEME_PREFIX + base64.b64encode(payload).decode("ascii")


def sign_local_actor_header(name: str) -> str:
    """Sign ``name`` with this machine's *existing* master key.

    For signing clients running as the install owner's OS user. Unlike
    :func:`mint_bootstrap_actor` it never creates a key: a client that
    generated one would hold an identity no daemon trusts. ``name`` has
    no default: a client signs only as an actor its operator named.

    Raises :class:`IdentityError` when there is no readable key or the
    name is empty.
    """
    try:
        master_key = load_master_key()
    except KeystoreError as exc:
        raise IdentityError(f"Cannot sign as {name!r}: {exc}") from exc
    try:
        actor = Actor.sign(
            name,
            master_key=master_key,
            fingerprint=fingerprint_for_key(master_key),
        )
    except ValueError as exc:
        raise IdentityError(f"Cannot sign as {name!r}: {exc}") from exc
    return actor_authorization_header(actor)


_MAX_ERROR_DETAIL_CHARS = 200


def _describe(exc: BaseException) -> str:
    """Short ``Type: message`` for a 401 detail, bounded in length.

    The message can echo caller-supplied data, so it is truncated.
    """
    text = f"{type(exc).__name__}: {exc}"
    if len(text) > _MAX_ERROR_DETAIL_CHARS:
        text = text[:_MAX_ERROR_DETAIL_CHARS] + "..."
    return text


def extract_actor_from_header(authorization_header: str) -> Actor:
    """Parse and verify a signed Actor from an Authorization header.

    Expected format: ``Phoenix-Actor <base64-payload>`` where the
    payload is the base64-encoded JSON of
    :meth:`actor.Actor.to_payload`'s output.

    Raises :class:`IdentityError` when:
    - The header doesn't start with ``Phoenix-Actor``.
    - The base64 / JSON parse fails, for *any* reason. That includes
      :class:`RecursionError` from deeply nested JSON.
    - The vendored ``Actor.from_signed_payload`` rejects the payload or
      the signature (HMAC mismatch, stale timestamp window, a malformed
      field, etc.). Every exception it raises is wrapped, including the
      :class:`OverflowError` from ``int(float("inf"))`` on an
      ``issued_at`` of ``1e400``.

    The header is untrusted caller input, so no parse or verification
    failure may escape as anything other than :class:`IdentityError`
    (HTTP 401). Before 2026-09-16 an ``OverflowError`` or
    ``RecursionError`` escaped and surfaced as HTTP 500.
    """
    if not authorization_header.startswith(_SCHEME_PREFIX):
        raise IdentityError(
            "Authorization header must start with 'Phoenix-Actor ' for Phoenix Actor verification."
        )
    encoded = authorization_header[len(_SCHEME_PREFIX) :].strip()
    if not encoded:
        raise IdentityError("Authorization header missing payload.")

    try:
        decoded = base64.b64decode(encoded.encode("ascii"), validate=True)
        payload = json.loads(decoded.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 -- untrusted input: every parse failure is a 401
        raise IdentityError(
            f"Authorization header payload is not valid base64 JSON: {_describe(exc)}"
        ) from exc

    try:
        master_key = load_or_generate_master_key()
    except KeystoreError as exc:
        raise IdentityError(f"Cannot verify Actor: keystore unavailable ({exc.path}).") from exc

    try:
        actor = Actor.from_signed_payload(payload, master_key=master_key)
        valid_now = actor.is_valid_now()
    except Exception as exc:  # noqa: BLE001 -- untrusted input: every rejection is a 401
        raise IdentityError(f"Actor signature verification failed: {_describe(exc)}") from exc

    if not valid_now:
        raise IdentityError(
            f"Actor signature is outside the valid timestamp window "
            f"(issued at {actor.issued_at}; vendored 5-minute window)."
        )

    return actor


def require_actor(authorization_header: str | None) -> Actor:
    """Front-door helper: return the verified Actor or raise.

    A missing, empty, or whitespace-only header raises
    :class:`IdentityError` (HTTP 401 at every route). It is never
    replaced by a default actor, whatever the peer address, bind
    address, or environment.
    """
    if authorization_header is None or not authorization_header.strip():
        raise IdentityError(
            "Missing Authorization header. Phoenix requires a signed actor: "
            "send 'Authorization: Phoenix-Actor <signed payload>'. The phoenix "
            "CLI signs as the actor you configure (default_actor in "
            "~/.phoenix/config.yaml, or --actor); 'phoenix --actor <name> identity "
            "header' prints a header for curl or the /docs UI."
        )
    return extract_actor_from_header(authorization_header)


__all__ = [
    "ACTOR_AUTH_SCHEME",
    "BOOTSTRAP_ACTOR_NAME",
    "IdentityError",
    "actor_authorization_header",
    "extract_actor_from_header",
    "mint_bootstrap_actor",
    "require_actor",
    "sign_local_actor_header",
]
