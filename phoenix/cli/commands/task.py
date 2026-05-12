"""``phoenix task ...`` command group (Phase 9 Step 7).

Subcommands:

- ``submit --spec <inline-or-@file>`` -- POST /v1/tasks. The full
  Result envelope is rendered and a copy is written to the per-user
  task cache so ``phoenix task get`` can surface it offline.
- ``get <task-id>`` -- read the task envelope from the local
  cache (Phase 9 v1: the daemon has no GET /v1/tasks/{id}).
- ``replay <task-id> --mode strict`` -- POST
  /v1/tasks/{id}/replay.
- ``stream <task-id>`` -- WebSocket subscription to
  /v1/ws/tasks/{id}/stream. Phase 9 v1 ships this as a
  documented stub (websocket clients land in v1.x); the
  command emits a usage hint pointing at the WS endpoint.
"""

from __future__ import annotations

import argparse
from typing import Any

from phoenix.cli.commands._shared import (
    SpecError,
    load_spec_json,
    print_payload,
    read_task_cache,
    write_task_cache,
)
from phoenix.cli.config_loader import CLIConfig
from phoenix.cli.http_client import CLIHTTPClient


def _cmd_submit(
    args: argparse.Namespace,
    _config: CLIConfig,
    client: CLIHTTPClient,
    fmt: str,
) -> int:
    try:
        body = load_spec_json(args.spec)
    except SpecError as exc:
        print(f"phoenix task submit: {exc}")
        return 2
    if not isinstance(body, dict):
        print("phoenix task submit: spec must be a JSON object")
        return 2
    response = client.post("/v1/tasks", json_body=body)
    print_payload(response, fmt)

    # Cache the response so `task get` can surface it later.
    task_id = _extract_task_id(response)
    if task_id:
        cache = read_task_cache()
        cache[task_id] = response
        write_task_cache(cache)
    return 0


def _cmd_get(
    args: argparse.Namespace,
    _config: CLIConfig,
    _client: CLIHTTPClient,
    fmt: str,
) -> int:
    cache = read_task_cache()
    task_id: str = args.task_id
    if task_id not in cache:
        print(
            f"phoenix task get: no cached envelope for task_id={task_id!r}. "
            f"Phase 9 v1 has no GET /v1/tasks/{{id}} -- the CLI surfaces "
            f"the cached POST response. Submit the task again or wait "
            f"for v1.x's read endpoint."
        )
        return 1
    print_payload(cache[task_id], fmt)
    return 0


def _cmd_replay(
    args: argparse.Namespace,
    _config: CLIConfig,
    client: CLIHTTPClient,
    fmt: str,
) -> int:
    body: dict[str, Any] = {"reproducibility_mode": args.mode}
    response = client.post(
        f"/v1/tasks/{args.task_id}/replay",
        json_body=body,
    )
    print_payload(response, fmt)
    return 0


def _cmd_stream(
    args: argparse.Namespace,
    config: CLIConfig,
    _client: CLIHTTPClient,
    _fmt: str,
) -> int:
    ws_url = (
        config.rest_url.replace("http://", "ws://").replace("https://", "wss://")
        + f"/v1/ws/tasks/{args.task_id}/stream"
    )
    print(
        f"phoenix task stream: WebSocket streaming is not yet wired in "
        f"the Phase 9 v1 CLI. Mint a token via "
        f"`phoenix identity show` (or POST /v1/identity/ws-token directly), "
        f"then connect with any WS client to:\n  {ws_url}?token=<token>"
    )
    return 0


def _extract_task_id(response: Any) -> str | None:
    """Try to locate the task_id in a Result envelope."""
    if not isinstance(response, dict):
        return None
    # Step 7's task_submit response carries task_id at the top level
    if "task_id" in response and isinstance(response["task_id"], str):
        return response["task_id"]
    provenance = response.get("provenance")
    if isinstance(provenance, dict) and isinstance(provenance.get("task_id"), str):
        return str(provenance["task_id"])
    return None


HANDLERS = {
    "submit": _cmd_submit,
    "get": _cmd_get,
    "replay": _cmd_replay,
    "stream": _cmd_stream,
}


__all__ = ["HANDLERS"]
