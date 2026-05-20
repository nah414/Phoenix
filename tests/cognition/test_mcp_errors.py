"""Tests for MCP-client typed errors."""

from __future__ import annotations

import pytest

from phoenix.mcp.errors import (
    MCPError,
    MCPRegistrationError,
    MCPServerBudgetExceeded,
    MCPServerNotRegistered,
    MCPServerRevoked,
    MCPToolNotAllowed,
    MCPTransportError,
)


def test_all_errors_subclass_mcp_error() -> None:
    for cls in (
        MCPServerNotRegistered,
        MCPToolNotAllowed,
        MCPServerBudgetExceeded,
        MCPServerRevoked,
        MCPRegistrationError,
        MCPTransportError,
    ):
        assert issubclass(cls, MCPError)


def test_not_registered_carries_server_name() -> None:
    exc = MCPServerNotRegistered("github")
    assert exc.server_name == "github"
    assert "github" in str(exc)
    assert "register" in str(exc).lower()


def test_tool_not_allowed_carries_both_names() -> None:
    exc = MCPToolNotAllowed("github", "delete_repo")
    assert exc.server_name == "github"
    assert exc.tool_name == "delete_repo"
    assert "delete_repo" in str(exc)
    assert "github" in str(exc)


def test_budget_exceeded_carries_retry_after() -> None:
    exc = MCPServerBudgetExceeded(
        "expensive-llm",
        spent_usd=12.5,
        budget_usd=10.0,
        retry_after_seconds=3600,
    )
    assert exc.retry_after_seconds == 3600
    assert exc.spent_usd == 12.5
    assert exc.budget_usd == 10.0
    assert "$12.5000" in str(exc) or "12.5" in str(exc)


def test_server_revoked_carries_server_name() -> None:
    exc = MCPServerRevoked("oldserver")
    assert exc.server_name == "oldserver"
    assert "de-registered" in str(exc)


def test_transport_error_carries_reason() -> None:
    exc = MCPTransportError("flaky", reason="connection refused")
    assert exc.server_name == "flaky"
    assert exc.reason == "connection refused"
    assert "connection refused" in str(exc)


def test_errors_are_raiseable_as_mcp_error() -> None:
    with pytest.raises(MCPError):
        raise MCPServerNotRegistered("x")
    with pytest.raises(MCPError):
        raise MCPToolNotAllowed("x", "y")
    with pytest.raises(MCPError):
        raise MCPServerBudgetExceeded("x", spent_usd=1.0, budget_usd=1.0, retry_after_seconds=1)
