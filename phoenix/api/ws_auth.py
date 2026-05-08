"""Bearer-token mint + verification for WebSocket connections.

Per architecture v1 Section 5.3 + 5.5: WebSocket authentication uses
short-lived bearer tokens. Client calls ``POST /v1/identity/ws-token``
with a full Actor signature; receives a token valid for 60 seconds,
single-use. The token is passed as ``Authorization: Bearer <token>``
on the WS handshake (or as a ``token=`` query parameter when the
client can't set headers).

Per Phase 6a scope (2026-05-08): in-memory token store; single-use,
single-process. Phase 6b's state backend provides cross-process
durability so tokens minted by one daemon worker are usable by another.
Phase 6a's daemon is single-process, so this is sufficient.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field


# Section 5.3 contract: 60-second window per minted token.
_TOKEN_VALIDITY_SECONDS = 60.0


class WSTokenError(Exception):
    """Bearer-token verification failed.

    Specific cases (token unknown / used / expired) carry the reason
    as the exception message; the WebSocket handler maps to a 1008
    policy-violation close code.
    """


@dataclass
class _TokenRecord:
    """Mutable in-memory token state."""

    actor_name: str
    issued_at_unix: float = field(default_factory=time.time)
    used: bool = False


class WSTokenStore:
    """In-memory short-lived bearer-token mint + consume.

    Phase 6a single-process. Phase 6b's state backend swaps in for
    multi-worker daemons.
    """

    def __init__(self, *, validity_seconds: float = _TOKEN_VALIDITY_SECONDS) -> None:
        self._tokens: dict[str, _TokenRecord] = {}
        self._lock = threading.Lock()
        self._validity = validity_seconds

    def mint(self, actor_name: str) -> str:
        """Generate a fresh single-use bearer token for ``actor_name``.

        Returns a 32-char URL-safe base64 string. Phase 6a uses
        :func:`secrets.token_urlsafe` for cryptographic randomness.
        """
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._tokens[token] = _TokenRecord(actor_name=actor_name)
        return token

    def consume(self, token: str) -> str:
        """Validate + consume a token; return the bound ``actor_name``.

        Raises :class:`WSTokenError` when:
        - The token is unknown.
        - The token was already used (single-use semantics).
        - The token expired (>60s since mint).

        On success, marks the token as used so subsequent calls fail.
        """
        with self._lock:
            record = self._tokens.get(token)
            if record is None:
                raise WSTokenError("Bearer token not recognized.")
            if record.used:
                raise WSTokenError("Bearer token already consumed (single-use).")
            elapsed = time.time() - record.issued_at_unix
            if elapsed > self._validity:
                raise WSTokenError(
                    f"Bearer token expired ({elapsed:.1f}s > "
                    f"{self._validity:.0f}s validity window)."
                )
            record.used = True
            return record.actor_name

    def reap_expired(self) -> int:
        """Remove expired token records. Returns count reaped.

        Phase 6a ops helper; the WebSocket endpoint handler can call
        this on a schedule. Phase 6b's state backend handles TTL via
        the underlying store.
        """
        now = time.time()
        with self._lock:
            expired = [
                token
                for token, record in self._tokens.items()
                if now - record.issued_at_unix > self._validity
            ]
            for token in expired:
                del self._tokens[token]
            return len(expired)

    def clear_all(self) -> None:
        """Drop all tokens. Phase 6a tests use this for isolation."""
        with self._lock:
            self._tokens.clear()


# Module-level singleton.
_STORE: WSTokenStore | None = None


def get_store() -> WSTokenStore:
    """Lazy module-level :class:`WSTokenStore` singleton."""
    global _STORE
    if _STORE is None:
        _STORE = WSTokenStore()
    return _STORE
