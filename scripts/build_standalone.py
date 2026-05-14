"""Phoenix v1 standalone-binary builder -- Phase 12 Step 5.

Wraps Nuitka with the flags + plugin hints Phoenix needs to compile its
launcher into a single-file native executable for the host platform.

Per architecture v1 Section 1 Decision 29, the standalone binary is one
of three Phoenix v1 release artifacts (the other two: pip wheel +
Docker image). Per Section 5.4 + Section 11.3.3 RESOLVED, the binary
bundles the daemon by default; --external-daemon is the opt-out.

Usage:
    python scripts/build_standalone.py
    python scripts/build_standalone.py --debug      # keep build/ tree
    python scripts/build_standalone.py --no-onefile # multi-file dist

Output:
    dist/phoenix-<os>-<arch>(.exe)      single executable (onefile)
    dist/phoenix-<os>-<arch>.dist/      sidecar dir (multi-file mode)

The script verifies Nuitka is installed (it's a pip extra, not a main
dep -- Phoenix users don't need Nuitka unless they're building binaries)
and prints a clear "pip install nuitka" hint if it's missing. CI installs
Nuitka as part of the release workflow setup; dev users build wheels +
Docker images without needing Nuitka.

Nuitka considerations Phoenix needs:
  - FastAPI / Pydantic / Uvicorn use runtime module discovery in places;
    --include-package flags ensure their full trees compile in.
  - The vendored namespace packages (synthesis, wobble, grammar, actor,
    omega, ml) must be included explicitly -- Nuitka's static analysis
    doesn't follow the sys.path-injection import pattern from
    phoenix/__init__.py.
  - --onefile bundles into a self-extracting executable; first launch
    extracts to a temp dir (~/.cache/nuitka-onefile on Linux,
    %TEMP%\\nuitka-onefile on Windows) then runs. Subsequent launches
    reuse the cached extraction.
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Packages that need explicit --include-package to survive Nuitka's
# static-analysis pruning. The order doesn't matter; Nuitka takes
# multiple --include-package flags.
INCLUDE_PACKAGES = [
    # Phoenix itself
    "phoenix",
    # Web stack
    "fastapi",
    "starlette",
    "uvicorn",
    "pydantic",
    "pydantic_core",
    "httpx",
    "httpcore",
    # Scientific stack
    "numpy",
    "scipy",
    "yaml",
    # Stdlib + crypto
    "cryptography",
    # Vendored namespaces -- these are PEP 420 implicit namespace
    # packages discovered via the sys.path injection in
    # phoenix/__init__.py, which Nuitka's static analysis won't follow.
    "synthesis",
    "wobble",
    "grammar",
    "actor",
    "omega",
    "ml",
]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if not _check_nuitka_installed():
        print(
            "phoenix build_standalone: Nuitka is not installed.\n"
            "  Install via: pip install 'nuitka>=2.0,<3.0'\n"
            "  Nuitka is a build-time dependency only; Phoenix users who\n"
            "  install via pip wheel or Docker do not need it.",
            file=sys.stderr,
        )
        return 1

    output_name = _resolve_output_name()
    output_dir = REPO_ROOT / "dist"
    output_dir.mkdir(exist_ok=True)

    cmd = _build_nuitka_command(
        output_name=output_name,
        output_dir=output_dir,
        onefile=not args.no_onefile,
        debug=args.debug,
    )

    print("phoenix build_standalone: invoking Nuitka")
    print(" ", " ".join(cmd))
    print()

    result = subprocess.run(cmd, cwd=REPO_ROOT)
    if result.returncode != 0:
        print(
            f"phoenix build_standalone: Nuitka exited with code {result.returncode}",
            file=sys.stderr,
        )
        return result.returncode

    output_path = _find_output(output_dir, output_name, onefile=not args.no_onefile)
    if output_path is None:
        print(
            "phoenix build_standalone: Nuitka reported success but the "
            "expected output file is missing.",
            file=sys.stderr,
        )
        return 2

    print()
    print(f"phoenix build_standalone: built {output_path}")
    print(f"  size: {output_path.stat().st_size / 1024 / 1024:.1f} MB")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="build_standalone",
        description="Compile Phoenix's launcher into a Nuitka standalone binary.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Keep Nuitka's build tree under build/ for inspection.",
    )
    parser.add_argument(
        "--no-onefile",
        action="store_true",
        help=(
            "Emit a multi-file distribution (dist/phoenix-*.dist/) "
            "instead of a single-file executable. Faster cold-start, "
            "larger distribution surface."
        ),
    )
    return parser.parse_args(argv)


def _check_nuitka_installed() -> bool:
    """Return True iff `python -m nuitka --version` works."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "nuitka", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, OSError):
        return False


def _resolve_output_name() -> str:
    """Return the platform-specific output binary name."""
    os_name = platform.system().lower()  # 'windows' | 'linux' | 'darwin'
    arch = platform.machine().lower()  # 'amd64' | 'x86_64' | 'arm64' | ...
    # Normalize arch labels: amd64 + x86_64 -> x64; arm64 + aarch64 -> arm64.
    if arch in ("amd64", "x86_64"):
        arch = "x64"
    elif arch in ("arm64", "aarch64"):
        arch = "arm64"
    return f"phoenix-{os_name}-{arch}"


def _build_nuitka_command(
    *,
    output_name: str,
    output_dir: Path,
    onefile: bool,
    debug: bool,
) -> list[str]:
    """Construct the Nuitka invocation."""
    cmd: list[str] = [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        f"--output-dir={output_dir}",
        f"--output-filename={output_name}",
        # Don't tell users to switch to "deployment mode"; we're already
        # building a distributable.
        "--no-deployment-flag=self-execution",
        # Help Nuitka's progress output stay legible in CI logs.
        "--assume-yes-for-downloads",
    ]
    if onefile:
        cmd.append("--onefile")
    if not debug:
        # Remove the build/ tree after a successful compile.
        cmd.append("--remove-output")

    # Anti-bloat plugins -- pkg-resources for setuptools-style data,
    # multiprocessing for Phoenix's threaded background workers, anti-bloat
    # for size reductions.
    cmd.extend(
        [
            "--plugin-enable=pkg-resources",
            "--plugin-enable=multiprocessing",
            "--plugin-enable=anti-bloat",
        ]
    )

    for pkg in INCLUDE_PACKAGES:
        cmd.append(f"--include-package={pkg}")

    # Entry point: phoenix/launcher.py compiled as if invoked via
    # `python -m phoenix`. We point Nuitka at the file path directly
    # so the resulting binary's argv[0] is "phoenix-<os>-<arch>".
    cmd.append(str(REPO_ROOT / "phoenix" / "launcher.py"))
    return cmd


def _find_output(
    output_dir: Path,
    output_name: str,
    *,
    onefile: bool,
) -> Path | None:
    """Locate the produced executable inside output_dir."""
    if onefile:
        # Onefile mode: single executable at the top level of dist/.
        # Windows: <name>.exe; Linux: <name>.
        for candidate in (output_dir / f"{output_name}.exe", output_dir / output_name):
            if candidate.exists():
                return candidate
        return None
    # Multi-file mode: dist/<name>.dist/<name>(.exe)
    dist_dir = output_dir / f"{output_name}.dist"
    for candidate in (dist_dir / f"{output_name}.exe", dist_dir / output_name):
        if candidate.exists():
            return candidate
    return None


__all__ = ["main"]


if __name__ == "__main__":
    sys.exit(main())
