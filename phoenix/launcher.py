"""Phoenix launcher -- standalone-binary entry + bundled-daemon orchestration.

Per architecture v1 Section 1 Decision 33, a solo Phoenix install boots
**two processes** (Phoenix daemon + NATS JetStream) under a single
launcher. Per Section 5.4 + Section 11.3.3 RESOLVED, the standalone
binary bundles the daemon by default; the ``--external-daemon`` flag
is the opt-out for sysadmins running Phoenix's daemon under systemd /
nssm / docker-compose separately.

This module is the Nuitka build target (``scripts/build_standalone.py``)
and the ``python -m phoenix`` entry. The module deliberately stays
synchronous (no asyncio) because Nuitka's static-analysis pass has
historically been brittle around runtime-discovered coroutines; the
launcher itself doesn't need async concurrency -- it spawns two child
processes and waits.

CLI flags (mirrors :mod:`phoenix.api.__main__` where applicable):

- ``--port <int>``         -- daemon port (default 8003, Section 5.4)
- ``--host <str>``         -- daemon bind (default 127.0.0.1)
- ``--external-daemon``    -- skip spawning the daemon; assume one
  reachable at ``http://<host>:<port>/v1/health``. The launcher still
  health-probes before opening the docs URL.
- ``--external-nats``      -- skip spawning NATS; assume one reachable
  at ``$PHOENIX_NATS_URL`` (default ``nats://127.0.0.1:4222``). Phoenix
  itself runs cleanly without NATS (the queue module gracefully
  degrades to SQLite-backed task ingest); this flag is the explicit
  opt-out for installs that want a single-process daemon.
- ``--nats-port <int>``    -- NATS client port (default 4222)
- ``--nats-store <path>``  -- JetStream store dir (default
  ``~/.phoenix/runtime/nats``)
- ``--no-browser``         -- don't open the docs URL after boot;
  useful for headless servers / SSH sessions.
- ``--version``            -- print version + exit 0.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from types import FrameType

import httpx

from phoenix._internal.version import __version__

EXIT_OK = 0
EXIT_USAGE_ERROR = 2
EXIT_DAEMON_UNREACHABLE = 3
EXIT_BOOT_FAILED = 4

DEFAULT_PORT = 8003
DEFAULT_HOST = "127.0.0.1"
DEFAULT_NATS_PORT = 4222
DEFAULT_NATS_MONITOR_PORT = 8222
HEALTH_POLL_TIMEOUT_S = 30.0
HEALTH_POLL_INTERVAL_S = 0.5


def main(argv: list[str] | None = None) -> int:
    """Top-level launcher.

    Returns process exit code; the console-script wrapper calls
    :func:`sys.exit` with the result.
    """
    args = _parse_args(argv)

    # Track child processes so the shutdown handler can clean up.
    children: list[subprocess.Popen[bytes]] = []

    def shutdown(signum: int, _frame: FrameType | None) -> None:
        print(f"\nphoenix launcher: caught signal {signum}, stopping children...")
        _stop_children(children)
        sys.exit(EXIT_OK)

    # SIGINT on every platform; SIGTERM on Unix (Windows has no SIGTERM
    # at the signal-handler level but subprocess.terminate() still works).
    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    # 1. Start NATS unless --external-nats. NATS is optional; if the
    #    binary isn't on PATH, log a warning and continue (Phoenix
    #    degrades cleanly to SQLite-only task ingest).
    if not args.external_nats:
        nats_proc = _spawn_nats(
            port=args.nats_port,
            monitor_port=args.nats_monitor_port,
            store_dir=args.nats_store,
        )
        if nats_proc is not None:
            children.append(nats_proc)
            # NATS needs a moment to bind sockets before the daemon's
            # queue module tries to connect.
            time.sleep(2.0)

    # 2. Start the daemon unless --external-daemon.
    if not args.external_daemon:
        daemon_proc = _spawn_daemon(host=args.host, port=args.port, nats_port=args.nats_port)
        if daemon_proc is None:
            print("phoenix launcher: failed to spawn daemon", file=sys.stderr)
            _stop_children(children)
            return EXIT_BOOT_FAILED
        children.append(daemon_proc)

    # 3. Health-probe the daemon (regardless of who owns it).
    daemon_url = f"http://{args.host}:{args.port}"
    if not _wait_for_daemon(daemon_url):
        print(
            f"phoenix launcher: daemon at {daemon_url} did not become healthy "
            f"within {HEALTH_POLL_TIMEOUT_S}s",
            file=sys.stderr,
        )
        _stop_children(children)
        return EXIT_DAEMON_UNREACHABLE

    print(f"phoenix launcher: daemon healthy at {daemon_url}/v1/health")

    # 4. Open docs URL in default browser unless --no-browser.
    if not args.no_browser:
        docs_url = f"{daemon_url}/docs"
        print(f"phoenix launcher: opening docs at {docs_url}")
        try:
            webbrowser.open(docs_url)
        except Exception as exc:  # pragma: no cover - browser-side failure
            print(f"phoenix launcher: could not open browser ({exc}); docs at {docs_url}")

    # 5. If we own the daemon, wait on it (so Ctrl+C cleanup runs).
    #    If --external-daemon, we exit cleanly after the docs-open hint.
    if args.external_daemon:
        print("phoenix launcher: --external-daemon set; exiting (daemon stays running).")
        return EXIT_OK

    print("phoenix launcher: Phoenix v1 running. Press Ctrl+C to stop.")
    daemon_proc = children[-1]  # daemon is the last one we appended
    try:
        daemon_proc.wait()
    except KeyboardInterrupt:
        pass

    _stop_children(children)
    return EXIT_OK


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="phoenix",
        description=(
            "Phoenix v1 launcher -- bundled daemon + NATS bootstrap per Section 1 Decision 33."
        ),
    )
    parser.add_argument("--version", action="version", version=f"phoenix {__version__}")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Daemon port (default {DEFAULT_PORT}, Section 5.4).",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=DEFAULT_HOST,
        help=f"Daemon bind host (default {DEFAULT_HOST}, loopback only).",
    )
    parser.add_argument(
        "--external-daemon",
        action="store_true",
        help="Skip spawning the daemon; assume one reachable at --host:--port.",
    )
    parser.add_argument(
        "--external-nats",
        action="store_true",
        help="Skip spawning NATS; assume one reachable at --nats-port.",
    )
    parser.add_argument(
        "--nats-port",
        type=int,
        default=DEFAULT_NATS_PORT,
        help=f"NATS client port (default {DEFAULT_NATS_PORT}).",
    )
    parser.add_argument(
        "--nats-monitor-port",
        type=int,
        default=DEFAULT_NATS_MONITOR_PORT,
        help=f"NATS monitoring HTTP port (default {DEFAULT_NATS_MONITOR_PORT}).",
    )
    parser.add_argument(
        "--nats-store",
        type=Path,
        default=Path.home() / ".phoenix" / "runtime" / "nats",
        help="JetStream store dir (default ~/.phoenix/runtime/nats).",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't open the docs URL after boot (headless servers / SSH).",
    )
    return parser.parse_args(argv)


def _spawn_nats(
    *,
    port: int,
    monitor_port: int,
    store_dir: Path,
) -> subprocess.Popen[bytes] | None:
    """Spawn ``nats-server --jetstream``; return the Popen or None.

    Returns None if nats-server isn't on PATH; the caller logs a
    warning and continues (Phoenix degrades cleanly without NATS).
    """
    nats_bin = shutil.which("nats-server")
    if nats_bin is None:
        print(
            "phoenix launcher: nats-server not found on PATH; continuing without NATS.\n"
            "  Install via: winget install NATSAuthors.NATSServer (Windows)\n"
            "  Or: see https://docs.nats.io/running-a-nats-service/introduction/installation",
            file=sys.stderr,
        )
        return None

    store_dir.mkdir(parents=True, exist_ok=True)
    print(
        f"phoenix launcher: starting NATS (port {port}, monitor {monitor_port}, store {store_dir})"
    )
    return subprocess.Popen(
        [
            nats_bin,
            "--jetstream",
            "--store_dir",
            str(store_dir),
            "--port",
            str(port),
            "--http_port",
            str(monitor_port),
        ],
        # On Windows, CREATE_NEW_PROCESS_GROUP lets us send Ctrl-Break to
        # the child without killing our own process. On Unix the default
        # process group behavior is fine.
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )


def _spawn_daemon(
    *,
    host: str,
    port: int,
    nats_port: int,
) -> subprocess.Popen[bytes] | None:
    """Spawn ``python -m phoenix.api --host <host> --port <port>``."""
    print(f"phoenix launcher: starting Phoenix daemon at {host}:{port}")
    env = os.environ.copy()
    # Phoenix's queue module picks up PHOENIX_NATS_URL at boot.
    env.setdefault("PHOENIX_NATS_URL", f"nats://127.0.0.1:{nats_port}")
    return subprocess.Popen(
        [sys.executable, "-m", "phoenix.api", "--host", host, "--port", str(port)],
        env=env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )


def _wait_for_daemon(daemon_url: str) -> bool:
    """Poll ``/v1/health`` until 200 OK or timeout."""
    deadline = time.monotonic() + HEALTH_POLL_TIMEOUT_S
    last_error: str | None = None
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{daemon_url}/v1/health", timeout=2.0)
            if resp.status_code == 200:
                return True
            last_error = f"status {resp.status_code}"
        except httpx.HTTPError as exc:
            last_error = str(exc)
        time.sleep(HEALTH_POLL_INTERVAL_S)
    if last_error:
        print(f"phoenix launcher: last health-probe error: {last_error}", file=sys.stderr)
    return False


def _stop_children(children: list[subprocess.Popen[bytes]]) -> None:
    """Send SIGTERM (Unix) / Ctrl-Break (Windows) to each child, then SIGKILL on holdouts."""
    for proc in children:
        if proc.poll() is not None:
            continue  # already exited
        try:
            if os.name == "nt":
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                proc.terminate()
        except Exception as exc:  # pragma: no cover - terminate-side failure
            print(f"phoenix launcher: terminate({proc.pid}) failed: {exc}", file=sys.stderr)

    # Give children up to 5s to exit gracefully, then escalate.
    deadline = time.monotonic() + 5.0
    for proc in children:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            print(f"phoenix launcher: killing pid {proc.pid} (did not exit after terminate)")
            proc.kill()


__all__ = ["main"]


if __name__ == "__main__":  # pragma: no cover - module is a script entry
    sys.exit(main())
