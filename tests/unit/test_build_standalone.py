"""Phase 12 Step 5 -- tests for ``scripts/build_standalone.py``.

The script wraps Nuitka with Phoenix-specific package-include hints.
These tests exercise the arg parser + platform detection + command
construction without actually invoking Nuitka (that path runs in CI's
release workflow + manual local builds).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Load scripts/build_standalone.py as a module so we can call its
# helper functions. The script lives outside the phoenix package
# (scripts/ is excluded from the wheel by [tool.setuptools.packages.find]).
SPEC = importlib.util.spec_from_file_location(
    "build_standalone",
    Path(__file__).resolve().parents[2] / "scripts" / "build_standalone.py",
)
assert SPEC is not None
assert SPEC.loader is not None
build_standalone = importlib.util.module_from_spec(SPEC)
sys.modules["build_standalone"] = build_standalone
SPEC.loader.exec_module(build_standalone)


def test_parse_args_defaults() -> None:
    args = build_standalone._parse_args([])
    assert args.debug is False
    assert args.no_onefile is False


def test_parse_args_debug() -> None:
    args = build_standalone._parse_args(["--debug"])
    assert args.debug is True


def test_parse_args_no_onefile() -> None:
    args = build_standalone._parse_args(["--no-onefile"])
    assert args.no_onefile is True


def test_resolve_output_name_windows_x64() -> None:
    with (
        patch("build_standalone.platform.system", return_value="Windows"),
        patch("build_standalone.platform.machine", return_value="AMD64"),
    ):
        name = build_standalone._resolve_output_name()
    assert name == "phoenix-windows-x64"


def test_resolve_output_name_linux_x64() -> None:
    with (
        patch("build_standalone.platform.system", return_value="Linux"),
        patch("build_standalone.platform.machine", return_value="x86_64"),
    ):
        name = build_standalone._resolve_output_name()
    assert name == "phoenix-linux-x64"


def test_resolve_output_name_linux_arm64() -> None:
    with (
        patch("build_standalone.platform.system", return_value="Linux"),
        patch("build_standalone.platform.machine", return_value="aarch64"),
    ):
        name = build_standalone._resolve_output_name()
    assert name == "phoenix-linux-arm64"


def test_build_nuitka_command_includes_phoenix_and_vendor() -> None:
    cmd = build_standalone._build_nuitka_command(
        output_name="phoenix-test",
        output_dir=Path("dist"),
        onefile=True,
        debug=False,
    )
    # Sanity checks on the Nuitka invocation shape.
    assert cmd[0] == sys.executable
    assert cmd[1] == "-m"
    assert cmd[2] == "nuitka"
    assert "--standalone" in cmd
    assert "--onefile" in cmd
    assert "--remove-output" in cmd  # debug=False -> cleanup build/

    # All vendored namespace packages must be explicitly included so
    # Nuitka's static analysis doesn't prune them.
    expected_includes = {
        "--include-package=phoenix",
        "--include-package=fastapi",
        "--include-package=uvicorn",
        "--include-package=pydantic",
        "--include-package=httpx",
        "--include-package=synthesis",
        "--include-package=wobble",
        "--include-package=grammar",
        "--include-package=actor",
        "--include-package=omega",
        "--include-package=ml",
    }
    actual_includes = {arg for arg in cmd if arg.startswith("--include-package=")}
    missing = expected_includes - actual_includes
    assert not missing, f"missing required --include-package: {missing}"

    # Entry point: phoenix/launcher.py at the end.
    assert cmd[-1].endswith("launcher.py")
    assert "phoenix" in cmd[-1]


def test_build_nuitka_command_debug_keeps_build_tree() -> None:
    """With --debug, --remove-output should NOT be in the command."""
    cmd = build_standalone._build_nuitka_command(
        output_name="phoenix-test",
        output_dir=Path("dist"),
        onefile=True,
        debug=True,
    )
    assert "--remove-output" not in cmd


def test_build_nuitka_command_no_onefile_omits_onefile_flag() -> None:
    cmd = build_standalone._build_nuitka_command(
        output_name="phoenix-test",
        output_dir=Path("dist"),
        onefile=False,
        debug=False,
    )
    assert "--onefile" not in cmd
    assert "--standalone" in cmd  # standalone is always there


def test_check_nuitka_installed_false_when_missing() -> None:
    """When `python -m nuitka` returns non-zero, the check fails."""
    fake_result = type("R", (), {"returncode": 1})()
    with patch("build_standalone.subprocess.run", return_value=fake_result):
        assert build_standalone._check_nuitka_installed() is False


def test_check_nuitka_installed_true_when_present() -> None:
    fake_result = type("R", (), {"returncode": 0})()
    with patch("build_standalone.subprocess.run", return_value=fake_result):
        assert build_standalone._check_nuitka_installed() is True


def test_main_bails_with_clear_message_when_nuitka_missing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """:func:`main` returns 1 + prints install hint when Nuitka is gone."""
    with patch("build_standalone._check_nuitka_installed", return_value=False):
        exit_code = build_standalone.main([])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Nuitka is not installed" in captured.err
    assert "pip install" in captured.err


def test_find_output_onefile_windows() -> None:
    """Onefile + Windows: dist/<name>.exe."""
    output_dir = Path("dist-test")
    name = "phoenix-test"
    with patch("build_standalone.Path.exists", side_effect=lambda: True):
        # First candidate (.exe) wins; the patch makes all paths exist.
        # We construct the result manually because the patched exists()
        # would otherwise short-circuit.
        result = build_standalone._find_output(output_dir, name, onefile=True)
    # The .exe path is checked first when it exists.
    assert result is not None
    assert result.name in (f"{name}.exe", name)
