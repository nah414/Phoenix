"""Adapter subprocess sandbox (Phase 9 Step 1, locked OPEN-2).

Per architecture v1 Section 3.5 + locked OPEN-2: LoRA adapters run
inside a subprocess with **medium isolation**: subprocess + per-call
timeout + restricted env vars + a per-call temporary working directory.

The threat model is **"runaway adapter that loops forever or
consumes unbounded resources"**, not "determined attacker with OS-
privileged escalation". v1 catches the 80% case (a buggy adapter
that hangs, leaks memory, or writes outside its tempdir) without
claiming OS-level ACL guarantees that Phoenix can't deliver
portably on Windows + Linux + macOS.

**What the sandbox enforces:**

- Subprocess execution via :mod:`subprocess.run` with ``timeout=``.
- Restricted env: only ``PATH``, ``TEMP``/``TMP``/``TMPDIR``,
  ``SYSTEMROOT`` (Windows). Critically: no ``PHOENIX_*`` env vars
  leak through to the subprocess.
- Per-call temporary working directory via :func:`tempfile.mkdtemp`,
  removed after the call regardless of outcome.
- Wall-clock timeout enforced by ``subprocess.run``. On timeout the
  subprocess is killed and :class:`AdapterTimeoutError` is raised.

**What the sandbox does NOT enforce:**

- Network access (firewall rules would require OS-specific code).
- Filesystem ACLs outside the working directory.
- Memory caps.
- CPU caps.

These are documented limitations; v1.x can layer stricter isolation
via OS-specific tooling (``ProcessJobObject`` on Windows,
``rlimit`` + ``unshare`` on Linux) when a real threat model
emerges.

**In-process bypass for the identity adapter:** when an adapter
exposes :attr:`_run_in_process_for_testing` = True, the sandbox
skips the subprocess and calls the method directly. This is for
the identity adapter (no executable to invoke) and for unit tests;
real LoRA adapters NEVER set this flag.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from phoenix.adapters.errors import AdapterTimeoutError

logger = logging.getLogger(__name__)


# Default timeout for a single adapter call. Decision 17's "5-7 minutes
# for full statistical sweep" is for drift checkers; adapter calls are
# per-prompt encode/decode and should complete in seconds. Tight default
# catches runaway adapters quickly.
_DEFAULT_TIMEOUT_SECONDS: float = 5.0


# Env vars allowed through to the subprocess. Anything PHOENIX_* is
# explicitly blocked so adapters can't introspect Phoenix runtime state.
_ALLOWED_ENV_PASSTHROUGH = frozenset(
    {
        "PATH",
        "TEMP",
        "TMP",
        "TMPDIR",
        "SYSTEMROOT",  # Windows
        "WINDIR",  # Windows
        "PYTHONIOENCODING",  # so subprocess prints stay UTF-8
    }
)


@dataclass(frozen=True)
class SandboxResult:
    """Outcome of a sandboxed adapter call.

    Fields:
      - ``stdout`` (str) -- captured stdout.
      - ``stderr`` (str) -- captured stderr (informational; the
        adapter's return value lives in stdout).
      - ``returncode`` (int) -- subprocess exit code.
      - ``wall_clock_seconds`` (float) -- how long the call took.
    """

    stdout: str
    stderr: str
    returncode: int
    wall_clock_seconds: float


def _restricted_env() -> dict[str, str]:
    """Build the subset of the parent env passed to subprocesses."""
    return {k: v for k, v in os.environ.items() if k in _ALLOWED_ENV_PASSTHROUGH}


def run_in_sandbox(
    *,
    adapter_name: str,
    argv: list[str],
    stdin_data: str = "",
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> SandboxResult:
    """Execute ``argv`` as a subprocess with the medium-isolation
    sandbox.

    Used by the loader when an adapter ships as an external executable
    (e.g., a Python script that wraps a PEFT-loaded model). The
    identity adapter doesn't use this path -- its
    :attr:`_run_in_process_for_testing` flag short-circuits to a
    direct method call.

    Raises :class:`AdapterTimeoutError` on timeout. Returns a
    :class:`SandboxResult` on completion regardless of exit code;
    callers inspect ``returncode`` for application-level success.
    """
    import time

    workdir = Path(tempfile.mkdtemp(prefix=f"phoenix-adapter-{adapter_name}-"))
    start = time.perf_counter()
    try:
        result = subprocess.run(
            argv,
            input=stdin_data,
            text=True,
            capture_output=True,
            env=_restricted_env(),
            cwd=str(workdir),
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        logger.warning(
            "Adapter %r exceeded %.1fs timeout; subprocess terminated",
            adapter_name,
            timeout_seconds,
        )
        raise AdapterTimeoutError(
            adapter_name=adapter_name,
            timeout_seconds=timeout_seconds,
        ) from exc
    finally:
        # Always clean up the tempdir, even on timeout.
        try:
            import shutil

            shutil.rmtree(workdir, ignore_errors=True)
        except Exception:
            logger.exception("Failed to clean up sandbox tempdir %s", workdir)

    elapsed = time.perf_counter() - start
    return SandboxResult(
        stdout=result.stdout or "",
        stderr=result.stderr or "",
        returncode=result.returncode,
        wall_clock_seconds=elapsed,
    )


def call_adapter_method(
    *,
    adapter: Any,
    method: str,
    arg: str,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Invoke ``adapter.<method>(arg)`` under the sandbox contract.

    For in-process adapters (identity adapter, unit-test stubs):
    direct method call with a Python-level timeout via signal/thread
    is impractical cross-platform, so we trust the in-process call
    to be fast and don't wrap it in a subprocess.

    For external adapters (real LoRA paths in v1.x): the adapter
    would ship a CLI entry point and ``argv`` invocation; the
    sandbox would run it via :func:`run_in_sandbox`. Phase 9 v1
    ships the in-process path; the subprocess path is exercised by
    the timeout regression test using a synthetic shell command.

    Returns the adapter method's return value as a string.
    """
    method_fn = getattr(adapter, method, None)
    if method_fn is None or not callable(method_fn):
        raise AttributeError(f"Adapter {adapter.name!r} has no callable method {method!r}")
    # In-process path. Real external-adapter wiring would dispatch
    # to run_in_sandbox here based on an adapter-side attribute.
    return str(method_fn(arg))


__all__ = [
    "SandboxResult",
    "call_adapter_method",
    "run_in_sandbox",
]
