"""Phoenix vendor source sync (architecture v1 Section 10.4).

Reads ``scripts/vendor_manifest.json``, validates a clean clone of dr-frank-and-eddy
at a specific commit, copies the file-by-file mapping into ``vendor/``, and
regenerates ``vendor/VENDOR_VERSION.txt``.

Per the 2026-05-06 architecture revision, only frank-data is vendored; Trinity
Core's Orchestrate subsystem is greenfield Phoenix code under
``phoenix/trinity/orchestrate/``, NOT vendored from SynQc TDS Core (Section 1
Decision 37 + Section 2.5).

Phase 1 build guide Section 3.2 modes:

- ``--validate-only``  : run validation only; exit before any copy.
- ``--dry-run``        : validate end-to-end + report what would copy; never write.
- ``--target NAME``    : process only one manifest target (default: all).
- ``--update-version-manifest`` : regenerate ``vendor/VENDOR_VERSION.txt`` only.
- (default = full sync : validate + copy + regenerate VENDOR_VERSION.txt)

SAFETY:

- Refuses to write outside ``C:/Phoenix/vendor/``. ``..`` in manifest paths is
  rejected at parse time; resolved destinations are re-checked before each copy.
- Refuses to proceed without ``PHOENIX_ADMIN_OVERRIDE=1`` in the environment
  for any path that writes (Phase 0 placeholder for the safety-gate Actor check
  that lands in Phase 6). ``--dry-run`` and ``--validate-only`` skip the admin
  check because they don't write.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Typed errors (audit-grade discipline carried from architecture Section 3.7).


class VendorSyncError(Exception):
    """Base for all vendor-sync failures."""


class AdminPermissionRequired(VendorSyncError):
    """``PHOENIX_ADMIN_OVERRIDE`` is not set and the operation needs to write."""


class ManifestError(VendorSyncError):
    """The manifest is malformed or self-inconsistent."""


class SourceMissing(VendorSyncError):
    """A target's ``source_path`` does not exist or is not a git repo."""


class SourceCommitMismatch(VendorSyncError):
    """A target's source HEAD does not match the manifest's ``expected_commit``."""


class SourceBranchMismatch(VendorSyncError):
    """A target's source branch does not match the manifest's ``expected_branch``."""


class SourceUnclean(VendorSyncError):
    """A target's source has uncommitted changes (working tree not clean)."""


class PathTraversal(VendorSyncError):
    """A manifest entry's path tries to escape the source root or vendor/."""


class WriteOutsideVendor(VendorSyncError):
    """A copy operation would write outside ``C:/Phoenix/vendor/``."""


class CalibrationFailed(VendorSyncError):
    """The source-side calibration suite did not pass cleanly."""


# ---------------------------------------------------------------------------
# Constants.

PHOENIX_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = PHOENIX_ROOT / "vendor"
DEFAULT_MANIFEST = PHOENIX_ROOT / "scripts" / "vendor_manifest.json"
PYPROJECT_PATH = PHOENIX_ROOT / "pyproject.toml"
ADMIN_OVERRIDE_ENV = "PHOENIX_ADMIN_OVERRIDE"


# ---------------------------------------------------------------------------
# Typed manifest.


@dataclass(frozen=True)
class FileMapping:
    src: str
    dst: str
    kind: str  # "file" or "directory"
    exclude_patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class VendorTarget:
    name: str
    source_path: Path
    expected_branch: str | None
    expected_commit: str | None
    files: tuple[FileMapping, ...]


@dataclass(frozen=True)
class Manifest:
    targets: tuple[VendorTarget, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class TargetValidation:
    head: str
    branch: str
    files_validated: int


# ---------------------------------------------------------------------------
# Manifest parsing.


def parse_manifest(path: Path) -> Manifest:
    if not path.exists():
        raise ManifestError(f"manifest not found at {path}")
    try:
        raw_text = path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ManifestError("manifest must be a JSON object")

    targets: list[VendorTarget] = []
    for name, target_data in data.items():
        if name.startswith("_"):
            # Underscore-prefixed top-level keys are documentation/comments, not targets.
            continue
        if not isinstance(target_data, dict):
            raise ManifestError(f"target {name!r} is not a JSON object")
        targets.append(_parse_target(name, target_data))

    if not targets:
        raise ManifestError("manifest declares no targets")

    return Manifest(targets=tuple(targets))


def _parse_target(name: str, data: dict[str, Any]) -> VendorTarget:
    if "source_path" not in data:
        raise ManifestError(f"target {name!r} missing 'source_path'")
    if not isinstance(data.get("files"), list):
        raise ManifestError(f"target {name!r} 'files' must be a list")

    source_path = Path(str(data["source_path"]))
    expected_branch = data.get("expected_branch")
    expected_commit = data.get("expected_commit")

    file_mappings: list[FileMapping] = []
    for entry in data["files"]:
        if not isinstance(entry, dict):
            raise ManifestError(f"target {name!r}: file entry is not an object: {entry!r}")
        for required in ("from", "to", "kind"):
            if required not in entry:
                raise ManifestError(f"target {name!r}: file entry missing {required!r}: {entry!r}")
        src = str(entry["from"])
        dst = str(entry["to"])
        kind = str(entry["kind"])
        excludes = entry.get("exclude_patterns", [])

        if kind not in ("file", "directory"):
            raise ManifestError(f"target {name!r}: invalid kind {kind!r}")
        if ".." in Path(src).parts:
            raise PathTraversal(f"target {name!r}: 'from' contains '..': {src}")
        if ".." in Path(dst).parts:
            raise PathTraversal(f"target {name!r}: 'to' contains '..': {dst}")
        if not isinstance(excludes, list):
            raise ManifestError(f"target {name!r}: exclude_patterns must be a list")

        file_mappings.append(
            FileMapping(
                src=src,
                dst=dst,
                kind=kind,
                exclude_patterns=tuple(str(p) for p in excludes),
            )
        )

    return VendorTarget(
        name=name,
        source_path=source_path,
        expected_branch=expected_branch,
        expected_commit=expected_commit,
        files=tuple(file_mappings),
    )


# ---------------------------------------------------------------------------
# Validation.


def check_admin_permission() -> None:
    if os.environ.get(ADMIN_OVERRIDE_ENV) != "1":
        raise AdminPermissionRequired(
            f"vendor sync requires {ADMIN_OVERRIDE_ENV}=1 in the environment "
            "(Phase 0 placeholder for the safety-gate Actor check that lands in Phase 6)."
        )


def _run_git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def validate_target(target: VendorTarget) -> TargetValidation:
    """Validate one target. Raises a typed error on failure."""
    if not target.source_path.exists():
        raise SourceMissing(
            f"target {target.name!r}: source_path does not exist: {target.source_path}"
        )
    if not (target.source_path / ".git").exists():
        raise SourceMissing(
            f"target {target.name!r}: not a git repo (no .git/ at {target.source_path})"
        )

    try:
        head = _run_git(target.source_path, "rev-parse", "HEAD")
        branch = _run_git(target.source_path, "branch", "--show-current")
    except subprocess.CalledProcessError as exc:
        raise SourceMissing(f"target {target.name!r}: git command failed: {exc.stderr}") from exc

    if target.expected_branch is not None and branch != target.expected_branch:
        raise SourceBranchMismatch(
            f"target {target.name!r}: expected branch {target.expected_branch!r}, got {branch!r}"
        )
    if target.expected_commit is not None and head != target.expected_commit:
        raise SourceCommitMismatch(
            f"target {target.name!r}: expected commit {target.expected_commit!r}, got {head!r}"
        )

    status = _run_git(target.source_path, "status", "--porcelain")
    if status:
        raise SourceUnclean(
            f"target {target.name!r}: source working tree has uncommitted changes:\n{status}"
        )

    # Validate every src exists.
    missing: list[str] = []
    for fm in target.files:
        if not (target.source_path / fm.src).exists():
            missing.append(fm.src)
    if missing:
        raise SourceMissing(f"target {target.name!r}: source missing expected paths: {missing}")

    # Validate every dst resolves under VENDOR_DIR.
    vendor_resolved = VENDOR_DIR.resolve()
    for fm in target.files:
        dst_resolved = (PHOENIX_ROOT / fm.dst).resolve()
        try:
            dst_resolved.relative_to(vendor_resolved)
        except ValueError as exc:
            raise WriteOutsideVendor(
                f"target {target.name!r}: dst {fm.dst!r} resolves outside vendor/: {dst_resolved}"
            ) from exc

    return TargetValidation(head=head, branch=branch, files_validated=len(target.files))


# ---------------------------------------------------------------------------
# File copy.


def make_ignore(
    patterns: tuple[str, ...],
) -> Callable[[str, list[str]], set[str]] | None:
    if not patterns:
        return None

    def _ignore(_directory: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        for name in names:
            for pat in patterns:
                if fnmatch.fnmatch(name, pat):
                    ignored.add(name)
                    break
        return ignored

    return _ignore


def perform_copy(target: VendorTarget, dry_run: bool) -> list[str]:
    """Copy files for a target. Returns human-readable operation descriptions."""
    operations: list[str] = []
    vendor_resolved = VENDOR_DIR.resolve()
    for fm in target.files:
        src = target.source_path / fm.src
        dst = PHOENIX_ROOT / fm.dst

        # Defense-in-depth: re-check dst is under vendor/.
        try:
            dst.resolve().relative_to(vendor_resolved)
        except ValueError as exc:
            raise WriteOutsideVendor(
                f"copy refused: dst {fm.dst!r} escapes vendor/: {dst.resolve()}"
            ) from exc

        if fm.kind == "file":
            description = f"file: {fm.src} -> {fm.dst}"
            operations.append(description)
            if not dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        elif fm.kind == "directory":
            description = f"dir : {fm.src} -> {fm.dst}"
            if fm.exclude_patterns:
                description += f" (excluding: {list(fm.exclude_patterns)})"
            operations.append(description)
            if not dry_run:
                if dst.exists():
                    shutil.rmtree(dst)
                dst.parent.mkdir(parents=True, exist_ok=True)
                ignore = make_ignore(fm.exclude_patterns)
                shutil.copytree(src, dst, ignore=ignore)
        else:
            raise ManifestError(f"unknown kind: {fm.kind}")
    return operations


# ---------------------------------------------------------------------------
# VENDOR_VERSION.txt.

VENDOR_VERSION_PATH = VENDOR_DIR / "VENDOR_VERSION.txt"

VENDOR_VERSION_HEADER_LINES = (
    "# Phoenix vendored substrate -- version manifest",
    "# Populated by scripts/vendor_sync.py per architecture v1 Section 10.4.",
    "# Per the 2026-05-06 architecture revision, only frank-data is vendored;",
    "# Trinity Core's Orchestrate subsystem is greenfield Phoenix code (Section",
    "# 2.5 + Decision 37). The replay path (Section 19-21) reads this file to",
    "# verify the running snapshot matches the ledger entry's recorded versions.",
    "",
)


def read_phoenix_version() -> str:
    with PYPROJECT_PATH.open("rb") as fh:
        data = tomllib.load(fh)
    project = data.get("project")
    if not isinstance(project, dict):
        raise ManifestError("pyproject.toml has no [project] table")
    version = project.get("version")
    if not isinstance(version, str):
        raise ManifestError("pyproject.toml [project] table has no string 'version'")
    return version


def compute_calibration_hash(profile_path: Path) -> str:
    if not profile_path.exists():
        return ""
    return sha256(profile_path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Calibration profile generation.

CALIBRATION_PROFILE_PATH = VENDOR_DIR / "calibration_profile.json"
CALIBRATION_TEST_FILE = "tests/test_calibration_suite.py"
CALIBRATION_CONSTANT_PATTERN = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*=\s*([\d.eE+\-]+)\s*(?:#.*)?$")


def _extract_constants_from_test_file(test_path: Path) -> dict[str, float]:
    """Parse a Python file for top-level NAME = NUMERIC_LITERAL assignments.

    Captures the physical constants the calibration suite uses (HBAR, M_ELECTRON,
    MU_BOHR, C_LIGHT, G_NEWTON, EV_TO_JOULE, etc.) so the profile records what
    physics-constant snapshot was active when the calibration was run.

    Only matches *top-level* (un-indented) module-scope assignments. Local
    variables inside test methods (like `B = 1.0` or `L = 4e-9`) are skipped
    so they don't pollute the constants dict with per-test fixture values.
    """
    constants: dict[str, float] = {}
    for raw_line in test_path.read_text(encoding="utf-8").splitlines():
        # Skip indented lines -- those are inside functions/classes, not module scope.
        if raw_line and raw_line[0] in (" ", "\t"):
            continue
        match = CALIBRATION_CONSTANT_PATTERN.match(raw_line.rstrip())
        if not match:
            continue
        name, raw_value = match.group(1), match.group(2)
        try:
            constants[name] = float(raw_value)
        except ValueError:
            continue
    return constants


def _run_calibration_suite(source_path: Path) -> dict[str, Any]:
    """Run frank-data's calibration suite via pytest. Raises if anything fails."""
    test_file = CALIBRATION_TEST_FILE

    collect = subprocess.run(
        [sys.executable, "-m", "pytest", test_file, "--collect-only", "-q"],
        cwd=source_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if collect.returncode != 0:
        raise CalibrationFailed(
            f"pytest --collect-only failed at {source_path}:\n"
            f"stdout:\n{collect.stdout}\nstderr:\n{collect.stderr}"
        )
    test_names = sorted(line.strip() for line in collect.stdout.splitlines() if "::" in line)

    run = subprocess.run(
        [sys.executable, "-m", "pytest", test_file, "--tb=line", "-q"],
        cwd=source_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if run.returncode != 0:
        raise CalibrationFailed(
            f"calibration suite did not pass cleanly at {source_path} "
            f"(exit code {run.returncode}):\n{run.stdout}\n{run.stderr}"
        )

    summary_lines = [
        line for line in run.stdout.splitlines() if "passed" in line or "failed" in line
    ]
    summary = summary_lines[-1].strip() if summary_lines else ""

    return {
        "test_file": test_file,
        "total_tests": len(test_names),
        "passed": len(test_names),
        "failed": 0,
        "test_names": test_names,
        "pytest_summary": summary,
    }


def generate_calibration_profile(
    target: VendorTarget, validation: TargetValidation
) -> dict[str, Any]:
    """Run the source-side calibration suite for `target` and produce the profile."""
    test_path = target.source_path / CALIBRATION_TEST_FILE
    if not test_path.exists():
        raise CalibrationFailed(
            f"calibration suite not found at {test_path} for target {target.name!r}"
        )

    suite = _run_calibration_suite(target.source_path)
    constants = _extract_constants_from_test_file(test_path)

    return {
        "profile_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_commit": validation.head,
        "source_branch": validation.branch,
        "calibration_suite": suite,
        "physical_constants": constants,
    }


def write_calibration_profile(profile: dict[str, Any]) -> None:
    """Write `profile` to vendor/calibration_profile.json with deterministic ordering."""
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    CALIBRATION_PROFILE_PATH.write_text(
        json.dumps(profile, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_vendor_version(validations: dict[str, TargetValidation]) -> None:
    """Write vendor/VENDOR_VERSION.txt with the current state."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    phoenix_version = read_phoenix_version()
    profile_path = VENDOR_DIR / "calibration_profile.json"
    cal_hash = compute_calibration_hash(profile_path)

    lines = list(VENDOR_VERSION_HEADER_LINES)
    lines.append(f"phoenix_release: {phoenix_version}")
    lines.append(f"vendor_synced_at: {now}")
    lines.append(
        f"dr_frank_and_eddy_commit: {validations['frank_data'].head}"
        if "frank_data" in validations
        else "dr_frank_and_eddy_commit:"
    )
    lines.append(
        f"calibration_profile_hash: {cal_hash}" if cal_hash else "calibration_profile_hash:"
    )
    lines.append("")

    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    VENDOR_VERSION_PATH.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI.


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vendor_sync",
        description="Phoenix vendor source sync (architecture v1 Section 10.4).",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=f"path to manifest (default: {DEFAULT_MANIFEST})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate end-to-end + report what would copy; never write to vendor/",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="run validation only; exit before any copy",
    )
    parser.add_argument(
        "--target",
        default=None,
        help="process only this manifest target (default: all)",
    )
    parser.add_argument(
        "--update-version-manifest",
        action="store_true",
        help="regenerate vendor/VENDOR_VERSION.txt only (after manual changes to vendor/)",
    )
    parser.add_argument(
        "--generate-calibration",
        action="store_true",
        help=(
            "run the source-side calibration suite + write vendor/calibration_profile.json + "
            "regenerate vendor/VENDOR_VERSION.txt with the new hash. Calibration target is "
            "always the manifest's frank_data target; other targets are skipped."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if not (args.dry_run or args.validate_only):
            check_admin_permission()

        manifest = parse_manifest(args.manifest)
        targets: tuple[VendorTarget, ...] = manifest.targets
        if args.target is not None:
            targets = tuple(t for t in targets if t.name == args.target)
            if not targets:
                raise ManifestError(f"--target {args.target!r} not found in manifest")

        print(f"Vendor sync starting (manifest: {args.manifest})", file=sys.stderr)
        print(f"Phoenix release: {read_phoenix_version()}", file=sys.stderr)
        print(f"Targets: {', '.join(t.name for t in targets)}", file=sys.stderr)

        validations: dict[str, TargetValidation] = {}
        for target in targets:
            print(f"  validating {target.name}...", file=sys.stderr)
            validations[target.name] = validate_target(target)
            v = validations[target.name]
            print(f"    OK: branch={v.branch} head={v.head}", file=sys.stderr)

        if args.validate_only:
            print("Validation complete (--validate-only).", file=sys.stderr)
            return 0

        if args.update_version_manifest:
            write_vendor_version(validations)
            print(f"Wrote {VENDOR_VERSION_PATH} (--update-version-manifest).", file=sys.stderr)
            return 0

        if args.generate_calibration:
            for target in targets:
                if target.name != "frank_data":
                    print(
                        f"  skipping {target.name} (calibration suite is frank-data-only)",
                        file=sys.stderr,
                    )
                    continue
                print(f"  generating calibration profile for {target.name}...", file=sys.stderr)
                profile = generate_calibration_profile(target, validations[target.name])
                write_calibration_profile(profile)
                suite = profile["calibration_suite"]
                print(
                    f"    {suite['passed']}/{suite['total_tests']} tests passed",
                    file=sys.stderr,
                )
                print(f"    wrote {CALIBRATION_PROFILE_PATH}", file=sys.stderr)
            write_vendor_version(validations)
            print(
                f"Wrote {VENDOR_VERSION_PATH} with new calibration_profile_hash.", file=sys.stderr
            )
            return 0

        for target in targets:
            verb = "would copy" if args.dry_run else "copying"
            print(f"  {verb} {target.name}...", file=sys.stderr)
            ops = perform_copy(target, dry_run=args.dry_run)
            for op in ops:
                print(f"    {op}", file=sys.stderr)

        if args.dry_run:
            print("Dry run complete; nothing was written.", file=sys.stderr)
            return 0

        write_vendor_version(validations)
        print(f"Wrote {VENDOR_VERSION_PATH}", file=sys.stderr)
        return 0

    except VendorSyncError as exc:
        print(f"vendor_sync: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
