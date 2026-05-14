"""``phoenix mcp ...`` command group (Phase 9 Step 9).

Single subcommand:

- ``phoenix mcp serve`` -- boots the MCP server on stdin/stdout
  transport. Used by IDE clients that spawn Phoenix as a
  subprocess (Claude Code, Cursor, Cline). Blocks until the
  IDE disconnects.
"""

from __future__ import annotations

import argparse

from phoenix.cli.config_loader import CLIConfig
from phoenix.cli.http_client import CLIHTTPClient


def _cmd_serve(
    args: argparse.Namespace,
    _config: CLIConfig,
    _client: CLIHTTPClient,
    _fmt: str,
) -> int:
    """Boot the MCP server on stdio.

    Lazy-imports :mod:`phoenix.mcp.server` so the CLI starts
    quickly when MCP isn't being used and so missing-extra
    failures surface with a clear message at use time.
    """
    try:
        from phoenix.mcp.server import run_stdio
    except ImportError as exc:
        print(f"phoenix mcp serve: {exc}")
        return 4
    run_stdio(
        config_path=args.config,
        actor_override=args.actor,
    )
    return 0


HANDLERS = {
    "serve": _cmd_serve,
}


__all__ = ["HANDLERS"]
