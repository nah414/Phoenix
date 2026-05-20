"""``MCPToolProvider`` Protocol + reference implementation.

Per Phase 13 build guide Step 6: an MCP server can act either as a
remote LLM (rare; mostly local LLM servers like Ollama-MCP) or as a
remote tool set (most common; GitHub MCP, Postgres MCP, etc.). Step 6
ships the tool-provider surface; the cognition-via-MCP integration
with the sync :class:`CognitionProvider` Protocol is deferred to
Step 9+ per the OPEN noted in :mod:`phoenix.mcp.client`.

The :class:`MCPToolProvider` Protocol is async-native — it matches
the MCP SDK's natural async shape and the FastAPI async handlers
that consume it.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from phoenix.mcp.client import MCPClient
from phoenix.mcp.errors import MCPToolNotAllowed
from phoenix.mcp.server_spec import MCPServerSpec


@runtime_checkable
class MCPToolProvider(Protocol):
    """Async contract for using an MCP server as a tool provider.

    Phase 13 Step 6 ships :class:`DefaultMCPToolProvider` as the
    reference impl. Customer deployments can register their own
    impls (e.g. with extra audit hooks, per-tool argument
    sanitization) and the dispatch path uses them by structural
    conformance.
    """

    spec: MCPServerSpec

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return the tools the server exposes."""
        ...

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Invoke a tool. Caller must verify ``tool_name`` is in
        ``self.spec.allowed_tools`` first (the dispatch helper at
        :func:`phoenix.mcp.dispatch.check_mcp_dispatch` is the gate)."""
        ...


class DefaultMCPToolProvider:
    """Phase 13 Step 6 reference :class:`MCPToolProvider` impl.

    Wraps :class:`MCPClient` and enforces the
    :attr:`MCPServerSpec.allowed_tools` allowlist inside
    :meth:`call_tool` as a second-line defense (the primary line is
    the dispatch helper at the call site; this is belt-and-
    suspenders per 13-D4's load-bearing discipline).
    """

    def __init__(self, spec: MCPServerSpec, *, client: MCPClient | None = None) -> None:
        self.spec = spec
        self._client = client if client is not None else MCPClient(spec)

    async def connect(self) -> None:
        await self._client.connect()

    async def list_tools(self) -> list[dict[str, Any]]:
        return await self._client.list_tools()

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        # Second-line allowlist enforcement. The dispatch helper at the
        # call site should have already raised MCPToolNotAllowed if
        # this would fail; we re-check here as defense-in-depth.
        if not self.spec.tool_allowed(tool_name):
            raise MCPToolNotAllowed(self.spec.name, tool_name)
        return await self._client.call_tool(tool_name, arguments)

    async def close(self) -> None:
        await self._client.close()

    async def __aenter__(self) -> DefaultMCPToolProvider:
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()
