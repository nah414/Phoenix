"""``phoenix lora ...`` command group (Phase 9 Step 7).

Thin CLI wrapper over Phase 9 Step 3's REST surface:

- ``load <spec>`` -- POST /v1/adapters.
- ``list`` -- GET /v1/adapters.
- ``unload <adapter-id>`` -- DELETE /v1/adapters/{id}.

The ``spec`` argument follows the loader contract --
``"module.path:callable"`` -- since Phase 9 v1 doesn't ship
file-path loading (501 response from the daemon).
"""

from __future__ import annotations

import argparse

from phoenix.cli.commands._shared import print_payload
from phoenix.cli.config_loader import CLIConfig
from phoenix.cli.http_client import CLIHTTPClient


def _cmd_load(
    args: argparse.Namespace,
    _config: CLIConfig,
    client: CLIHTTPClient,
    fmt: str,
) -> int:
    response = client.post("/v1/adapters", json_body={"spec": args.spec})
    print_payload(response, fmt)
    return 0


def _cmd_list(
    _args: argparse.Namespace,
    _config: CLIConfig,
    client: CLIHTTPClient,
    fmt: str,
) -> int:
    response = client.get("/v1/adapters")
    print_payload(response, fmt)
    return 0


def _cmd_unload(
    args: argparse.Namespace,
    _config: CLIConfig,
    client: CLIHTTPClient,
    fmt: str,
) -> int:
    response = client.delete(f"/v1/adapters/{args.adapter_id}")
    print_payload(response, fmt)
    return 0


HANDLERS = {
    "load": _cmd_load,
    "list": _cmd_list,
    "unload": _cmd_unload,
}


__all__ = ["HANDLERS"]
