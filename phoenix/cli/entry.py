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
from collections.abc import Callable, Sequence
from pathlib import Path

from phoenix._internal.version import __version__
from phoenix.cli.commands import admin as admin_cmd
from phoenix.cli.commands import audit as audit_cmd
from phoenix.cli.commands import calibration as calibration_cmd
from phoenix.cli.commands import identity as identity_cmd
from phoenix.cli.commands import lora as lora_cmd
from phoenix.cli.commands import mcp_server as mcp_cmd
from phoenix.cli.commands import providers as providers_cmd
from phoenix.cli.commands import task as task_cmd
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

    handler = _resolve_handler(args)
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


# Callable[..., int] rather than a strict positional signature so
# handlers can use keyword-only / underscore-prefixed parameter names
# (mypy can't match named param signatures across module boundaries).
_CommandHandler = Callable[..., int]


def _resolve_handler(args: argparse.Namespace) -> _CommandHandler | None:
    """Route an argparse Namespace to its command handler.

    Top-level commands (``health``) resolve via
    :data:`_TOPLEVEL_HANDLERS`. Grouped commands (``task submit``,
    ``lora list``, ...) resolve via the group module's ``HANDLERS``
    dict using ``args.<group>_command``.
    """
    command = args.command
    if command in _TOPLEVEL_HANDLERS:
        return _TOPLEVEL_HANDLERS[command]
    group_map: dict[str, _CommandHandler] | None = _GROUP_HANDLER_MAPS.get(command)
    if group_map is None:
        return None
    subcommand_attr = f"{command}_command"
    subcommand = getattr(args, subcommand_attr, None)
    if subcommand is None:
        # `phoenix task` with no subcommand
        print(
            f"phoenix {command}: subcommand required (available: {sorted(group_map.keys())})",
            file=sys.stderr,
        )
        return _print_usage_handler
    return group_map.get(subcommand)


def _print_usage_handler(
    _args: argparse.Namespace,
    _config: CLIConfig,
    _client: CLIHTTPClient,
    _fmt: str,
) -> int:
    """No-op handler that returns ``EXIT_USAGE_ERROR``.

    Routed when a group is invoked without a subcommand;
    :func:`_resolve_handler` has already printed the helpful list.
    """
    return EXIT_USAGE_ERROR


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

    # Step 9 -- MCP server
    _add_mcp_group(subparsers)

    return parser


def _add_mcp_group(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    sp = subparsers.add_parser("mcp", help="MCP server commands.")
    inner = sp.add_subparsers(dest="mcp_command")
    inner.add_parser("serve", help="Boot the MCP server on stdio.")


# --------------------------------------------------------------------
# Command group scaffolds. Each adds an `<group>_command` attribute on
# the parsed args so Step 7/8 handlers can route. Step 6 ships only
# the `health` handler (Step 7+ wires the rest).


def _add_task_group(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    sp = subparsers.add_parser("task", help="Task lifecycle commands.")
    inner = sp.add_subparsers(dest="task_command")

    submit = inner.add_parser("submit", help="POST /v1/tasks.")
    submit.add_argument(
        "--spec",
        required=True,
        help="Task spec JSON. Inline string or @path-to-file.json.",
    )

    get = inner.add_parser("get", help="Show cached task envelope.")
    get.add_argument("task_id", help="Task ID returned by submit.")

    replay = inner.add_parser("replay", help="POST /v1/tasks/{id}/replay.")
    replay.add_argument("task_id")
    replay.add_argument(
        "--mode",
        choices=["permissive", "strict", "replay"],
        default="strict",
    )

    stream = inner.add_parser("stream", help="Print WS connect hint.")
    stream.add_argument("task_id")


def _add_lora_group(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    sp = subparsers.add_parser("lora", help="LoRA adapter commands.")
    inner = sp.add_subparsers(dest="lora_command")

    load = inner.add_parser("load", help="POST /v1/adapters.")
    load.add_argument(
        "spec",
        help='Adapter spec (e.g., "phoenix.adapters.identity_adapter:make_identity_adapter").',
    )

    inner.add_parser("list", help="GET /v1/adapters.")

    unload = inner.add_parser("unload", help="DELETE /v1/adapters/{id}.")
    unload.add_argument("adapter_id")


def _add_identity_group(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    sp = subparsers.add_parser("identity", help="Identity commands.")
    inner = sp.add_subparsers(dest="identity_command")

    inner.add_parser("show", help="Show effective actor + daemon reachability.")

    enroll = inner.add_parser("enroll", help="POST /v1/identity/enroll.")
    enroll.add_argument("actor_name")
    enroll.add_argument(
        "--permission",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Permission key=value (repeatable). e.g., can_load_adapter=true.",
    )


def _add_providers_group(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    sp = subparsers.add_parser("providers", help="Provider registry commands.")
    inner = sp.add_subparsers(dest="providers_command")
    inner.add_parser("list", help="GET /v1/admin/providers/health-history.")


def _add_audit_group(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    sp = subparsers.add_parser("audit", help="Audit log commands.")
    inner = sp.add_subparsers(dest="audit_command")

    tail = inner.add_parser("tail", help="GET /v1/audit/events.")
    tail.add_argument(
        "--since",
        type=float,
        default=None,
        help="Lower bound (unix timestamp). Default: 0.",
    )
    tail.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Max events to return (default 100).",
    )

    inner.add_parser("verify", help="GET /v1/audit/ledger/verify.")


def _add_calibration_group(
    subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    sp = subparsers.add_parser("calibration", help="Calibration commands.")
    inner = sp.add_subparsers(dest="calibration_command")
    inner.add_parser("status", help="GET /v1/admin/calibration/detail.")
    inner.add_parser("run", help="POST /v1/admin/calibration/run.")


def _add_admin_group(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    sp = subparsers.add_parser("admin", help="Admin commands.")
    inner = sp.add_subparsers(dest="admin_command")

    ks = inner.add_parser(
        "kill-switch",
        help="Engage / release / status the kill switch.",
    )
    ks_inner = ks.add_subparsers(dest="admin_kill_switch_subcommand")

    ks_engage = ks_inner.add_parser("engage", help="Engage the kill switch.")
    ks_engage.add_argument(
        "--reason",
        required=True,
        help="Operator-supplied reason recorded in the ledger.",
    )

    ks_release = ks_inner.add_parser("release", help="Release the kill switch.")
    ks_release.add_argument(
        "--reason",
        required=True,
        help="Operator-supplied reason recorded in the ledger.",
    )

    ks_inner.add_parser("status", help="GET /v1/admin/kill-switch/status.")

    inner.add_parser("health", help="GET /v1/admin/health/detailed.")
    inner.add_parser("governor", help="GET /v1/admin/governor.")
    inner.add_parser("budget", help="GET /v1/admin/budget.")

    override = inner.add_parser(
        "override",
        help="POST /v1/admin/tasks-pending-review/{task_id}/override.",
    )
    override.add_argument("task_id")
    override.add_argument(
        "--disposition",
        choices=["pass", "fail", "degraded"],
        required=True,
    )
    override.add_argument(
        "--reason",
        required=True,
        help="Operator-supplied reason recorded in the audit log.",
    )

    gen_key = inner.add_parser(
        "generate-encryption-key",
        help="Generate an age keypair for ENCRYPTED_OPT_IN ledger encryption.",
    )
    gen_key.add_argument(
        "--name",
        default="primary",
        help=(
            "Name slug for the keypair files. Default 'primary' writes "
            "identity.txt + recipients/primary.pub."
        ),
    )
    gen_key.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files. Default: refuse.",
    )
    gen_key.add_argument(
        "--keys-dir",
        default=None,
        help=(
            "Override the default keys directory. "
            "Default: $PHOENIX_ENCRYPTION_KEYS_DIR or "
            "~/.phoenix/runtime/encryption_keys/."
        ),
    )


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


_TOPLEVEL_HANDLERS: dict[str, _CommandHandler] = {
    "health": _cmd_health,
}

# Group dispatch: args.command -> group's HANDLERS dict.
_GROUP_HANDLER_MAPS: dict[str, dict[str, _CommandHandler]] = {
    "task": task_cmd.HANDLERS,
    "lora": lora_cmd.HANDLERS,
    "identity": identity_cmd.HANDLERS,
    "providers": providers_cmd.HANDLERS,
    "audit": audit_cmd.HANDLERS,
    "calibration": calibration_cmd.HANDLERS,
    "admin": admin_cmd.HANDLERS,
    "mcp": mcp_cmd.HANDLERS,
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
