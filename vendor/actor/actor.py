#!/usr/bin/env python3
"""
Dr. Frank & Eddy v6.5 -- Verified Actor authentication (Wave C Phase 6)

Replaces the string-actor whitelist with a typed `Actor` value. The
discovery engine and any future human-gated mutation path accept ONLY
`Actor` instances; passing a raw string or dict raises `TypeError`
before any whitelist check runs. This closes the actor-string-spoofing
loophole that lets any local process post `{"actor": "adam"}` to a
mutation endpoint and have it land as ACCEPTED.

Threat model honestly stated:

  * What HMAC catches:
      - Misuse at the type system (forgetting to construct an Actor
        properly fails closed, not silently succeeds).
      - Cross-machine spoofing via a forwarded port (no master key on
        the remote machine -> no valid signature).
      - Stale-replay attacks (timestamp window of
        SIGNATURE_VALIDITY_SECONDS).
      - Audit-trail provenance: every sealed event records the
        signature, so retroactively we can prove a DPAPI-credentialed
        process issued the request.
  * What HMAC does NOT catch:
      - A malicious local process running as the same OS user can read
        the install Ed25519 master key out of DPAPI and sign too.
        True per-app isolation requires Windows ACLs or per-app
        credential stores, neither of which DPAPI provides on its own.
        This signing layer is defense-in-depth, not an airtight gate.

Design:
  * `Actor.sign(name, master_key=..., fingerprint=...)` -- build a
    fresh signed Actor locally. Used by CLI clients and the backend's
    `/api/auth/sign-actor` bootstrap.
  * `Actor.from_signed_payload(payload, master_key)` -- parse + verify
    a wire payload. Constant-time HMAC comparison; raises
    PermissionError on mismatch or expiry.
  * `Actor.is_valid_now()` -- timestamp-window check. The engine
    re-checks at use time so a payload that ages out between transport
    and consumption still fails closed.
  * `Actor.to_payload()` -- wire form for transport (base64 signature).

The master key is the install's Ed25519 private-key bytes (raw 32
bytes), fetched via codec_crypto._load_master_secret_from_keyring().
Re-using the install identity keypair is intentional: we already trust
that key for codec sealing, and the messaging subsystem already trusts
it for peer authentication. Adding a third consumer (actor signing) is
load-balanced over the same DPAPI-protected secret rather than
introducing a fourth.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional


# Maximum wall-clock skew between issue time and verification time.
# Five minutes matches the BUILDGUIDE; tune if clocks drift more in
# practice. Past + future skew is symmetric to handle small NTP wobble.
SIGNATURE_VALIDITY_SECONDS: int = 300


@dataclass(frozen=True)
class Actor:
    """A verified human actor authorised to gate evolution mutations.

    Construct via `Actor.sign(...)` (locally) or
    `Actor.from_signed_payload(...)` (after verifying a wire payload).
    Direct instantiation is allowed for in-process trust (e.g. tests
    building a fixture); the engine still requires the type and a
    fresh timestamp at use time.

    The four fields below are sufficient for the engine to make a
    gate decision: who, on which install, when, and proof-by-HMAC.
    """
    name: str                       # "adam" or "ash"  (lowercase ascii)
    identity_fingerprint: str       # hex of install Ed25519 fingerprint
    issued_at: int                  # unix seconds, UTC
    signature: bytes                # HMAC-SHA256 over canonical payload

    # ---- Canonical signing payload -------------------------------------

    @staticmethod
    def _canonical_payload(name: str, fingerprint: str, issued_at: int) -> bytes:
        """The exact bytes that get HMAC'd. Pipe-separator picked so
        none of the three components can ambiguously bleed into the
        next (names are lowercase ascii, fingerprints are hex, issued_at
        is digits -- pipes never occur in any of them)."""
        return f"{name}|{fingerprint}|{issued_at}".encode("utf-8")

    # ---- Construction ---------------------------------------------------

    @classmethod
    def sign(
        cls,
        name: str,
        *,
        master_key: bytes,
        fingerprint: str,
        issued_at: Optional[int] = None,
    ) -> "Actor":
        """Build a fresh signed Actor.

        Args:
            name: human identifier ("adam" or "ash"); normalised to
                stripped + lowercase. Empty raises ValueError.
            master_key: 32 raw bytes of the install Ed25519 private
                key. Caller is responsible for zeroizing afterwards.
            fingerprint: hex string of the install identity
                fingerprint. Public information; safe to log.
            issued_at: optional override (test fixtures); defaults to
                current wall-clock seconds.
        """
        name_norm = (name or "").strip().lower()
        if not name_norm:
            raise ValueError("actor name required")
        ts = int(issued_at if issued_at is not None else time.time())
        sig = hmac.new(
            master_key,
            cls._canonical_payload(name_norm, fingerprint, ts),
            hashlib.sha256,
        ).digest()
        return cls(
            name=name_norm,
            identity_fingerprint=fingerprint,
            issued_at=ts,
            signature=sig,
        )

    @classmethod
    def from_signed_payload(
        cls, payload: Dict[str, Any], master_key: bytes,
    ) -> "Actor":
        """Parse a wire payload and verify the HMAC + freshness window.

        Returns the Actor on success. Raises PermissionError on:
          - malformed payload (missing or wrong-typed fields)
          - signature mismatch
          - timestamp outside the validity window

        Constant-time signature comparison via hmac.compare_digest.
        """
        if not isinstance(payload, dict):
            raise PermissionError(
                f"actor payload must be a dict, got {type(payload).__name__}"
            )
        try:
            name = str(payload["name"]).strip().lower()
            fingerprint = str(payload["identity_fingerprint"])
            issued_at = int(payload["issued_at"])
            sig_b64 = str(payload["signature"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PermissionError(f"actor payload malformed: {exc}") from exc

        try:
            sig = base64.b64decode(sig_b64, validate=True)
        except Exception as exc:
            raise PermissionError(f"actor signature not base64: {exc}") from exc

        expected = hmac.new(
            master_key,
            cls._canonical_payload(name, fingerprint, issued_at),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(sig, expected):
            raise PermissionError("actor signature does not verify")

        actor = cls(
            name=name,
            identity_fingerprint=fingerprint,
            issued_at=issued_at,
            signature=sig,
        )
        if not actor.is_valid_now():
            raise PermissionError(
                f"actor signature expired (issued at {issued_at}, "
                f"validity {SIGNATURE_VALIDITY_SECONDS}s)"
            )
        return actor

    # ---- Lifetime / wire form ------------------------------------------

    def is_valid_now(self) -> bool:
        """True iff `issued_at` is within SIGNATURE_VALIDITY_SECONDS of
        the current wall-clock time. Symmetric window absorbs NTP skew."""
        return abs(time.time() - self.issued_at) <= SIGNATURE_VALIDITY_SECONDS

    def to_payload(self) -> Dict[str, Any]:
        """Wire form for transport. The base64 signature is the only
        non-trivially-typed field."""
        return {
            "name": self.name,
            "identity_fingerprint": self.identity_fingerprint,
            "issued_at": self.issued_at,
            "signature": base64.b64encode(self.signature).decode("ascii"),
        }


__all__ = [
    "Actor",
    "SIGNATURE_VALIDITY_SECONDS",
]
