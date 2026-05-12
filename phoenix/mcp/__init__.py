"""Phoenix MCP server subsystem (Phase 9 Step 9).

Per architecture v1 Section 5.5 + locked OPEN-3 + OPEN-4: Phoenix
ships an MCP (Model Context Protocol) server that exposes the
canonical task-lifecycle surface to IDE-integrated AI clients
(Claude Code, Cursor, Cline). Phase 9 ships **stdio transport
only** (Section 5.5 Decision A) using the official Anthropic
``mcp`` SDK.

The server is a thin shim over the Phoenix REST surface: every
tool call dispatches an HTTP request to the daemon via the same
:class:`CLIHTTPClient` the CLI uses, so the auth + safety-gate
+ audit story is identical between CLI and MCP entry points.

**Eight v1 tools** (Section 5.5 Table):

- ``phoenix_task_submit`` -- POST /v1/tasks
- ``phoenix_task_get`` -- read local task cache
- ``phoenix_task_replay`` -- POST /v1/tasks/{id}/replay
- ``phoenix_provenance_get`` -- read local task cache provenance block
- ``phoenix_providers_list`` -- GET /v1/admin/providers/health-history
- ``phoenix_calibration_status`` -- GET /v1/admin/calibration/detail
- ``phoenix_health`` -- GET /v1/health
- ``phoenix_audit_verify`` -- GET /v1/audit/ledger/verify

The MCP SDK is an **optional dependency**. Importing
:mod:`phoenix.mcp.server` without the ``[mcp]`` extra installed
raises :class:`ImportError` with a clear install message.
"""

from __future__ import annotations

__all__: list[str] = []
