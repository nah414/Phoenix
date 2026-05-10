"""Integration tests for phoenix.queue (Phase 6b Step 5).

Coverage focus:

- :func:`_find_nats_server` binary discovery precedence:
  ``$NATS_SERVER_PATH`` env var > PATH lookup > :class:`EmbeddedNATSNotFound`.
- :func:`connect_nats` raises a clear :class:`ImportError` (naming the
  ``[nats]`` extra) when ``nats-py`` is not installed.
- Real ``nats-server`` lifecycle exercises -- **gated behind
  ``$PHOENIX_NATS_TEST_ENABLED=1`` so a CI without the binary, or a
  developer with concurrent NATS usage (e.g. another Phoenix daemon
  or unrelated benchmark on port 4222), can safely skip them.**

Critically, **none of the tests in this file launch a real NATS
subprocess unless the env-var opt-in is set**. The default Phase 6b
test run never starts NATS -- protecting parallel work that may use
the standard ports (4222 client, 8222 monitor).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Binary discovery


class TestFindNatsServer:
    """``_find_nats_server`` covers three discovery paths."""

    def test_env_var_with_existing_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_binary = tmp_path / "nats-server"
        fake_binary.write_text("#!/bin/sh\necho fake\n", encoding="utf-8")
        monkeypatch.setenv("NATS_SERVER_PATH", str(fake_binary))

        from phoenix.queue.embedded_runner import _find_nats_server

        assert _find_nats_server() == str(fake_binary)

    def test_env_var_with_missing_path_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        bogus_path = tmp_path / "definitely-not-here"
        monkeypatch.setenv("NATS_SERVER_PATH", str(bogus_path))

        from phoenix.queue.embedded_runner import (
            EmbeddedNATSNotFound,
            _find_nats_server,
        )

        with pytest.raises(EmbeddedNATSNotFound, match="does not exist"):
            _find_nats_server()

    def test_falls_back_to_shutil_which(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NATS_SERVER_PATH", raising=False)
        monkeypatch.setattr(
            shutil,
            "which",
            lambda name: "/usr/local/bin/nats-server" if name == "nats-server" else None,
        )

        from phoenix.queue.embedded_runner import _find_nats_server

        assert _find_nats_server() == "/usr/local/bin/nats-server"

    def test_not_found_raises_with_install_hint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NATS_SERVER_PATH", raising=False)
        monkeypatch.setattr(shutil, "which", lambda name: None)

        from phoenix.queue.embedded_runner import (
            EmbeddedNATSNotFound,
            _find_nats_server,
        )

        with pytest.raises(EmbeddedNATSNotFound) as exc_info:
            _find_nats_server()
        # Install hint covers the three major platforms.
        msg = str(exc_info.value)
        assert "winget" in msg
        assert "brew" in msg
        assert "NATS_SERVER_PATH" in msg


# ---------------------------------------------------------------------------
# Lazy nats-py import


def test_connect_nats_missing_extra_raises_importerror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``nats-py`` is not installed, :func:`connect_nats` raises
    :class:`ImportError` with a message naming the ``[nats]`` extra so
    the user gets a clear install hint."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> Any:
        if name == "nats" or name.startswith("nats."):
            raise ImportError("simulated missing nats-py")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    import asyncio

    from phoenix.queue.nats_client import connect_nats

    with pytest.raises(ImportError, match=r"\[nats\]"):
        asyncio.run(connect_nats(url="nats://does-not-matter:4222"))


def test_connect_nats_url_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the URL precedence rules without actually connecting.

    Patches ``nats.connect`` to record the URL it received."""
    monkeypatch.delenv("PHOENIX_NATS_URL", raising=False)
    monkeypatch.delenv("NATS_URL", raising=False)

    from phoenix.queue.nats_client import _default_url

    # No env vars -> localhost default.
    assert _default_url() == "nats://127.0.0.1:4222"

    # NATS_URL set -> uses it.
    monkeypatch.setenv("NATS_URL", "nats://nats.example.com:4223")
    assert _default_url() == "nats://nats.example.com:4223"

    # PHOENIX_NATS_URL takes precedence over NATS_URL.
    monkeypatch.setenv("PHOENIX_NATS_URL", "nats://phoenix.example.com:4224")
    assert _default_url() == "nats://phoenix.example.com:4224"


# ---------------------------------------------------------------------------
# Public package surface


def test_queue_package_exports() -> None:
    """The package-level ``__init__`` exports the documented surface."""
    from phoenix import queue as q

    assert hasattr(q, "connect_nats")
    assert hasattr(q, "start_embedded_nats")
    assert hasattr(q, "stop_embedded_nats")
    assert hasattr(q, "EmbeddedNATSNotFound")
    assert hasattr(q, "EmbeddedNATSNotReady")
    assert hasattr(q, "EmbeddedNATSHandle")


# ---------------------------------------------------------------------------
# Real NATS lifecycle (opt-in only)


_NATS_TEST_ENABLED = os.environ.get("PHOENIX_NATS_TEST_ENABLED") == "1"


@pytest.mark.skipif(
    not _NATS_TEST_ENABLED,
    reason=(
        "Real NATS lifecycle skipped by default to avoid disturbing concurrent "
        "NATS usage. Set PHOENIX_NATS_TEST_ENABLED=1 to enable. Also requires "
        "nats-server on PATH (or $NATS_SERVER_PATH)."
    ),
)
def test_start_and_stop_real_nats(tmp_path: Path) -> None:
    """End-to-end lifecycle: start, verify alive + monitor URL, stop.

    Uses non-default ports (14222 / 18222) to minimize collision risk
    with any concurrent NATS instance on the standard ports.
    """
    from phoenix.queue.embedded_runner import (
        start_embedded_nats,
        stop_embedded_nats,
    )

    handle = start_embedded_nats(
        store_dir=tmp_path / "nats",
        port=14222,
        monitor_port=18222,
        ready_timeout_seconds=15.0,
    )
    try:
        assert handle.is_alive()
        assert handle.pid() > 0
        assert handle.monitor_url == "http://127.0.0.1:18222/healthz"
        assert handle.store_dir == tmp_path / "nats"
    finally:
        stop_embedded_nats(handle, drain_timeout_seconds=5.0)

    assert not handle.is_alive()
