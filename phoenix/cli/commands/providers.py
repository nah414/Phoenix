"""``phoenix providers ...`` command group (Phase 9 Step 7).

Phase 9 v1 has no public provider-listing endpoint -- the router's
provider registry is internal-only by Section 4 design. The CLI
exposes the read surface via the admin endpoint
``GET /v1/admin/providers/health-history`` so operators can see
which providers Phoenix knows about + recent quarantine state.
"""

from __future__ import annotations

import argparse

from phoenix.cli.commands._shared import print_payload
from phoenix.cli.config_loader import CLIConfig
from phoenix.cli.http_client import CLIHTTPClient


def _cmd_list(
    _args: argparse.Namespace,
    _config: CLIConfig,
    client: CLIHTTPClient,
    fmt: str,
) -> int:
    response = client.get("/v1/admin/providers/health-history")
    print_payload(response, fmt)
    return 0


HANDLERS = {
    "list": _cmd_list,
}


__all__ = ["HANDLERS"]
