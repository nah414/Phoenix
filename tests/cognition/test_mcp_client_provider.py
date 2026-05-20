"""Tests for ``DefaultMCPToolProvider`` (Step 6 reference impl)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from phoenix.mcp.client_provider import DefaultMCPToolProvider, MCPToolProvider
from phoenix.mcp.errors import MCPToolNotAllowed
from phoenix.mcp.server_spec import MCPServerSpec


def _spec(allowed_tools: tuple[str, ...] = ("read_file",)) -> MCPServerSpec:
    return MCPServerSpec(
        name="test",
        transport="stdio",
        endpoint="test-server",
        allowed_tools=allowed_tools,
    )


@pytest.mark.asyncio
async def test_default_provider_implements_protocol() -> None:
    """Structural conformance: DefaultMCPToolProvider satisfies MCPToolProvider."""
    fake_client = AsyncMock()
    provider: MCPToolProvider = DefaultMCPToolProvider(_spec(), client=fake_client)
    assert isinstance(provider, MCPToolProvider)


@pytest.mark.asyncio
async def test_provider_list_tools_delegates_to_client() -> None:
    fake_client = AsyncMock()
    fake_client.list_tools.return_value = [
        {"name": "read_file", "description": "read", "input_schema": {}},
    ]
    provider = DefaultMCPToolProvider(_spec(), client=fake_client)

    tools = await provider.list_tools()

    assert len(tools) == 1
    assert tools[0]["name"] == "read_file"
    fake_client.list_tools.assert_awaited_once()


@pytest.mark.asyncio
async def test_provider_call_tool_passes_through_allowed_tool() -> None:
    fake_client = AsyncMock()
    fake_client.call_tool.return_value = {"content": [], "is_error": False}
    provider = DefaultMCPToolProvider(_spec(allowed_tools=("read_file",)), client=fake_client)

    result = await provider.call_tool("read_file", {"path": "/tmp/x"})

    assert result["is_error"] is False
    fake_client.call_tool.assert_awaited_once_with("read_file", {"path": "/tmp/x"})


@pytest.mark.asyncio
async def test_provider_call_tool_rejects_disallowed_tool() -> None:
    """Second-line defense: provider re-checks allowlist (belt-and-suspenders per 13-D4)."""
    fake_client = AsyncMock()
    provider = DefaultMCPToolProvider(_spec(allowed_tools=("read_file",)), client=fake_client)

    with pytest.raises(MCPToolNotAllowed):
        await provider.call_tool("delete_file", {})

    # The underlying client.call_tool MUST NOT have been invoked.
    fake_client.call_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_close_delegates_to_client() -> None:
    fake_client = AsyncMock()
    provider = DefaultMCPToolProvider(_spec(), client=fake_client)
    await provider.close()
    fake_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_provider_context_manager() -> None:
    """async with: connect on enter, close on exit."""
    fake_client = AsyncMock()
    fake_client.call_tool.return_value = {"content": [], "is_error": False}

    async with DefaultMCPToolProvider(_spec(), client=fake_client) as provider:
        await provider.call_tool("read_file", {})

    fake_client.connect.assert_awaited_once()
    fake_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_provider_spec_attribute_accessible() -> None:
    spec = _spec()
    provider = DefaultMCPToolProvider(spec, client=AsyncMock())
    assert provider.spec is spec
