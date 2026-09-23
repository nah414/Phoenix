"""CLI HTTP client (Phase 9 Step 6, locked OPEN-5).

Thin wrapper around :mod:`httpx` that handles:

- Base URL resolution from :class:`CLIConfig`.
- Actor signing for the ``Authorization`` header (architecture v1
  Sections 7.2 + 9.4). A request is HMAC-signed with this machine's
  existing install master key **only as an actor the operator named**:
  ``--actor`` on the command line, or ``default_actor`` in
  ``~/.phoenix/config.yaml``. With neither, the header is omitted
  (there is no implicit ``adam``), and authenticated routes answer 401
  with a CLI hint on how to configure an actor. Signing only *reads*
  the key; it never creates one.
- No replayable header for remote hosts by default. A signed header is
  a bearer credential for its ~5-minute validity window, so an actor
  taken from ``default_actor`` alone is signed only for a loopback *IP
  literal* ``rest_url`` (``127.0.0.0/8``, ``[::1]``, IPv4-mapped
  loopback). The name ``localhost`` does not qualify: it can resolve to
  ``::1`` before ``127.0.0.1``, and the daemon binds ``127.0.0.1`` only,
  so any local process can listen on ``[::1]`` at the same port and
  collect the header (the refusal names the ``127.0.0.1`` URL to use).
  For any other host the request goes out unsigned and the CLI prints a
  refusal; ``--actor <name>`` on that invocation is the explicit opt-in.
- Unauthenticated routes are never signed. ``GET /v1/health`` (the only
  one the CLI and MCP server call) goes out without ``Authorization``
  whatever actor is configured, so probing a ``rest_url`` that turns out
  not to be Phoenix leaks nothing.
- No proxy for local hosts. A request to a loopback IP or ``localhost``
  is sent with ``trust_env=False``, so ``HTTP(S)_PROXY`` / ``ALL_PROXY``
  and the Windows registry proxy settings never see a signed header
  meant for the local daemon (httpx would otherwise route ``127.0.0.1``
  through them unless ``NO_PROXY`` names it). Any other ``rest_url``
  keeps the environment's proxy settings.
- A ``default_actor`` this machine cannot sign (no readable install key,
  e.g. a host CLI pointed at a daemon in Docker) does not block
  unauthenticated routes: the request goes out unsigned and a 401 says
  why. ``--actor`` stays a hard failure, because that invocation asked
  for the identity explicitly.
- Helpful error messages -- a connection refused or 5xx becomes a
  :class:`CLIHTTPError` with the URL + status code, not an
  opaque :class:`httpx.HTTPError`.

The wrapper is deliberately small. Step 7-8 command modules call
``client.get``/``client.post``/``client.delete`` directly; they
don't need to know about httpx internals or actor signing
mechanics.
"""

from __future__ import annotations

import ipaddress
import json
import sys
from dataclasses import dataclass, field
from typing import Any

import httpx

from phoenix.cli.config_loader import CLIConfig
from phoenix.identity.bootstrap import IdentityError, sign_local_actor_header

#: How to configure the actor the CLI signs as. Shown on a 401 for an
#: unsigned request and by ``phoenix identity show`` / ``header``.
ACTOR_SETUP_HINT = (
    "Configure the actor the phoenix CLI signs as: add 'default_actor: adam' "
    "(or another enrolled actor) to ~/.phoenix/config.yaml, next to "
    "'rest_url: http://127.0.0.1:8003' (the daemon's default address), or pass "
    "--actor <name> on the command line."
)

# Values of :attr:`CLIHTTPClient.signing_state`.
SIGNING_SIGNED = "signed"
SIGNING_NO_ACTOR = "unsigned: no actor configured"
SIGNING_REFUSED_REMOTE = (
    "unsigned: default_actor is signed only for a loopback IP rest_url (127.0.0.1 or [::1])"
)
#: Reported by ``phoenix identity show`` when the policy would sign but this
#: machine has no usable install key.
SIGNING_UNAVAILABLE = "unsigned: this machine cannot sign (install key unavailable)"

#: Why a signature made on this machine may not verify, and what to do for Docker.
FOREIGN_KEY_HINT = (
    "The CLI signs with this machine's install key (~/.phoenix/runtime/master_key.bin). "
    "A daemon running with a different key -- in a Docker container, as another OS user, "
    "or on another host -- cannot verify it. For a daemon in Docker, run the command "
    "inside the container instead: docker exec <container> phoenix --actor <name> ..."
)


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


#: Daemon routes that take no ``Authorization`` header. Requests to them are
#: never signed, so a probe of the wrong port (or of a squatter) carries no
#: credential. Kept in step with the app by
#: ``test_auth_headerless_rejected.py``.
UNAUTHENTICATED_PATHS = frozenset({"/v1/health"})


def _url_host(url: str) -> str:
    """``url``'s host as :class:`httpx.URL` parses it, or ``""``."""
    try:
        return httpx.URL(url).host or ""
    except (httpx.InvalidURL, TypeError, ValueError):
        return ""


def _is_localhost_name(host: str) -> bool:
    return host.rstrip(".").lower() == "localhost"


def is_loopback_url(url: str) -> bool:
    """True only when ``url``'s host is a loopback IP *literal*.

    ``127.0.0.0/8``, ``::1`` and IPv4-mapped loopback qualify. The name
    ``localhost`` does not: name resolution can return ``::1`` before
    ``127.0.0.1`` (Windows does), and the daemon binds ``127.0.0.1`` only,
    so another local process can listen on ``[::1]`` at the same port
    without any conflict and receive whatever the client sends there.

    Uses :class:`httpx.URL`, the parser that decides where the request
    actually goes. Anything unparsable, hostless, or spelled unusually
    (``127.1``, ``2130706433``) counts as *not* loopback, which only ever
    errs toward not signing.
    """
    host = _url_host(url)
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped.is_loopback
    return ip.is_loopback


def _bypasses_proxy(url: str) -> bool:
    """Local hosts connect directly (``trust_env=False``): loopback IPs and ``localhost``."""
    return is_loopback_url(url) or _is_localhost_name(_url_host(url))


def _loopback_ip_url(url: str) -> str:
    """``url`` with its host replaced by ``127.0.0.1`` (for the ``localhost`` refusal)."""
    try:
        return str(httpx.URL(url).copy_with(host="127.0.0.1")).rstrip("/")
    except (httpx.InvalidURL, TypeError, ValueError):
        return "http://127.0.0.1:8003"


@dataclass
class CLIHTTPClient:
    """Lightweight Phoenix REST client used by CLI command groups.

    Instantiate via :func:`build_client` (the canonical factory)
    rather than directly so config + actor wiring is consistent.

    Fields:
      - ``actor_name`` -- the actor to sign as, or ``None`` for unsigned
        requests. Never defaulted to ``adam``.
      - ``actor_from_flag`` -- ``True`` only when ``actor_name`` came from
        ``--actor`` on this invocation. That explicit choice is what
        permits signing for a non-loopback ``base_url``.
    """

    base_url: str
    actor_name: str | None
    timeout_seconds: float = 30.0
    actor_from_flag: bool = False
    _refusal_reported: bool = field(default=False, init=False, repr=False, compare=False)
    #: Why the last attempt to sign a ``default_actor`` failed, if it did.
    _signing_error: str | None = field(default=None, init=False, repr=False, compare=False)

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

    # ----- signing policy ----------------------------------------------

    @property
    def signing_refused(self) -> bool:
        """An actor is configured but must not be signed for this ``base_url``.

        True for a ``default_actor`` (not ``--actor``) whose ``base_url``
        host is not a loopback IP literal (a remote host, or the name
        ``localhost``): a config file alone never sends a replayable header
        anywhere but a loopback address.
        """
        if not self.actor_name or self.actor_from_flag:
            return False
        return not is_loopback_url(self.base_url)

    @property
    def signing_actor(self) -> str | None:
        """The actor requests are signed as, or ``None`` when they go unsigned."""
        if not self.actor_name or self.signing_refused:
            return None
        return self.actor_name

    @property
    def signing_state(self) -> str:
        """:data:`SIGNING_SIGNED`, :data:`SIGNING_NO_ACTOR` or :data:`SIGNING_REFUSED_REMOTE`.

        The policy only; :meth:`signing_problem` checks that this machine can sign.
        """
        if not self.actor_name:
            return SIGNING_NO_ACTOR
        if self.signing_refused:
            return SIGNING_REFUSED_REMOTE
        return SIGNING_SIGNED

    def signing_refusal_message(self) -> str:
        """The refusal printed when a ``default_actor`` is not signed."""
        if _is_localhost_name(_url_host(self.base_url)):
            why = (
                f"rest_url {self.base_url} names the host 'localhost', which can resolve to ::1 "
                f"before 127.0.0.1; the daemon listens on 127.0.0.1, so another local process "
                f"can listen on [::1] at the same port and collect a signed Phoenix-Actor header "
                f"that can be replayed for ~5 minutes. Set rest_url to "
                f"{_loopback_ip_url(self.base_url)} in ~/.phoenix/config.yaml."
            )
        else:
            why = (
                f"rest_url {self.base_url} is not a loopback IP address, and a signed "
                f"Phoenix-Actor header can be replayed for ~5 minutes by whoever receives it."
            )
        return (
            f"not signing as default_actor {self.actor_name!r}: {why} This request is sent "
            f"unsigned, so authenticated routes answer 401. To sign for this daemon "
            f"deliberately, pass --actor {self.actor_name} on this invocation."
        )

    def signing_problem(self) -> str | None:
        """Why the actor the policy would sign cannot be signed here, or ``None``.

        Tries to sign once (reading, never creating, the install key).
        ``None`` also when nothing would be signed (no actor, or refused).
        """
        actor = self.signing_actor
        if actor is None:
            return None
        try:
            _sign_actor(actor)
        except IdentityError as exc:
            return str(exc)
        return None

    def _unauthenticated_hint(self, *, signed: bool) -> str | None:
        """Why a 401 happened: the CLI left the header off, or it did not verify."""
        if self.signing_refused:
            return f"phoenix: {self.signing_refusal_message()}"
        if not self.actor_name:
            return f"phoenix: request sent unsigned (no actor configured). {ACTOR_SETUP_HINT}"
        if not signed and self._signing_error:
            return (
                f"phoenix: request sent unsigned: could not sign as default_actor "
                f"{self.actor_name!r}. {self._signing_error} {FOREIGN_KEY_HINT}"
            )
        if signed:
            return (
                f"phoenix: the daemon did not accept the header signed as "
                f"{self.actor_name!r}. {FOREIGN_KEY_HINT}"
            )
        return None

    # ----- internals ---------------------------------------------------

    def _build_headers(self, path: str | None = None) -> dict[str, str]:
        """Headers for one request to ``path``, with a freshly signed actor when allowed.

        Signed per request, so a long-running ``phoenix mcp serve`` never
        sends a header older than the 300-second validity window.

        - ``path`` in :data:`UNAUTHENTICATED_PATHS` (``/v1/health``): no
          ``Authorization`` header and no refusal message, whatever the
          actor. ``None`` means an authenticated route.
        - No actor configured: no ``Authorization`` header.
        - ``default_actor`` with a ``base_url`` that is not a loopback IP
          literal: no header, and the refusal is printed to stderr once per
          client.
        - Otherwise the actor is signed. When this machine cannot sign
          (no readable master key), an ``--actor`` raises
          :class:`CLIHTTPError`, because the operator asked for that
          identity on this invocation; a ``default_actor`` is sent
          unsigned instead, so ``/v1/health`` still works, and the error
          is kept for the 401 message.
        """
        headers: dict[str, str] = {"Accept": "application/json"}
        if path is not None and _route_path(path) in UNAUTHENTICATED_PATHS:
            return headers
        if self.signing_refused:
            if not self._refusal_reported:
                print(f"phoenix: {self.signing_refusal_message()}", file=sys.stderr)
                self._refusal_reported = True
            return headers
        actor = self.signing_actor
        if actor is None:
            return headers
        try:
            headers["Authorization"] = _sign_actor(actor)
        except IdentityError as exc:
            if self.actor_from_flag:
                raise CLIHTTPError(f"{exc} {FOREIGN_KEY_HINT}", url=self.base_url) from exc
            self._signing_error = str(exc)
            return headers
        self._signing_error = None
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
        headers = self._build_headers(path)
        # Local traffic always connects directly: with the default
        # trust_env=True, env / registry proxy settings would receive the
        # signed header. Proxies stay in effect for any other host.
        direct = _bypasses_proxy(url)
        try:
            with httpx.Client(timeout=self.timeout_seconds, trust_env=not direct) as client:
                resp = client.request(
                    method,
                    url,
                    headers=headers,
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
            message = f"HTTP {method} {url} returned {resp.status_code}: {body}"
            hint = (
                self._unauthenticated_hint(signed="Authorization" in headers)
                if resp.status_code == 401
                else None
            )
            if hint:
                message = f"{message}\n{hint}"
            raise CLIHTTPError(
                message,
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

    Actor resolution: ``actor_override`` (the ``--actor`` flag, which
    also marks the choice as explicit) > ``config.default_actor`` >
    ``None``. With ``None`` requests go unsigned: nobody is signed as
    ``adam`` implicitly. An empty name counts as not configured.
    """
    actor: str | None
    if actor_override:
        actor, from_flag = actor_override, True
    else:
        actor, from_flag = (config.default_actor or None), False
    return CLIHTTPClient(
        base_url=config.rest_url,
        actor_name=actor,
        timeout_seconds=timeout_seconds,
        actor_from_flag=from_flag,
    )


def _route_path(path: str) -> str:
    """``path`` without query string, fragment or trailing slash (``/`` stays ``/``)."""
    bare = path.split("?", 1)[0].split("#", 1)[0]
    return bare.rstrip("/") or "/"


def _sign_actor(actor_name: str) -> str:
    """Build a signed ``Phoenix-Actor`` header for ``actor_name``.

    HMAC-signs with this machine's existing install master key via
    :func:`phoenix.identity.bootstrap.sign_local_actor_header`; the
    daemon verifies it with :func:`~phoenix.identity.bootstrap.require_actor`.
    (Before 2026-09-16 this sent an unsigned ``{"name": ...}`` payload,
    which the daemon always rejected, so named actors only ever got a
    401.) Raises :class:`IdentityError` when no key can be read.
    """
    return sign_local_actor_header(actor_name)


__all__ = [
    "ACTOR_SETUP_HINT",
    "FOREIGN_KEY_HINT",
    "SIGNING_NO_ACTOR",
    "SIGNING_REFUSED_REMOTE",
    "SIGNING_SIGNED",
    "SIGNING_UNAVAILABLE",
    "UNAUTHENTICATED_PATHS",
    "CLIHTTPClient",
    "CLIHTTPError",
    "build_client",
    "is_loopback_url",
]
