# Build Guide — Phoenix v1 Phase 11: Compositional acceptance tests + per-directory READMEs

**Status:** DRAFT under active design with Adam.
**Authoritative location:** `C:\Phoenix\BUILDGUIDE_phoenix_v1_phase11_acceptance_composition.md`
**Architectural reference:** `C:\Phoenix\PHOENIX_ARCHITECTURE_v1.md` (Section 10.7 acceptance criteria — "Compositional fail-closed test" + "Long-window replay test" + "Documentation: every directory under phoenix/ and vendor/ has a non-empty README.md").
**Phase scope:** Phase 11 only — NO Phase 12 distribution artifacts (pip wheel / Docker / Nuitka binary); NO additional architectural deliverables beyond the three Section 10.7 items.
**Date opened:** 2026-05-14.
**Author of record:** Adam (with Claude as design partner).

---

## 0 — What this build guide is

Phase 11 closes three of the four remaining v1 acceptance items (the fourth — distribution artifacts — gets its own Phase 12). Each item is **architectural maturity**, not new feature work:

1. **Compositional fail-closed test ("panic mode") — Section 10.7.** A single integration test that simultaneously crashes NATS + state backend + drift detector and verifies Phoenix fails fast and loud with typed errors (`QueueUnavailable`, `StateBackendUnavailable`, `DriftStateUnavailable`) — never silently degrades. The §10.7 canonical assertion is that fail-closed posture **composes** across subsystems: multiple simultaneous failures produce a deterministic error response naming the *first* failing condition, not a confused mixture. Three isolation tests precede the combined canonical test so a regression localizes to the subsystem that broke (per locked OPEN-1).

2. **Long-window replay test — Section 10.7.** Take a v1.0 ledger entry whose solve completed cleanly, fast-forward 6+ simulated months via clock monkeypatch (per locked OPEN-2), then run `POST /v1/tasks/{id}/replay` and confirm bit-exact match. Validates the `requirements.lock` + `vendor/VENDOR_VERSION.txt` + per-RNG-seed + FP-environment discipline (Section 1 Decision 21) composes for the long horizon — not just same-day replay. Failure modes the test catches: a vendored library upgraded silently, RNG seed not recorded, FP env differs across runs.

3. **Per-directory READMEs — Section 10.7.** Every directory under `phoenix/` and `vendor/` has a non-empty `README.md`. Rich content for top-level + heavily-touched directories (`phoenix/router/`, `phoenix/verification/`, `phoenix/trinity/`); minimal one-paragraph descriptors for leaves (`phoenix/router/pricing/`). Per locked OPEN-4: avoid busy-work without sacrificing audit-grade documentation.

**This guide does NOT cover:**

- Distribution artifacts (pip wheel + Docker image + Nuitka binary) — Phase 12 (v1 release prep).
- Cross-platform CI workflows (the Section 10.7 "clean Linux container + clean macOS runner" assertion) — Phase 12 wires the CI matrix; Phase 11 ships the tests that run on each.
- New architectural code. Phase 11 is acceptance + docs, NOT feature work. Any in-scope code change is in service of fixing a panic-mode regression or filling a docs gap.
- `phoenix admin pricing-update` CLI (Section 11.2.2 disposition defers to v1.x).

---

## 1 — Prerequisites

- Phase 10 closed at `1a3f5ab` (Phase 10 Step 10).
- `python -m pytest --ignore=tests/tier1 -q` green: 735 passed, 39 skipped.
- `pytest -m smoke` green.
- Clean working tree (no in-flight changes on the branch).
- No OneDrive paths.
- Phoenix version `1.0.0.dev11` (pyproject + version.py + the three `_DEFAULT_PHOENIX_RELEASE` constants + drift detector default).

---

## 2 — Phase-gate review protocol

Each of the 10 steps below ends with the canonical stop-gate marker:

```
=== STEP N COMPLETE — AWAITING ADAM REVIEW ===
```

Adam reviews the diff + verification at each gate before the next step begins. Architectural ambiguities discovered mid-step trip the `[OPEN: ...]` escalation discipline — drop the marker into the BUILDGUIDE and surface to Adam rather than guessing.

Per Adam's 2026-05-13 direction ("keep building for a while before we create a PR"): Phase 11 stays on the same `phase-9-adapters-mcp-cli` branch as Phases 9-10 (commits stack on top of `1a3f5ab`). PR ceremony is deferred to whenever Adam asks for it.

---

## 3 — Phase 11 deliverables

### 3.1 — Step 1: Typed-error audit + panic-mode test harness scaffolding

**What lands:**

- Audit of the three typed errors the §10.7 acceptance names:
  - `QueueUnavailable` — lives in `phoenix/queue/` or `phoenix/api/event_broker.py`. Confirm import path + verify it's raised by the relevant subsystem on connection loss.
  - `StateBackendUnavailable` — lives in `phoenix/state/`. Confirm import path + raise sites.
  - `DriftStateUnavailable` — lives in `phoenix/verification/drift_state.py` (already imported by `phoenix/verification/gate.py`).
- If any of the three typed errors is missing or misplaced, file as `[OPEN-7]` for Adam rather than silently creating.
- `tests/integration/test_panic_mode/` directory + `conftest.py` with the shared fixtures (isolated runtime, kill-each-subsystem context managers).
- `@pytest.mark.acceptance` registered in `pyproject.toml`'s `[tool.pytest.ini_options]` markers section so the new tests are discoverable as a discrete suite.

**Verification:** the three typed errors all import cleanly; the new conftest fixtures exist; `pytest -m acceptance` lists zero tests (steps 2-5 fill the suite).

```
=== STEP 1 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.2 — Step 2: NATS-down isolation test

**What lands:**

- `tests/integration/test_panic_mode/test_nats_down.py` — one test that:
  - Forces a `QueueUnavailable` from the NATS broker (monkeypatch the connection to raise, OR point the broker at an unreachable address).
  - Submits a task / hits an endpoint that consumes the queue.
  - Asserts the response surfaces `QueueUnavailable` (or its HTTP mapping) and emits the corresponding audit event.
  - Verifies NO silent degradation: the task does NOT execute against an in-memory fallback.

**Verification:** test passes; the fail-closed contract for NATS-down is pinned.

```
=== STEP 2 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.3 — Step 3: State-backend-down isolation test

**What lands:**

- `tests/integration/test_panic_mode/test_state_backend_down.py` — one test that:
  - Forces a `StateBackendUnavailable` (monkeypatch the SQLite path to a directory that doesn't exist, OR close the connection mid-flight).
  - Hits an endpoint that needs the state backend (admin health, audit query, ledger append, kill-switch read).
  - Asserts the typed error fires; no silent fallback to in-memory state.

**Verification:** test passes; state-backend fail-closed pinned.

```
=== STEP 3 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.4 — Step 4: Drift-detector-down isolation test

**What lands:**

- `tests/integration/test_panic_mode/test_drift_detector_down.py` — one test that:
  - Marks one of the three drift detectors as unavailable (the existing `DriftStateUnavailable` raise site in `phoenix/verification/drift_state.py`).
  - Submits a task that goes through the verification gate.
  - Asserts the gate fails fast with `DriftStateUnavailable` per Section 6.8.

**Verification:** test passes; drift-state fail-closed pinned (the gate's existing `# Section 6.8 fail-closed: read drift state first; raise if unavailable` comment is the contract this test locks).

```
=== STEP 4 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.5 — Step 5: Combined "simultaneous three-failure" panic test

**What lands:**

- `tests/integration/test_panic_mode/test_simultaneous_three.py` — the §10.7 canonical:
  - Crashes NATS + state backend + drift detector simultaneously (composed context managers from steps 2-4's setup).
  - Submits a task.
  - Asserts the response carries **one** typed error naming the *first* failing condition, not a confused mixture. Per Section 10.7: "produce a deterministic error response naming the *first* failing condition, not a confused mixture."
  - The deterministic ordering is whichever subsystem fail-closes first in the request path (probably state backend, since safety gate reads it before NATS publish + drift state).
  - Verifies the audit log records the typed error AND the fact that the other two subsystems were also down (no false-success "drift was healthy" in the audit when the drift detector was actually unavailable).

**Verification:** the §10.7 acceptance gate's centerpiece test passes; fail-closed COMPOSES.

```
=== STEP 5 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.6 — Step 6: Long-window replay fixture + clock-monkeypatch helper

**What lands:**

- `tests/integration/test_long_window_replay/` directory + `conftest.py`.
- `fixture_solve_entry.py` — a hand-built deterministic `SolveEntry` payload (per locked OPEN-3) with:
  - Pinned `request_id`, pinned RNG seed, pinned FP environment snapshot, pinned vendor version manifest.
  - Pinned provider response (deterministic shots, from `phoenix.local_simulator` since cloud shots are non-deterministic per Decision 20).
  - Pinned `result_hash` that replay must reproduce bit-exactly.
- `clock_advance.py` — a `monkeypatch_clock(target_unix)` context-manager helper that fast-forwards `time.time()` (and `datetime.now()` if needed) to the target. Per locked OPEN-2.

**Verification:** the fixture loads + the SolveEntry composes into a valid ledger row; the clock helper advances + restores cleanly.

```
=== STEP 6 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.7 — Step 7: Long-window replay bit-exact verification test

**What lands:**

- `tests/integration/test_long_window_replay/test_six_month_replay.py` — the §10.7 canonical:
  - Loads the fixture from Step 6 into a fresh ledger.
  - Advances the clock 6+ simulated months (180+ days).
  - Calls the replay engine (`POST /v1/tasks/{id}/replay` against the fixture's `request_id`).
  - Asserts:
    - Reproducibility mode = `replay` (not `default` — the test validates the replay-mode contract).
    - Returned `Result.value`, `error_bar`, `sigma` bit-exact match the fixture's recorded values.
    - `Result.provenance.cloud_shots_recorded` is `False` (local sim) or honors the recorded shots (if we eventually fixture a cloud-shot solve in v1.x).
    - Vendor version manifest comparison: the ledger entry's recorded `vendor_synced_at` + `dr_frank_and_eddy_commit` match the current vendor manifest. If they ever diverge in CI (silently upgraded library), THIS test catches it.

**Verification:** the §10.7 long-window-replay acceptance gate passes; the replay discipline holds across simulated 6 months.

```
=== STEP 7 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.8 — Step 8: Per-directory README audit + fill missing under `phoenix/`

**What lands:**

- Audit script run (or manual sweep): enumerate every directory under `phoenix/`. Some already have READMEs (e.g., `phoenix/router/README.md`, `phoenix/state/README.md`); others don't.
- For directories WITHOUT a README, add one:
  - **Rich content** for top-level + heavily-touched dirs (`phoenix/_internal/`, `phoenix/api/`, `phoenix/trinity/`, `phoenix/cli/`, `phoenix/mcp/`, `phoenix/adapters/`, `phoenix/safety/`, `phoenix/ledger/`, `phoenix/audit/`, `phoenix/verification/`, `phoenix/router/`, `phoenix/state/`, `phoenix/identity/`, `phoenix/admin/`, `phoenix/queue/`, `phoenix/providers/`). Per locked OPEN-4.
  - **Minimal one-paragraph descriptors** for leaves (e.g., `phoenix/router/pricing/`, `phoenix/state/migrations/`, `phoenix/providers/classical/`, `phoenix/adapters/` sub-modules, `phoenix/trinity/solver/`, `phoenix/trinity/control/`, `phoenix/trinity/orchestrate/`).
- Update existing READMEs that haven't kept pace (e.g., add Phase 8/9/10 surface mentions).

**Verification:** `find phoenix -type d -not -name __pycache__ -exec test -e {}/README.md \;` returns no errors (every dir under `phoenix/` has a README). Spot-check three random READMEs for content accuracy vs current code.

```
=== STEP 8 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.9 — Step 9: Per-directory README pass under `vendor/` + top-level docs index

**What lands:**

- Same audit + fill pass for `vendor/`:
  - Top-level `vendor/README.md` describes the vendoring contract per Phase 1 + Section 10.2 ("the vendoring map").
  - Per-vendored-package READMEs (e.g., `vendor/synthesis/`, `vendor/wobble/`, `vendor/omega/`, `vendor/actor/`) with a one-paragraph "what this package provides + which Phoenix subsystem consumes it".
- Top-level `phoenix/README.md` (if it doesn't exist) — package-level summary linking to the per-subsystem READMEs.
- Repo-root `README.md` Phase 11 update: bump the version + add a one-line note about Phase 11's acceptance work.

**Verification:** same `find` check on `vendor/`; root `README.md` mentions Phase 11.

```
=== STEP 9 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.10 — Step 10: Acceptance, version bump, CHANGELOG

**What lands:**

- `pyproject.toml` + `phoenix/_internal/version.py` bump `1.0.0.dev11` → `1.0.0.dev12`.
- `CHANGELOG.md` Phase 11 entry at the top.
- Test-version assertions updated.
- `_DEFAULT_PHOENIX_RELEASE` constants in SQLite + Postgres backends + drift detector default.
- Full pytest green.
- Optional: a single `pytest -m acceptance` smoke run that exits 0 with the canonical 4 panic + 1 replay tests all passing.

```
=== STEP 10 COMPLETE — AWAITING ADAM REVIEW ===
```

---

## 4 — Open items (locked 2026-05-14)

Adam approved all six recommendations on 2026-05-14 via AskUserQuestion at phase kickoff. Recorded here so future readers see the why.

### 4.1 — OPEN-1 LOCKED — Panic-mode tests = 4 (3 isolation + 1 combined)

**Choice:** Ship four tests rather than just the §10.7 canonical combined one. **Rationale:** the three isolation tests pin each subsystem's typed-error contract individually; the combined test proves simultaneous-failure composition. When a regression lands, the relevant isolation test fails specifically — operators don't have to bisect three subsystems from a single failing assertion. The maintenance overhead is small (three short tests vs one big one) and the localization payoff is large.

### 4.2 — OPEN-2 LOCKED — Long-window replay = monkeypatch system clock

**Choice:** Fast-forward `time.time()` via monkeypatch rather than waiting wall-clock or building a fixture-only test. **Rationale:** the test verifies timestamp-independence + vendor-version invariance, not actual elapsed time. Monkeypatching keeps the test sub-second while genuinely exercising the "what if a solve from 6 months ago replays today" invariant. A fixture-only approach (just bake an old timestamp in) doesn't actually simulate elapsed time and would pass on day 1 of the original solve too — weakening the long-window guarantee.

### 4.3 — OPEN-3 LOCKED — Replay fixture = hand-built SolveEntry

**Choice:** Build a deterministic `SolveEntry` fixture in the test rather than depending on a captured production solve. **Rationale:** self-contained tests don't break when production fixtures rotate. The fixture is small (pinned request_id + RNG seed + FP env + vendor manifest + recorded result) and lives in the test directory.

### 4.4 — OPEN-4 LOCKED — README depth = rich-for-top, minimal-for-leaves

**Choice:** Rich READMEs (multi-section, with file index + architectural commentary) for top-level + heavily-touched dirs; minimal one-paragraph descriptors for leaves. **Rationale:** §10.7 wants every dir to have a README, but doesn't mandate depth. Heavily-touched dirs benefit from rich content (`phoenix/router/README.md` already exemplifies this); leaf dirs (`phoenix/router/pricing/`) need only "this dir contains the pricing JSON consumed by phoenix.router.pricing.estimate_cost_usd; updated per Phoenix release per Section 4.7." Avoids busy-work without sacrificing audit-grade documentation.

### 4.5 — OPEN-5 LOCKED — Distribution deferred to Phase 12

**Choice:** All three distribution artifacts (pip wheel, Docker image, Nuitka binary) defer to Phase 12 = v1 release prep. **Rationale:** distribution work is qualitatively different — it's CI infrastructure + binary compilation + container build orchestration. Bundling it into Phase 11 dilutes the "fail-closed composition" theme and risks Phase 11 stretching into a long cycle. Phase 12 gets its own time horizon to debug Nuitka edge cases without pressure on the acceptance gate.

### 4.6 — OPEN-6 LOCKED — Test marker = `@pytest.mark.acceptance`

**Choice:** Mark the new panic-mode + long-window-replay tests with `@pytest.mark.acceptance`. **Rationale:** CI can run the §10.7 acceptance gate as a discrete suite (separate from smoke / integration / tier1). Local dev runs `pytest -m acceptance` to verify the v1 acceptance gate in <30 seconds. The marker is registered in `pyproject.toml`'s pytest config to avoid the unknown-marker warning.

### 4.7 — OPEN-7 LOCKED — Missing typed errors created in Step 1

**Choice:** Step 1's audit surfaced that two of the three §10.7-named typed errors don't exist yet:

- `DriftStateUnavailable` ships in `phoenix/verification/drift_state.py` (Phase 6b Step 7).
- `QueueUnavailable` did NOT exist → created in `phoenix/queue/errors.py` (Phase 11 Step 1).
- `StateBackendUnavailable` did NOT exist → created in `phoenix/state/errors.py` (Phase 11 Step 1).

**Rationale:** The §10.7 acceptance text names all three typed errors as if they shipped. The previous 10 phases got away with a half-shipped typed-error contract because the happy path didn't exercise these failure modes — drift state was the only subsystem that wired fail-closed (Section 6.8). Phase 11's panic-mode tests are exactly what would catch this gap, so creating the typed errors IS Phase 11 work.

The Step 1 implementation is minimal:

- New `phoenix/state/errors.py` with `StateBackendUnavailable(Exception)` carrying `backend_kind` ("sqlite" | "postgres" | "unknown").
- New `phoenix/queue/errors.py` with `QueueUnavailable(Exception)` carrying `subject` (NATS subject of the failing op).
- `SQLiteStateBackend._require_conn` raises `StateBackendUnavailable` instead of the prior generic `RuntimeError`.
- `PostgresStateBackend._require_pool` raises `StateBackendUnavailable`.
- `TaskQueue.publish_submit` / `TaskQueue.subscribe_submit` raise `QueueUnavailable` when `setup_streams` wasn't called (previously `RuntimeError`).

The class shape matches `DriftStateUnavailable`'s pattern (Exception subclass + structured attribute carrying actionable subsystem-specific context). Future v1.x phases can extend the raise coverage (e.g., NATS connection-loss-mid-flight catches `nats.errors.ConnectionClosedError` → translates to `QueueUnavailable` with subject populated).

---

## 5 — Verification (end to end)

After Step 10 completes:

1. Full `pytest --ignore=tests/tier1 -q`: expect ~745 passing (4 panic + 1 replay = 5 new tests beyond Phase 10's 735 + a buffer for any helper-test coverage).
2. `pytest -m acceptance` runs the 5 §10.7 acceptance tests in isolation; all pass; runtime <30s.
3. Smoke gate green.
4. ruff / ruff-format / mypy strict clean.
5. End-to-end manual: pick a fresh dir under `phoenix/` and read its README; it should explain what's in there without requiring source-code dives.
6. Repo-root `README.md` reads as the v1-ready surface (mentions all 10 ready phases + the Phase 12 release-prep plan).

---

## 6 — What I am NOT proposing

- No PR creation after Phase 11 (per Adam's 2026-05-13 direction).
- No changes to `C:\frank-data\` (DF&E) or its benchmark shell.
- No new architectural code beyond what's required to fix a panic-mode bug surfaced by the new tests (and if any is found, it's filed as an `[OPEN]` for Adam before implementing).
- No `phoenix admin pricing-update` CLI (Section 11.2.2 v1.x).
- No CI matrix wiring (Linux + macOS runners per §10.7 long-window-replay note) — Phase 12 owns CI infrastructure.
- No reduction in existing test coverage. Every Phase 11 test ADDS to the gate; nothing removes.
