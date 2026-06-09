# BUILDGUIDE -- Phoenix v1 Phase 12 (Distribution + Release Artifacts)

**Phase:** 12 of v1 (final pre-release phase)
**Version target:** `1.0.0.dev12` -> `1.0.0.rc1`
**Architectural reference:**
- Section 1 Decision 29 (three release artifacts: pip wheel, Docker image, Nuitka standalone binary)
- Section 1 Decision 30 (CI tests all three before release)
- Section 1 Decision 32 (NATS JetStream regardless of deployment size)
- Section 1 Decision 33 (solo Phoenix install boots two processes -- Phoenix + NATS)
- Section 5.4 + Section 11.3.3 RESOLVED ("bundle daemon, with `--external-daemon` flag")
- Section 7.2 (same-OS-user threat model -- no privilege elevation at install)
- Section 10.4 (vendor/ frozen at distribution-build time; never re-synced at install/runtime)

**Start state:** main @ `706bd7f` (Phase 11 merged, `1.0.0.dev12`).
**End state:** main + Phase 12 merged, three working distribution artifacts on a CI
matrix (Linux + Windows), docs cover install/run for all three, version `1.0.0.rc1`.

## Locked scope decisions (Phase 12 entry, 2026-05-14)

1. **Cross-platform matrix: Linux + Windows.** macOS deferred to v1.1. Rationale:
   minimal user base, doubles CI matrix complexity (Apple-Silicon native build chain),
   and the Apache 2.0 surface lets community contributors add macOS if demand surfaces.
2. **Docker base: `python:3.12-slim`.** Official, plays well with numpy/scipy/pyyaml
   wheels (alpine's musl-libc breaks scientific Python without manual wheel builds).
   ~50 MB compressed image; Phoenix + bundled NATS adds ~30 MB.
3. **Code signing: deferred to v1.0 release-prep.** Certificate provisioning + signing
   pipelines are a separate workstream from "the artifact builds correctly." Phase 12
   ships unsigned artifacts; v1.0 release prep adds signing.
4. **CI: GitHub Actions.** Repo is on `nah414/Phoenix`. GitHub-hosted runners cover
   linux + windows + macos at zero cost for public repos; macOS runner stays unused in v1.
5. **Standalone binary: Nuitka** per Section 1 Decision 29. Not PyInstaller.

## Step decomposition (10 phase-gated steps)

Each step ends with `=== STEP N COMPLETE -- AWAITING ADAM REVIEW ===`. No advancement
without explicit approval. Per-step pre-commit + pytest gate before commit.

| # | Step | New/modified files |
|---|---|---|
| 1 | pyproject.toml packaging tightening (classifiers, urls, package-data, build hooks) | `pyproject.toml` |
| 2 | `phoenix/launcher.py` -- standalone-binary entry; `--external-daemon` flag | `phoenix/launcher.py`, `phoenix/__main__.py` |
| 3 | MANIFEST.in + sdist hygiene audit | `MANIFEST.in`, `pyproject.toml` (find-config tweaks) |
| 4 | Dockerfile (multi-stage, `python:3.12-slim`) + `.dockerignore` + entrypoint | `Dockerfile`, `.dockerignore` |
| 5 | `scripts/build_standalone.py` -- Nuitka wrapper for Win + Linux | `scripts/build_standalone.py` |
| 6 | `scripts/launch_with_nats.sh` -- Linux mirror of the Windows NATS-bundled launcher | `scripts/launch_with_nats.sh` |
| 7 | GitHub Actions CI matrix (Linux + Windows, build + test all 3 artifacts) | `.github/workflows/ci.yml`, `.github/workflows/release.yml` |
| 8 | `docs/distribution/` + `docs/reproducibility/` (cloud-shots asterisk) | `docs/distribution/README.md`, `docs/distribution/install.md`, `docs/distribution/run.md`, `docs/reproducibility/README.md` |
| 9 | Distribution acceptance battery (`@pytest.mark.distribution`) | `tests/distribution/test_wheel_install.py`, `tests/distribution/test_docker_smoke.py`, `tests/distribution/test_standalone_binary.py` |
| 10 | Version bump `1.0.0.dev12` -> `1.0.0.rc1` + CHANGELOG + PR | `pyproject.toml`, `phoenix/_internal/version.py`, all `_DEFAULT_PHOENIX_RELEASE`, `CHANGELOG.md`, `README.md`, `vendor/VENDOR_VERSION.txt` |

## Critical files to be modified or created

- **`pyproject.toml`** (modified, Step 1) -- adds `classifiers` (OSI license, Python
  versions, OS, intended audience, topic), `[project.urls]` (homepage, repository,
  changelog, documentation), tightens `[tool.setuptools.packages.find]` to also
  exclude `docs*` (already there) and explicit include of `phoenix.*` subpackages,
  adds `[tool.setuptools.package-data]` to ship the per-section README files inside
  the wheel (so `pip install phoenix-middleware` users can introspect them).

- **`phoenix/launcher.py`** (new, Step 2) -- the standalone-binary entry. Spawns
  NATS (if not `--external-nats`), spawns the daemon (if not `--external-daemon`),
  opens docs URL in default browser, traps Ctrl+C to clean up child processes. Per
  Section 1 Decision 33, the solo install boots Phoenix + NATS together; the
  `--external-*` flags are the opt-out for sophisticated users running components
  under systemd / nssm / docker-compose separately.

- **`phoenix/__main__.py`** (new, Step 2) -- thin shim so `python -m phoenix`
  invokes `phoenix.launcher.main`. Mirrors `python -m phoenix.api` (which keeps the
  daemon-only entry for backward compat with the launch scripts).

- **`MANIFEST.in`** (new, Step 3) -- pip's sdist build (via `python -m build`) reads
  this to decide what to include in the source distribution beyond the wheel's
  package contents. Explicit `include LICENSE README.md CHANGELOG.md
  PHOENIX_ARCHITECTURE_v1.md`, `graft vendor`, `graft phoenix` (READMEs), `prune
  tests`, `prune evals`, `prune .github`, `prune scripts`, `global-exclude *.pyc
  __pycache__`. The vendor/ tree IS shipped in the sdist (and via package-data in
  the wheel) per Section 10.4 -- vendor is frozen at distribution-build time.

- **`Dockerfile`** (new, Step 4) -- multi-stage:
  - Stage 1 (`builder`): `python:3.12-slim` + build deps; `pip wheel . -w /wheels`;
    fetch and verify nats-server binary checksum.
  - Stage 2 (`runtime`): `python:3.12-slim` minus build deps; copy `/wheels/`,
    `pip install --no-deps /wheels/*.whl`; copy nats-server to `/usr/local/bin/`;
    `useradd -r phoenix`; `ENTRYPOINT ["python", "-m", "phoenix"]`. Exposes port
    8003 (daemon) and 4222 (NATS client). Healthcheck hits `/v1/health`.

- **`.dockerignore`** (new, Step 4) -- excludes `.git`, `.github`, `tests/`,
  `evals/`, `__pycache__`, `*.pyc`, `dist/`, `build/`, `.venv*`, `.mypy_cache`,
  `.ruff_cache`. Otherwise the build context bloats to gigabytes from vendor/'s
  test fixtures.

- **`scripts/build_standalone.py`** (new, Step 5) -- Python script that invokes
  Nuitka with the right flags for FastAPI/uvicorn (Nuitka needs explicit
  `--include-package` hints for runtime-discovered modules). Builds for the host
  platform (Win .exe on Windows, Linux ELF on Linux). Output: `dist/phoenix-<os>-<arch>`
  single-file executable. Drops nats-server into a `dist/phoenix-<os>-<arch>.d/`
  sidecar directory the binary discovers via `_MEIPASS`-equivalent runtime path.

- **`scripts/launch_with_nats.sh`** (new, Step 6) -- Linux equivalent of
  `launch_with_nats.bat`. Bash with `set -euo pipefail` + `trap` for clean Ctrl+C
  shutdown of both NATS and Phoenix. Verifies nats-server on PATH; uses
  `~/.phoenix/runtime/nats/` for JetStream storage (mirrors Windows
  `%USERPROFILE%\.phoenix\runtime\nats`).

- **`.github/workflows/ci.yml`** (new, Step 7) -- matrix CI on every push + PR:
  - jobs: `lint` (ruff + ruff-format + mypy), `tests-unit` (pytest unit + smoke),
    `tests-integration` (FastAPI TestClient + httpx ASGI), `tests-acceptance`
    (`@pytest.mark.acceptance`).
  - matrix: `os: [ubuntu-latest, windows-latest]` x `python: ['3.11', '3.12', '3.13']`.

- **`.github/workflows/release.yml`** (new, Step 7) -- triggered on release tag:
  - builds wheel + sdist via `python -m build`, uploads as artifact.
  - builds Docker image via `docker build`, smoke-tests it, pushes to ghcr.io.
  - builds Nuitka standalone for Linux + Windows, smoke-tests each, uploads as
    artifact.
  - creates GitHub Release with all three artifacts attached.

- **`docs/distribution/README.md`** (new, Step 8) -- index page; one-paragraph each
  for the three artifacts; trade-off table (size / install complexity / sandboxing).

- **`docs/distribution/install.md`** (new, Step 8) -- step-by-step for each:
  - `pip install phoenix-middleware` (with `[postgres]`, `[nats]`, `[mcp]`, `[otel]`
    extras explained)
  - `docker pull ghcr.io/nah414/phoenix:1.0.0rc1 && docker run -p 8003:8003 ...`
  - Download standalone binary from GitHub Releases, chmod +x, run.

- **`docs/distribution/run.md`** (new, Step 8) -- runtime topology notes; the
  two-process model (Phoenix + NATS) explained; `--external-daemon` and
  `--external-nats` flags documented; healthcheck endpoints; log locations.

- **`docs/reproducibility/README.md`** (new, Step 8) -- the cloud-shots-recorded
  asterisk doc from Section 11 RESOLVED dispositions. Clarifies that strict /
  replay mode guarantees post-shot bit-exactness for cloud-quantum solves; the
  original cloud run cannot be reproduced bit-exactly because cloud shots are
  intrinsically nondeterministic. Cross-references `cloud_shots_recorded`
  provenance field on `Result`.

- **`tests/distribution/`** (new directory, Step 9) -- distribution acceptance
  tests under `@pytest.mark.distribution`. These are EXPENSIVE (build artifacts,
  spawn containers); they don't run in the unit/integration pytest sweep and
  only run in CI release workflow + manual invocation.

- **`pyproject.toml` + `phoenix/_internal/version.py` + sqlite_backend/postgres_backend
  `_DEFAULT_PHOENIX_RELEASE` + drift_detector `phoenix_release` default kwarg + all
  test version assertions + `vendor/VENDOR_VERSION.txt` + `README.md` + `CHANGELOG.md`**
  (modified, Step 10) -- version bump in lockstep. 1.0.0.dev12 -> 1.0.0.rc1.

## Reused existing functions and patterns

- **`scripts/launch_with_nats.bat`** -- Step 6's `launch_with_nats.sh` mirrors its
  structure: verify nats-server on PATH, create storage dir, boot NATS, sleep
  briefly, boot Phoenix, trap shutdown to kill NATS.
- **`scripts/launch.sh`** -- the bash idiom (trap on INT/TERM, wait for daemon PID)
  carries directly to `launch_with_nats.sh`.
- **`phoenix/api/__main__.py`** -- the argparse pattern + uvicorn.run boot sequence
  is the template `phoenix/launcher.py` reuses (it delegates to
  `phoenix.api.__main__.main` after spawning NATS).
- **`tests/acceptance/`** (Phase 11) -- the `@pytest.mark.acceptance` marker setup
  + `tests/conftest.py` skip-when-no-deps pattern is the template for
  `@pytest.mark.distribution`.

## Risk register

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | Nuitka may fail to discover dynamically-imported modules (uvicorn workers, pydantic plugins) | Medium | Step 5's build script enumerates known-needed packages via `--include-package` flags; Step 9's acceptance test boots the standalone binary and hits `/v1/health` -- any import-time failure surfaces immediately. Fall back to `--standalone --onefile` debug if `--onefile-no-progress` fails. |
| R2 | Docker image size bloats from vendor/ test fixtures | Medium | `.dockerignore` prunes `tests/` from build context; multi-stage build copies only the wheel + nats-server into runtime stage. Target: < 200 MB compressed. Step 9 acceptance asserts image size < 250 MB. |
| R3 | nats-server checksum URL drifts when NATS team rotates downloads | Low | Pin to a specific NATS release (v2.10.x latest stable as of build); checksum recorded in Dockerfile + `scripts/build_standalone.py`. Annual review during vendor sync cadence. |
| R4 | GitHub Actions Windows runner missing build deps for numpy/scipy native compilation | Low | Use wheels from PyPI (we don't compile numpy/scipy ourselves); pip install reuses the already-built wheels. Windows runner has VS Build Tools preinstalled for any fallback compilation. |
| R5 | Standalone binary on Linux may need glibc version compatibility | Medium | Build on `ubuntu-20.04` (oldest LTS GitHub Actions supports) -- glibc 2.31, which covers ~99% of Linux installs. Document the glibc floor in `docs/distribution/install.md`. |
| R6 | `--external-daemon` flag behavior under-specified (does the binary connect to a remote daemon, or local-only?) | Medium | Phase 12 ships local-only -- `--external-daemon` skips spawning the daemon but still hits `http://localhost:8003/v1/health` to verify reachability before opening docs URL. Remote-daemon support is v1.1 scope when the CLI itself becomes the "remote" client. |
| R7 | Vendor/ test fixtures balloon sdist size beyond PyPI's 100 MB hard limit | Medium | Check sdist size at Step 3 acceptance (`du -sh dist/*.tar.gz`); if > 80 MB, exclude vendor test data subdirs in MANIFEST.in. The vendored *source* (synthesis/wobble/grammar/actor/omega/ml) is required at runtime; vendor tests are not. |
| R8 | Wheel install on Postgres-only install (no `[nats]`) fails because launcher.py imports nats-py | Low | `phoenix/launcher.py` guards NATS spawn behind `--external-nats` opt-out AND a try/except on `import nats` failure (fall through to `--external-nats` mode with a printed warning). The wheel install path that doesn't include `[nats]` still works. |
| R9 | Code-signing absence triggers Windows SmartScreen on first download | Known | Deferred per locked scope. Documented in `docs/distribution/install.md` -- users see a SmartScreen warning on the unsigned standalone binary in v1.0.rc; v1.0 final ships signed. |

## Verification

End-to-end after Phase 12:

```powershell
Set-Location C:\Phoenix
# Pre-merge gate
pytest tests/ -v                                  # Unit + integration green
pytest tests/acceptance -v -m acceptance          # Section 10.7 still green
pytest tests/distribution -v -m distribution      # New Phase 12 acceptance green
pre-commit run --all-files                        # Lint + format + mypy strict clean

# Build all three artifacts locally
python -m build                                   # dist/phoenix_middleware-1.0.0rc1-py3-none-any.whl + .tar.gz
docker build -t phoenix:1.0.0rc1 .                # local Docker image
python scripts/build_standalone.py                # dist/phoenix-windows-x64.exe (or linux ELF)

# Smoke each
python -m venv .venv-test && .venv-test\Scripts\activate
pip install dist/phoenix_middleware-1.0.0rc1-py3-none-any.whl
phoenix --version                                 # 1.0.0rc1
phoenix health                                    # 200 OK (after daemon boots)

docker run -d --rm -p 8003:8003 phoenix:1.0.0rc1
curl http://localhost:8003/v1/health              # 200 OK

dist\phoenix-windows-x64.exe --version            # 1.0.0rc1
dist\phoenix-windows-x64.exe                      # opens docs URL, daemon starts

git status                                        # clean
```

Per-step verification commands match the prior phase build-guide pattern: each step's
stop-gate runs `pytest <new test file> -v` and a small smoke that exercises the
just-landed surface.

## After Phase 12

Phase 12 is the **final v1 phase**. Post-Phase-12, the work splits between:

- **v1.0 release prep** (post-Phase-12, pre-release work): code signing, release-notes
  draft, security disclosure process, license-audit pass, public-repo readme polish,
  PyPI account + ghcr.io repo setup, signed-binary GitHub release.
- **v1.1 backlog** (deferred items): macOS standalone build, remote-daemon support
  in CLI (`phoenix --rest-url https://prod.phoenix.example.com task submit ...`),
  org enrollment with HKDF subkeys (Section 7.6), drift detector ML-statistical-detector
  enhancement, Phoenix Cloud commercial bundle scaffolding (out-of-tree).

The architecture spec stays at `PHOENIX_ARCHITECTURE_v1.md`; v1.1 architecture changes
land via doc-amendment commits with the same Section 11.13 changelog discipline.
