"""Tests for the MCP dispatch gate — 13-D4 enforcement.

These are the load-bearing security tests. The gate function MUST
raise typed exceptions BEFORE any network call when:
1. The server is not registered → MCPServerNotRegistered (403).
2. The tool is not in the server's allowed_tools → MCPToolNotAllowed (403).
3. The server's budget is exceeded → MCPServerBudgetExceeded (429).

The empty-registry default state must allow ZERO dispatch attempts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from phoenix.mcp.dispatch import (
    check_mcp_dispatch,
    reset_budget_check_hook,
    set_budget_check_hook,
)
from phoenix.mcp.errors import (
    MCPServerBudgetExceeded,
    MCPServerNotRegistered,
    MCPToolNotAllowed,
)
from phoenix.mcp.server_registry import MCPServerRegistry
from phoenix.mcp.server_spec import MCPServerSpec


@pytest.fixture
def fresh_registry(tmp_path: Path) -> MCPServerRegistry:
    """A tmp-path-scoped registry that doesn't touch the user's home dir."""
    return MCPServerRegistry(persistence_path=tmp_path / "mcp.json")


@pytest.fixture(autouse=True)
def reset_hooks() -> None:
    """Clear the module-level budget hook between tests."""
    reset_budget_check_hook()
    yield
    reset_budget_check_hook()


def _spec(name: str, *, allowed_tools: tuple[str, ...] = ("read_file",)) -> MCPServerSpec:
    return MCPServerSpec(
        name=name,
        transport="stdio",
        endpoint=f"{name}-server",
        allowed_tools=allowed_tools,
    )


def test_unregistered_server_raises(fresh_registry: MCPServerRegistry) -> None:
    """13-D4 load-bearing: dispatch to unregistered server fails BEFORE any network."""
    with pytest.raises(MCPServerNotRegistered) as exc_info:
        check_mcp_dispatch(
            "not-registered",
            tool_name="anything",
            registry=fresh_registry,
        )
    assert exc_info.value.server_name == "not-registered"


def test_empty_registry_blocks_all_dispatches(fresh_registry: MCPServerRegistry) -> None:
    """Default empty state: ZERO possible network calls."""
    for name in ("a", "b", "c", "anything", ""):
        with pytest.raises(MCPServerNotRegistered):
            check_mcp_dispatch(name, tool_name="t", registry=fresh_registry)


def test_registered_with_matching_tool_passes(fresh_registry: MCPServerRegistry) -> None:
    """Happy path: registered server + allowed tool → returns spec."""
    fresh_registry.register(_spec("github", allowed_tools=("read_file", "list_files")))
    spec = check_mcp_dispatch("github", tool_name="read_file", registry=fresh_registry)
    assert spec.name == "github"


def test_tool_not_in_allowlist_raises(fresh_registry: MCPServerRegistry) -> None:
    """13-D4: tool must be in explicit allowlist."""
    fresh_registry.register(_spec("github", allowed_tools=("read_file",)))
    with pytest.raises(MCPToolNotAllowed) as exc_info:
        check_mcp_dispatch("github", tool_name="delete_repo", registry=fresh_registry)
    assert exc_info.value.server_name == "github"
    assert exc_info.value.tool_name == "delete_repo"


def test_check_order_registration_before_tool(fresh_registry: MCPServerRegistry) -> None:
    """Registration check fires before allowlist check (avoids extra work)."""
    # Unregistered server with a never-checked tool name.
    with pytest.raises(MCPServerNotRegistered):
        check_mcp_dispatch("nope", tool_name="whatever", registry=fresh_registry)


def test_budget_hook_not_called_when_unset(fresh_registry: MCPServerRegistry) -> None:
    """No budget hook → budget check is no-op."""
    fresh_registry.register(_spec("api", allowed_tools=("call",)))
    spec = check_mcp_dispatch("api", tool_name="call", registry=fresh_registry)
    assert spec.name == "api"


def test_budget_hook_exceeded_raises(fresh_registry: MCPServerRegistry) -> None:
    """Budget hook returning a tuple → MCPServerBudgetExceeded raised."""
    fresh_registry.register(_spec("expensive", allowed_tools=("query",)))
    set_budget_check_hook(lambda _name, _cap: (15.0, 3600))  # spent=$15, retry in 1h

    with pytest.raises(MCPServerBudgetExceeded) as exc_info:
        check_mcp_dispatch("expensive", tool_name="query", registry=fresh_registry)
    assert exc_info.value.spent_usd == 15.0
    assert exc_info.value.retry_after_seconds == 3600


def test_budget_hook_passes_when_returns_none(fresh_registry: MCPServerRegistry) -> None:
    """Budget hook returning None → check passes."""
    fresh_registry.register(_spec("api", allowed_tools=("call",)))
    called_with: list[tuple[str, float]] = []

    def hook(name: str, cap: float) -> None:
        called_with.append((name, cap))
        return None

    set_budget_check_hook(hook)
    spec = check_mcp_dispatch("api", tool_name="call", registry=fresh_registry)
    assert spec.name == "api"
    assert called_with == [("api", 10.0)]  # default budget passed through


def test_check_order_budget_after_tool(fresh_registry: MCPServerRegistry) -> None:
    """Tool-allowlist check fires before budget check (cheaper local check first)."""
    fresh_registry.register(_spec("api", allowed_tools=("allowed",)))
    set_budget_check_hook(lambda _n, _c: pytest.fail("budget hook should not fire"))

    with pytest.raises(MCPToolNotAllowed):
        check_mcp_dispatch("api", tool_name="disallowed", registry=fresh_registry)


def test_unregister_takes_effect_immediately(fresh_registry: MCPServerRegistry) -> None:
    """13-D4: de-registration is immediate. Next dispatch attempt fails."""
    fresh_registry.register(_spec("temp", allowed_tools=("op",)))
    # Initially registered, passes.
    check_mcp_dispatch("temp", tool_name="op", registry=fresh_registry)
    # Unregister.
    fresh_registry.unregister("temp")
    # Now fails.
    with pytest.raises(MCPServerNotRegistered):
        check_mcp_dispatch("temp", tool_name="op", registry=fresh_registry)
