"""Defense-in-depth tests for ``MCPClient.call_tool()`` (Phase 13.x.1).

Per Phase 13.x defense-in-depth audit (2026-05-21): the first-line
pre-dispatch gate :func:`check_mcp_dispatch` is discipline-based —
callers MUST invoke it before opening a session. These tests pin the
second-line enforcement: :meth:`MCPClient.call_tool` itself
re-checks the tool allowlist + budget hook on every call via
:func:`enforce_call_gates`, so a caller that forgets the
pre-dispatch gate still cannot bypass per-tool-allowlist or per-server
budget enforcement.

The first-line/second-line split matches Phoenix's audit-grade
discipline: the front-door gate catches the common case fast; the
in-method re-check is the cheap belt-and-suspenders that guarantees
the invariant even when the front door is bypassed (e.g., when an
in-process consumer constructs an ``MCPClient`` directly).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from phoenix.mcp.client import MCPClient
from phoenix.mcp.dispatch import (
    reset_budget_check_hook,
    set_budget_check_hook,
)
from phoenix.mcp.errors import MCPServerBudgetExceeded, MCPToolNotAllowed
from phoenix.mcp.server_spec import MCPServerSpec


def _spec(
    allowed_tools: tuple[str, ...] = ("read_file",),
    budget: float = 1.00,
) -> MCPServerSpec:
    return MCPServerSpec(
        name="test-server",
        transport="stdio",
        endpoint="test-binary",
        allowed_tools=allowed_tools,
        max_budget_usd_per_day=budget,
    )


@pytest.fixture(autouse=True)
def reset_hook() -> None:
    """Clear the budget hook after every test so tests stay isolated."""
    yield
    reset_budget_check_hook()


@pytest.mark.asyncio
async def test_call_tool_rejects_disallowed_tool(monkeypatch) -> None:
    """Calling a tool not in the allowlist raises MCPToolNotAllowed
    BEFORE any session attempt — even when call_tool is invoked
    directly without going through check_mcp_dispatch first."""
    client = MCPClient(_spec(allowed_tools=("read_file",)))
    # Install a fake session so we can prove call_tool's gate fires
    # BEFORE the SDK is touched (if it didn't, AsyncMock would let
    # the call go through).
    client._session = AsyncMock()  # type: ignore[attr-defined]

    with pytest.raises(MCPToolNotAllowed) as exc_info:
        await client.call_tool("delete_repo", {})

    assert exc_info.value.server_name == "test-server"
    assert exc_info.value.tool_name == "delete_repo"
    # The SDK was never touched.
    client._session.call_tool.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_call_tool_enforces_budget_when_hook_set() -> None:
    """When a budget hook reports the budget exceeded, call_tool raises
    MCPServerBudgetExceeded BEFORE the SDK is touched."""
    client = MCPClient(_spec(allowed_tools=("read_file",), budget=2.50))
    client._session = AsyncMock()  # type: ignore[attr-defined]

    # Install a hook that says "you've spent $5.00 of your $2.50 budget;
    # retry in 3600 seconds."
    def _hook(server_name: str, budget_usd: float) -> tuple[float, int]:
        assert server_name == "test-server"
        assert budget_usd == pytest.approx(2.50)
        return (5.00, 3600)

    set_budget_check_hook(_hook)

    with pytest.raises(MCPServerBudgetExceeded) as exc_info:
        await client.call_tool("read_file", {"path": "/etc/passwd"})

    err = exc_info.value
    assert err.server_name == "test-server"
    assert err.spent_usd == pytest.approx(5.00)
    assert err.budget_usd == pytest.approx(2.50)
    assert err.retry_after_seconds == 3600
    # The SDK was never touched.
    client._session.call_tool.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_call_tool_proceeds_when_budget_hook_returns_none() -> None:
    """A budget hook returning None means "under budget" — call proceeds."""
    client = MCPClient(_spec(allowed_tools=("read_file",)))

    fake_session = AsyncMock()
    # Build an MCP-SDK-shaped response.
    fake_session.call_tool.return_value = type(
        "FakeResponse",
        (),
        {
            "content": [
                type("FakeBlock", (), {"type": "text", "text": "ok"})(),
            ],
            "isError": False,
        },
    )()
    client._session = fake_session  # type: ignore[attr-defined]

    # Hook says "under budget."
    set_budget_check_hook(lambda *_: None)

    result = await client.call_tool("read_file", {"path": "/safe"})

    assert result["is_error"] is False
    assert result["content"][0]["type"] == "text"
    assert result["content"][0]["text"] == "ok"
    fake_session.call_tool.assert_awaited_once_with("read_file", {"path": "/safe"})


@pytest.mark.asyncio
async def test_call_tool_proceeds_when_no_hook_registered() -> None:
    """The default no-hook state is a no-op for the budget check.

    Pinned because the Phase 13 build guide explicitly says the budget
    check is opt-in via :func:`set_budget_check_hook`; the no-hook
    state must NOT block calls or surprise existing callers.
    """
    client = MCPClient(_spec(allowed_tools=("read_file",)))

    fake_session = AsyncMock()
    fake_session.call_tool.return_value = type(
        "FakeResponse",
        (),
        {"content": [], "isError": False},
    )()
    client._session = fake_session  # type: ignore[attr-defined]

    # Confirm no hook is registered (the autouse fixture cleared it).
    result = await client.call_tool("read_file", {})

    assert result["is_error"] is False
    fake_session.call_tool.assert_awaited_once()


@pytest.mark.asyncio
async def test_call_tool_gate_fires_before_connect_check() -> None:
    """The allowlist + budget re-check fires BEFORE the not-connected
    error — fail-fastest on the cheapest check.

    Why this ordering matters: a caller that built an ``MCPClient`` but
    skipped ``connect()`` should still get the policy-rejection error
    rather than a transport-shaped error. The typed exception then
    carries the correct semantic for the caller to handle (retry on
    budget; fix bug on tool-not-allowed) rather than masking the
    policy issue as a connect problem.
    """
    client = MCPClient(_spec(allowed_tools=("read_file",)))
    # NOT connected.

    with pytest.raises(MCPToolNotAllowed):
        await client.call_tool("delete_repo", {})
