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

## [1.0.0.dev0] — 2026-05-06

The repository skeleton lands. No physics yet; this release is the foundation
that subsequent phases build on.

### Added

- Locked architecture specification at v1 (`PHOENIX_ARCHITECTURE_v1.md`,
  ~2,900 lines covering Trinity Core's three subsystems, the seven wrapping
  layers, mandatory three-axis wobble verification, hashchained Omega Ledger
  provenance, end-to-end cost-ceiling enforcement, the Phoenix Cloud commercial
  path, and 14 catalogued open design tensions).
- Phase 0 build guide (`BUILDGUIDE_phoenix_v1_phase0_skeleton.md`) directing
  the eight-step skeleton work with phase-gated reviews between each step.
- Top-level repository scaffolding: `pyproject.toml` with pinned upper bounds
  and `>=3.11,<3.13` Python constraint; `requirements.lock` placeholder for
  Phase 1's `uv` lockfile; `.gitignore`, `.gitattributes`,
  `.pre-commit-config.yaml`, `CHANGELOG.md`.

### Changed

- LICENSE switched from MIT (auto-generated at repo creation) to Apache 2.0
  per architecture v1 Decision 34 — open source plus the patent grant as
  belt-and-suspenders against future patent claims on calibration methodology.
- README expanded from the one-line repo-creation placeholder to a project-
  shaped overview with the v1 status table and pointers to the architecture
  spec and the Phase 0 build guide.

### Pending in subsequent Phase 0 steps

- `phoenix/` package skeleton with one directory per architecture Section.
- `vendor/` skeleton with `VENDOR_VERSION.txt` placeholder.
- `scripts/launch.bat`, `scripts/launch.sh`, `scripts/create_shortcut.ps1`.
- 29 per-section READMEs (21 `phoenix/`+`vendor/` + 8 `evals/` subdirectories).
- `tests/unit/test_smoke.py` and `tests/integration/test_health.py`.
- Empty FastAPI daemon at `phoenix/api/routes.py` with `GET /v1/health` only.
