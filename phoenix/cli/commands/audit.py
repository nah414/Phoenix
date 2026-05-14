"""``phoenix audit ...`` command group (Phase 9 Step 8).

Subcommands:

- ``tail [--since SECS] [--limit N]`` -- GET /v1/audit/events.
  Returns the most recent N audit events newer than the cursor.
- ``verify`` -- GET /v1/audit/ledger/verify.
  Walks the Omega Ledger chain and reports pass/fail.
"""

from __future__ import annotations

import argparse

from phoenix.cli.commands._shared import print_payload
from phoenix.cli.config_loader import CLIConfig
from phoenix.cli.http_client import CLIHTTPClient


def _cmd_tail(
    args: argparse.Namespace,
    _config: CLIConfig,
    client: CLIHTTPClient,
    fmt: str,
) -> int:
    params: dict[str, str | int] = {"limit": args.limit}
    if args.since is not None:
        params["since_unix"] = args.since
    response = client.get("/v1/audit/events", params=params)
    print_payload(response, fmt)
    return 0


def _cmd_verify(
    _args: argparse.Namespace,
    _config: CLIConfig,
    client: CLIHTTPClient,
    fmt: str,
) -> int:
    response = client.get("/v1/audit/ledger/verify")
    print_payload(response, fmt)
    return 0


HANDLERS = {
    "tail": _cmd_tail,
    "verify": _cmd_verify,
}


__all__ = ["HANDLERS"]
