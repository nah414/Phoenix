"""``phoenix admin ...`` command group (Phase 9 Step 8).

Subcommands:

- ``kill-switch engage --reason TEXT`` -- POST
  /v1/admin/kill-switch/engage.
- ``kill-switch release --reason TEXT`` -- POST
  /v1/admin/kill-switch/release.
- ``kill-switch status`` -- GET /v1/admin/kill-switch/status.
- ``health`` -- GET /v1/admin/health/detailed.
- ``governor`` -- GET /v1/admin/governor.
- ``budget`` -- GET /v1/admin/budget.
- ``override <task-id> --disposition {pass|fail|degraded}
  --reason TEXT`` -- POST
  /v1/admin/tasks-pending-review/{task_id}/override.
"""

from __future__ import annotations

import argparse

from phoenix.cli.commands._shared import print_payload
from phoenix.cli.config_loader import CLIConfig
from phoenix.cli.http_client import CLIHTTPClient


def _cmd_kill_switch(
    args: argparse.Namespace,
    _config: CLIConfig,
    client: CLIHTTPClient,
    fmt: str,
) -> int:
    sub = args.admin_kill_switch_subcommand
    if sub == "engage":
        body = {"reason": args.reason}
        response = client.post("/v1/admin/kill-switch/engage", json_body=body)
    elif sub == "release":
        body = {"reason": args.reason}
        response = client.post("/v1/admin/kill-switch/release", json_body=body)
    elif sub == "status":
        response = client.get("/v1/admin/kill-switch/status")
    else:
        print("phoenix admin kill-switch: subcommand required (engage / release / status)")
        return 2
    print_payload(response, fmt)
    return 0


def _cmd_health(
    _args: argparse.Namespace,
    _config: CLIConfig,
    client: CLIHTTPClient,
    fmt: str,
) -> int:
    response = client.get("/v1/admin/health/detailed")
    print_payload(response, fmt)
    return 0


def _cmd_governor(
    _args: argparse.Namespace,
    _config: CLIConfig,
    client: CLIHTTPClient,
    fmt: str,
) -> int:
    response = client.get("/v1/admin/governor")
    print_payload(response, fmt)
    return 0


def _cmd_budget(
    _args: argparse.Namespace,
    _config: CLIConfig,
    client: CLIHTTPClient,
    fmt: str,
) -> int:
    response = client.get("/v1/admin/budget")
    print_payload(response, fmt)
    return 0


def _cmd_override(
    args: argparse.Namespace,
    _config: CLIConfig,
    client: CLIHTTPClient,
    fmt: str,
) -> int:
    body = {
        "disposition": args.disposition,
        "reason": args.reason,
    }
    response = client.post(
        f"/v1/admin/tasks-pending-review/{args.task_id}/override",
        json_body=body,
    )
    print_payload(response, fmt)
    return 0


HANDLERS = {
    "kill-switch": _cmd_kill_switch,
    "health": _cmd_health,
    "governor": _cmd_governor,
    "budget": _cmd_budget,
    "override": _cmd_override,
}


__all__ = ["HANDLERS"]
