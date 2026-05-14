"""Phase 12 Step 2 -- unit tests for :mod:`phoenix.launcher`.

The launcher orchestrates the two-process model (Phoenix daemon +
NATS) per Section 1 Decision 33. These tests exercise the argument
parser + helper functions without actually spawning the daemon / NATS
(those land in the distribution-acceptance battery in Step 9, which
runs in CI but not the unit-test sweep).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from phoenix import launcher


def test_parse_args_defaults() -> None:
    args = launcher._parse_args([])
    assert args.port == 8003
    assert args.host == "127.0.0.1"
    assert args.nats_port == 4222
    assert args.nats_monitor_port == 8222
    assert args.external_daemon is False
    assert args.external_nats is False
    assert args.no_browser is False
    assert isinstance(args.nats_store, Path)
    assert args.nats_store.parts[-2:] == ("runtime", "nats")


def test_parse_args_external_daemon() -> None:
    args = launcher._parse_args(["--external-daemon"])
    assert args.external_daemon is True


def test_parse_args_external_nats() -> None:
    args = launcher._parse_args(["--external-nats"])
    assert args.external_nats is True


def test_parse_args_no_browser() -> None:
    args = launcher._parse_args(["--no-browser"])
    assert args.no_browser is True


def test_parse_args_custom_port() -> None:
    args = launcher._parse_args(["--port", "9999", "--host", "0.0.0.0"])
    assert args.port == 9999
    assert args.host == "0.0.0.0"


def test_parse_args_version_exits(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        launcher._parse_args(["--version"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "phoenix" in captured.out
    assert "1.0.0" in captured.out  # don't pin to .dev12 -- test survives version bumps


def test_spawn_nats_returns_none_when_missing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """If nats-server isn't on PATH, the launcher logs and returns None."""
    with patch("phoenix.launcher.shutil.which", return_value=None):
        result = launcher._spawn_nats(
            port=4222,
            monitor_port=8222,
            store_dir=Path("/tmp/nats-test-store"),
        )
    assert result is None
    captured = capsys.readouterr()
    assert "nats-server not found" in captured.err
    assert "continuing without NATS" in captured.err


def test_wait_for_daemon_returns_true_on_200() -> None:
    """:func:`_wait_for_daemon` succeeds when /v1/health returns 200."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    with patch("phoenix.launcher.httpx.get", return_value=mock_response):
        assert launcher._wait_for_daemon("http://127.0.0.1:8003") is True


def test_wait_for_daemon_returns_false_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If /v1/health never returns 200, :func:`_wait_for_daemon` times out."""
    # Shrink the timeout so the test stays fast.
    monkeypatch.setattr(launcher, "HEALTH_POLL_TIMEOUT_S", 0.3)
    monkeypatch.setattr(launcher, "HEALTH_POLL_INTERVAL_S", 0.1)
    mock_response = MagicMock()
    mock_response.status_code = 503
    with patch("phoenix.launcher.httpx.get", return_value=mock_response):
        assert launcher._wait_for_daemon("http://127.0.0.1:8003") is False


def test_wait_for_daemon_handles_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connection error during health probe doesn't crash the launcher."""
    import httpx as httpx_module

    monkeypatch.setattr(launcher, "HEALTH_POLL_TIMEOUT_S", 0.3)
    monkeypatch.setattr(launcher, "HEALTH_POLL_INTERVAL_S", 0.1)

    def raise_connect_error(*args: object, **kwargs: object) -> None:
        raise httpx_module.ConnectError("daemon not ready")

    with patch("phoenix.launcher.httpx.get", side_effect=raise_connect_error):
        assert launcher._wait_for_daemon("http://127.0.0.1:8003") is False


def test_stop_children_skips_already_exited(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A child that's already exited (poll() returns non-None) is skipped."""
    proc = MagicMock()
    proc.poll.return_value = 0  # exited
    launcher._stop_children([proc])
    proc.terminate.assert_not_called()


def test_stop_children_terminates_live_child() -> None:
    """A live child (poll() returns None) gets terminate()."""
    proc = MagicMock()
    proc.poll.return_value = None  # still running
    proc.wait.return_value = 0
    launcher._stop_children([proc])
    # On Windows, send_signal(CTRL_BREAK_EVENT); on Unix, terminate().
    # We assert at least one of the two was called.
    assert proc.terminate.called or proc.send_signal.called


def test_module_main_attribute() -> None:
    """``phoenix.__main__`` exposes the launcher's main as the module entry."""
    import phoenix.__main__ as phoenix_main

    assert phoenix_main.main is launcher.main
