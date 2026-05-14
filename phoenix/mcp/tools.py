"""Phoenix MCP tool implementations (Phase 9 Step 9).

Each tool is a small callable that takes a typed parameter dict
and returns a JSON-serialisable result. Tools are deliberately
**pure-Python functions** (not :class:`FastMCP` decorators) so
they can be unit-tested without spinning up the MCP framework.
:mod:`phoenix.mcp.server` is the wiring layer that registers
these with :class:`FastMCP`.

Tool contract:

- ``client`` -- a :class:`CLIHTTPClient` (same one the CLI uses).
- Tools dispatch to the daemon over HTTP; auth + safety gate +
  audit run on the daemon side, identical to CLI requests.
- Tools may also read local CLI state (e.g., task cache for
  ``phoenix_task_get`` + ``phoenix_provenance_get``).
- Return values are JSON-serialisable Python (dict / list / str
  / number / bool / None).

Eight v1 tools per Section 5.5:

  phoenix_task_submit
  phoenix_task_get
  phoenix_task_replay
  phoenix_provenance_get
  phoenix_providers_list
  phoenix_calibration_status
  phoenix_health
  phoenix_audit_verify
"""

from __future__ import annotations

from typing import Any

from phoenix.cli.commands._shared import read_task_cache, write_task_cache
from phoenix.cli.http_client import CLIHTTPClient


# ---------------------------------------------------------------------
# Task lifecycle
# ---------------------------------------------------------------------


def tool_task_submit(
    *,
    client: CLIHTTPClient,
    spec: dict[str, Any],
) -> dict[str, Any]:
    """POST /v1/tasks with the given task spec.

    The response (Result envelope) is also cached locally so
    :func:`tool_task_get` and :func:`tool_provenance_get` can
    surface it offline.
    """
    response = client.post("/v1/tasks", json_body=spec)
    if isinstance(response, dict):
        task_id = _extract_task_id(response)
        if task_id:
            cache = read_task_cache()
            cache[task_id] = response
            write_task_cache(cache)
    return response if isinstance(response, dict) else {"raw": response}


def tool_task_get(
    *,
    client: CLIHTTPClient,
    task_id: str,
) -> dict[str, Any]:
    """Return the cached Result envelope for ``task_id``.

    Phase 9 v1 has no daemon GET /v1/tasks/{id} endpoint, so the
    MCP tool reads from the same per-user cache the CLI uses.
    Returns ``{"error": "..."}``  on cache miss so the caller
    surfaces it as a tool-level error rather than a transport
    failure.
    """
    del client  # unused -- local cache only
    cache = read_task_cache()
    record = cache.get(task_id)
    if record is None:
        return {"error": f"no cached envelope for task_id={task_id!r}"}
    return dict(record) if isinstance(record, dict) else {"raw": record}


def tool_task_replay(
    *,
    client: CLIHTTPClient,
    task_id: str,
    reproducibility_mode: str = "strict",
) -> dict[str, Any]:
    """POST /v1/tasks/{task_id}/replay."""
    response = client.post(
        f"/v1/tasks/{task_id}/replay",
        json_body={"reproducibility_mode": reproducibility_mode},
    )
    return response if isinstance(response, dict) else {"raw": response}


def tool_provenance_get(
    *,
    client: CLIHTTPClient,
    task_id: str,
) -> dict[str, Any]:
    """Return the cached envelope's ``provenance`` block.

    Same cache as :func:`tool_task_get`; surfaces the
    architecturally-correct provenance trace (solver / control /
    orchestrate sub-blocks) rather than the full Result envelope.
    """
    del client  # unused -- local cache only
    cache = read_task_cache()
    record = cache.get(task_id)
    if record is None:
        return {"error": f"no cached envelope for task_id={task_id!r}"}
    if not isinstance(record, dict):
        return {"error": "cached envelope has unexpected shape"}
    prov = record.get("provenance")
    if prov is None:
        return {"error": f"cached envelope for {task_id!r} has no provenance"}
    return dict(prov) if isinstance(prov, dict) else {"raw": prov}


# ---------------------------------------------------------------------
# Read-only inspection
# ---------------------------------------------------------------------


def tool_providers_list(*, client: CLIHTTPClient) -> dict[str, Any]:
    """GET /v1/admin/providers/health-history.

    Admin-only on the daemon side; non-admin actors will get a
    CLIHTTPError surfaced as a tool-level error.
    """
    response = client.get("/v1/admin/providers/health-history")
    return response if isinstance(response, dict) else {"raw": response}


def tool_calibration_status(*, client: CLIHTTPClient) -> dict[str, Any]:
    """GET /v1/admin/calibration/detail (admin-only)."""
    response = client.get("/v1/admin/calibration/detail")
    return response if isinstance(response, dict) else {"raw": response}


def tool_health(*, client: CLIHTTPClient) -> dict[str, Any]:
    """GET /v1/health (open to any actor)."""
    response = client.get("/v1/health")
    return response if isinstance(response, dict) else {"raw": response}


def tool_audit_verify(*, client: CLIHTTPClient) -> dict[str, Any]:
    """GET /v1/audit/ledger/verify -- chain integrity check."""
    response = client.get("/v1/audit/ledger/verify")
    return response if isinstance(response, dict) else {"raw": response}


# ---------------------------------------------------------------------
# Registry: tool name -> implementation
# ---------------------------------------------------------------------


# Map MCP tool name (the public API surface) to the in-module function.
# :mod:`phoenix.mcp.server` consumes this when registering tools with
# FastMCP, so all callers see the same exhaustive list.
TOOL_REGISTRY = {
    "phoenix_task_submit": tool_task_submit,
    "phoenix_task_get": tool_task_get,
    "phoenix_task_replay": tool_task_replay,
    "phoenix_provenance_get": tool_provenance_get,
    "phoenix_providers_list": tool_providers_list,
    "phoenix_calibration_status": tool_calibration_status,
    "phoenix_health": tool_health,
    "phoenix_audit_verify": tool_audit_verify,
}


def _extract_task_id(response: dict[str, Any]) -> str | None:
    """Pull the task_id out of a Result envelope (best-effort)."""
    if "task_id" in response and isinstance(response["task_id"], str):
        return response["task_id"]
    provenance = response.get("provenance")
    if isinstance(provenance, dict) and isinstance(provenance.get("task_id"), str):
        return str(provenance["task_id"])
    return None


__all__ = [
    "TOOL_REGISTRY",
    "tool_audit_verify",
    "tool_calibration_status",
    "tool_health",
    "tool_provenance_get",
    "tool_providers_list",
    "tool_task_get",
    "tool_task_replay",
    "tool_task_submit",
]
