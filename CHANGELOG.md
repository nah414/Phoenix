# Phoenix Changelog

All notable changes to Phoenix are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) loosely; Phoenix's
release cadence is phase-gated rather than calendar-gated, so entries
correspond to phase landings rather than fixed-interval releases.

Version semantics: Phoenix follows [PEP 440](https://peps.python.org/pep-0440/).
Pre-release builds during build-guide phases use `1.0.0.dev<N>` where `<N>`
is the phase number (`1.0.0.dev0` = Phase 0, `1.0.0.dev1` = Phase 1, etc.).
Once Phoenix enters integration testing it moves to `1.0.0a0` (alpha 0),
`1.0.0b0` (beta), `1.0.0rc0` (release candidate), and finally `1.0.0` for
the stable release. PEP 440 compliance is required by setuptools and lets
Phoenix interoperate with pip, uv, and the broader Python tooling ecosystem.

---

## [1.0.0.dev1] — 2026-05-06

Phase 1 lands. The vendored substrate from `dr-frank-and-eddy` is real,
calibrated, and exercisable through Phoenix's package boundary. Trinity
Core's Solver and Control subsystems now have the substrate they need
when Phases 2–3 wire them through the pipeline; Orchestrate stays
greenfield per the 2026-05-06 architecture revision.

### Added

- **Step 0 — frank-data housekeeping.** Adam's lab bench at
  `C:\frank-data\` cleaned: 4 untracked items moved to git
  (DrFrankEddy_Capabilities_Overview_for_Ash.md, evolution/candidates/epoch_0001/),
  `electron-debug.log*` added to `.gitignore`. Commit `fa074e5` on
  `nah414/dr-frank-and-eddy/wave-a-through-f-merge`. Prep for the
  vendor source clone.
- **Step 1 — vendor source workspace.** `C:\Phoenix-vendor-source\frank-data\`
  cloned at commit `fa074e5` (sibling of `C:\Phoenix\`, never inside).
  `Phoenix-vendor-source/` added to Phoenix `.gitignore` defensively.
- **Step 2 — vendor sync infrastructure.** `scripts/vendor_sync.py`
  (~370 lines, type-annotated, mypy-strict-clean) + `scripts/vendor_manifest.json`.
  Eight typed errors, four frozen dataclasses, five CLI modes
  (default / --validate-only / --dry-run / --target / --update-version-manifest /
  --generate-calibration). Admin-gate placeholder via `PHOENIX_ADMIN_OVERRIDE=1`
  env var (Phase 6 replaces with safety-gate Actor check).
- **Step 3 — frank-data substrate vendored.** 56 files / 379 KB copied
  per the manifest's 11 mappings: `vendor/synthesis/equations/` (29
  files, 12 solvers + base + registry + llm_context + 12 specs +
  README + __init__), `vendor/synthesis/core/` (6: dpd_engine,
  lindblad_rk4, probe_model, hardware_backends + README + __init__),
  `vendor/synthesis/quantum/tensor_lindblad.py`, `vendor/grammar/`
  (11: 6 grammar files + 5 codec files), `vendor/actor/actor.py`,
  `vendor/wobble/` (6 files). `pyproject.toml` `[tool.ruff]` gains
  `extend-exclude = ["vendor"]` so vendored substrate keeps upstream
  formatting verbatim.
- **Step 4 — sys.path injection.** `phoenix/__init__.py` defines
  `_inject_vendor_path()` that appends `C:\Phoenix\vendor\` to
  `sys.path` on package load. Vendored modules now import at their
  upstream paths (`from synthesis.equations.base import EquationSolver`).
  `[[tool.mypy.overrides]]` block silences mypy on `synthesis.*`,
  `wobble.*`, `grammar.*`, `actor.*` (the vendored code has no type
  stubs and is excluded from analysis already).
- **Step 5 — calibration profile generation.** `vendor/calibration_profile.json`
  (3.9 KB) ships with the source-side calibration suite results: 32/32
  tests passing in 1.4 seconds, all module-level physical constants
  captured (HBAR, M_ELECTRON, MU_BOHR, C_LIGHT, G_NEWTON, EV_TO_JOULE),
  source commit + branch + ISO timestamp. `vendor/VENDOR_VERSION.txt`'s
  `calibration_profile_hash` field now populated.
- **Step 6 — Tier-1 + invariants + DPD test infrastructure.** 13 new
  Phoenix-side tests across three directories:
  - `tests/tier1/`: 5 nominal Tier-1 benchmarks (HO-1 QHO, ISW-1 PIB,
    H1S-1 Dirac, RABI-1 Pauli/Zeeman, SCG-1 weak-field gravity).
  - `tests/invariants/`: 4 grammar invariants (load, safe-load
    discipline, generate-then-parse round-trip, bounded generation).
  - `tests/dpd/`: 4 DPDScheduler structural tests.
  - Runtime deps grow: `numpy>=1.26,<3.0`, `scipy>=1.11,<2.0`,
    `pyyaml>=6.0,<7.0`.
- **Step 7 — Phase 1 acceptance + push.** Version bumps `1.0.0.dev0` →
  `1.0.0.dev1` in `pyproject.toml`, `phoenix/_internal/version.py`,
  test assertions. `vendor/VENDOR_VERSION.txt` regenerated with
  `phoenix_release: 1.0.0.dev1`. `/v1/health` end-to-end check confirms
  daemon serves dev1 with full vendor manifest.

### Changed

- `phoenix.__version__`: `1.0.0.dev0` → `1.0.0.dev1`.
- `vendor/VENDOR_VERSION.txt`: all four hash fields populated (was
  Phase 0 placeholder with empty values).
- `tests/unit/test_smoke.py::test_internal_version_module`: now asserts
  `vendor_synced_at`, `dr_frank_and_eddy_commit`, `calibration_profile_hash`
  are non-empty (Phase 0 had asserted them empty as the placeholder).
- `tests/integration/test_health.py::test_health_returns_200_and_expected_shape`:
  same flip on the `/v1/health` response shape assertions.

### Open architectural drifts (logged for follow-up before Phase 5)

Three spec-vs-source naming drifts surfaced during Phase 1 execution
when the test code touched the actual vendored API:

1. **`AgreementType` (spec §6.2) vs `DisagreementType` (vendored).**
   The architecture spec names the wobble enum `AgreementType` with
   extended physics-wobble values; the actual vendored `wobble/disagreement_types.py`
   has `class DisagreementType(Enum)` (the upstream cognition-wobble name).
2. **`DPDEngine` (spec) vs `DPDScheduler` (vendored).** Spec references
   `DPDEngine`; the actual vendored class in `synthesis/core/dpd_engine.py`
   is `DPDScheduler`.
3. **`ProbeType.STRONG` (spec implied) vs `ProbeType.STRONG_PROJECTIVE`
   (vendored).** The vendored enum value spells out `STRONG_PROJECTIVE`,
   `WEAK_MEASUREMENT`, `ANCILLA_BASED`, `NONE`.

Phase 1's Phoenix-side tests use the real (vendored) class names and
pass. A single spec-drift-correction commit before Phase 5 (verification
gate work) will resolve all three: rename in spec, alias on Phoenix
side, or accept upstream names as authoritative.

### Acceptance

Phase 1 acceptance (build guide §3.7):
- ✅ `vendor/` populated with frank-data content; `VENDOR_VERSION.txt`
  has all four fields (phoenix_release, vendor_synced_at,
  dr_frank_and_eddy_commit, calibration_profile_hash) with real values.
- ✅ `vendor/calibration_profile.json` exists, hash matches `VENDOR_VERSION.txt`.
- ✅ `python -c "from synthesis.equations.base import EquationSolver"` works.
- ✅ `pytest tests/tier1/`: 5/5 (HO-1, ISW-1, H1S-1, RABI-1, SCG-1).
- ✅ `pytest tests/invariants/`: 4/4 grammar invariants.
- ✅ `pytest tests/dpd/`: 4/4 DPD structural tests.
- ✅ `pytest tests/`: 19/19 combined.
- ✅ `pre-commit run --all-files`: ruff, ruff-format, mypy strict, smoke -- all 4 Passed.
- ✅ `python -m phoenix.api --port 8003`: daemon boots; `GET /v1/health`
  returns `phoenix_version=1.0.0.dev1` and the full vendor manifest.
- ✅ `git status`: working tree clean after Step 7 commit.

### Process notes

- 7 phase-gated commits + 1 housekeeping commit in `frank-data`. Each
  Phoenix-side step ended at `=== STEP N COMPLETE — AWAITING ADAM REVIEW ===`;
  no auto-advancement.
- Build-guide sequencing fix: Phase 1's Step 4 (sys.path) and Step 5
  (calibration generation) both surfaced live in execution rather than
  ahead of time -- they were `[OPEN: ...]` items in the Phase 1 build
  guide that resolved as the code was written.

---

## [1.0.0.dev0] — 2026-05-06

The repository skeleton lands. No physics yet; this release is the foundation
that subsequent phases build on. All eight Phase 0 build-guide steps executed
through phase-gated review with Adam; final acceptance verified end-to-end.

### Added

- **Architecture specification** at v1 (`PHOENIX_ARCHITECTURE_v1.md`, ~2,900
  lines covering Trinity Core's three subsystems, the seven wrapping layers,
  mandatory three-axis wobble verification, hashchained Omega Ledger
  provenance, end-to-end cost-ceiling enforcement, the Phoenix Cloud
  commercial path, and 14 catalogued open design tensions).
- **Phase 0 build guide** (`BUILDGUIDE_phoenix_v1_phase0_skeleton.md`)
  directing the eight-step skeleton work with phase-gated reviews between
  each step.
- **Top-level scaffolding** (Step 1): `pyproject.toml` with pinned upper
  bounds and `>=3.11,<3.14` Python constraint; `requirements.lock`
  placeholder for Phase 1's `uv` lockfile; `.gitignore`, `.gitattributes`,
  `.pre-commit-config.yaml` (ruff + mypy strict + smoke-test), `CHANGELOG.md`.
- **`phoenix/` package skeleton** (Step 2): 26 directories with `__init__.py`,
  one per architectural Section. Two non-empty: `phoenix/__init__.py` exports
  `__version__`; `phoenix/_internal/version.py` defines the constant +
  `read_vendor_version()`.
- **`vendor/` scaffold** (Step 3): directory + `VENDOR_VERSION.txt`
  placeholder with `phoenix_release: 1.0.0.dev0` and four hash fields empty
  (Phase 1 vendor sync populates).
- **Launcher chain** (Step 4): `scripts/launch.bat`, `scripts/launch.sh`,
  `scripts/create_shortcut.ps1` — Phoenix daemon on port 8003 (port 8002
  reserved for dr-frank-and-eddy).
- **29 per-section READMEs** (Step 5): 21 across `phoenix/`+`vendor/` +
  8 in the `evals/` audit/debug scaffold (audit, ledger, replay, drift,
  routing, cost_ceiling, frontier_physics).
- **Test infrastructure + FastAPI daemon** (Steps 6+7 combined due to
  inter-step dependency): `tests/unit/test_smoke.py` (3 tests),
  `tests/integration/test_health.py` (2 tests using FastAPI TestClient),
  `phoenix/api/routes.py` (FastAPI app exposing `GET /v1/health`),
  `phoenix/api/__main__.py` (`python -m phoenix.api --port 8003`),
  `phoenix/api/error_envelope.py` (typed envelope dataclass per §5.2).

### Changed

- **LICENSE**: switched from MIT (auto-generated at repo creation) to Apache 2.0
  per architecture v1 Decision 34 — open source plus the patent grant as
  belt-and-suspenders against future patent claims on calibration methodology.
- **README**: expanded from the one-line repo-creation placeholder to a
  project-shaped overview with the v1 status table and pointers to the
  architecture spec and the Phase 0 build guide.

### Fixed

- **PEP 440 version compliance**: setuptools rejected the originally-chosen
  `1.0.0-phase0` literal as not-PEP-440. All artifacts now use `1.0.0.dev0`
  (Phase 0 development pre-release per PEP 440); subsequent phases use
  `1.0.0.dev1`, `1.0.0.dev2`, etc.
- **Python version constraint**: widened from `>=3.11,<3.13` to `>=3.11,<3.14`
  to accommodate the actual development environment (Python 3.13.9). The
  upper-bound discipline from the dep-tightening pass is preserved (3.14
  stays gated until validated).

### Acceptance

Phase 0 acceptance criteria from build guide §3.8 (15 items):

- ✅ `python -c "import phoenix; print(phoenix.__version__)"` returns `1.0.0.dev0`.
- ✅ `pytest tests/` passes 5/5 (3 unit + 2 integration via FastAPI TestClient).
- ✅ `pytest evals/ --collect-only` exits 5 ("no tests collected") — expected
  Phase 0 state since `evals/` ships placeholder READMEs only.
- ✅ `python -m phoenix.api --port 8003` boots the daemon; `/v1/health`
  responds with the expected contract; `/v1/openapi.json` serves OpenAPI 3.1
  with the matching version.
- ✅ All 29 READMEs present and non-empty.
- ✅ `pre-commit install` placed the hook; `pre-commit run --all-files` clean
  on all four hooks (ruff, ruff-format, mypy strict, pytest smoke-test).
- ✅ Working tree clean after commits.
- ⏸ Interactive: browser at `http://127.0.0.1:8003/docs` (Adam-driven).
- ⏸ Interactive: `scripts/launch.bat` end-to-end (Adam-driven).
- ⏸ Interactive: `scripts/create_shortcut.ps1` + double-click flow (Adam-driven).

### Process notes

- Step 6 and Step 7 were combined into a single commit because the build
  guide's sequencing was wrong: the integration test depends on the daemon
  code. Documented in the commit message; a future build-guide revision
  should re-sequence (or merge) the two steps.

### Architecture revision (2026-05-06, post Phase 0)

- **`PHOENIX_ARCHITECTURE_v1.md` revised** to remove the v0 spec's
  internal contradiction around SynQc TDS Core. Decision 37 originally
  said "code skeleton, not literal git fork" but other places in the
  spec (§1 Decision 4, §2.5, §10.2) said SynQc was vendored verbatim
  alongside frank-data. The revised spec aligns with Decision 37: SynQc
  is a *design reference* for Trinity Core's Orchestrate subsystem;
  Orchestrate is greenfield Phoenix code under
  `phoenix/trinity/orchestrate/`, not vendored.
- **Affected sections:** §1 Decisions 4, 5, 7, 9, 37 (reworded);
  §2.5 (rewritten — Orchestrate as greenfield with Phoenix-native
  module breakdown: bundle_builder, provider_client, result_extractor,
  drift_feedback, cross_provider, kpi_bundle, engine);
  §10.1 (drops `vendor/synqc_tds/` from directory tree);
  §10.2 (drops the SynQc TDS vendoring table; updates VENDOR_VERSION
  format to remove `synqc_tds_commit` field);
  §10.3 (`phoenix/trinity/orchestrate/` description specifies the
  seven Phoenix-native modules);
  §10.4 (vendor sync script takes only frank-data as input);
  preamble adds a v1-revision transition note.
- **Phase 0 README updates:** `phoenix/trinity/orchestrate/README.md`,
  `phoenix/trinity/README.md`, `phoenix/providers/README.md`,
  `vendor/README.md` updated to match the revised spec.
- **`vendor/VENDOR_VERSION.txt`** drops the `synqc_tds_commit` field.
- **Phase 0 build guide** stale `synqc_tds_commit:` example updated.
- **Phase 1 build guide drafted** at `BUILDGUIDE_phoenix_v1_phase1_vendor_sync.md`
  reflecting the simpler frank-data-only scope (8 phase-gated steps,
  vs. 9 in the original draft that included a SynQc-vendoring step).
- **Discovery driver:** Phase 1 build-guide drafting against actual
  source state (the SynQc zip in Adam's Downloads) found that SynQc's
  module structure (`backend/synqc_backend/` FastAPI service) didn't
  match the v0 spec's named files (`scheduler.py`, `probes/`,
  `demod.py`, `adapt.py`). Live reads beat memory.

The architecture's load-bearing structure (seven layers, three peer
engines, mandatory three-axis wobble, hashchained provenance, Phoenix
Cloud commercial path, fourteen open tensions, all v1 acceptance
criteria) is unchanged. The revision narrows the substrate that
Phoenix vendors and clarifies that Orchestrate is Phoenix-native code
informed by SynQc patterns, not vendored from SynQc.
