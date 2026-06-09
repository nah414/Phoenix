# BUILDGUIDE — Phoenix v1 Phase 1: Vendor Sync + Tier-1 Battery

**Status:** DRAFT — under active design with Adam.
**Authoritative location:** `C:\Phoenix\BUILDGUIDE_phoenix_v1_phase1_vendor_sync.md`
**Architectural reference:** `C:\Phoenix\PHOENIX_ARCHITECTURE_v1.md` (post 2026-05-06 SynQc-greenfield revision).
**Phase scope:** Phase 1 only. Phase 2 (Solver wiring through Trinity Core's pipeline) is a separate build guide.
**Date opened:** 2026-05-06.
**Author of record:** Adam (with Claude as design partner).

---

## 0 — What this build guide is

Phase 1's job is to make `vendor/` real. Phase 0 shipped an empty placeholder; Phase 1 populates it with the actual frozen frank-data substrate, runs the source-side calibration battery, and proves the vendored substrate executes correctly through Phoenix's package skeleton.

**Phase 1's definition of done:**
- `vendor/synthesis/`, `vendor/grammar/`, `vendor/wobble/`, `vendor/actor/`, and `vendor/calibration_profile.json` all populated with real content.
- `vendor/VENDOR_VERSION.txt` has real commit hash + calibration profile hash (4 fields populated; `synqc_tds_commit` no longer exists per the 2026-05-06 architecture revision).
- `python -c "from synthesis.equations.base import EquationSolver; print('OK')"` succeeds (sys.path injection works).
- `pytest tests/tier1/` passes — five Tier-1 benchmarks (HO-1, ISW-1, H1S-1, RABI-1, SCG-1) execute against the vendored solvers and produce expected calibration values.
- `pytest tests/invariants/` passes — the 31-test grammar invariant suite runs against the vendored grammar.
- `pytest tests/dpd/` passes — the DPD engine self-test passes against the vendored `synthesis/core/`.
- `scripts/vendor_sync.py` is implemented, idempotent, and re-runnable when source clones are updated.

**Out of scope (Phase 1 does NOT cover):**
- Wiring the vendored Solver into Trinity Core's pipeline (Phase 2).
- Wiring the vendored DPD engine into the Control subsystem (Phase 3).
- Building Trinity Core's Orchestrate subsystem (Phase 3) — this is greenfield Phoenix code per the 2026-05-06 revision; it does NOT involve `vendor/`.
- SynQc TDS Core does not appear in Phase 1 at all. SynQc is now a design reference for the Orchestrate phase, not a vendoring source.
- Provider routing, the verification gate, the safety gate, the dev-ops backdoor, the LoRA adapter sandbox, the MCP server, or the CLI commands. Phase 4+ work.

## 1 — Prerequisites

Before starting Phase 1:

1. **Phase 0 acceptance.** All Phase 0 commits on `origin/main`. `python -m phoenix.api --port 8003` boots and `GET /v1/health` returns `phoenix_version=1.0.0.dev0`.
2. **Architecture revision committed.** The 2026-05-06 SynQc-greenfield revision has landed in `PHOENIX_ARCHITECTURE_v1.md` (its v1-revision transition note is in the preamble). Phase 1 cites the revised spec.
3. **`C:\frank-data\` accessible.** Currently on branch `wave-a-through-f-merge` at HEAD `96377ef` (pre-housekeeping). Step 0 cleans up the uncommitted state.
4. **Disk space.** Vendor source clone of frank-data is ~few hundred MB; vendored output in `C:\Phoenix\vendor\` is similar magnitude.
5. **No OneDrive.** Adam's standing rule. Vendor source workspace lands at `C:\Phoenix-vendor-source\` (sibling of `C:\Phoenix\`), never under OneDrive.

## 2 — Phase-gate review protocol

Eight steps (Section 3.0 through 3.7), each ending with `=== STEP N COMPLETE — AWAITING ADAM REVIEW ===`. No advancement past a stop gate without explicit Adam approval.

**Standing rule from Phase 0 carried forward:** the build guide drafts the work; Adam reviews it before execution. If a step reveals an architectural drift or ambiguity not resolved by the v1 spec, mark it `[OPEN: ...]` and surface to Adam — do not silently invent a resolution.

## 3 — Phase 1 deliverables

### 3.0 — Step 0: `C:\frank-data\` housekeeping (operates outside Phoenix)

This step cleans up `C:\frank-data\` so subsequent vendor-source clones come from a known clean commit. Adam asked for this to be automated; the housekeeping commit lands in `C:\frank-data\` and pushes to `nah414/dr-frank-and-eddy`, not in `C:\Phoenix\`.

**Current uncommitted state in `C:\frank-data\`:**
- `DrFrankEddy_Capabilities_Overview_for_Ash.md` — substantive 2026-05-04 doc; commit it.
- `evolution/candidates/epoch_0001/` — directory with content from 2026-05-02; commit it.
- `electron-debug.log.1`, `electron-debug.log.2` — startup debug logs from launch.vbs; gitignore them rather than commit.

**What lands:**
- `C:\frank-data\.gitignore` — append `electron-debug.log*` (or similar pattern).
- One commit on `wave-a-through-f-merge` staging the substantive items + the .gitignore update.
- Push to `origin` (`nah414/dr-frank-and-eddy`).

**Verification:**

```powershell
Set-Location C:\frank-data
git status                                                # → working tree clean
git log -1 --oneline                                      # → the new housekeeping commit
git status --ignored | Select-String "electron-debug"     # → ignored, not untracked
```

**SAFETY:** This step touches `C:\frank-data\`, not Phoenix. We commit on the existing branch (`wave-a-through-f-merge`), no new branches in frank-data. Adam approves the commit message + push before execution.

```
=== STEP 0 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.1 — Step 1: vendor source preparation

**What lands:**
- `C:\Phoenix-vendor-source\` directory created (sibling of `C:\Phoenix\`, NOT inside it).
- `C:\Phoenix-vendor-source\frank-data\` — clean clone of `C:\frank-data\` at the HEAD commit captured by Step 0. Includes `.git/` so we can verify commit hashes; read-only afterward (vendor sync never writes here).
- `C:\Phoenix\.gitignore` — adds `Phoenix-vendor-source/` (just in case someone moves the workspace inside Phoenix's repo by mistake; the workspace lives at the sibling path by design).

**Verification:**

```powershell
Test-Path C:\Phoenix-vendor-source\frank-data\.git           # → True
Set-Location C:\Phoenix-vendor-source\frank-data
git log -1 --oneline                                          # → matches Step 0's housekeeping commit
git status                                                   # → working tree clean
```

**Why a separate workspace?** Per architecture v1 Section 10.4: vendor sync runs against a clean clone at a specific commit, not the live working tree. `C:\frank-data\` is Adam's lab bench (development happens there); `C:\Phoenix-vendor-source\frank-data\` is the immutable snapshot. The vendor sync script reads from the snapshot and writes to `C:\Phoenix\vendor\`.

**[OPEN: should `C:\Phoenix-vendor-source\` be permanent or transient? Permanent (kept across vendor syncs) is faster to re-run; transient (deleted after each sync) is cleaner. v0 disposition: keep permanent.]**

```
=== STEP 1 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.2 — Step 2: `scripts/vendor_sync.py` + `scripts/vendor_manifest.json`

**What lands:**

`scripts/vendor_manifest.json` declares the accepted source commit + the file-by-file path mapping. Read by `vendor_sync.py` at run time:

```json
{
  "frank_data": {
    "source_path": "C:\\Phoenix-vendor-source\\frank-data",
    "expected_branch": "wave-a-through-f-merge",
    "expected_commit": "<populated by Step 1>",
    "files": [
      { "from": "synthesis/equations", "to": "vendor/synthesis/equations", "kind": "directory" },
      { "from": "synthesis/core",      "to": "vendor/synthesis/core",      "kind": "directory" },
      { "from": "synthesis/quantum/tensor_lindblad.py",
        "to":   "vendor/synthesis/quantum/tensor_lindblad.py", "kind": "file" },
      { "from": "evolution/knowledge/grammar", "to": "vendor/grammar", "kind": "directory" },
      { "from": "evolution/knowledge/sanskrit_codec.py",
        "to":   "vendor/grammar/sanskrit_codec.py", "kind": "file" },
      { "from": "evolution/knowledge/actor.py",
        "to":   "vendor/actor/actor.py", "kind": "file" },
      { "from": "wobble", "to": "vendor/wobble", "kind": "directory" }
    ]
  }
}
```

`scripts/vendor_sync.py` — the workhorse. Per architecture v1 Section 10.4:

1. Loads `scripts/vendor_manifest.json`.
2. Validates the source: checks `source_path` exists; runs `git -C <path> rev-parse HEAD` and compares against `expected_commit`; rejects path traversal in `from` paths (no `..`).
3. Refuses to proceed if validation fails. Typed error names the failure (`SourceCommitMismatch`, `SourceMissing`, `SourceUnclean`).
4. Reads the file mapping; copies from source to `vendor/`. Honors per-entry `exclude_patterns` if specified.
5. Generates `vendor/VENDOR_VERSION.txt` after copying — populated with the real commit hash, current ISO timestamp, and the calibration profile hash (Step 5 produces the calibration profile; `vendor_sync.py --update-version-manifest` re-runs to update VENDOR_VERSION with the final hash).
6. Runs Phoenix-side vendor-integrity checks: import-from-vendor smoke check (`python -c "from synthesis.equations.base import EquationSolver"`), then the suites that Step 6 lands.
7. Reports diff vs previous vendor sync; any unexpected file added/removed/renamed in source fails the script and requires manual review (`UnexpectedSourceChange` error).

**Acceptance for Step 2:**
- `python scripts/vendor_sync.py --dry-run` runs end-to-end against the manifest without writing anything to `vendor/`. Reports what it would do.
- `python scripts/vendor_sync.py --validate-only` runs validation steps 1-3 and exits.
- Both paths succeed (no errors) with the current manifest pointing at the Step 1 source workspace.

**SAFETY:** the script never writes outside `vendor/`. Path traversal in the manifest is rejected. The script requires the running Phoenix install to have `is_admin=True` (per architecture §10.4). Phase 0's safety gate isn't wired yet, so Phase 1's check is a placeholder: refuse to run if `os.environ.get("PHOENIX_ADMIN_OVERRIDE") != "1"`. Phase 6 replaces this with a proper Actor-based check.

```
=== STEP 2 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.3 — Step 3: frank-data vendoring

**What lands:** running the vendor sync against the manifest. Files copied per Section 10.2:

- `vendor/synthesis/equations/` — 12 solvers + base.py + registry.py + llm_context.py + specs/.
- `vendor/synthesis/core/` — dpd_engine.py, lindblad_rk4.py, probe_model.py, hardware_backends.py.
- `vendor/synthesis/quantum/tensor_lindblad.py` — MPS/TJM path for Phase 1.x medium-systems extension.
- `vendor/grammar/` — grammar_loader.py, generator.py, parser.py, physics_v1.yaml, plus the supporting modules under `evolution/knowledge/grammar/`.
- `vendor/grammar/sanskrit_codec.py` — vendored from `evolution/knowledge/sanskrit_codec.py`.
- `vendor/actor/actor.py` — vendored from `evolution/knowledge/actor.py`.
- `vendor/wobble/` — disagreement_types.py, disagreement_classifier.py, supporting files.

**What does NOT come over:** anything not named in the manifest. Specifically out of scope for Phase 1: `agents/`, `archive/`, `backend/`, `circuits/`, `cli/`, `custom_gates/`, `docs/`, `integration/`, `mcp_server/`, `messaging/`, `ml/`, `models/`, `omega/`, `orchestration/`, `quantum_engine/`, `router/`, `security/`, `tkg/`, `tools/`, `ui/`. These are dr-frank-and-eddy's lab-bench scaffolding that Phoenix doesn't need.

**Verification:**

```powershell
python scripts/vendor_sync.py --target frank_data
# Copies the seven manifest entries

# Spot-check the vendored content
Test-Path vendor/synthesis/equations/base.py            # → True
Test-Path vendor/synthesis/core/dpd_engine.py           # → True
Test-Path vendor/grammar/physics_v1.yaml                # → True
Test-Path vendor/wobble/disagreement_types.py           # → True
Test-Path vendor/actor/actor.py                         # → True

# Count files vs source
$srcCount = (Get-ChildItem -Recurse "C:\Phoenix-vendor-source\frank-data\synthesis\equations" -File).Count
$dstCount = (Get-ChildItem -Recurse vendor\synthesis\equations -File).Count
$srcCount -eq $dstCount                                  # → True (no files dropped silently)
```

**[OPEN: which test directories under `C:\frank-data\tests\` (if any) map to Phoenix's `tests/tier1/`, `tests/invariants/`, `tests/dpd/`? Step 6 resolves; Step 3 doesn't vendor tests yet.]**

```
=== STEP 3 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.4 — Step 4: sys.path injection in `phoenix/__init__.py`

**What lands:** updates to `phoenix/__init__.py` so vendored modules become importable.

Per architecture v1 §11.7.1's resolved disposition (verbatim through v1): vendored modules retain their original import paths. So `vendor/synthesis/equations/base.py` declares itself as part of the `synthesis.equations.base` module — not `phoenix.vendor.synthesis.equations.base`. For that to import correctly, Phoenix must inject `C:\Phoenix\vendor\` into `sys.path` *before* any vendored code is loaded.

**Implementation:**

```python
# phoenix/__init__.py (Phase 1 update)
"""Phoenix — production-grade quantum-accuracy middleware.

See PHOENIX_ARCHITECTURE_v1.md for the architecture spec.
"""

import sys as _sys
from pathlib import Path as _Path

# Vendored substrate (verbatim through v1, per Section 11.7.1).
# Injected at the END of sys.path so stdlib + installed packages take
# precedence; only Phoenix-specific module names (synthesis, wobble,
# actor, grammar) fall through to vendor/.
_VENDOR = _Path(__file__).parent.parent / "vendor"
if _VENDOR.exists() and str(_VENDOR) not in _sys.path:
    _sys.path.append(str(_VENDOR))

from phoenix._internal.version import __version__

__all__ = ["__version__"]
```

**SAFETY:** `sys.path.append` (not `insert(0, ...)`) means stdlib and installed packages always win in name resolution. Phoenix's vendored module names (`synthesis`, `wobble`, `actor`, `grammar`) don't collide with anything in stdlib or PyPI.

**Verification:**

```powershell
python -c "import phoenix; from synthesis.equations.base import EquationSolver; print(EquationSolver.__module__)"
# → synthesis.equations.base

python -c "import phoenix; from wobble.disagreement_types import AgreementType; print(AgreementType.__module__)"
# → wobble.disagreement_types

python -c "import phoenix; from grammar.parser import parse; print('grammar.parser OK')"
# → grammar.parser OK

# Stdlib precedence still works
python -c "import phoenix; import os; print(os.path.basename('a/b/c'))"
# → c
```

```
=== STEP 4 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.5 — Step 5: calibration profile generation + populated `VENDOR_VERSION.txt`

**What lands:**
- `vendor/calibration_profile.json` — generated by running frank-data's source-side calibration suite and capturing the output as a JSON manifest with per-solver constants, expected values, and tolerances. Hash of this file becomes part of `VENDOR_VERSION.txt`.
- `vendor/VENDOR_VERSION.txt` — re-generated by `scripts/vendor_sync.py --update-version-manifest` to populate all four fields with real values:
  - `phoenix_release: 1.0.0.dev1`
  - `vendor_synced_at: 2026-05-06T<HH:MM:SS>+00:00`
  - `dr_frank_and_eddy_commit: <40-char SHA from C:\Phoenix-vendor-source\frank-data\>`
  - `calibration_profile_hash: <SHA-256 of vendor/calibration_profile.json>`

**Calibration suite mechanics:** the build guide assumes `C:\frank-data\` has a calibration runner (likely under `tests/`, `synthesis/equations/specs/`, or similar) that produces the profile. **[OPEN: the exact entry point for the source-side calibration battery — Adam to confirm during Step 5 execution. Possibilities: a pytest target, a CLI script, a manually-invoked function. If no single entry point exists, Step 5's first sub-task is to compose one — run each Tier-1 benchmark via a small helper, collect reference values, emit calibration_profile.json with the structure below.]**

```json
{
  "profile_version": "<frank-data tag or commit short hash>",
  "generated_at": "2026-05-06T...",
  "source_commit": "<frank-data SHA>",
  "tier_1": {
    "HO-1": { "expected_energy_eigenvalues": [...], "tolerance": 1e-9 },
    "ISW-1": { ... },
    "H1S-1": { ... },
    "RABI-1": { ... },
    "SCG-1": { ... }
  }
}
```

**Verification:**

```powershell
python scripts/vendor_sync.py --generate-calibration
Test-Path vendor/calibration_profile.json                    # → True
$prof = Get-Content vendor/calibration_profile.json | ConvertFrom-Json
$prof.tier_1.PSObject.Properties.Name                         # → HO-1, ISW-1, H1S-1, RABI-1, SCG-1

python scripts/vendor_sync.py --update-version-manifest
Get-Content vendor/VENDOR_VERSION.txt
# Expected: all four fields populated
python -c "from phoenix._internal.version import read_vendor_version; v = read_vendor_version(); assert v['vendor_synced_at'] != ''; print('OK')"
```

**[OPEN: should the vendored calibration profile be committed to Phoenix's git, or generated fresh on every release? Committed-to-git makes vendor sync deterministic; generated-fresh catches drift in frank-data between releases. v1 disposition: commit to git as a frozen artifact (so a Phoenix re-clone gets the calibrated state without needing to re-run frank-data's calibration suite). Drift between generations surfaces during the next vendor sync.]**

```
=== STEP 5 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.6 — Step 6: Tier-1 + grammar invariants + DPD self-test execution

**What lands:**
- `tests/tier1/` directory with the five Tier-1 benchmarks vendored from frank-data (or composed fresh if the source doesn't have a single `tests/tier1/` directory).
- `tests/invariants/` directory with the 31-test grammar invariant suite vendored alongside the grammar per architecture §3.2.
- `tests/dpd/` directory with the DPD engine's self-test (verifies trace preservation, positivity, RK4 4th-order convergence).

**Vendoring caveat:** the grammar invariant suite ships *with* the vendored grammar per Section 3.2 ("vendored alongside the code"). So the 31-test suite probably lives under `vendor/grammar/tests/` after vendoring. The `tests/invariants/` Phoenix-side directory then contains a thin wrapper that pytest-collects against the vendored suite.

**Phoenix-side `pyproject.toml` deps update:** the Tier-1 battery requires `numpy` and `scipy` for the solvers, plus `pyyaml` for grammar loading. Phase 1 adds:

```toml
dependencies = [
    "fastapi>=0.115,<0.120",
    "uvicorn[standard]>=0.27,<0.35",
    "pydantic>=2.6,<3.0",
    "numpy>=1.26,<3.0",         # Phase 1: vendored solvers' core dep
    "scipy>=1.11,<2.0",         # Phase 1: vendored solvers' linear-algebra + ODE
    "pyyaml>=6.0,<7.0",         # Phase 1: vendored grammar's YAML loader
]
```

Heavier deps wait for the phase that actually invokes them: `qutip` for DPD verification → Phase 3, cloud quantum SDKs → Phase 4+.

**Verification:**

```powershell
# Update deps + reinstall
python -m pip install -e ".[dev]"

# Run Tier-1 battery
python -m pytest tests/tier1/ -v
# → 5 passed (HO-1, ISW-1, H1S-1, RABI-1, SCG-1)

# Run grammar invariants
python -m pytest tests/invariants/ -v
# → 31 passed (vendored invariant suite)

# Run DPD self-test
python -m pytest tests/dpd/ -v
# → trace preservation, positivity, RK4 4th-order convergence

# Combined (existing Phase 0 tests still pass)
python -m pytest tests/ -v
# → 5 (Phase 0) + 5 (Tier-1) + 31 (invariants) + N (DPD) all pass
```

**[OPEN: the Tier-1 test runners need to load `vendor/calibration_profile.json` to know what values to expect. The test fixtures should reference `phoenix._internal.version.read_vendor_version()` to confirm the profile is present, and load the profile via a helper. Phase 7 (audit + ledger + drift) integrates this with the drift detector; for Phase 1, the tests load the profile directly.]**

```
=== STEP 6 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.7 — Step 7: Phase 1 acceptance + version bump + push

**What lands:**

1. **Version bump:** `pyproject.toml` and `phoenix/_internal/version.py` move from `1.0.0.dev0` to `1.0.0.dev1` (Phase 1 development release per the PEP 440 sequence).
2. **Test updates:** `tests/unit/test_smoke.py` and `tests/integration/test_health.py` update their version assertions; the smoke test additionally asserts `read_vendor_version()` returns non-empty hash fields (was empty in Phase 0).
3. **CHANGELOG entry:** new `## [1.0.0.dev1] — 2026-05-06` entry covering the vendored substrate landing, Tier-1 + invariants + DPD self-test passing, and any open tensions raised during Phase 1.

**Acceptance checklist:**
- ✅ `vendor/` is populated with frank-data content; `VENDOR_VERSION.txt` has all four fields with real values.
- ✅ `vendor/calibration_profile.json` exists and is referenced by `read_vendor_version()`.
- ✅ `python -c "from synthesis.equations.base import EquationSolver"` succeeds (sys.path injection works).
- ✅ `pytest tests/tier1/` passes 5/5.
- ✅ `pytest tests/invariants/` passes 31/31 (or the actual count from frank-data's invariant suite).
- ✅ `pytest tests/dpd/` passes.
- ✅ `pytest tests/` (combined Phase 0 + Phase 1 tests) all pass.
- ✅ `pre-commit run --all-files` clean.
- ✅ `python -m phoenix.api --port 8003` boots; `GET /v1/health` shows the new `phoenix_version=1.0.0.dev1` and the populated `vendor_manifest`.
- ✅ `git status` reports working tree clean after staging Phase 1.

**Push:**

```powershell
Set-Location C:\Phoenix
git push origin main
```

```
=== STEP 7 COMPLETE — PHASE 1 SHIPPED ===
```

## 4 — What's not in Phase 1

Explicitly out of scope:

| Item | Phase | Build guide |
|---|---|---|
| Wiring vendored solvers through Trinity Core's pipeline | Phase 2 | BUILDGUIDE_phoenix_v1_phase2_solver.md |
| Wiring DPD engine into Control subsystem | Phase 3 | BUILDGUIDE_phoenix_v1_phase3_control_orchestrate.md |
| Building Orchestrate subsystem (greenfield, NOT vendored) | Phase 3 | (same) |
| Provider routing | Phase 4 | BUILDGUIDE_phoenix_v1_phase4_router.md |
| Verification gate (wobble protocol orchestration) | Phase 5 | BUILDGUIDE_phoenix_v1_phase5_verification.md |
| Safety gate + identity + state + queue | Phase 6 | BUILDGUIDE_phoenix_v1_phase6_safety_state_queue.md |
| Audit log + Omega Ledger + drift monitor | Phase 7 | BUILDGUIDE_phoenix_v1_phase7_audit_ledger.md |
| Admin endpoints + kill switch | Phase 8 | BUILDGUIDE_phoenix_v1_phase8_admin.md |
| LoRA adapter sandbox + MCP server + CLI | Phase 9 | BUILDGUIDE_phoenix_v1_phase9_adapters_mcp_cli.md |
| OTel + cloud seams + standalone binary | Phase 10 | BUILDGUIDE_phoenix_v1_phase10_observability_distribution.md |
| Final §10.7 acceptance + release | Phase 11 | BUILDGUIDE_phoenix_v1_phase11_release.md |

## 5 — Phase 2 preview

Phase 2's job is to wire Trinity Core's Solver subsystem through the pipeline. Concretely:
- `phoenix/trinity/data_model.py` — typed `PhysicsTask`, `CandidateAnswer`, `VerifiedAnswer`, `Result` dataclasses per architecture §2.2.
- `phoenix/trinity/solver/engine.py` — adapts the vendored `EquationSolver` registry into Trinity's pipeline; uses `HamiltonianClassifier::can_handle()` for solver auto-selection.
- `phoenix/trinity/solver/cross_precision.py` — Axis 1 wobble (cross-grid-resolution).
- `phoenix/trinity/pipeline.py` — early three-subsystem pipeline orchestrator (Solver only; Control + Orchestrate land in Phase 3).
- `phoenix/api/routes.py` — gains a `POST /v1/tasks` stub that constructs a `PhysicsTask` and runs it through Solver only (no Control or Orchestrate yet).

Phase 2 does NOT yet implement the verification gate's adaptive depth (Phase 5) and does NOT touch Orchestrate (Phase 3 — greenfield, not vendored).

## 6 — Standing rules carried from Phase 0

1. Phase gates with explicit Adam review (`=== STEP N COMPLETE ===`). No silent advancement.
2. Stop and ask on architectural ambiguity. Mark `[OPEN: ...]` and surface to Adam.
3. PERF and SAFETY callouts inline.
4. Per-section READMEs. Phase 1 updates the existing READMEs in `phoenix/trinity/`, `phoenix/grammar/`, `phoenix/safety/`, `vendor/` to reflect what's now actually vendored (the architecture revision already updated several of these; Phase 1 just adds Phase-1-specific notes).
5. Launcher updated when startup behavior changes. Phase 1 doesn't change startup yet (vendor-integrity check lands in Phase 1.5 or Phase 2's launcher revision).
6. No OneDrive paths.
7. Live reads beat memory. Already exercised: discovered SynQc TDS Core's actual structure differs from the v0 spec, leading to the 2026-05-06 architecture revision and a simpler Phase 1.

```
=== BUILD GUIDE COMPLETE — AWAITING ADAM REVIEW ===
```
