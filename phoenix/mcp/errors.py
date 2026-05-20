"""Typed errors for the MCP-client dispatch path (Phase 13 Step 6).

Per 13-D4 (locked 2026-05-18): Phoenix-as-MCP-client may only dispatch
to MCP servers that have been **explicitly registered** by an admin
Actor. No TOFU, no empty-default-allows-all, no discovery-based
auto-add. The dispatch path enforces this in front of any network
call; these errors are what the registry + dispatch helper raise.

All errors carry the failing server name + (when relevant) the
attempted tool name so audit entries have the full context for
post-incident forensics.
"""

from __future__ import annotations


class MCPError(Exception):
    """Base for MCP-client subsystem failures."""


class MCPServerNotRegistered(MCPError):
    """A task referenced an MCP server name that isn't in the registry.

    Maps to HTTP 403 (Forbidden) at the front door. Raised BEFORE any
    network call — the registry lookup is the gate.

    Per 13-D4: this is the load-bearing case. The default empty
    registry state must result in zero possible network calls to any
    MCP server.
    """

    def __init__(self, server_name: str) -> None:
        super().__init__(
            f"MCP server {server_name!r} is not registered. "
            f"Register via POST /v1/admin/mcp-servers/{server_name} "
            f"with an admin Actor signature."
        )
        self.server_name = server_name


class MCPToolNotAllowed(MCPError):
    """A task referenced a tool not in the server's ``allowed_tools`` list.

    Maps to HTTP 403. Raised BEFORE the tool call — the
    :class:`MCPServerSpec.allowed_tools` list is the gate.

    Per 13-D4: ``["*"]`` is explicitly forbidden in ``allowed_tools``;
    tool access requires explicit per-tool listing.
    """

    def __init__(self, server_name: str, tool_name: str) -> None:
        super().__init__(
            f"Tool {tool_name!r} is not in the allowed_tools list for "
            f"MCP server {server_name!r}. Update the server's "
            f"registration via POST /v1/admin/mcp-servers/{server_name} "
            f"to add the tool."
        )
        self.server_name = server_name
        self.tool_name = tool_name


class MCPServerBudgetExceeded(MCPError):
    """The MCP server's 24h budget has been exceeded.

    Maps to HTTP 429 (Too Many Requests) with ``Retry-After`` header
    set to ``retry_after_seconds``. The budget window resets at the
    next 24h boundary; Phoenix's cost-ledger (Phase 10) computes the
    window-end timestamp.
    """

    def __init__(
        self,
        server_name: str,
        *,
        spent_usd: float,
        budget_usd: float,
        retry_after_seconds: int,
    ) -> None:
        super().__init__(
            f"MCP server {server_name!r} has exceeded its daily budget "
            f"(${spent_usd:.4f} / ${budget_usd:.4f}). "
            f"Window resets in {retry_after_seconds}s."
        )
        self.server_name = server_name
        self.spent_usd = spent_usd
        self.budget_usd = budget_usd
        self.retry_after_seconds = retry_after_seconds


class MCPServerRevoked(MCPError):
    """The MCP server was de-registered mid-dispatch.

    Maps to HTTP 503 (Service Unavailable). Phase 13 Step 6 contract:
    de-registration takes effect immediately — any in-flight calls to
    the server receive this error on the next round-trip.
    """

    def __init__(self, server_name: str) -> None:
        super().__init__(
            f"MCP server {server_name!r} was de-registered. "
            f"In-flight calls fail with this error on the next round-trip."
        )
        self.server_name = server_name


class MCPRegistrationError(MCPError):
    """Registering an MCP server failed.

    Wraps validation failures (e.g. ``["*"]`` in allowed_tools,
    invalid transport spec) and persistence failures (e.g. JSON file
    write error). Raised by :meth:`MCPServerRegistry.register`.
    """


class MCPTransportError(MCPError):
    """The underlying MCP transport failed (stdio process died, HTTP
    connection dropped, etc.).

    Maps to HTTP 502 (Bad Gateway). Distinct from :class:`MCPServerRevoked`
    in that the server is still registered — the transport is the
    failure point.
    """

    def __init__(self, server_name: str, reason: str) -> None:
        super().__init__(f"MCP transport failure for server {server_name!r}: {reason}")
        self.server_name = server_name
        self.reason = reason


__all__ = [
    "MCPError",
    "MCPServerNotRegistered",
    "MCPToolNotAllowed",
    "MCPServerBudgetExceeded",
    "MCPServerRevoked",
    "MCPRegistrationError",
    "MCPTransportError",
]
