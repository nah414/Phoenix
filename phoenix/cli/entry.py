"""CLI entry point (Phase 9 Step 6).

The ``phoenix`` console script declared in ``pyproject.toml`` (
``[project.scripts] phoenix = phoenix.cli.entry:main``) calls
:func:`main` with ``sys.argv[1:]``. Step 6 ships the dispatcher
+ config loading + global flags; Steps 7-8 fill in the
per-command behaviour.

Top-level command groups:

- ``phoenix task <subcommand>``       -- Step 7
- ``phoenix lora <subcommand>``       -- Step 7
- ``phoenix identity <subcommand>``   -- Step 7
- ``phoenix providers <subcommand>``  -- Step 7
- ``phoenix audit <subcommand>``      -- Step 8
- ``phoenix calibration <subcommand>``-- Step 8
- ``phoenix admin <subcommand>``      -- Step 8

Global flags:

- ``--config <path>``    -- override ~/.phoenix/config.yaml.
- ``--rest-url <url>``   -- override config + env REST URL.
- ``--actor <name>``     -- run as the given actor (default:
  config or bootstrap).
- ``--format <fmt>``     -- output format (``auto`` | ``json`` |
  ``text`` | ``table``); default: config or ``auto``.
- ``--version``          -- print version + exit 0.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from phoenix._internal.version import __version__
from phoenix.cli.config_loader import CLIConfig, ConfigError, load_config
from phoenix.cli.http_client import CLIHTTPClient, CLIHTTPError, build_client
from phoenix.cli.output_formats import render

logger = logging.getLogger(__name__)


# Exit codes
EXIT_OK = 0
EXIT_USAGE_ERROR = 2
EXIT_HTTP_ERROR = 3
EXIT_CONFIG_ERROR = 4
EXIT_NOT_IMPLEMENTED = 64


def main(argv: Sequence[str] | None = None) -> int:
    """Top-level :mod:`argparse` dispatcher.

    Returns the process exit code; the console-script wrapper
    calls :func:`sys.exit` with the result.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help(sys.stderr)
        return EXIT_USAGE_ERROR

    try:
        config = load_config(
            config_path=Path(args.config) if args.config else None,
        )
    except ConfigError as exc:
        print(f"phoenix: config error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    # CLI flags override config-file values
    if args.rest_url:
        config = _with_overrides(config, rest_url=args.rest_url)
    output_format = args.format if args.format != "inherit" else config.output_format

    try:
        client = build_client(config, actor_override=args.actor)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"phoenix: client setup error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    handler = _COMMAND_HANDLERS.get(args.command)
    if handler is None:
        print(
            f"phoenix: '{args.command}' is not implemented yet (lands in Phase 9 Step 7/8).",
            file=sys.stderr,
        )
        return EXIT_NOT_IMPLEMENTED

    try:
        return handler(args, config, client, output_format)
    except CLIHTTPError as exc:
        print(f"phoenix: HTTP error: {exc}", file=sys.stderr)
        return EXIT_HTTP_ERROR


def _build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argparse tree.

    Step 6 ships the scaffold with stub subparsers for every
    command group; Steps 7-8 land the real subcommand handlers.
    """
    parser = argparse.ArgumentParser(
        prog="phoenix",
        description=(
            "Phoenix v1 CLI: task lifecycle + LoRA adapters + audit "
            "+ admin dev-ops over the REST surface."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"phoenix {__version__}",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to config.yaml (default: ~/.phoenix/config.yaml).",
    )
    parser.add_argument(
        "--rest-url",
        default=None,
        help="Override the REST base URL.",
    )
    parser.add_argument(
        "--actor",
        default=None,
        help="Actor name (default: config or bootstrap).",
    )
    parser.add_argument(
        "--format",
        choices=["auto", "json", "text", "table", "inherit"],
        default="inherit",
        help="Output format (default: inherit from config).",
    )

    subparsers = parser.add_subparsers(dest="command")

    # Step 6 smoke command -- the dispatcher's first working surface.
    subparsers.add_parser("health", help="Daemon health check.")

    # Step 7 groups
    _add_task_group(subparsers)
    _add_lora_group(subparsers)
    _add_identity_group(subparsers)
    _add_providers_group(subparsers)

    # Step 8 groups
    _add_audit_group(subparsers)
    _add_calibration_group(subparsers)
    _add_admin_group(subparsers)

    return parser


# --------------------------------------------------------------------
# Command group scaffolds. Each adds an `<group>_command` attribute on
# the parsed args so Step 7/8 handlers can route. Step 6 ships only
# the `health` handler (Step 7+ wires the rest).


def _add_task_group(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    sp = subparsers.add_parser("task", help="Task lifecycle commands.")
    inner = sp.add_subparsers(dest="task_command")
    inner.add_parser("submit", help="Submit a task (Step 7).")
    inner.add_parser("get", help="Get a task result (Step 7).")
    inner.add_parser("replay", help="Replay a task (Step 7).")
    inner.add_parser("stream", help="Stream verification events (Step 7).")


def _add_lora_group(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    sp = subparsers.add_parser("lora", help="LoRA adapter commands.")
    inner = sp.add_subparsers(dest="lora_command")
    inner.add_parser("load", help="Load an adapter (Step 7).")
    inner.add_parser("list", help="List loaded adapters (Step 7).")
    inner.add_parser("unload", help="Unload an adapter (Step 7).")


def _add_identity_group(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    sp = subparsers.add_parser("identity", help="Identity commands.")
    inner = sp.add_subparsers(dest="identity_command")
    inner.add_parser("show", help="Show current actor (Step 7).")
    inner.add_parser("enroll", help="Enroll a new actor (Step 7).")


def _add_providers_group(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    sp = subparsers.add_parser("providers", help="Provider registry commands.")
    inner = sp.add_subparsers(dest="providers_command")
    inner.add_parser("list", help="List providers (Step 7).")


def _add_audit_group(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    sp = subparsers.add_parser("audit", help="Audit log commands.")
    inner = sp.add_subparsers(dest="audit_command")
    inner.add_parser("tail", help="Tail audit events (Step 8).")
    inner.add_parser("verify", help="Verify ledger chain (Step 8).")


def _add_calibration_group(
    subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    sp = subparsers.add_parser("calibration", help="Calibration commands.")
    inner = sp.add_subparsers(dest="calibration_command")
    inner.add_parser("status", help="Calibration status (Step 8).")
    inner.add_parser("run", help="Run calibration cycle (Step 8).")


def _add_admin_group(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    sp = subparsers.add_parser("admin", help="Admin commands.")
    inner = sp.add_subparsers(dest="admin_command")

    ks = inner.add_parser("kill-switch", help="Kill switch (Step 8).")
    ks_inner = ks.add_subparsers(dest="admin_kill_switch_subcommand")
    ks_inner.add_parser("engage")
    ks_inner.add_parser("release")
    ks_inner.add_parser("status")

    inner.add_parser("health", help="Detailed health (Step 8).")
    inner.add_parser("governor", help="Governor snapshot (Step 8).")
    inner.add_parser("budget", help="Budget snapshot (Step 8).")
    inner.add_parser("override", help="Override task disposition (Step 8).")


# --------------------------------------------------------------------
# Handler registry. Phase 9 Step 6 ships a single working command
# ``health`` as a smoke test for the dispatcher + http_client +
# config flow. Steps 7-8 wire the full command surface.


def _cmd_health(
    args: argparse.Namespace,
    config: CLIConfig,
    client: CLIHTTPClient,
    output_format: str,
) -> int:
    """Hit ``GET /v1/health`` and print the response.

    Smoke command for Step 6: exercises config -> client ->
    REST -> output formatting end-to-end.
    """
    payload = client.get("/v1/health")
    print(render(payload, format_name=output_format))
    return EXIT_OK


_COMMAND_HANDLERS = {
    "health": _cmd_health,
}


def _with_overrides(config: CLIConfig, *, rest_url: str | None = None) -> CLIConfig:
    """Return a new :class:`CLIConfig` with the given field overridden."""
    return CLIConfig(
        rest_url=rest_url.rstrip("/") if rest_url else config.rest_url,
        reproducibility_mode=config.reproducibility_mode,
        default_actor=config.default_actor,
        output_format=config.output_format,
        raw=dict(config.raw),
    )


__all__ = ["main"]
