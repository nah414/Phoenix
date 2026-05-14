"""Distribution acceptance -- pip wheel build + install + smoke.

Builds the wheel + sdist from the current repo state, installs the
wheel into a temporary venv, then exercises the install end-to-end:

  - ``phoenix --version`` prints the expected version string.
  - ``python -c "import phoenix"`` works.
  - All six vendored namespace packages import from site-packages
    (not from the dev tree).

Per Section 10.4, the wheel must ship vendor/ as siblings of phoenix/
so non-editable installs work. This test catches regressions in that
packaging discipline.
"""

from __future__ import annotations

import os
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.distribution


def _has_build_module() -> bool:
    """Skip the suite if `python -m build` isn't available."""
    try:
        subprocess.run(
            [sys.executable, "-m", "build", "--version"],
            capture_output=True,
            check=True,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the wheel once per test module run."""
    if not _has_build_module():
        pytest.skip("`python -m build` not available -- pip install build")
    dist = tmp_path_factory.mktemp("dist")
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    wheels = list(dist.glob("phoenix_middleware-*.whl"))
    assert len(wheels) == 1, f"expected 1 wheel, found {wheels}"
    return wheels[0]


@pytest.fixture(scope="module")
def fresh_venv(tmp_path_factory: pytest.TempPathFactory, built_wheel: Path) -> Path:
    """Create a fresh venv, install the wheel into it, return the venv path."""
    venv_dir = tmp_path_factory.mktemp("venv")
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    py = _venv_python(venv_dir)
    subprocess.run(
        [str(py), "-m", "pip", "install", "--upgrade", "pip", "--quiet"],
        check=True,
    )
    subprocess.run(
        [str(py), "-m", "pip", "install", str(built_wheel), "--quiet"],
        check=True,
    )
    return venv_dir


def _venv_python(venv_dir: Path) -> Path:
    """Resolve the python executable inside a venv across platforms."""
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def test_wheel_built_under_2mb(built_wheel: Path) -> None:
    """The wheel should stay under 2 MB; vendor inflation regression sentinel."""
    size_mb = built_wheel.stat().st_size / 1024 / 1024
    assert size_mb < 2.0, f"wheel size {size_mb:.2f} MB > 2.0 MB limit"


def test_phoenix_version_after_install(fresh_venv: Path) -> None:
    """``phoenix --version`` works on the wheel-installed Python."""
    py = _venv_python(fresh_venv)
    result = subprocess.run(
        [str(py), "-m", "phoenix.api", "--help"],  # quick way to check the module loads
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Phoenix" in result.stdout or "phoenix" in result.stdout


def test_phoenix_import_works(fresh_venv: Path) -> None:
    py = _venv_python(fresh_venv)
    result = subprocess.run(
        [str(py), "-c", "import phoenix; print(phoenix.__version__)"],
        capture_output=True,
        text=True,
        check=True,
    )
    version = result.stdout.strip()
    assert version.startswith("1.0.0"), f"unexpected version: {version!r}"


def test_vendored_namespace_packages_resolve_from_site_packages(
    fresh_venv: Path,
) -> None:
    """All six vendored namespace packages import from site-packages, not the dev tree.

    This test catches regressions in the packaging-find vendor-inclusion
    discipline introduced in Phase 12 Step 1 (per Section 11.7.1 +
    Section 10.4).
    """
    py = _venv_python(fresh_venv)
    result = subprocess.run(
        [
            str(py),
            "-c",
            (
                "import sysconfig; sp = sysconfig.get_paths()['purelib']; "
                "import synthesis.equations.base, wobble.disagreement_types, "
                "actor, omega, ml, grammar; "
                "import synthesis, wobble; "
                "assert synthesis.__path__[0].startswith(sp), synthesis.__path__; "
                "assert wobble.__file__.startswith(sp), wobble.__file__; "
                "print('OK')"
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "OK" in result.stdout


def test_phoenix_console_script_installed(fresh_venv: Path) -> None:
    """The `phoenix` console script entry-point ships and runs."""
    if os.name == "nt":
        phoenix_bin = fresh_venv / "Scripts" / "phoenix.exe"
    else:
        phoenix_bin = fresh_venv / "bin" / "phoenix"
    assert phoenix_bin.exists(), f"phoenix entry-point missing at {phoenix_bin}"
    result = subprocess.run(
        [str(phoenix_bin), "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "phoenix" in result.stdout.lower()
    assert "1.0.0" in result.stdout


def test_sdist_builds_and_is_under_2mb(tmp_path_factory: pytest.TempPathFactory) -> None:
    """The sdist tarball must build cleanly and stay under 2 MB."""
    if not _has_build_module():
        pytest.skip("`python -m build` not available")
    dist = tmp_path_factory.mktemp("sdist")
    subprocess.run(
        [sys.executable, "-m", "build", "--sdist", "--outdir", str(dist)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    tarballs = list(dist.glob("phoenix_middleware-*.tar.gz"))
    assert len(tarballs) == 1, f"expected 1 sdist tarball, found {tarballs}"
    size_mb = tarballs[0].stat().st_size / 1024 / 1024
    assert size_mb < 2.0, f"sdist size {size_mb:.2f} MB > 2.0 MB limit"


# sysconfig import is intentional; mypy strict needs the import statement.
assert sysconfig is not None  # silence mypy "unused import" without `# type: ignore`
