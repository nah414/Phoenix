# vendor/

## Purpose
**Frozen v6.6 substrate** that Phoenix vendors verbatim from dr-frank-and-eddy + SynQc TDS Core per architecture v1 Section 10.2. Read-only at runtime. Phoenix never live-imports from `C:\frank-data\` or the SynQc repo; it vendors stamped copies under `C:\Phoenix\vendor\` so Phoenix releases ship a frozen, dated, reproducible substrate independent of the lab-bench evolution. Phase 0 ships only `VENDOR_VERSION.txt`; Phase 1's vendor sync script populates the actual code.

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 10.2 (vendoring map — file-by-file mapping from upstream sources), Section 10.4 (vendor sync script behavior + safety rails), Section 1 Decision 7 (frozen substrate version-stamped at v6.6, does not auto-update), Decision 9 (dr-frank-and-eddy and SynQc TDS stay untouched as Adam's lab bench).

## Key files and their roles (post Phase 1)
| Path | Source |
|---|---|
| `VENDOR_VERSION.txt` | (Phase 0 — landed) Single source of truth: `phoenix_release`, `vendor_synced_at`, `dr_frank_and_eddy_commit`, `synqc_tds_commit`, `calibration_profile_hash`. |
| `synthesis/equations/` | (Phase 1) 12 equation solvers + base.py + registry.py + llm_context.py + specs/. |
| `synthesis/core/` | (Phase 1) dpd_engine.py + lindblad_rk4.py + probe_model.py + hardware_backends.py. |
| `synthesis/quantum/tensor_lindblad.py` | (Phase 1.x) MPS/TJM path for medium-systems extension. |
| `grammar/` | (Phase 1) grammar_loader.py + generator.py + parser.py + physics_v1.yaml + 31-test invariant suite. |
| `grammar/codec/` | (Phase 1) Sanskrit codec (sanskrit_codec.py + supporting codec_*.py). |
| `wobble/` | (Phase 1) disagreement_types.py + disagreement_classifier.py + supporting files. |
| `actor/` | (Phase 1) Vendored Actor module: typed `Actor`, signing, verification, 5-minute window. |
| `synqc_tds/` | (Phase 1) `scheduler.py`, `probes/`, `demod.py`, `adapt.py`, `provider_clients/` — vendored from SynQc TDS Core. |
| `calibration_profile.json` | (Phase 1) Hash + per-solver calibration constants from the v6.6 calibration suite output. |

## Vendoring discipline
1. Phoenix never modifies vendored code at runtime.
2. The vendor sync script (`scripts/vendor_sync.py`, lands in Phase 1) requires admin permission and refuses to proceed if the source's Tier-1 calibration suite fails.
3. `VENDOR_VERSION.txt` is the single source of truth; the replay path (Section 19–21) reads it to verify the running snapshot matches the ledger entry's recorded versions.
4. Per Section 11.7.1 (resolved verbatim through v1): vendored modules retain their dr-frank-and-eddy import paths internally; sys.path manipulation in `phoenix/__init__.py` exposes them. Path rewriting deferred until empirical maintenance burden justifies the change.

## Phase 0 state
- `vendor/` directory exists.
- `vendor/VENDOR_VERSION.txt` placeholder ships with `phoenix_release: 1.0.0.dev0` and four hash fields empty. The empty hashes are recognized by audit and replay paths as "vendoring not yet performed."
- `read_vendor_version()` in `phoenix/_internal/version.py` returns the parsed dict (with empty values) — earlier in Phase 0 it returned None because the file didn't exist.

## Common failure modes
- After Phase 1: vendor-integrity test fails on `python -m phoenix.cli verify-vendor --quick` if a vendored module's content hash doesn't match `VENDOR_VERSION.txt`. The launcher refuses to start until the integrity check passes.
- Mid-development: a fresh clone without `vendor/` populated runs `scripts/vendor_sync.py` against a known-good `C:\frank-data\` clone; the script enforces the source's commit-manifest acceptance list and re-runs the Tier-1 battery before copying anything.

## Recent changes
- 2026-05-06 — Phase 0 (BUILDGUIDE_phoenix_v1_phase0_skeleton.md): directory + `VENDOR_VERSION.txt` placeholder created (Step 3); module README created (Step 5).
