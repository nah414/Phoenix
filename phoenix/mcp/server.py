"""Phoenix MCP server wiring (Phase 9 Step 9).

Registers the 8 v1 tools (from :mod:`phoenix.mcp.tools`) with a
:class:`FastMCP` instance and exposes :func:`run_stdio` that boots
the server on stdin/stdout transport.

The MCP SDK is an optional dependency (``pip install
phoenix-middleware[mcp]``). This module raises a clear
:class:`ImportError` if the SDK isn't installed instead of a
ModuleNotFoundError deep in the stack.

Usage from the CLI (Phase 9 ``phoenix mcp serve``)::

    from phoenix.mcp.server import run_stdio
    run_stdio()  # blocks until the IDE client disconnects

The server reads CLI config (REST URL + actor) the same way
:mod:`phoenix.cli.entry` does, so MCP tools execute against the
same daemon the CLI talks to.
"""

from __future__ import annotations

import asyncio
from typing import Any

from phoenix.cli.config_loader import CLIConfig, load_config
from phoenix.cli.http_client import CLIHTTPClient, CLIHTTPError, build_client
from phoenix.mcp.tools import TOOL_REGISTRY

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - optional dep
    raise ImportError(
        "The MCP server requires the 'mcp' extra: install with "
        "`pip install phoenix-middleware[mcp]`."
    ) from exc


# Tool descriptions surfaced through MCP's tools/list response.
_TOOL_DESCRIPTIONS = {
    "phoenix_task_submit": (
        "Submit a physics task to Phoenix. Body must include "
        "physics_context + tolerance per Section 5.2's SolveRequest."
    ),
    "phoenix_task_get": (
        "Return the cached Result envelope for a previously-submitted "
        "task_id (read from the per-user task cache)."
    ),
    "phoenix_task_replay": (
        "Re-run a previously-submitted task with the given "
        "reproducibility_mode (permissive | strict | replay)."
    ),
    "phoenix_provenance_get": (
        "Return the cached envelope's provenance trace -- solver, "
        "control, orchestrate sub-blocks. Equivalent to "
        "phoenix_task_get(task_id)['provenance']."
    ),
    "phoenix_providers_list": (
        "Return the recent provider health-history snapshot (admin-only on the daemon)."
    ),
    "phoenix_calibration_status": (
        "Return the current drift detector state (admin-only on the daemon)."
    ),
    "phoenix_health": (
        "Return the daemon's /v1/health response: version + vendor manifest summary."
    ),
    "phoenix_audit_verify": (
        "Run the Omega Ledger chain-integrity check; returns {valid: bool, ...}."
    ),
}


def build_server(
    config: CLIConfig,
    *,
    actor_override: str | None = None,
    client_factory: Any | None = None,
) -> FastMCP:
    """Construct a :class:`FastMCP` server pre-wired with the 8 v1 tools.

    Parameters:
      - ``config``: resolved :class:`CLIConfig`.
      - ``actor_override``: optional actor name; falls back to
        ``config.default_actor`` then to dev-mode bootstrap.
      - ``client_factory``: testing seam -- a callable that
        returns a :class:`CLIHTTPClient`. Defaults to
        :func:`build_client`.

    Returns the wired-but-not-yet-running server. Call
    :func:`run_stdio` or use FastMCP's APIs directly.
    """
    server = FastMCP(name="phoenix")

    factory = client_factory if client_factory is not None else build_client

    # Capture the client at construction time so the per-call
    # closures don't keep rebuilding it.
    client: CLIHTTPClient = factory(config, actor_override=actor_override)

    # FastMCP introspects each tool's signature for argument validation,
    # so we register typed per-tool wrappers (kwargs blob isn't accepted).
    # The FastMCP @tool decorator is untyped upstream; the # type: ignore
    # silences pre-commit mypy without dropping our local type checks.
    @server.tool(  # type: ignore[misc]
        name="phoenix_task_submit",
        description=_TOOL_DESCRIPTIONS["phoenix_task_submit"],
    )
    async def _task_submit(spec: dict[str, Any]) -> dict[str, Any]:
        return _invoke_safely(TOOL_REGISTRY["phoenix_task_submit"], client, {"spec": spec})

    @server.tool(  # type: ignore[misc]
        name="phoenix_task_get",
        description=_TOOL_DESCRIPTIONS["phoenix_task_get"],
    )
    async def _task_get(task_id: str) -> dict[str, Any]:
        return _invoke_safely(TOOL_REGISTRY["phoenix_task_get"], client, {"task_id": task_id})

    @server.tool(  # type: ignore[misc]
        name="phoenix_task_replay",
        description=_TOOL_DESCRIPTIONS["phoenix_task_replay"],
    )
    async def _task_replay(task_id: str, reproducibility_mode: str = "strict") -> dict[str, Any]:
        return _invoke_safely(
            TOOL_REGISTRY["phoenix_task_replay"],
            client,
            {"task_id": task_id, "reproducibility_mode": reproducibility_mode},
        )

    @server.tool(  # type: ignore[misc]
        name="phoenix_provenance_get",
        description=_TOOL_DESCRIPTIONS["phoenix_provenance_get"],
    )
    async def _provenance_get(task_id: str) -> dict[str, Any]:
        return _invoke_safely(TOOL_REGISTRY["phoenix_provenance_get"], client, {"task_id": task_id})

    @server.tool(  # type: ignore[misc]
        name="phoenix_providers_list",
        description=_TOOL_DESCRIPTIONS["phoenix_providers_list"],
    )
    async def _providers_list() -> dict[str, Any]:
        return _invoke_safely(TOOL_REGISTRY["phoenix_providers_list"], client, {})

    @server.tool(  # type: ignore[misc]
        name="phoenix_calibration_status",
        description=_TOOL_DESCRIPTIONS["phoenix_calibration_status"],
    )
    async def _calibration_status() -> dict[str, Any]:
        return _invoke_safely(TOOL_REGISTRY["phoenix_calibration_status"], client, {})

    @server.tool(  # type: ignore[misc]
        name="phoenix_health",
        description=_TOOL_DESCRIPTIONS["phoenix_health"],
    )
    async def _health() -> dict[str, Any]:
        return _invoke_safely(TOOL_REGISTRY["phoenix_health"], client, {})

    @server.tool(  # type: ignore[misc]
        name="phoenix_audit_verify",
        description=_TOOL_DESCRIPTIONS["phoenix_audit_verify"],
    )
    async def _audit_verify() -> dict[str, Any]:
        return _invoke_safely(TOOL_REGISTRY["phoenix_audit_verify"], client, {})

    return server


def _invoke_safely(
    impl: Any,
    client: CLIHTTPClient,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Invoke a tool impl and translate :class:`CLIHTTPError` to an
    in-payload error rather than letting it propagate as a transport
    failure.

    MCP clients see ``{"error": "..."}`` for daemon-side rejections
    (403, 404, 503, ...). Transport-level failures (daemon
    unreachable) still propagate.
    """
    try:
        return dict(impl(client=client, **kwargs))
    except CLIHTTPError as exc:
        return {
            "error": str(exc),
            "status_code": exc.status_code,
            "body": exc.body,
        }


def run_stdio(
    *,
    config_path: str | None = None,
    actor_override: str | None = None,
) -> None:
    """Boot the MCP server on stdin/stdout transport.

    Blocks until the IDE client closes the connection. Suitable
    for the ``phoenix mcp serve`` CLI command and for IDE
    configurations that spawn Phoenix as a subprocess.
    """
    from pathlib import Path

    config = load_config(
        config_path=Path(config_path) if config_path else None,
    )
    server = build_server(config, actor_override=actor_override)
    asyncio.run(server.run_stdio_async())


__all__ = ["build_server", "run_stdio"]
