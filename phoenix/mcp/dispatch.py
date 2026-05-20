"""MCP dispatch gate — pre-network checks per 13-D4.

Per Phase 13 build guide Step 6 + 13-D4 (locked 2026-05-18):
Phoenix-as-MCP-client may ONLY dispatch to registered MCP servers,
ONLY for tools in the server's ``allowed_tools`` list, ONLY within
the server's per-day budget. This module ships the gate function
that enforces all three checks BEFORE any network operation.

**Discipline:** every code path that opens a connection to an MCP
server MUST call :func:`check_mcp_dispatch` first. If
:func:`check_mcp_dispatch` raises, NO network call happens. This is
the load-bearing security invariant of 13-D4.

The function returns the :class:`MCPServerSpec` on success so the
caller has the validated spec in hand without a second
registry lookup.
"""

from __future__ import annotations

import logging
from typing import Callable

from phoenix.mcp.errors import (
    MCPServerBudgetExceeded,
    MCPServerNotRegistered,
    MCPToolNotAllowed,
)
from phoenix.mcp.server_registry import MCPServerRegistry, get_registry
from phoenix.mcp.server_spec import MCPServerSpec

log = logging.getLogger(__name__)


# Budget callback hook signature. The cost-ceiling engine (Phase 10)
# wires its per-server 24h accumulator in via this signature. When
# unset, the budget check is a no-op (Phase 13 Step 6 ships the gate;
# Phase 10 integration extends in a follow-up).
#
# Returns the (spent_usd, retry_after_seconds) tuple iff the budget
# is exceeded; otherwise returns None.
BudgetCheckFn = Callable[[str, float], tuple[float, int] | None]

_budget_check_hook: BudgetCheckFn | None = None


def set_budget_check_hook(hook: BudgetCheckFn | None) -> None:
    """Install a budget-check callback.

    Phase 10's cost-ceiling engine registers a callback that consults
    the 24h-window accumulator for the given server. ``None``
    disables the budget check (Step 6 default; Phase 10 integration
    enables).
    """
    global _budget_check_hook
    _budget_check_hook = hook


def check_mcp_dispatch(
    server_name: str,
    *,
    tool_name: str,
    registry: MCPServerRegistry | None = None,
) -> MCPServerSpec:
    """Pre-dispatch gate: verify registration + allowlist + budget.

    Args:
        server_name: The MCP server name from the task's routing hint.
        tool_name: The tool the caller intends to invoke.
        registry: Optional explicit registry (tests use a fresh
            registry; production uses the module-level singleton via
            :func:`get_registry`).

    Returns:
        The validated :class:`MCPServerSpec` if all three checks pass.

    Raises:
        MCPServerNotRegistered: ``server_name`` not in the registry.
        MCPToolNotAllowed: ``tool_name`` not in the spec's
            ``allowed_tools`` list.
        MCPServerBudgetExceeded: the budget-check hook returned a
            non-None tuple (budget exceeded; ``Retry-After`` value
            included).

    The check order is intentional: registration first (cheapest +
    binary), tool allowlist second (still local), budget last
    (consults the cost-ledger which may be a state-backend round
    trip).
    """
    reg = registry if registry is not None else get_registry()

    spec = reg.get(server_name)
    if spec is None:
        raise MCPServerNotRegistered(server_name)

    if not spec.tool_allowed(tool_name):
        raise MCPToolNotAllowed(server_name, tool_name)

    if _budget_check_hook is not None:
        result = _budget_check_hook(server_name, spec.max_budget_usd_per_day)
        if result is not None:
            spent_usd, retry_after_seconds = result
            raise MCPServerBudgetExceeded(
                server_name,
                spent_usd=spent_usd,
                budget_usd=spec.max_budget_usd_per_day,
                retry_after_seconds=retry_after_seconds,
            )

    return spec


def reset_budget_check_hook() -> None:
    """Test helper: clear the budget-check hook."""
    global _budget_check_hook
    _budget_check_hook = None
