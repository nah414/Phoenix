"""``phoenix identity ...`` command group (Phase 9 Step 7).

Subcommands:

- ``show`` -- introspect the resolved CLI actor + reachability.
  Phase 9 v1 has no daemon "whoami" endpoint, so the CLI surfaces
  the configured actor + a daemon-ping result.
- ``enroll <actor_name> --permission key=value ...`` -- POST
  /v1/identity/enroll. The ``--permission`` flag is repeated for
  each capability; values are coerced (``true``/``false`` -> bool,
  ``elevated``/``admin``/``default`` -> str for rate_limit_tier).
"""

from __future__ import annotations

import argparse
from typing import Any

from phoenix.cli.commands._shared import print_payload
from phoenix.cli.config_loader import CLIConfig
from phoenix.cli.http_client import CLIHTTPClient, CLIHTTPError


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
    """Print the effective CLI actor + daemon reachability check."""
    actor_name = client.actor_name or "<bootstrap>"
    daemon_reachable = True
    daemon_info: Any = {}
    try:
        daemon_info = client.get("/v1/health")
    except CLIHTTPError as exc:
        daemon_reachable = False
        daemon_info = {"error": str(exc)}
    payload = {
        "actor": actor_name,
        "rest_url": config.rest_url,
        "reproducibility_mode": config.reproducibility_mode,
        "daemon_reachable": daemon_reachable,
        "daemon_info": daemon_info,
    }
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


HANDLERS = {
    "show": _cmd_show,
    "enroll": _cmd_enroll,
}


__all__ = ["HANDLERS"]
