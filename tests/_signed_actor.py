"""Signed-actor helpers for API tests.

Phoenix no longer turns a request without an ``Authorization`` header into
the all-privileged ``adam`` bootstrap actor (see
``phoenix/identity/bootstrap.py``). Tests that exercise authenticated routes
therefore send a real HMAC-signed ``Phoenix-Actor`` header, minted from the
install master key exactly the way a local signing client (the ``phoenix``
CLI) does it.

- :func:`actor_header` builds one header value. It serializes the payload
  itself rather than calling the production serializer, so the tests also
  pin the wire format.
- :class:`SignedActorAuth` is an :class:`httpx.Auth` that signs *each*
  request freshly, so a long-lived client never sends a header older than
  the vendored 300-second window. A request that already carries an
  ``Authorization`` header (for example an ``_alice_header()`` negative
  case) is left untouched.
- :func:`signed_client` returns a :class:`TestClient` wired with that auth.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Generator
from typing import Any

import httpx
from fastapi.testclient import TestClient

import phoenix  # noqa: F401  -- puts the vendored ``actor`` package on sys.path

ADMIN_ACTOR = "adam"


def actor_header(name: str = ADMIN_ACTOR) -> str:
    """Return a freshly signed ``Phoenix-Actor`` header value for ``name``."""
    from phoenix.identity.bootstrap import mint_bootstrap_actor

    actor = mint_bootstrap_actor(name)
    payload = json.dumps(actor.to_payload()).encode("utf-8")
    return "Phoenix-Actor " + base64.b64encode(payload).decode("ascii")


class SignedActorAuth(httpx.Auth):
    """Sign every outgoing request as ``name`` unless it is already authorized."""

    def __init__(self, name: str = ADMIN_ACTOR) -> None:
        self.name = name

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        if "authorization" not in request.headers:
            request.headers["Authorization"] = actor_header(self.name)
        yield request


def signed_client(app: Any, *, actor: str = ADMIN_ACTOR, **kwargs: Any) -> TestClient:
    """A :class:`TestClient` whose requests are signed as ``actor`` (default ``adam``)."""
    client = TestClient(app, **kwargs)
    client.auth = SignedActorAuth(actor)
    return client


__all__ = ["ADMIN_ACTOR", "SignedActorAuth", "actor_header", "signed_client"]
