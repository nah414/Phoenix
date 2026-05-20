"""``MCPClient`` — Phoenix-side wrapper around the official ``mcp`` SDK.

Per Phase 13 build guide Step 6: Phoenix-as-MCP-client opens MCP
sessions against externally-registered servers (per 13-D4) and
invokes tools on them. The MCP SDK is async-native; this class
preserves that surface (async methods) and provides a context-manager
protocol for clean transport teardown.

**Why async-native at this layer:** Step 6 ships the
transport-correct interface. Phoenix's existing FastAPI handlers
are async, so the MCP-client dispatch sites in Step 9+ can ``await``
directly without a sync/async bridge. The :class:`CognitionProvider`
Protocol (which is sync) integration with MCP-as-cognition is a
Step 9+ concern — see [OPEN] in this module.

**[OPEN: P13-Step6-A]:** ``MCPClient`` integration with the sync
:class:`CognitionProvider` Protocol from Step 1. Two paths:
(1) wrap MCPClient with a sync surface backed by a background event
loop (matches Phase 6b's ``embedded_runner`` pattern), or
(2) refactor :class:`CognitionProvider` to be async-native
(broader change). Step 6 defers; the dispatch site at Step 9+ picks.

**SAFETY:** ``MCPServerSpec.auth_config`` is passed to the SDK
without logging. The SDK's stderr/stdout from stdio transport
processes are NOT captured by Phoenix (would risk leaking auth
material); ops debug via the SDK's own logging discipline.
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

from phoenix.mcp.errors import MCPTransportError
from phoenix.mcp.server_spec import MCPServerSpec

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


class MCPClient:
    """Async MCP client for one registered MCP server.

    Construct with a :class:`MCPServerSpec`. The session is opened
    via :meth:`connect` (or by using the instance as an async context
    manager) and closed via :meth:`close` (or on context exit).

    Lazy-imports the ``mcp`` SDK so module-level import succeeds
    without the ``[mcp]`` extra installed. Calls to :meth:`connect`
    on a non-installed extra raise :class:`MCPTransportError` with
    the install instruction.
    """

    def __init__(self, spec: MCPServerSpec) -> None:
        self._spec = spec
        self._session: Any = None
        self._exit_stack: AsyncExitStack | None = None

    @property
    def spec(self) -> MCPServerSpec:
        return self._spec

    async def connect(self) -> None:
        """Open an MCP session against ``self.spec.endpoint``.

        Per Phase 13 build guide §4.6: supports both ``stdio`` and
        ``http+sse`` transports.

        Raises :class:`MCPTransportError` on any failure:
        - SDK not installed (``[mcp]`` extra missing).
        - Transport-spec invalid.
        - Server process crashed at startup (stdio).
        - HTTP connection refused (http+sse).
        """
        try:
            from mcp import ClientSession
            from mcp.client.sse import sse_client
            from mcp.client.stdio import StdioServerParameters, stdio_client
        except ImportError as exc:
            raise MCPTransportError(
                self._spec.name,
                "mcp SDK not installed; install via `pip install phoenix-middleware[mcp]`.",
            ) from exc

        self._exit_stack = AsyncExitStack()
        try:
            if self._spec.transport == "stdio":
                # Endpoint is the command; auth_config may carry env vars.
                params = StdioServerParameters(
                    command=self._spec.endpoint,
                    args=list(self._spec.auth_config.get("args", [])),
                    env=dict(self._spec.auth_config.get("env", {})),
                )
                read, write = await self._exit_stack.enter_async_context(stdio_client(params))
            elif self._spec.transport == "http+sse":
                # Endpoint is the URL; auth_config may carry headers
                # (bearer tokens, etc.).
                headers = dict(self._spec.auth_config.get("headers", {}))
                read, write = await self._exit_stack.enter_async_context(
                    sse_client(self._spec.endpoint, headers=headers)
                )
            else:
                # Transport already validated at MCPServerSpec construction.
                raise MCPTransportError(
                    self._spec.name,
                    f"Unsupported transport {self._spec.transport!r}",
                )

            session = await self._exit_stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self._session = session
        except Exception as exc:
            # Tear down any partial state.
            if self._exit_stack is not None:
                await self._exit_stack.aclose()
                self._exit_stack = None
            if isinstance(exc, MCPTransportError):
                raise
            raise MCPTransportError(self._spec.name, str(exc)) from exc

    async def list_tools(self) -> list[dict[str, Any]]:
        """List the tools exposed by the connected server.

        Returns a list of dicts: ``{"name": str, "description": str,
        "inputSchema": dict}``. The Phoenix admin endpoint surfaces
        this for ops to confirm the server's actual tool surface
        matches the ``allowed_tools`` registration.

        Raises :class:`MCPTransportError` if not connected.
        """
        if self._session is None:
            raise MCPTransportError(
                self._spec.name,
                "MCPClient is not connected; call connect() first.",
            )
        try:
            response = await self._session.list_tools()
        except Exception as exc:
            raise MCPTransportError(self._spec.name, f"list_tools failed: {exc}") from exc
        # The SDK returns a ListToolsResult with .tools list of Tool objects.
        tools_raw = getattr(response, "tools", []) or []
        out: list[dict[str, Any]] = []
        for tool in tools_raw:
            out.append(
                {
                    "name": getattr(tool, "name", ""),
                    "description": getattr(tool, "description", "") or "",
                    "input_schema": getattr(tool, "inputSchema", {}) or {},
                }
            )
        return out

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Invoke a tool on the connected server.

        Caller is responsible for verifying ``tool_name`` is in
        ``self.spec.allowed_tools`` BEFORE invoking this method —
        the dispatch helper at :func:`phoenix.mcp.dispatch.check_mcp_dispatch`
        is the gate; this method just executes.

        Returns a dict with ``content`` (list of content blocks),
        ``isError`` (bool), and ``_raw`` (the SDK's raw response for
        debugging).

        Raises :class:`MCPTransportError` on transport failure.
        """
        if self._session is None:
            raise MCPTransportError(
                self._spec.name,
                "MCPClient is not connected; call connect() first.",
            )
        try:
            response = await self._session.call_tool(tool_name, arguments)
        except Exception as exc:
            raise MCPTransportError(
                self._spec.name,
                f"call_tool({tool_name!r}) failed: {exc}",
            ) from exc

        content_raw = getattr(response, "content", []) or []
        content: list[dict[str, Any]] = []
        for block in content_raw:
            btype = getattr(block, "type", None)
            entry: dict[str, Any] = {"type": btype}
            if btype == "text":
                entry["text"] = getattr(block, "text", "")
            elif btype == "image":
                entry["data"] = getattr(block, "data", "")
                entry["mime_type"] = getattr(block, "mimeType", "")
            content.append(entry)

        return {
            "content": content,
            "is_error": bool(getattr(response, "isError", False)),
        }

    async def close(self) -> None:
        """Tear down the session and transport cleanly."""
        if self._exit_stack is not None:
            try:
                await self._exit_stack.aclose()
            except Exception as exc:
                log.warning(
                    "MCPClient(%s): error during close: %s",
                    self._spec.name,
                    exc,
                )
            finally:
                self._exit_stack = None
                self._session = None

    async def __aenter__(self) -> MCPClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()
