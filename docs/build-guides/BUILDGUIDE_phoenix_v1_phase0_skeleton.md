# BUILDGUIDE — Phoenix v1 Phase 0: Repository Skeleton

**Status:** DRAFT — under active design with Adam.
**Authoritative location:** `C:\Phoenix\BUILDGUIDE_phoenix_v1_phase0_skeleton.md`
**Architectural reference:** `C:\Phoenix\PHOENIX_ARCHITECTURE_v1.md`
**Phase scope:** Phase 0 only. Phase 1 (vendor sync + Tier-1 calibration battery) is a separate build guide.
**Date opened:** 2026-05-06.
**Author of record:** Adam (with Claude as design partner).

---

## 0 — What this build guide is

This is the **first build guide** for Phoenix v1, executed against the locked architecture in `PHOENIX_ARCHITECTURE_v1.md`. It directs Claude Code through Phase 0 — the absolute-minimum repository skeleton that needs to exist before any substantive Phoenix code can land.

**Phase 0's definition of done:**
- `python -c "import phoenix"` succeeds without error.
- `python -m phoenix.api --port 8003` boots and serves `GET /v1/health` returning `{"status": "ok", "phoenix_version": "1.0.0.dev0", ...}`.
- Double-clicking the desktop shortcut on Windows opens `http://localhost:8003/docs` after the daemon boots.
- Every directory under `phoenix/` and `vendor/` has a non-empty `README.md` following the Section 10.6 template.
- One smoke test passes: `pytest tests/unit/test_smoke.py`.
- Phoenix does NOT yet do any physics. No vendored solvers, no DPD engine, no SynQc TDS, no MCP server, no CLI commands beyond `phoenix --version`. Those are Phase 1+ work.

**This guide does NOT cover:**
- Vendoring `C:\frank-data\` or SynQc TDS substrate (Phase 1).
- Trinity Core's three subsystems (Phases 1-3).
- The router's seven-stage decision algorithm (Phase 4).
- The verification gate's wobble protocol orchestration (Phase 5).
- The safety gate's nine-stage validation pipeline (Phase 6).
- The admin backdoor's mutation endpoints (Phase 7).
- LoRA hot-swap, MCP server, or the reference admin client (Phases 8+).

Each subsequent phase has its own build guide. Phase numbering is for execution order; it does not imply priority within a phase.

## 1 — Prerequisites

Before starting Phase 0:

1. **Architecture spec read.** Claude Code (or whoever is executing this guide) has read `PHOENIX_ARCHITECTURE_v1.md` end-to-end. Do not proceed without this. The architecture is the source of truth for every directory name, every file name, every locked decision Phase 0 implements.
2. **Git initialized.** `C:\Phoenix\.git\` exists. The remote `origin` is `https://github.com/nah414/Phoenix.git`. The architecture doc was committed as `15a44ae` (the root commit).
3. **Python 3.11+ available.** Phoenix targets Python 3.11 minimum. Verify with `python --version`. If 3.11+ is not available, install it via the official python.org installer (NOT Microsoft Store; NOT WSL — Phoenix runs natively on Windows per Adam's environment standards).
4. **A clean working tree.** `git status` reports `nothing to commit, working tree clean` before Step 1 begins. Phase 0 lands as a single squashable commit, not as a series of WIP commits.
5. **No OneDrive paths.** Adam's standing rule: nothing under `OneDrive`. All work happens under `C:\Phoenix\`. If any tooling or IDE tries to redirect, refuse and reroute.

## 2 — Phase-gate review protocol

This guide has **eight steps** (Section 3.1 through 3.8). Each step ends with a stop gate:

```
=== STEP N COMPLETE — AWAITING ADAM REVIEW ===
```

Claude Code does NOT advance to Step N+1 until Adam has explicitly approved Step N. Approval is signaled in chat, not by silence. If Step N reveals an architectural ambiguity that the v1 spec does not resolve, mark it `[OPEN: ...]` and surface it to Adam — do not silently invent a resolution.

Each step's checkpoint includes:
1. **What lands** — the specific files created or modified.
2. **Verification commands** — the exact commands Adam runs to confirm the step works.
3. **Expected output** — what those commands should print.
4. **Common failure modes** — what goes wrong and how to diagnose.

**Standing rule from architecture §10.5:** if a step changes startup behavior (env vars, NATS configuration, calibration drill on first run, port handling), the step also updates `scripts/launch.bat`, `scripts/launch.sh`, and `scripts/create_shortcut.ps1` together. This is the same rule Adam applies to dr-frank-and-eddy build guides.

**Standing rule from architecture §10.6:** every directory under `phoenix/` and `vendor/` ends Phase 0 with a non-empty `README.md` using the §10.6 template. No exceptions.

## 3 — Phase 0 deliverables

### 3.1 — Step 1: Top-level scaffolding

**What lands:**
- `C:\Phoenix\pyproject.toml` — package metadata, dependencies (with upper bounds), Python version pinned `>=3.11,<3.14`, build-system config.
- `C:\Phoenix\requirements.lock` — empty placeholder with a comment explaining its role per architecture §1 Decision 21. Will be populated by `uv pip compile pyproject.toml --output-file requirements.lock` in Phase 1.
- `C:\Phoenix\.gitignore` — standard Python plus Phoenix's audit-output exclusion (`.audit/`, `.runtime/`).
- `C:\Phoenix\.gitattributes` — pin LF line endings for `*.py`, `*.md`, `*.yaml`, `*.json`; default for the rest.
- `C:\Phoenix\.pre-commit-config.yaml` — git pre-commit hooks running ruff (lint+format), mypy, and the smoke test before each commit. Audit-friendly: every commit that lands has passed lint/type/test.
- `C:\Phoenix\LICENSE` — Apache License, Version 2.0 (full text, per §1 Decision 34).
- `C:\Phoenix\README.md` — top-level README pointing readers at `PHOENIX_ARCHITECTURE_v1.md`, `BUILDGUIDE_phoenix_v1_phase0_skeleton.md`, and `docs/getting-started/` (the latter created in later phases).
- `C:\Phoenix\CHANGELOG.md` — skeleton with one entry: `1.0.0.dev0` reflecting Phase 0's skeleton landing.

**`pyproject.toml` contents (Phase 0 minimum, with upper bounds per the 2026-05-06 tightening):**

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "phoenix-middleware"
version = "1.0.0.dev0"
description = "Production-grade quantum-accuracy middleware (Phase 0 skeleton)"
readme = "README.md"
license = { file = "LICENSE" }
authors = [{ name = "Adam" }]
requires-python = ">=3.11,<3.14"
dependencies = [
    "fastapi>=0.115,<0.120",
    "uvicorn[standard]>=0.27,<0.35",
    "pydantic>=2.6,<3.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0,<9.0",
    "pytest-asyncio>=0.23,<0.25",
    "httpx>=0.27,<0.29",
    "ruff>=0.3,<0.10",
    "mypy>=1.8,<2.0",
    "pre-commit>=3.6,<4.0",
]

[project.scripts]
phoenix = "phoenix.cli.entry:main"

[tool.setuptools.packages.find]
where = ["."]
include = ["phoenix*"]
exclude = ["vendor*", "tests*", "evals*", "scripts*", "docs*"]

[tool.pytest.ini_options]
testpaths = ["tests", "evals"]
python_files = ["test_*.py", "eval_*.py"]
asyncio_mode = "auto"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.mypy]
python_version = "3.11"
strict = true
exclude = ["vendor/", "build/", "dist/"]
```

**Note on dependency choices and the dep manager.** Phase 0 ships `fastapi`, `uvicorn[standard]`, `pydantic` as *runtime* dependencies, with explicit upper bounds on every package so a breaking minor or major release can't silently land between Phoenix releases. NumPy, SciPy, the quantum SDKs (Qiskit Runtime, Braket, IonQ), NATS, and SQLAlchemy are NOT added in Phase 0 — each lands in the phase that first needs it.

**Dependency manager: `uv`.** Phoenix uses `uv` (Astral, Rust-based) for dependency management. The `requirements.lock` artifact specified in architecture §1 Decision 21 is produced by `uv pip compile pyproject.toml --output-file requirements.lock`. Phase 0 does not run the compile step (the lock stays empty as a placeholder); Phase 1 runs the first real compile and pins the full transitive tree. Standard install path: `uv venv && uv pip install -e .[dev]` (or fall back to `pip install -e .[dev]` if `uv` is unavailable — the `pyproject.toml` works with both).

**Hooks for audit-friendly commits: `pre-commit`.** `.pre-commit-config.yaml` ships in Phase 0 and runs ruff (lint+format), mypy, and the smoke test before each commit. The architectural goal: every commit that lands in `nah414/Phoenix` has passed the same checks CI will run, so the git log is itself an audit-ready artifact (Section 1 Decision 16's audit discipline starts at commit time, not just at runtime).

Keep Phase 0's import surface small so the skeleton smoke test runs fast and the dependency footprint stays auditable.

**`.gitignore` contents:**

```
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
*.egg-info/
*.egg
build/
dist/
.eggs/

# Virtualenvs
.venv/
venv/
env/

# Pytest
.pytest_cache/
.coverage
htmlcov/

# Mypy / Ruff caches
.mypy_cache/
.ruff_cache/

# Phoenix-specific runtime artifacts (per architecture §10.1)
.audit/
.runtime/

# IDE
.vscode/
.idea/
*.swp
*.swo

# OS
Thumbs.db
.DS_Store
```

**`.gitattributes` contents:**

```
* text=auto eol=lf
*.bat text eol=crlf
*.ps1 text eol=crlf
```

The `.bat` and `.ps1` exception is intentional — Windows shells expect CRLF in those files, and Phoenix's launcher scripts run on Windows (per §10.5).

**`.pre-commit-config.yaml` contents:**

```yaml
# Phoenix pre-commit hooks — audit-friendly commit gate
# Per the 2026-05-06 tightening: every commit lands having passed lint/type/test.
# Install once per local clone with: pre-commit install
# Run manually with: pre-commit run --all-files

repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9  # Pin specific version; bump deliberately, never via auto-update
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.11.2
    hooks:
      - id: mypy
        additional_dependencies: [pydantic, fastapi]
        exclude: ^(vendor/|tests/|evals/)

  - repo: local
    hooks:
      - id: smoke-test
        name: pytest smoke test
        entry: pytest tests/unit/test_smoke.py -q
        language: system
        pass_filenames: false
        always_run: true
        stages: [pre-commit]
```

**Verification:**

```powershell
Set-Location C:\Phoenix
Test-Path pyproject.toml          # → True
Test-Path requirements.lock       # → True
Test-Path .gitignore              # → True
Test-Path .gitattributes          # → True
Test-Path .pre-commit-config.yaml # → True
Test-Path LICENSE                 # → True
Test-Path README.md               # → True
Test-Path CHANGELOG.md            # → True
git status                        # → working tree clean (after staging)

# After Step 2 lands the package, install pre-commit hooks:
# pre-commit install
# (Skipped here because pre-commit's `smoke-test` hook depends on Step 6's smoke test existing.)
```

After this step, every file above exists. No `phoenix/` package code yet — that is Step 2. The `pre-commit install` runs at the end of Step 6 (when the smoke test exists), not here.

```
=== STEP 1 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.2 — Step 2: `phoenix/` package skeleton

**What lands:** the directory tree from architecture §10.1 (the `phoenix/` package only). Every directory gets an empty `__init__.py`. The two non-empty files in this step are `phoenix/__init__.py` (with `__version__`) and `phoenix/_internal/version.py` (the constant + vendor-version reader stub).

**Directory tree to create:**

```
phoenix/
├── __init__.py
├── api/
│   └── __init__.py
├── cli/
│   └── __init__.py
├── mcp/
│   └── __init__.py
├── trinity/
│   ├── __init__.py
│   ├── solver/
│   │   └── __init__.py
│   ├── control/
│   │   └── __init__.py
│   └── orchestrate/
│       └── __init__.py
├── grammar/
│   └── __init__.py
├── router/
│   └── __init__.py
├── verification/
│   └── __init__.py
├── safety/
│   └── __init__.py
├── admin/
│   └── __init__.py
├── ledger/
│   └── __init__.py
├── audit/
│   └── __init__.py
├── identity/
│   └── __init__.py
├── adapters/
│   └── __init__.py
├── providers/
│   ├── __init__.py
│   ├── quantum/
│   │   └── __init__.py
│   ├── classical/
│   │   └── __init__.py
│   ├── cognition/
│   │   └── __init__.py
│   └── cloud_gpu/
│       └── __init__.py
├── state/
│   ├── __init__.py
│   └── migrations/
│       └── __init__.py
├── queue/
│   └── __init__.py
└── _internal/
    ├── __init__.py
    └── version.py
```

Every `__init__.py` (except `phoenix/__init__.py`) is created empty. They exist to make Python treat the directory as a package; that is all Phase 0 needs.

**`phoenix/__init__.py`:**

```python
"""Phoenix — production-grade quantum-accuracy middleware.

See PHOENIX_ARCHITECTURE_v1.md for the architecture spec.
"""

from phoenix._internal.version import __version__

__all__ = ["__version__"]
```

**`phoenix/_internal/version.py`:**

```python
"""Phoenix version constant and vendor-version reader stub.

Phase 0 ships the version constant only. The vendor-version reader is stubbed
to return None until Phase 1 lands the vendored substrate.
"""

from __future__ import annotations

from pathlib import Path

__version__ = "1.0.0.dev0"

VENDOR_VERSION_FILE = Path(__file__).parent.parent.parent / "vendor" / "VENDOR_VERSION.txt"


def read_vendor_version() -> dict[str, str] | None:
    """Return the parsed contents of vendor/VENDOR_VERSION.txt, or None if absent.

    Phase 0 returns a placeholder when the file exists with all empty hashes.
    Phase 1 onward returns the real vendored versions.
    """
    if not VENDOR_VERSION_FILE.exists():
        return None
    text = VENDOR_VERSION_FILE.read_text(encoding="utf-8")
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        parsed[key.strip()] = value.strip()
    return parsed
```

**Verification:**

```powershell
Set-Location C:\Phoenix
python -c "import phoenix; print(phoenix.__version__)"
# → 1.0.0.dev0
python -c "from phoenix._internal.version import read_vendor_version; print(read_vendor_version())"
# → None (vendor/VENDOR_VERSION.txt does not yet exist; that is Step 3)
```

**Common failure mode:** `ModuleNotFoundError: No module named 'phoenix'` if the package was not installed in editable mode. Fix: `pip install -e .[dev]` from `C:\Phoenix\`. The pip-installable wheel is the canonical development install per architecture §1 Decision 29.

```
=== STEP 2 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.3 — Step 3: `vendor/` skeleton

**What lands:**
- `C:\Phoenix\vendor\` directory.
- `C:\Phoenix\vendor\VENDOR_VERSION.txt` — placeholder with all hashes empty per architecture §10.2.

**Note:** `vendor/` does NOT get an `__init__.py` in Phase 0. The vendored substrate is loaded by `phoenix/__init__.py`'s `sys.path` manipulation when Phase 1 ships (per §11.7.1's verbatim-import disposition). For Phase 0, `vendor/` is just a directory with one text file in it.

**`vendor/VENDOR_VERSION.txt` contents:**

```
# Phoenix vendored substrate — version manifest
# Populated by scripts/vendor_sync.py (lands in Phase 1).
# Phase 0 ships an empty placeholder; reading this file in Phase 0 returns
# all-empty hashes, which the audit and replay paths recognize as "vendoring
# not yet performed."

phoenix_release: 1.0.0.dev0
vendor_synced_at:
dr_frank_and_eddy_commit:
calibration_profile_hash:
```

**Verification:**

```powershell
Set-Location C:\Phoenix
Test-Path vendor                                  # → True
Test-Path vendor\VENDOR_VERSION.txt              # → True
Get-Content vendor\VENDOR_VERSION.txt            # → the placeholder above
python -c "from phoenix._internal.version import read_vendor_version; v = read_vendor_version(); print(v)"
# → {'phoenix_release': '1.0.0.dev0', 'vendor_synced_at': '', ...}
```

**Common failure mode:** the reader returns `None` instead of the placeholder dict. Cause: file path mismatch in `phoenix/_internal/version.py::VENDOR_VERSION_FILE`. The path traversal is `__file__.parent.parent.parent / "vendor" / "VENDOR_VERSION.txt"` because `phoenix/_internal/version.py` is three levels deep relative to `C:\Phoenix\vendor\`.

```
=== STEP 3 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.4 — Step 4: `scripts/` launcher and shortcut installer

**What lands per architecture §10.5:**
- `C:\Phoenix\scripts\launch.bat` — Windows launcher.
- `C:\Phoenix\scripts\launch.sh` — macOS/Linux launcher.
- `C:\Phoenix\scripts\create_shortcut.ps1` — Windows desktop-shortcut installer.

**Phase 0 launcher behavior:** boot the empty Phoenix daemon (just `/v1/health`), open the docs URL in the default browser, wait for Ctrl+C. NATS JetStream is NOT started in Phase 0 — that is Phase 6 (queue + state backend). Phoenix daemon's port is `8003` by default per §5.4 (dr-frank-and-eddy uses 8002; Phoenix avoids the collision).

**`scripts/launch.bat`:**

```bat
@echo off
REM Phoenix v1 launcher — Phase 0
REM Boots the empty Phoenix daemon (FastAPI on port 8003) and opens docs.
REM NATS JetStream and vendor-integrity check land in later phases.

setlocal

set PHOENIX_HOME=C:\Phoenix
set PHOENIX_PORT=8003

if not exist "%PHOENIX_HOME%\pyproject.toml" (
    echo ERROR: %PHOENIX_HOME% does not look like a Phoenix install.
    echo Expected pyproject.toml at %PHOENIX_HOME%\pyproject.toml
    exit /b 1
)

cd /d "%PHOENIX_HOME%"

echo Booting Phoenix daemon on port %PHOENIX_PORT%...
start /B python -m phoenix.api --port %PHOENIX_PORT%

REM Give the daemon a moment to start before opening the browser.
timeout /T 2 /NOBREAK > nul

echo Opening docs at http://localhost:%PHOENIX_PORT%/docs
start http://localhost:%PHOENIX_PORT%/docs

echo Phoenix v1 (Phase 0) running. Press Ctrl+C to stop.
pause
```

**`scripts/launch.sh`:**

```bash
#!/usr/bin/env bash
# Phoenix v1 launcher — Phase 0 (macOS/Linux)
# Boots the empty Phoenix daemon (FastAPI on port 8003).
# NATS JetStream and vendor-integrity check land in later phases.

set -euo pipefail

PHOENIX_HOME="${PHOENIX_HOME:-$HOME/Phoenix}"
PHOENIX_PORT="${PHOENIX_PORT:-8003}"

if [ ! -f "$PHOENIX_HOME/pyproject.toml" ]; then
    echo "ERROR: $PHOENIX_HOME does not look like a Phoenix install." >&2
    echo "Expected pyproject.toml at $PHOENIX_HOME/pyproject.toml" >&2
    exit 1
fi

cd "$PHOENIX_HOME"

echo "Booting Phoenix daemon on port $PHOENIX_PORT..."
python -m phoenix.api --port "$PHOENIX_PORT" &
DAEMON_PID=$!

# Give the daemon a moment to start.
sleep 2

# Open docs in the platform's default browser.
case "$(uname -s)" in
    Darwin) open "http://localhost:$PHOENIX_PORT/docs" ;;
    Linux)  xdg-open "http://localhost:$PHOENIX_PORT/docs" 2>/dev/null || true ;;
esac

echo "Phoenix v1 (Phase 0) running (daemon PID $DAEMON_PID). Press Ctrl+C to stop."

# Wait for the daemon and clean up on Ctrl+C.
trap "kill $DAEMON_PID 2>/dev/null || true; exit 0" INT TERM
wait $DAEMON_PID
```

**`scripts/create_shortcut.ps1`:**

```powershell
# Phoenix v1 — desktop shortcut installer
# Creates a Windows .lnk on the user's Desktop pointing at scripts/launch.bat.
# Per architecture §10.5: every Phoenix release ships its own desktop launcher,
# never modifies dr-frank-and-eddy's launcher.

$ErrorActionPreference = "Stop"

$PhoenixHome = "C:\Phoenix"
$LauncherBat = Join-Path $PhoenixHome "scripts\launch.bat"
$DesktopPath = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $DesktopPath "Phoenix.lnk"

if (-not (Test-Path $LauncherBat)) {
    Write-Error "Launcher not found at $LauncherBat. Run from a checked-out Phoenix install."
    exit 1
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($ShortcutPath)
$shortcut.TargetPath = $LauncherBat
$shortcut.WorkingDirectory = $PhoenixHome
$shortcut.IconLocation = "$LauncherBat,0"  # Phase 0 placeholder; designed icon lands before public release per §11.7.2
$shortcut.Description = "Phoenix v1 — quantum-accuracy middleware"
$shortcut.Save()

Write-Host "Created Phoenix desktop shortcut at $ShortcutPath"
Write-Host "Double-click it to launch Phoenix on port 8003."
```

**Verification:**

```powershell
Set-Location C:\Phoenix
Test-Path scripts\launch.bat                # → True
Test-Path scripts\launch.sh                 # → True
Test-Path scripts\create_shortcut.ps1       # → True

# Run the shortcut installer
.\scripts\create_shortcut.ps1
# → "Created Phoenix desktop shortcut at C:\Users\<you>\Desktop\Phoenix.lnk"
Test-Path "$env:USERPROFILE\Desktop\Phoenix.lnk"   # → True
```

The launcher itself is verified end-to-end in Step 8 (Phase 0 acceptance). Phase 0 does not test the launcher in this step because the empty daemon does not exist yet — that is Step 7.

**Common failure mode:** `create_shortcut.ps1` fails with `Cannot find type [WScript.Shell]`. Cause: PowerShell execution policy is too restrictive. Adam's environment runs PowerShell with `RemoteSigned` policy at minimum. Verify with `Get-ExecutionPolicy`. If `Restricted`, run `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`.

```
=== STEP 4 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.5 — Step 5: Per-section README stubs + `evals/` scaffold

**What lands per architecture §10.6:** every directory under `phoenix/` and `vendor/` ends Phase 0 with a non-empty `README.md` using the §10.6 template. Phase 0's READMEs are *stubs* — the "Common failure modes" and "Recent changes" sections name Phase 0 as the only entry; later phases extend.

**What also lands (per the 2026-05-06 tightening): the `evals/` scaffold.** Phoenix has two kinds of test surface:
- `tests/` — unit and integration tests of *specific behavior*. Standard pytest collection.
- `evals/` — *audit and debugging* evaluations that verify *holistic correctness* across subsystems: does the audit log capture every required event, does the ledger hashchain stay valid under all operations, does replay produce bit-exact match, does the drift detector fire when it should, etc. Also pytest-collected, but kept structurally separate so a reader can see at a glance "is this checking a unit, or is this checking an audit-shaped property?"

Phase 0 ships `evals/` with empty subdirectories and per-subdirectory READMEs explaining what each will hold once the relevant subsystem lands. No actual eval code in Phase 0.

**Template (from §10.6, applied to each directory):**

```markdown
# phoenix/{module-name}

## Purpose
{One-paragraph description of what this module does.}

## Architectural reference
See PHOENIX_ARCHITECTURE_v1.md, Section {N}.

## Key files and their roles
{Table mapping each file to its function. Phase 0 stubs may have only an empty __init__.py listed.}

## Vendored substrate (if applicable)
{Which files are vendored from where; pointer to vendor/VENDOR_VERSION.txt for the pinned commit. Phase 0: "Not yet vendored — vendoring lands in Phase 1.".}

## Common failure modes
{Bulleted list of typical bugs encountered in this module, root causes, and resolutions. Phase 0: "None yet — module is a Phase 0 skeleton stub.".}

## Troubleshooting
{How to diagnose when this module misbehaves: logs to check, environment variables to set, audit-event types to filter on. Phase 0: "Module is empty; see Phase 1+ for real diagnostics."}

## Tests
{Pointer to the test files that exercise this module. Phase 0: "tests/unit/test_smoke.py asserts the package imports."}

## Recent changes
{Per Adam's troubleshooting-log discipline from dr-frank-and-eddy: a chronological list of significant changes (and the build guide that produced them). Phase 0:}
- 2026-05-06 — Phase 0 (BUILDGUIDE_phoenix_v1_phase0_skeleton.md): module created as empty stub.
```

**Required README files (§10.6 template, `phoenix/` and `vendor/`):**

| Path | Section reference |
|---|---|
| `phoenix/api/README.md` | §5 |
| `phoenix/cli/README.md` | §5 |
| `phoenix/mcp/README.md` | §5.5 |
| `phoenix/trinity/README.md` | §2 |
| `phoenix/trinity/solver/README.md` | §2.3 |
| `phoenix/trinity/control/README.md` | §2.4 |
| `phoenix/trinity/orchestrate/README.md` | §2.5 |
| `phoenix/grammar/README.md` | §3 |
| `phoenix/router/README.md` | §4 |
| `phoenix/verification/README.md` | §6 |
| `phoenix/safety/README.md` | §7 |
| `phoenix/admin/README.md` | §8 |
| `phoenix/ledger/README.md` | §1 Decision 15 |
| `phoenix/audit/README.md` | §1 Decisions 16, 22 |
| `phoenix/identity/README.md` | §1 Decisions 10-12 + §7 |
| `phoenix/adapters/README.md` | §3.5 |
| `phoenix/providers/README.md` | §4.2 |
| `phoenix/state/README.md` | §1 Decision 31 + §10.3 |
| `phoenix/queue/README.md` | §1 Decisions 32-33 |
| `phoenix/_internal/README.md` | §10.3.1 |
| `vendor/README.md` | §10.2 + §10.4 |

**`phoenix/_internal/README.md`** has additional content because §10.3.1 lives there: the README links to the cloud-seams Protocol definitions (which land as actual code in a later phase, but the README references them now).

**`evals/` directory tree to create:**

```
evals/
├── README.md                      # Top-level: what evals are, how they differ from tests
├── audit/
│   └── README.md                  # Phase 7+ — audit log captures every event from §16
├── ledger/
│   └── README.md                  # Phase 7+ — Omega Ledger hashchain stays valid
├── replay/
│   └── README.md                  # Phase 7+ — replay produces bit-exact match (§19-21)
├── drift/
│   └── README.md                  # Phase 7+ — drift detectors fire when expected (§17)
├── routing/
│   └── README.md                  # Phase 4+ — routing decisions recorded with full provenance
├── cost_ceiling/
│   └── README.md                  # Phase 4+ — ceiling enforcement allows/denies correctly (§4.7)
└── frontier_physics/
    └── README.md                  # Phase 7+ — frontier-physics gating refuses correctly (§7.4 step 6)
```

**`evals/README.md` template (top-level):**

```markdown
# evals/ — audit and debugging evaluations

This directory contains evaluations that verify *holistic correctness* of Phoenix
across subsystems. Distinct from `tests/`:

- `tests/` checks specific behavior of specific code (a function returns X for input Y).
- `evals/` checks audit-shaped properties of the system (every required event reaches the
  audit log; the ledger hashchain remains valid under all operations; replay produces
  bit-exact match; drift detectors fire when expected).

Both are pytest-collected. Phase 0 ships placeholder subdirectories; later phases
populate as their subsystems land.

## Subdirectories

- `audit/` — audit-log correctness (§16). Phase 7+.
- `ledger/` — Omega Ledger hashchain integrity (§15). Phase 7+.
- `replay/` — strict/replay reproducibility (§19-21). Phase 7+.
- `drift/` — drift detector behavior (§17). Phase 7+.
- `routing/` — routing decision provenance (§4). Phase 4+.
- `cost_ceiling/` — cost-ceiling enforcement (§4.7). Phase 4+.
- `frontier_physics/` — frontier-physics gating refusal (§7.4 step 6). Phase 7+.

## Running evals

```bash
pytest evals/                           # all evals
pytest evals/audit/                     # one subsystem
pytest evals/ -m "not slow"             # exclude slow evals
```

## Why a separate directory?

Mixing audit-shaped evals into `tests/` makes the test suite harder to scan. When
an audit eval fails, the failure mode is "the system is producing wrong audit
trail" — operationally distinct from "this function has a bug." Keeping them in
their own directory makes that distinction visible to anyone reading the repo.
```

**Per-subdirectory `evals/<area>/README.md` template (used in all 7 subdirectories):**

```markdown
# evals/<area>/

## Purpose
{One paragraph: what eval shape goes here, what subsystem it covers.}

## Architectural reference
See PHOENIX_ARCHITECTURE_v1.md Section {N}.

## Phase
This directory is populated in Phase {X}. Phase 0 ships only this README placeholder.

## What evals will land here
{Bulleted list of the specific holistic properties that will be verified.}

## Recent changes
- 2026-05-06 — Phase 0 (BUILDGUIDE_phoenix_v1_phase0_skeleton.md): placeholder created.
```

**Verification:**

```powershell
Set-Location C:\Phoenix
$expectedReadmes = @(
    # phoenix/ and vendor/ READMEs
    "phoenix/api/README.md",
    "phoenix/cli/README.md",
    "phoenix/mcp/README.md",
    "phoenix/trinity/README.md",
    "phoenix/trinity/solver/README.md",
    "phoenix/trinity/control/README.md",
    "phoenix/trinity/orchestrate/README.md",
    "phoenix/grammar/README.md",
    "phoenix/router/README.md",
    "phoenix/verification/README.md",
    "phoenix/safety/README.md",
    "phoenix/admin/README.md",
    "phoenix/ledger/README.md",
    "phoenix/audit/README.md",
    "phoenix/identity/README.md",
    "phoenix/adapters/README.md",
    "phoenix/providers/README.md",
    "phoenix/state/README.md",
    "phoenix/queue/README.md",
    "phoenix/_internal/README.md",
    "vendor/README.md",
    # evals/ scaffold READMEs
    "evals/README.md",
    "evals/audit/README.md",
    "evals/ledger/README.md",
    "evals/replay/README.md",
    "evals/drift/README.md",
    "evals/routing/README.md",
    "evals/cost_ceiling/README.md",
    "evals/frontier_physics/README.md"
)
$missing = $expectedReadmes | Where-Object { -not (Test-Path $_) -or (Get-Item $_).Length -eq 0 }
if ($missing.Count -eq 0) {
    Write-Host "All Phase 0 READMEs present and non-empty (21 phoenix/vendor + 8 evals = 29 total)."
} else {
    Write-Host "MISSING or empty:"
    $missing | ForEach-Object { Write-Host "  $_" }
}
# → "All Phase 0 READMEs present and non-empty (21 phoenix/vendor + 8 evals = 29 total)."

# Also confirm pytest collects evals/ as a test path (no actual evals to run yet, but the
# directory must be discoverable by pytest):
pytest evals/ --collect-only -q
# → "no tests ran in 0.0Xs"  (no eval_*.py files yet — that's correct for Phase 0)
```

```
=== STEP 5 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.6 — Step 6: Smoke test + integration test + pre-commit install

**What lands:**
- `C:\Phoenix\tests\__init__.py` (empty).
- `C:\Phoenix\tests\unit\__init__.py` (empty).
- `C:\Phoenix\tests\unit\test_smoke.py` — three tests asserting the package imports, exposes `__version__`, and exposes every Section subpackage.
- `C:\Phoenix\tests\integration\__init__.py` (empty).
- `C:\Phoenix\tests\integration\test_health.py` — automated test that boots the FastAPI app via `TestClient` (provided by `httpx` per the 2026-05-06 dep tightening) and asserts the `/v1/health` response matches Phase 0's contract.
- `pre-commit install` is run at the end of this step (after the smoke test exists, since the pre-commit `smoke-test` hook depends on it).

**`tests/unit/test_smoke.py`:**

```python
"""Phase 0 smoke test — assert the package imports and exposes its version.

This is the single guard that the Phase 0 skeleton compiles. Every later phase
adds real tests; this one stays as the baseline that breaks first if a Phase
1+ change accidentally breaks the basic import path.
"""

from __future__ import annotations


def test_phoenix_imports() -> None:
    import phoenix
    assert hasattr(phoenix, "__version__")
    assert phoenix.__version__ == "1.0.0.dev0"


def test_internal_version_module() -> None:
    from phoenix._internal.version import __version__, read_vendor_version

    assert __version__ == "1.0.0.dev0"
    # Phase 0 ships the placeholder vendor manifest; reader returns the parsed dict
    # (with empty values), not None.
    vendor = read_vendor_version()
    assert vendor is not None
    assert vendor["phoenix_release"] == "1.0.0.dev0"
    # Empty hashes are expected at Phase 0 — vendor sync runs in Phase 1.
    assert vendor["vendor_synced_at"] == ""
    assert vendor["dr_frank_and_eddy_commit"] == ""


def test_all_section_subpackages_import() -> None:
    """Every directory listed in architecture §10.3 imports as a submodule."""
    import phoenix.api  # noqa: F401
    import phoenix.cli  # noqa: F401
    import phoenix.mcp  # noqa: F401
    import phoenix.trinity  # noqa: F401
    import phoenix.trinity.solver  # noqa: F401
    import phoenix.trinity.control  # noqa: F401
    import phoenix.trinity.orchestrate  # noqa: F401
    import phoenix.grammar  # noqa: F401
    import phoenix.router  # noqa: F401
    import phoenix.verification  # noqa: F401
    import phoenix.safety  # noqa: F401
    import phoenix.admin  # noqa: F401
    import phoenix.ledger  # noqa: F401
    import phoenix.audit  # noqa: F401
    import phoenix.identity  # noqa: F401
    import phoenix.adapters  # noqa: F401
    import phoenix.providers  # noqa: F401
    import phoenix.providers.quantum  # noqa: F401
    import phoenix.providers.classical  # noqa: F401
    import phoenix.providers.cognition  # noqa: F401
    import phoenix.providers.cloud_gpu  # noqa: F401
    import phoenix.state  # noqa: F401
    import phoenix.queue  # noqa: F401
    import phoenix.queue as q  # `queue` is a stdlib module — verify our package shadows it inside the phoenix namespace
    assert q.__name__ == "phoenix.queue"
```

**`tests/integration/test_health.py`:**

```python
"""Phase 0 integration test — assert /v1/health responds with the expected contract.

This is the first endpoint test. It boots the FastAPI app via TestClient (no actual
network I/O) and verifies the response shape. Architecture §5.2 specifies the /v1/health
contract; this test is the contract's executable witness.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from phoenix.api.routes import app


def test_health_returns_200_and_expected_shape() -> None:
    client = TestClient(app)
    response = client.get("/v1/health")
    assert response.status_code == 200
    body = response.json()

    # Required fields per Phase 0 contract.
    assert body["status"] == "ok"
    assert body["phoenix_version"] == "1.0.0.dev0"
    assert body["calibration_status"] == "not_loaded"  # Phase 0 placeholder
    assert "checked_at_utc" in body
    assert "vendor_manifest" in body

    # Vendor manifest is the Phase 0 placeholder (file exists with empty hashes).
    vendor = body["vendor_manifest"]
    assert vendor is not None
    assert vendor["phoenix_release"] == "1.0.0.dev0"
    assert vendor["vendor_synced_at"] == ""        # Phase 1 populates this
    assert vendor["dr_frank_and_eddy_commit"] == ""  # Phase 1 populates this


def test_openapi_schema_served() -> None:
    """Architecture §5.2 says OpenAPI 3.1 ships at /v1/openapi.json."""
    client = TestClient(app)
    response = client.get("/v1/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["openapi"].startswith("3.")
    assert schema["info"]["version"] == "1.0.0.dev0"
    # /v1/health is registered.
    assert "/v1/health" in schema["paths"]
```

**Verification:**

```powershell
Set-Location C:\Phoenix

# Install Phoenix in editable mode + dev deps. Prefer uv if available.
if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv pip install -e .[dev]
} else {
    pip install -e .[dev]
}

# Run the smoke test
pytest tests/unit/test_smoke.py -v
# → 3 passed in <1s

# Run the integration test
pytest tests/integration/test_health.py -v
# → 2 passed in <1s

# Combined
pytest tests/ -v
# → 5 passed total

# Now install pre-commit hooks (smoke test exists, so the smoke-test hook can fire)
pre-commit install
# → "pre-commit installed at .git/hooks/pre-commit"

# Verify pre-commit runs cleanly across all files
pre-commit run --all-files
# → ruff, mypy, smoke-test all pass
```

**Common failure modes:**
- `ModuleNotFoundError: No module named 'phoenix.queue'`. Cause: `queue/__init__.py` was not created. Verify the file exists. Note: `phoenix.queue` shadows stdlib `queue` *within the phoenix namespace only*. Outside the package, `import queue` still imports stdlib's; `from phoenix import queue` imports ours.
- `AssertionError: phoenix.__version__ ...`. Cause: `phoenix/__init__.py` does not import from `_internal.version`. Verify the file matches Step 2's content.
- `pytest` or `httpx` not found. Cause: `pip install -e .[dev]` (or `uv pip install -e .[dev]`) was not run, or was run in a different virtualenv. Verify `which pytest` / `where pytest` resolves under the active venv. `httpx` is a Phase 0 dev dep specifically for `TestClient`.
- `pre-commit run` fails on first invocation with "executable not found." Cause: `pre-commit install` was skipped, or pre-commit's environments are stale. Run `pre-commit clean && pre-commit install` to refresh.
- `mypy` fails on the smoke test or integration test with strict-mode complaints. Cause: missing type hints in test files. mypy strict mode is intentional from Phase 0; fix the hints rather than relaxing the config.

```
=== STEP 6 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.7 — Step 7: Empty FastAPI daemon

**What lands:**
- `C:\Phoenix\phoenix\api\__main__.py` — module entry point so `python -m phoenix.api` works.
- `C:\Phoenix\phoenix\api\routes.py` — FastAPI app exposing `GET /v1/health` only.
- `C:\Phoenix\phoenix\api\error_envelope.py` — typed error envelope per architecture §5.2 (Phase 0 ships the dataclass; later phases use it in real handlers).

**Phase 0 endpoint scope:** `GET /v1/health` only. Returns the placeholder version, the vendor manifest read (which is the Phase 0 placeholder), and a static "calibration profile not yet loaded" status. No other endpoints. The full endpoint surface in §5.2 lands across later phases.

**`phoenix/api/routes.py`:**

```python
"""Phoenix v1 — front-door REST surface. Phase 0 ships /v1/health only.

The full surface specified in PHOENIX_ARCHITECTURE_v1.md §5.2 lands across
later phases:
- Tasks endpoints — Phase 5+ (verification gate is the gating dependency).
- Audit/ledger — Phase 7+.
- Admin — Phase 8.
- Adapters — Phase 9.
- Identity — Phase 6 (state backend dependency).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI

from phoenix._internal.version import __version__, read_vendor_version

app = FastAPI(
    title="Phoenix",
    description="Production-grade quantum-accuracy middleware (Phase 0 skeleton)",
    version=__version__,
    openapi_url="/v1/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
)


@app.get("/v1/health")
def health() -> dict:
    """Liveness/readiness probe per architecture §5.2.

    Phase 0 returns: phoenix version, vendor-manifest read result, a static
    "calibration_status" of 'not_loaded' (drift monitoring lands in Phase 7),
    and the current UTC timestamp.
    """
    vendor = read_vendor_version()
    return {
        "status": "ok",
        "phoenix_version": __version__,
        "vendor_manifest": vendor,
        "calibration_status": "not_loaded",  # Phase 0 placeholder; Phase 7 wires in drift monitoring
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
    }
```

**`phoenix/api/__main__.py`:**

```python
"""Module entry point: `python -m phoenix.api --port 8003` boots the daemon.

Phase 0 supports a single CLI flag, `--port`, defaulting to 8003 per architecture
§5.4. Later phases add `--host`, `--workers`, `--reload`, plus configuration
loading from `~/.phoenix/config.yaml`.
"""

from __future__ import annotations

import argparse
import sys

import uvicorn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m phoenix.api",
        description="Phoenix v1 daemon — Phase 0 skeleton",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8003,
        help="Port to bind on (default 8003 per architecture §5.4)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host to bind on (default 127.0.0.1; loopback only in Phase 0)",
    )
    args = parser.parse_args(argv)

    uvicorn.run(
        "phoenix.api.routes:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**`phoenix/api/error_envelope.py`:**

```python
"""Standard error envelope per architecture §5.2.

Phase 0 ships the dataclass. Phase 5+ wires it into real handlers via
FastAPI's exception handlers; the envelope shape is the stable contract.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorEnvelope:
    """Stable error envelope. Code matches typed exception names from §3.7."""

    code: str
    message: str
    path: str
    request_id: str
    documentation_url: str

    def to_dict(self) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "path": self.path,
                "request_id": self.request_id,
                "documentation_url": self.documentation_url,
            }
        }
```

**Verification:**

```powershell
Set-Location C:\Phoenix
# In one terminal:
python -m phoenix.api --port 8003
# Expected output:
#   INFO:     Started server process [...]
#   INFO:     Uvicorn running on http://127.0.0.1:8003 (Press CTRL+C to quit)

# In another terminal:
Invoke-RestMethod -Uri http://127.0.0.1:8003/v1/health
# Expected:
#   status            : ok
#   phoenix_version   : 1.0.0.dev0
#   vendor_manifest   : @{phoenix_release=1.0.0.dev0; vendor_synced_at=; ...}
#   calibration_status: not_loaded
#   checked_at_utc    : 2026-05-06T...

# Optional: view the auto-generated docs
Start-Process http://127.0.0.1:8003/docs
```

**Common failure modes:**
- `Address already in use` on port 8003. Cause: another process is bound. Per architecture §10.5, Phoenix never silently bumps to a different port. Resolve the conflict at the OS level: `netstat -ano | findstr :8003` to find the offending PID, then decide whether to terminate it or use a different port via `--port 8004`.
- `ModuleNotFoundError: No module named 'fastapi'`. Cause: `pip install -e .[dev]` did not install runtime deps. Re-run from `C:\Phoenix\` after activating the right venv.
- The browser opens `/docs` to a blank page or 404. Cause: `openapi_url="/v1/openapi.json"` is wrong, or FastAPI version too old. Verify FastAPI ≥ 0.110.

```
=== STEP 7 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.8 — Step 8: Phase 0 acceptance verification

This step runs all of Phase 0's acceptance checks together and confirms the skeleton is shippable.

**Acceptance checklist:**

1. ✅ `python -c "import phoenix; print(phoenix.__version__)"` prints `1.0.0.dev0`.
2. ✅ `pytest tests/unit/test_smoke.py -v` passes 3/3.
3. ✅ `pytest tests/integration/test_health.py -v` passes 2/2 (added per the 2026-05-06 tightening).
4. ✅ `pytest tests/ -v` passes 5/5 (combined unit + integration).
5. ✅ `pytest evals/ --collect-only` runs cleanly with "no tests ran" — `evals/` is a registered pytest path with placeholder structure for later phases.
6. ✅ `python -m phoenix.api --port 8003` boots without error.
7. ✅ `curl http://127.0.0.1:8003/v1/health` returns the expected JSON with `"status": "ok"`.
8. ✅ `http://127.0.0.1:8003/docs` renders the OpenAPI page in a browser.
9. ✅ `scripts/launch.bat` boots the daemon and opens the docs URL on a clean Windows machine.
10. ✅ `scripts/create_shortcut.ps1` produces a working desktop `Phoenix.lnk` that re-runs the launcher.
11. ✅ Every directory under `phoenix/` and `vendor/` and `evals/` has a non-empty `README.md` (29 total: 21 phoenix/vendor + 8 evals).
12. ✅ `pre-commit run --all-files` passes ruff (lint+format), mypy (strict mode), and the smoke-test hook.
13. ✅ `pre-commit install` was run; `.git/hooks/pre-commit` exists and is executable.
14. ✅ Dependency manager: `requirements.lock` is present (empty placeholder); `pyproject.toml` has upper bounds on every dep; Python pinned `>=3.11,<3.14`.
15. ✅ `git status` reports a clean working tree after staging Phase 0's files.

**Combined verification command:**

```powershell
Set-Location C:\Phoenix

# Acceptance 1
python -c "import phoenix; assert phoenix.__version__ == '1.0.0.dev0'; print('1: OK')"

# Acceptance 2 + 3 + 4
pytest tests/ -v

# Acceptance 5: evals/ collects cleanly with no failures
pytest evals/ --collect-only -q
if ($LASTEXITCODE -eq 0 -or $LASTEXITCODE -eq 5) { Write-Host "5: OK (evals/ scaffold present)" } else { Write-Host "5: FAIL" }
# Note: pytest exits with code 5 when no tests collected — that is the expected Phase 0 state.

# Acceptance 6 + 7: boot, hit, kill
$daemon = Start-Process -PassThru python -ArgumentList "-m", "phoenix.api", "--port", "8003" -NoNewWindow
Start-Sleep -Seconds 3
try {
    $health = Invoke-RestMethod -Uri http://127.0.0.1:8003/v1/health
    if ($health.status -eq "ok" -and $health.phoenix_version -eq "1.0.0.dev0") {
        Write-Host "6+7: OK"
    } else {
        Write-Host "6+7: FAIL — health response unexpected"
        $health | Format-List
    }
} finally {
    Stop-Process -Id $daemon.Id -Force
}

# Acceptance 11 (READMEs across phoenix/, vendor/, evals/)
$expectedReadmes = @(
    "phoenix/api/README.md", "phoenix/cli/README.md", "phoenix/mcp/README.md",
    "phoenix/trinity/README.md", "phoenix/trinity/solver/README.md",
    "phoenix/trinity/control/README.md", "phoenix/trinity/orchestrate/README.md",
    "phoenix/grammar/README.md", "phoenix/router/README.md",
    "phoenix/verification/README.md", "phoenix/safety/README.md",
    "phoenix/admin/README.md", "phoenix/ledger/README.md",
    "phoenix/audit/README.md", "phoenix/identity/README.md",
    "phoenix/adapters/README.md", "phoenix/providers/README.md",
    "phoenix/state/README.md", "phoenix/queue/README.md",
    "phoenix/_internal/README.md", "vendor/README.md",
    "evals/README.md", "evals/audit/README.md", "evals/ledger/README.md",
    "evals/replay/README.md", "evals/drift/README.md", "evals/routing/README.md",
    "evals/cost_ceiling/README.md", "evals/frontier_physics/README.md"
)
$missing = $expectedReadmes | Where-Object { -not (Test-Path $_) -or (Get-Item $_).Length -eq 0 }
if ($missing.Count -eq 0) { Write-Host "11: OK (29 READMEs present)" } else { Write-Host "11: FAIL — missing $($missing.Count) READMEs" }

# Acceptance 12 + 13: pre-commit
if (Test-Path ".git/hooks/pre-commit") { Write-Host "13: OK (pre-commit installed)" } else { Write-Host "13: FAIL — pre-commit hook not installed" }
pre-commit run --all-files
if ($LASTEXITCODE -eq 0) { Write-Host "12: OK (pre-commit clean)" } else { Write-Host "12: FAIL — pre-commit reported issues" }

# Acceptance 14: dep tightening evidence
if ((Get-Content pyproject.toml -Raw) -match 'requires-python\s*=\s*">=3\.11,<3\.13"') {
    Write-Host "14: OK (Python pinned to >=3.11,<3.14)"
} else {
    Write-Host "14: FAIL — Python upper bound missing in pyproject.toml"
}

# Acceptance 15
git status --porcelain | Out-Null
if ($LASTEXITCODE -eq 0 -and -not (git status --porcelain)) { Write-Host "15: OK (tree clean)" } else { Write-Host "15: FAIL — uncommitted changes" }
```

**Acceptance criteria 8, 9, 10 are interactive** and require Adam to verify by:
- Opening `http://127.0.0.1:8003/docs` in a browser and confirming the OpenAPI page renders.
- Double-clicking `scripts/launch.bat` from File Explorer and confirming the daemon boots and the browser opens.
- Running `.\scripts\create_shortcut.ps1`, then double-clicking the resulting Phoenix shortcut on the desktop and confirming the same end-to-end flow.

**If all acceptance checks pass:**

1. Stage and commit Phase 0 to git:
   ```powershell
   git add .
   git commit -m "Phoenix v1 Phase 0: repository skeleton"
   ```
   (Use a HEREDOC for the full body per the conventions — body should reference this build guide and list which acceptance criteria were verified.)
2. Push when Adam is ready: `git push -u origin main`.
3. Update `CHANGELOG.md` with the Phase 0 entry.
4. Open the Phase 1 build guide draft (`BUILDGUIDE_phoenix_v1_phase1_vendor_sync.md`).

```
=== STEP 8 COMPLETE — AWAITING ADAM REVIEW ===
```

## 4 — What's not in Phase 0

Explicitly out of scope for Phase 0; do not build any of the following until the corresponding phase:

| Item | Phase | Build guide |
|---|---|---|
| Vendor sync from `C:\frank-data\` and SynQc TDS | Phase 1 | BUILDGUIDE_phoenix_v1_phase1_vendor_sync.md |
| Trinity Core Solver subsystem | Phase 2 | BUILDGUIDE_phoenix_v1_phase2_solver.md |
| Trinity Core Control + Orchestrate | Phase 3 | BUILDGUIDE_phoenix_v1_phase3_control_orchestrate.md |
| Router (provider selection + failover) | Phase 4 | BUILDGUIDE_phoenix_v1_phase4_router.md |
| Verification gate (wobble protocol) | Phase 5 | BUILDGUIDE_phoenix_v1_phase5_verification.md |
| Safety gate + identity + state backend + NATS | Phase 6 | BUILDGUIDE_phoenix_v1_phase6_safety_state_queue.md |
| Audit log + Omega Ledger + drift monitor | Phase 7 | BUILDGUIDE_phoenix_v1_phase7_audit_ledger.md |
| Admin endpoints (incl. kill switch) | Phase 8 | BUILDGUIDE_phoenix_v1_phase8_admin.md |
| LoRA adapter sandbox + MCP server + CLI commands | Phase 9 | BUILDGUIDE_phoenix_v1_phase9_adapters_mcp_cli.md |
| OTel adapter + cloud-seam tests + standalone binary build | Phase 10 | BUILDGUIDE_phoenix_v1_phase10_observability_distribution.md |
| Final Section 10.7 acceptance run + release | Phase 11 | BUILDGUIDE_phoenix_v1_phase11_release.md |

Phase numbers are *execution* order, not priority. Each phase has its own architectural-Section dependency; no phase advances until its inputs land.

## 5 — Phase 1 preview

Phase 1's job is to make `vendor/` real:

- `scripts/vendor_sync.py` is implemented to pull from a clean `C:\frank-data\` clone at a known-good commit, run the dr-frank-and-eddy Tier-1 calibration suite against the source, and copy the per-§10.2 file mapping into `C:\Phoenix\vendor\`.
- `vendor/VENDOR_VERSION.txt` is populated with real hashes.
- The `tests/tier1/` battery (HO-1, ISW-1, H1S-1, RABI-1, SCG-1) executes against the freshly-vendored substrate and passes.
- `phoenix/_internal/version.py::read_vendor_version()` returns real values, not the Phase 0 placeholder.

Phase 1 does NOT yet wire the vendored substrate through Trinity Core's pipeline — that is Phase 2 (Solver), Phase 3 (Control + Orchestrate). Phase 1 only proves "the vendoring works."

## 6 — Standing rules this build guide enforces

Per Adam's discipline carried forward from dr-frank-and-eddy:

1. **Phase gates with explicit Adam review.** No silent advancement past `=== STEP N COMPLETE ===`.
2. **Stop and ask on architectural ambiguity.** If a step reveals a question the v1 spec does not answer, mark it `[OPEN: ...]` and surface to Adam — do not invent a resolution.
3. **PERF and SAFETY callouts inline.** When a step's implementation has a performance or safety implication, flag it with `**PERF:**` / `**SAFETY:**` so it surfaces to reviewers.
4. **Per-section READMEs.** Every directory under `phoenix/` and `vendor/` has a non-empty README ending Phase 0; later phases extend, never delete.
5. **Launcher updated when startup behavior changes.** `scripts/launch.bat`, `scripts/launch.sh`, and `scripts/create_shortcut.ps1` are updated together, never one without the others.
6. **No OneDrive paths.** All Phoenix paths under `C:\Phoenix\`. Tooling that tries to redirect to OneDrive is refused.
7. **Live reads beat memory.** Before referencing behavior from a vendored module, the build-guide author reads the relevant file from disk. Phase 0 has no vendored code; Phase 1 onward this rule kicks in.

```
=== BUILD GUIDE COMPLETE — AWAITING ADAM REVIEW ===
```
