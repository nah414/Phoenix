"""Distribution acceptance -- Dockerfile + .dockerignore sanity.

Verifies the Dockerfile + .dockerignore at the repo root exist and have
the expected shape. The actual `docker build` is in CI's
.github/workflows/ci.yml + release.yml (Docker isn't always installed
locally; the CI Linux runners have it pre-installed).

These tests do NOT shell out to `docker`; they just lint the
text-level artifacts.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.distribution


def test_dockerfile_exists() -> None:
    assert (REPO_ROOT / "Dockerfile").is_file()


def test_dockerignore_exists() -> None:
    assert (REPO_ROOT / ".dockerignore").is_file()


def test_dockerfile_uses_python_slim_base() -> None:
    """The runtime base image must be python:*-slim per locked scope."""
    content = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    # FROM python:${PYTHON_VERSION}-slim AS runtime
    assert re.search(
        r"FROM\s+python:\$\{PYTHON_VERSION\}-slim\s+AS\s+runtime",
        content,
    ), "runtime stage must use python:<version>-slim"


def test_dockerfile_runs_as_non_root() -> None:
    """The runtime stage drops to USER phoenix (non-root)."""
    content = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"USER\s+phoenix", content), "Dockerfile must drop to non-root user"


def test_dockerfile_verifies_nats_checksum() -> None:
    """The NATS download step must check SHA256 against the upstream SHA256SUMS."""
    content = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "SHA256SUMS" in content, "Dockerfile must verify NATS checksum"
    assert "sha256sum -c" in content, "Dockerfile must invoke sha256sum -c"


def test_dockerfile_exposes_phoenix_and_nats_ports() -> None:
    content = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "EXPOSE 8003 4222" in content, "Dockerfile must EXPOSE both Phoenix + NATS ports"


def test_dockerfile_has_healthcheck() -> None:
    content = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "HEALTHCHECK" in content, "Dockerfile must declare a HEALTHCHECK"
    assert "/v1/health" in content, "HEALTHCHECK must hit /v1/health"


def test_dockerignore_excludes_caches_and_tests() -> None:
    """Crucial excludes that prevent build-context bloat."""
    content = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    for required in [
        ".git",
        "__pycache__",
        "*.pyc",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "tests",
        "evals",
        ".venv",
    ]:
        assert required in content, f".dockerignore missing required entry: {required}"


@pytest.mark.skipif(
    shutil.which("docker") is None,
    reason="docker not installed locally; CI's Linux runner exercises the real build",
)
def test_dockerfile_syntax_via_docker_lint() -> None:
    """If docker is installed, validate the Dockerfile parses cleanly."""
    result = subprocess.run(
        ["docker", "buildx", "build", "--check", str(REPO_ROOT)],
        capture_output=True,
        text=True,
        check=False,
    )
    # buildx --check returns 0 on syntax OK + 1+ on issues. Don't fail
    # this test on warnings -- only on hard parse errors. We look at
    # the stderr for the "FAILED" sentinel that signals a parse error.
    assert "ERROR: failed to solve" not in (result.stderr or ""), result.stderr
