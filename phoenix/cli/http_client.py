"""CLI HTTP client (Phase 9 Step 6, locked OPEN-5).

Thin wrapper around :mod:`httpx` that handles:

- Base URL resolution from :class:`CLIConfig`.
- Actor signing for the ``Authorization`` header (per Section 7.2
  bootstrap-actor pattern). When no ``actor_name`` is configured
  the dev-mode bootstrap path is exercised (Phoenix daemon falls
  back to ``adam``).
- Helpful error messages -- a connection refused or 5xx becomes a
  :class:`CLIHTTPError` with the URL + status code, not an
  opaque :class:`httpx.HTTPError`.

The wrapper is deliberately small. Step 7-8 command modules call
``client.get``/``client.post``/``client.delete`` directly; they
don't need to know about httpx internals or actor signing
mechanics.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

import httpx

from phoenix.cli.config_loader import CLIConfig


class CLIHTTPError(Exception):
    """Raised when an HTTP call fails.

    Carries the response status + detail when the server returned
    a structured error body; ``status_code`` is ``None`` for
    transport-level failures (connection refused, timeout, etc.).
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        url: str = "",
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url
        self.body = body


@dataclass
class CLIHTTPClient:
    """Lightweight Phoenix REST client used by CLI command groups.

    Instantiate via :func:`build_client` (the canonical factory)
    rather than directly so config + actor wiring is consistent.
    """

    base_url: str
    actor_name: str | None
    timeout_seconds: float = 30.0

    # ----- core verbs --------------------------------------------------

    def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return self._request("GET", path, params=params)

    def post(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        return self._request("POST", path, json_body=json_body)

    def delete(self, path: str) -> Any:
        return self._request("DELETE", path)

    # ----- internals ---------------------------------------------------

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.actor_name:
            headers["Authorization"] = _sign_actor(self.actor_name)
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = self.base_url.rstrip("/") + path
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                resp = client.request(
                    method,
                    url,
                    headers=self._build_headers(),
                    params=params,
                    json=json_body,
                )
        except httpx.RequestError as exc:
            raise CLIHTTPError(
                f"HTTP {method} {url} failed: {exc}",
                url=url,
            ) from exc

        if resp.status_code >= 400:
            try:
                body = resp.json()
            except json.JSONDecodeError:
                body = resp.text
            raise CLIHTTPError(
                f"HTTP {method} {url} returned {resp.status_code}: {body}",
                status_code=resp.status_code,
                url=url,
                body=body,
            )

        if not resp.content:
            return None
        try:
            return resp.json()
        except json.JSONDecodeError:
            return resp.text


def build_client(
    config: CLIConfig,
    *,
    actor_override: str | None = None,
    timeout_seconds: float = 30.0,
) -> CLIHTTPClient:
    """Build a :class:`CLIHTTPClient` from a resolved config.

    The actor resolution order: explicit ``actor_override`` >
    ``config.default_actor`` > ``None`` (dev-mode bootstrap).
    """
    actor = actor_override if actor_override is not None else config.default_actor
    return CLIHTTPClient(
        base_url=config.rest_url,
        actor_name=actor,
        timeout_seconds=timeout_seconds,
    )


def _sign_actor(actor_name: str) -> str:
    """Build a Phoenix-Actor header for the given name.

    Phase 9 v1 uses the bootstrap-style payload: a minimal JSON
    object encoded as base64. The Phoenix daemon's
    :func:`extract_or_bootstrap` honours this shape and resolves
    the actor's permissions via the registry. v1.x can layer
    cryptographic signing on top of the same header without
    changing the CLI side.
    """
    payload = {"name": actor_name}
    raw = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    return f"Phoenix-Actor {raw}"


__all__ = [
    "CLIHTTPClient",
    "CLIHTTPError",
    "build_client",
]
