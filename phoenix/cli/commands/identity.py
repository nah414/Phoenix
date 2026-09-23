"""``phoenix identity ...`` command group (Phase 9 Step 7).

Subcommands:

- ``show`` -- introspect the resolved CLI actor + reachability.
  Phase 9 v1 has no daemon "whoami" endpoint, so the CLI surfaces
  the configured actor (``null`` when none is configured), where it
  came from, whether requests to ``rest_url`` are signed (including
  whether this machine has an install key to sign with), and a
  daemon-ping result.
- ``enroll <actor_name> --permission key=value ...`` -- POST
  /v1/identity/enroll. The ``--permission`` flag is repeated for
  each capability; values are coerced (``true``/``false`` -> bool,
  ``elevated``/``admin``/``default`` -> str for rate_limit_tier).
- ``header`` -- print a freshly signed ``Phoenix-Actor`` Authorization
  header value (valid ~5 minutes) for the configured actor, for curl
  or the ``/docs`` UI. The daemon refuses requests without one. With
  no actor configured it prints how to configure one and exits 4;
  it never signs as ``adam`` implicitly.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from phoenix.cli.commands._shared import print_payload
from phoenix.cli.config_loader import CLIConfig
from phoenix.cli.http_client import (
    ACTOR_SETUP_HINT,
    FOREIGN_KEY_HINT,
    SIGNING_UNAVAILABLE,
    CLIHTTPClient,
    CLIHTTPError,
)
from phoenix.identity.bootstrap import IdentityError, sign_local_actor_header


_BOOL_TRUE = {"true", "1", "yes", "on"}
_BOOL_FALSE = {"false", "0", "no", "off"}


def _coerce(value: str) -> Any:
    """Turn a CLI-string into the right Python type.

    ``true``/``false`` (case-insensitive) -> bool; everything else
    stays as a string. Rate-limit tier values pass through
    unchanged.
    """
    low = value.lower()
    if low in _BOOL_TRUE:
        return True
    if low in _BOOL_FALSE:
        return False
    return value


def _parse_permissions(items: list[str]) -> dict[str, Any]:
    """``key=value`` pairs -> dict, with type coercion."""
    perms: dict[str, Any] = {}
    for raw in items:
        if "=" not in raw:
            raise ValueError(f"--permission must be 'key=value' (got {raw!r})")
        key, _, value = raw.partition("=")
        perms[key.strip()] = _coerce(value.strip())
    return perms


def _cmd_show(
    _args: argparse.Namespace,
    config: CLIConfig,
    client: CLIHTTPClient,
    fmt: str,
) -> int:
    """Print the configured CLI actor, its signing state + daemon reachability.

    ``actor`` is the configured actor or ``null`` (never an implied
    ``adam``). ``actor_source`` is ``--actor``, ``default_actor`` or
    ``null``. ``signing`` says whether requests to ``rest_url`` carry a
    signed header; it is :data:`SIGNING_UNAVAILABLE` when the policy would
    sign but this machine has no usable install key (e.g. a host CLI for a
    daemon in Docker). ``hint`` explains how to change it when they don't.
    """
    signing = client.signing_state
    signing_problem = client.signing_problem()
    if signing_problem is not None:
        signing = SIGNING_UNAVAILABLE
    daemon_reachable = True
    daemon_info: Any = {}
    try:
        # /v1/health is never signed (UNAUTHENTICATED_PATHS), so an --actor this
        # machine cannot sign does not misreport the daemon as down, and a
        # rest_url that is not Phoenix receives no credential.
        daemon_info = client.get("/v1/health")
    except CLIHTTPError as exc:
        daemon_reachable = False
        daemon_info = {"error": str(exc)}
    if not client.actor_name:
        source = None
    elif client.actor_from_flag:
        source = "--actor"
    else:
        source = "default_actor"
    payload: dict[str, Any] = {
        "actor": client.actor_name,
        "actor_source": source,
        "signing": signing,
        "rest_url": config.rest_url,
        "reproducibility_mode": config.reproducibility_mode,
        "daemon_reachable": daemon_reachable,
        "daemon_info": daemon_info,
    }
    if not client.actor_name:
        payload["hint"] = ACTOR_SETUP_HINT
    elif client.signing_refused:
        payload["hint"] = client.signing_refusal_message()
    elif signing_problem is not None:
        payload["hint"] = f"{signing_problem} {FOREIGN_KEY_HINT}"
    print_payload(payload, fmt)
    return 0


def _cmd_enroll(
    args: argparse.Namespace,
    _config: CLIConfig,
    client: CLIHTTPClient,
    fmt: str,
) -> int:
    try:
        permissions = _parse_permissions(args.permission or [])
    except ValueError as exc:
        print(f"phoenix identity enroll: {exc}")
        return 2
    body = {"actor_name": args.actor_name, "permissions": permissions}
    response = client.post("/v1/identity/enroll", json_body=body)
    print_payload(response, fmt)
    return 0


def _cmd_header(
    _args: argparse.Namespace,
    _config: CLIConfig,
    client: CLIHTTPClient,
    _fmt: str,
) -> int:
    """Print a signed ``Phoenix-Actor ...`` header value on stdout.

    Output is the bare value (no JSON wrapping) so shells can use it
    directly, e.g. ``curl -H "Authorization: $(phoenix identity header)"``.
    Signs the configured actor (``--actor``, else ``default_actor``) with
    this machine's existing master key; never creates one. Nothing is sent
    to ``rest_url``, so the loopback-only rule for ``default_actor`` does
    not apply: where the printed header goes is the operator's choice.

    Exit 4 (config error) when no actor is configured (there is no
    implicit ``adam``) or there is no readable key.
    """
    if not client.actor_name:
        print(
            f"phoenix identity header: no actor configured. {ACTOR_SETUP_HINT}",
            file=sys.stderr,
        )
        return 4
    try:
        header = sign_local_actor_header(client.actor_name)
    except IdentityError as exc:
        print(f"phoenix identity header: {exc}", file=sys.stderr)
        return 4
    print(header)
    return 0


HANDLERS = {
    "show": _cmd_show,
    "enroll": _cmd_enroll,
    "header": _cmd_header,
}


__all__ = ["HANDLERS"]
