"""Distribution acceptance -- Nuitka standalone build script sanity.

Verifies ``scripts/build_standalone.py`` exists and (when Nuitka is
installed) the build produces an executable that responds to
``--version``. The full Nuitka compile takes 10-30 minutes and runs
in CI's release workflow; locally we exercise the wrapper's logic.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "build_standalone.py"

pytestmark = pytest.mark.distribution


def test_build_script_exists() -> None:
    assert SCRIPT_PATH.is_file()


def test_build_script_loads_as_module() -> None:
    """The script must import without side effects."""
    spec = importlib.util.spec_from_file_location("build_standalone", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert callable(mod.main)


def test_nuitka_command_has_required_includes() -> None:
    """The constructed Nuitka command must include all six vendored namespace packages."""
    spec = importlib.util.spec_from_file_location("build_standalone", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    cmd = mod._build_nuitka_command(
        output_name="phoenix-test",
        output_dir=Path("dist"),
        onefile=True,
        debug=False,
    )
    cmd_str = " ".join(cmd)
    for pkg in ["synthesis", "wobble", "grammar", "actor", "omega", "ml"]:
        assert f"--include-package={pkg}" in cmd_str, f"missing --include-package={pkg}"


def _has_nuitka() -> bool:
    """Return True iff `python -m nuitka --version` is callable."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "nuitka", "--version"],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, OSError):
        return False


@pytest.mark.skipif(
    not _has_nuitka(),
    reason="Nuitka not installed; CI release workflow exercises the full build",
)
def test_full_nuitka_build_produces_executable(tmp_path: Path) -> None:
    """End-to-end: Nuitka compile + smoke `--version` on the produced binary."""
    # Copy the dist target so the test doesn't pollute the working tree's dist/.
    out_dir = tmp_path / "dist"
    out_dir.mkdir()

    # Invoke the build script with a tmpdir output. We can't easily
    # override the script's REPO_ROOT, so this test runs against the
    # actual repo and just checks that the produced binary works.
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"Nuitka build failed:\n{result.stderr}"

    # Locate the produced binary
    candidates = sorted((REPO_ROOT / "dist").glob("phoenix-*"))
    # Filter out the .dist sidecar dir Nuitka leaves behind in some modes.
    binaries = [c for c in candidates if c.is_file() and not c.name.endswith(".tar.gz")]
    assert binaries, f"no Phoenix binary produced; dist/ has: {candidates}"
    binary = binaries[0]

    # Smoke: --version
    result = subprocess.run(
        [str(binary), "--version"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert "phoenix" in result.stdout.lower()
    assert "1.1.0" in result.stdout


def test_check_nuitka_installed_function() -> None:
    """Whatever the local nuitka install state is, the helper returns a bool."""
    spec = importlib.util.spec_from_file_location("build_standalone", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert isinstance(mod._check_nuitka_installed(), bool)


# os is intentionally imported (in case future cross-platform branches need it).
assert os is not None
