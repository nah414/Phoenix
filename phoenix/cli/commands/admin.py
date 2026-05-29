"""``phoenix admin ...`` command group (Phase 9 Step 8).

Subcommands:

- ``kill-switch engage --reason TEXT`` -- POST
  /v1/admin/kill-switch/engage.
- ``kill-switch release --reason TEXT`` -- POST
  /v1/admin/kill-switch/release.
- ``kill-switch status`` -- GET /v1/admin/kill-switch/status.
- ``health`` -- GET /v1/admin/health/detailed.
- ``governor`` -- GET /v1/admin/governor.
- ``budget`` -- GET /v1/admin/budget.
- ``override <task-id> --disposition {pass|fail|degraded}
  --reason TEXT`` -- POST
  /v1/admin/tasks-pending-review/{task_id}/override.
- ``generate-encryption-key [--name SLUG] [--force]
  [--keys-dir PATH]`` -- Phase 13.x.7 local keygen wrapping
  :func:`phoenix.ledger.keygen.generate_age_keypair`. Does not call
  the daemon.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from phoenix.cli.commands._shared import print_payload
from phoenix.cli.config_loader import CLIConfig
from phoenix.cli.http_client import CLIHTTPClient


def _cmd_kill_switch(
    args: argparse.Namespace,
    _config: CLIConfig,
    client: CLIHTTPClient,
    fmt: str,
) -> int:
    sub = args.admin_kill_switch_subcommand
    if sub == "engage":
        body = {"reason": args.reason}
        response = client.post("/v1/admin/kill-switch/engage", json_body=body)
    elif sub == "release":
        body = {"reason": args.reason}
        response = client.post("/v1/admin/kill-switch/release", json_body=body)
    elif sub == "status":
        response = client.get("/v1/admin/kill-switch/status")
    else:
        print("phoenix admin kill-switch: subcommand required (engage / release / status)")
        return 2
    print_payload(response, fmt)
    return 0


def _cmd_health(
    _args: argparse.Namespace,
    _config: CLIConfig,
    client: CLIHTTPClient,
    fmt: str,
) -> int:
    response = client.get("/v1/admin/health/detailed")
    print_payload(response, fmt)
    return 0


def _cmd_governor(
    _args: argparse.Namespace,
    _config: CLIConfig,
    client: CLIHTTPClient,
    fmt: str,
) -> int:
    response = client.get("/v1/admin/governor")
    print_payload(response, fmt)
    return 0


def _cmd_budget(
    _args: argparse.Namespace,
    _config: CLIConfig,
    client: CLIHTTPClient,
    fmt: str,
) -> int:
    response = client.get("/v1/admin/budget")
    print_payload(response, fmt)
    return 0


def _cmd_override(
    args: argparse.Namespace,
    _config: CLIConfig,
    client: CLIHTTPClient,
    fmt: str,
) -> int:
    body = {
        "disposition": args.disposition,
        "reason": args.reason,
    }
    response = client.post(
        f"/v1/admin/tasks-pending-review/{args.task_id}/override",
        json_body=body,
    )
    print_payload(response, fmt)
    return 0


def _cmd_generate_encryption_key(
    args: argparse.Namespace,
    _config: CLIConfig,
    _client: CLIHTTPClient,
    _fmt: str,
) -> int:
    """``phoenix admin generate-encryption-key`` handler (Phase 13.x.7).

    Generates an age keypair at the conventional Phoenix encryption-keys
    directory and prints a summary to stdout. The call is local — the
    Phoenix daemon is not contacted (the encryptor reads the keypair at
    startup via :func:`encryptor_from_default_layout`).

    Returns:
        0 on success; 1 on :class:`KeyGenPathConflict` (with the
        conflict message and a ``--force`` hint on stderr) or any
        other :class:`KeyGenError` / :class:`ImportError`.
    """
    from phoenix.ledger.keygen import (
        KeyGenError,
        KeyGenPathConflict,
        generate_age_keypair,
    )

    keys_dir: Path | None = None
    if getattr(args, "keys_dir", None):
        keys_dir = Path(args.keys_dir).expanduser().resolve()

    try:
        result = generate_age_keypair(
            keys_dir=keys_dir,
            name=getattr(args, "name", "primary"),
            force=getattr(args, "force", False),
        )
    except KeyGenPathConflict as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(
            "       Pass --force to overwrite, or use --name <slug> for a different filename.",
            file=sys.stderr,
        )
        return 1
    except KeyGenError as exc:
        print(f"error: keygen failed: {exc}", file=sys.stderr)
        return 1
    except ImportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    posix_mode_note = "(mode 0o600)" if sys.platform != "win32" else "(Windows NTFS ACL)"
    print(f"Identity:     {result.identity_path}      {posix_mode_note}")
    print(f"Recipient:    {result.recipient_path}")
    print(f"Fingerprint:  {result.identity_fingerprint}")
    print()
    print("To activate, restart the Phoenix daemon (the encryptor reads keys")
    print("at startup via encryptor_from_default_layout()).")
    return 0


HANDLERS = {
    "kill-switch": _cmd_kill_switch,
    "health": _cmd_health,
    "governor": _cmd_governor,
    "budget": _cmd_budget,
    "override": _cmd_override,
    "generate-encryption-key": _cmd_generate_encryption_key,
}


__all__ = ["HANDLERS"]
