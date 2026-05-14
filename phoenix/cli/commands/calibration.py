"""``phoenix calibration ...`` command group (Phase 9 Step 8).

Subcommands:

- ``status`` -- GET /v1/admin/calibration/detail. Admin-only; the
  CLI surfaces the privilege-denied case cleanly via the
  underlying :class:`CLIHTTPError`.
- ``run`` -- POST /v1/admin/calibration/run. Force a drift cycle
  to run synchronously.
"""

from __future__ import annotations

import argparse

from phoenix.cli.commands._shared import print_payload
from phoenix.cli.config_loader import CLIConfig
from phoenix.cli.http_client import CLIHTTPClient


def _cmd_status(
    _args: argparse.Namespace,
    _config: CLIConfig,
    client: CLIHTTPClient,
    fmt: str,
) -> int:
    response = client.get("/v1/admin/calibration/detail")
    print_payload(response, fmt)
    return 0


def _cmd_run(
    _args: argparse.Namespace,
    _config: CLIConfig,
    client: CLIHTTPClient,
    fmt: str,
) -> int:
    response = client.post("/v1/admin/calibration/run", json_body={})
    print_payload(response, fmt)
    return 0


HANDLERS = {
    "status": _cmd_status,
    "run": _cmd_run,
}


__all__ = ["HANDLERS"]
