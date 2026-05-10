"""NATS embedded runner (Phase 6b Step 5).

Per architecture v1 Section 1 Decision 33: a solo Phoenix install boots
two processes -- Phoenix daemon + NATS -- managed by the same launcher
script. This module provides the programmatic API the launcher script
calls: locate the ``nats-server`` binary, launch it as a subprocess
with JetStream file-backed storage, wait for it to report ready, and
shut it down gracefully on Phoenix shutdown.

**The Phoenix daemon itself does NOT auto-launch NATS.** Per Decision
33 the launcher script orchestrates both processes; the daemon
connects to whatever is at ``$NATS_URL``. This separation means:

- Tests that use :class:`fastapi.testclient.TestClient` never trigger
  NATS startup (they would otherwise compete for port 4222 with other
  processes).
- SQLite-only installs without the ``nats-server`` binary can boot
  the Phoenix daemon without errors.
- The launcher script's two-process model is explicit and audit-clean
  per the "no Docker required for solo use" goal.

**Binary discovery precedence (per locked open-item 3, 2026-05-10):**

1. ``$NATS_SERVER_PATH`` env var (explicit override).
2. ``shutil.which("nats-server")`` (PATH lookup).
3. Raise :class:`EmbeddedNATSNotFound` with the platform-specific
   install command (``winget`` / ``brew``).

Bundling the binary inside the Phoenix release artifact is deferred to
the ``1.0.0`` release-artifact pipeline (Phase 10).

**JetStream storage:** the runner passes ``--jetstream --store_dir
<path>`` where ``<path>`` defaults to ``~/.phoenix/runtime/nats/``
(sibling to the SQLite state.db). Pass an explicit ``store_dir`` to
override.

**Lifecycle:** :func:`start_embedded_nats` launches the subprocess and
polls the monitoring port (``--http_port``, default 8222) for ready;
returns an opaque :class:`EmbeddedNATSHandle`. :func:`stop_embedded_nats`
calls ``terminate()`` (SIGTERM on Unix; CTRL_BREAK on Windows), waits
up to ``drain_timeout`` seconds, then calls ``kill()`` (SIGKILL) if
needed.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)


class EmbeddedNATSNotFound(Exception):
    """Raised when the ``nats-server`` binary cannot be located.

    The message includes platform-appropriate install instructions and
    notes the ``$NATS_SERVER_PATH`` escape hatch.
    """


class EmbeddedNATSNotReady(Exception):
    """Raised when the launched ``nats-server`` does not respond on its
    monitoring port within the ready timeout."""


def _find_nats_server() -> str:
    """Locate the nats-server binary per the precedence rules above.

    Returns the absolute path as a string. Raises
    :class:`EmbeddedNATSNotFound` if neither env var nor PATH lookup
    yields a usable binary.
    """
    override = os.environ.get("NATS_SERVER_PATH")
    if override:
        if not Path(override).exists():
            raise EmbeddedNATSNotFound(
                f"NATS_SERVER_PATH={override!r} does not exist on disk; "
                f"either fix the env var or unset it to fall back to PATH lookup"
            )
        return override

    found = shutil.which("nats-server")
    if found is None:
        raise EmbeddedNATSNotFound(
            "nats-server binary not found on PATH. Install via:\n"
            "  Windows: winget install nats-io.nats-server\n"
            "  macOS:   brew install nats-server\n"
            "  Linux:   see https://docs.nats.io/running-a-nats-service/introduction/installation\n"
            "Or set $NATS_SERVER_PATH to the binary location. Phase 6b "
            "(open-item 3) deferred bundling to v1.0.0 release."
        )
    return found


def _default_store_dir() -> Path:
    """Default JetStream file-backed storage location."""
    return Path.home() / ".phoenix" / "runtime" / "nats"


class EmbeddedNATSHandle:
    """Opaque handle returned by :func:`start_embedded_nats`.

    Caller passes this to :func:`stop_embedded_nats` for shutdown.
    Exposes :meth:`pid` and :meth:`is_alive` for observability;
    callers should NOT poke at the subprocess directly -- use
    :func:`stop_embedded_nats` for any state transitions.
    """

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        monitor_url: str,
        store_dir: Path,
    ) -> None:
        self._process = process
        self._monitor_url = monitor_url
        self._store_dir = store_dir

    def pid(self) -> int:
        """Subprocess PID. Stable for the runner's lifetime."""
        return self._process.pid

    def is_alive(self) -> bool:
        """Return whether the subprocess is still running."""
        return self._process.poll() is None

    @property
    def monitor_url(self) -> str:
        """The HTTP monitoring URL Phoenix used to confirm readiness."""
        return self._monitor_url

    @property
    def store_dir(self) -> Path:
        """The JetStream file-backed storage directory."""
        return self._store_dir


def start_embedded_nats(
    *,
    store_dir: Path | None = None,
    port: int = 4222,
    monitor_port: int = 8222,
    ready_timeout_seconds: float = 30.0,
) -> EmbeddedNATSHandle:
    """Launch ``nats-server`` as a subprocess with JetStream enabled.

    Blocks until the subprocess's monitoring port reports ready (200
    response on ``/healthz``) or until ``ready_timeout_seconds`` elapses.

    Args:
        store_dir: JetStream file-backed storage directory. Created if
            absent. Defaults to ``~/.phoenix/runtime/nats/``.
        port: NATS client port. Default ``4222`` (NATS standard).
        monitor_port: HTTP monitoring port. Default ``8222`` (NATS
            standard). Used for readiness checks.
        ready_timeout_seconds: Maximum seconds to wait for the subprocess
            to report ready before giving up.

    Returns:
        :class:`EmbeddedNATSHandle` -- pass to :func:`stop_embedded_nats`
        for shutdown.

    Raises:
        EmbeddedNATSNotFound: ``nats-server`` binary not on PATH and no
            ``$NATS_SERVER_PATH`` override set.
        EmbeddedNATSNotReady: subprocess exited prematurely OR failed to
            respond on the monitoring port within the timeout. The
            subprocess is terminated before this raises.
    """
    binary = _find_nats_server()
    resolved_store = store_dir if store_dir is not None else _default_store_dir()
    resolved_store.mkdir(parents=True, exist_ok=True)

    cmd = [
        binary,
        "--jetstream",
        "--store_dir",
        str(resolved_store),
        "--port",
        str(port),
        "--http_port",
        str(monitor_port),
    ]

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    monitor_url = f"http://127.0.0.1:{monitor_port}/healthz"
    deadline = time.monotonic() + ready_timeout_seconds
    poll_interval = 0.2
    last_exception: Exception | None = None

    while time.monotonic() < deadline:
        # Subprocess died early -- fail fast with stderr context.
        if process.poll() is not None:
            try:
                _, stderr_bytes = process.communicate(timeout=2.0)
            except subprocess.TimeoutExpired:
                stderr_bytes = b""
            stderr_msg = stderr_bytes.decode("utf-8", errors="replace")[:500]
            raise EmbeddedNATSNotReady(
                f"nats-server exited with code {process.returncode} before "
                f"reporting ready. stderr: {stderr_msg}"
            )
        try:
            with urllib.request.urlopen(monitor_url, timeout=1.0) as resp:
                if resp.status == 200:
                    logger.info(
                        "embedded NATS ready: pid=%d store=%s monitor=%s",
                        process.pid,
                        resolved_store,
                        monitor_url,
                    )
                    return EmbeddedNATSHandle(
                        process=process,
                        monitor_url=monitor_url,
                        store_dir=resolved_store,
                    )
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            last_exception = exc
        time.sleep(poll_interval)

    # Timeout -- terminate subprocess before raising.
    _terminate_quietly(process)
    raise EmbeddedNATSNotReady(
        f"nats-server did not report ready within {ready_timeout_seconds}s "
        f"on monitor URL {monitor_url}; last connection error: {last_exception!r}"
    )


def stop_embedded_nats(
    handle: EmbeddedNATSHandle,
    *,
    drain_timeout_seconds: float = 10.0,
) -> None:
    """Stop the embedded NATS subprocess gracefully.

    Sends ``terminate()`` (SIGTERM on Unix; CTRL_BREAK_EVENT on Windows
    via :meth:`subprocess.Popen.terminate`), waits up to
    ``drain_timeout_seconds`` for the subprocess to exit, then escalates
    to ``kill()`` (SIGKILL on Unix; TerminateProcess on Windows) if the
    subprocess is still running.

    Idempotent: calling on an already-exited process is a no-op.
    """
    process = handle._process
    if process.poll() is not None:
        logger.debug(
            "stop_embedded_nats: subprocess pid=%d already exited (code=%d)",
            process.pid,
            process.returncode,
        )
        return

    try:
        process.terminate()
        process.wait(timeout=drain_timeout_seconds)
        logger.info("embedded NATS stopped: pid=%d", process.pid)
    except subprocess.TimeoutExpired:
        logger.warning(
            "embedded NATS pid=%d did not exit within %ss; sending SIGKILL",
            process.pid,
            drain_timeout_seconds,
        )
        process.kill()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            logger.error("embedded NATS pid=%d did not exit after SIGKILL", process.pid)


def _terminate_quietly(process: subprocess.Popen[bytes]) -> None:
    """Best-effort subprocess termination used by the ready-poll
    timeout path. Catches exceptions so the caller's
    :class:`EmbeddedNATSNotReady` is the surfaced error, not a
    termination exception."""
    try:
        process.terminate()
        process.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=2.0)
        except Exception:
            logger.exception("Failed to forcibly terminate nats-server subprocess")
    except Exception:
        logger.exception("Failed to terminate nats-server subprocess")
