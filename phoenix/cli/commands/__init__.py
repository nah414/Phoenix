"""CLI command groups (Phase 9 Steps 7 + 8).

Each module exports a ``HANDLERS`` mapping subcommand name -> handler
function. ``phoenix.cli.entry`` imports these and dispatches based on
the argparse-parsed command tree.

Handler signature::

    def handler(args, config, client, output_format) -> int

Returns the process exit code (``phoenix.cli.entry.EXIT_*``).
"""

from __future__ import annotations

__all__: list[str] = []
