# BUILDGUIDE — Phoenix v1 Phase 6b: Infrastructure layer (state backend, NATS, drift detector, /v1/ws/calibration/drift)

**Status:** DRAFT — under active design with Adam.
**Authoritative location:** `C:\Phoenix\BUILDGUIDE_phoenix_v1_phase6b_infrastructure.md`
**Architectural reference:** `C:\Phoenix\PHOENIX_ARCHITECTURE_v1.md` (v1 with 2026-05-08 follow-up and the 2026-05-08 scope decision splitting Phase 6 into 6a + 6b).
**Phase scope:** Phase 6b only. Phase 7 (audit log + Omega Ledger + drift→routing feedback) is a separate build guide.
**Date opened:** 2026-05-10.
**Author of record:** Adam (with Claude as design partner).

---

## 0 — What this build guide is

Phase 6b's job is to land the **infrastructure layer**: the concrete persistence, queueing, and self-monitoring substrates that the rest of Phoenix has been building against through abstract Protocols and stubs. Phase 6a shipped the API-side enforcement (safety gate, identity, WebSocket events, bearer-token auth) on top of JSON-file state and an in-memory event broker; Phase 6b replaces those placeholders with durable infrastructure without breaking the Phase 6a contracts.

End-to-end at the end of Phase 6b: a regulated user POSTs a `PhysicsTask`, the request hits the safety gate (Phase 6a), the gate consults a real SQLite-backed `StateBackend` for kill-switch and `ActorPermissions`, the verification gate (Phase 5) consults a real drift detector with three independent checkers, a `drift.alert` event flows through `/v1/ws/calibration/drift` to a connected ops dashboard, and the entire solve survives a daemon restart because state is durable.

**Phase 6b's definition of done:**
- `phoenix/state/backend_protocol.py` expands (additively) with cost-ledger queries, audit-event writes, drift-state reads, pending-review-queue ops. Phase 6a's `get_kill_switch_state` / `set_kill_switch_state` contract is unchanged.
- `phoenix/state/sqlite_backend.py` ships a default zero-config SQLite implementation behind the Protocol. Schema migrations live in `phoenix/state/migrations/` (versioned with Phoenix releases per architecture §10.3 line 2279).
- `phoenix/state/postgres_backend.py` ships an opt-in Postgres implementation behind the same Protocol, with connection pooling.
- `phoenix/state/factory.py` selects backend at startup from config / env var; default is SQLite. Per Decision 31 the choice is not switchable at runtime.
- `phoenix/queue/nats_client.py`, `phoenix/queue/task_queue.py`, `phoenix/queue/embedded_runner.py` populate the `phoenix/queue/` package per architecture §10.3 lines 2282-2286. The embedded runner launches a NATS subprocess for solo installs (Decision 33).
- `phoenix/api/event_broker.py` gains an optional NATS-backed mode behind the same `emit / get_events / clear` contract; the in-memory broker remains the default for tests and dev. Phase 6a callers see no API change.
- `phoenix/verification/drift_detector.py` (new) ships three concrete detectors per Decision 17: Tier-1 analytical battery, ML statistical (frank-data `ml/drift_ensemble.py` pattern), cross-version. The detector orchestrator implements the cadence (6h + startup + `requirements.lock` change), runs on a low-priority background thread, and feeds `read_drift_state()` in `phoenix/verification/drift_state.py` with real telemetry. Single-detector firing → `state="warning"` with a `drift_warning` provenance flag; two-detector agreement → high-confidence drift escalation per Decision 17.
- `phoenix/verification/drift_state.py` updates to consume the real detector. The stub `return DriftState(state="healthy")` is removed. The stale Phase 7 docstring reference (line 13-14) is corrected to Phase 6b. `DriftStateUnavailable` is raised on telemetry-source failures with the unavailable detectors named.
- `phoenix/api/routes.py` adds `@app.websocket("/v1/ws/calibration/drift")` per architecture §5.3. Authentication reuses the existing `POST /v1/identity/ws-token` mint/consume flow shipped in Phase 6a (`phoenix/api/ws_auth.py`); the WS handler emits `drift.alert` events from the detector each time any of the three detectors fires.
- Tests: `tests/integration/test_state_backend.py` (SQLite + Postgres parity), `tests/integration/test_nats_queue.py`, `tests/integration/test_drift_detector.py`, `tests/integration/test_ws_calibration_drift.py`. Existing 113 tests still pass; expect roughly +25-35 new tests across the four files.
- Pre-commit hooks pass: ruff, ruff-format, mypy --strict, `pytest tests/unit/test_smoke.py -q`.
- `pyproject.toml` version bumps `1.0.0.dev6` → `1.0.0.dev7`.
- `CHANGELOG.md` gains a Phase 6b section in the established format.

**This guide does NOT cover:**
- Audit log + OpenTelemetry export (Phase 7 — Section 1 Decision 16 + Decision 22).
- Omega Ledger hashchained provenance store (Phase 7 — Section 1 Decision 15 + 19-21).
- Drift signals feeding *back into routing* (Phase 7 — Section 4.6's drift→fidelity rescoring). Phase 6b lands the detector and the verification-gate consumption path only; the router-feedback path is Phase 7.
- Admin dev-ops backdoor endpoints `/v1/admin/calibration/...` (Phase 8 — Section 8 generally).
- LoRA adapter sandbox, MCP server, CLI commands (Phase 9).
- OTel adapter concrete impl, cloud seams concrete impls, standalone binary (Phase 10).
- Final §10.7 acceptance + release (Phase 11).

## 1 — Prerequisites

Before starting Phase 6b:

1. **Phase 6a acceptance.** All Phase 6a commits on `origin/main` (currently `7c92f7f` at 2026-05-10). `pytest tests/unit/test_smoke.py -q` reports 4 passed.
2. **Architecture sections read fresh, not from memory.** Section 1 Decisions 17 (drift), 31 (state), 32-33 (NATS); Section 5.3 (WebSocket surface including `/v1/ws/calibration/drift`); Section 6.5 (drift integration with verification); Section 6.8 (`DriftStateUnavailable` typed exception); Section 10.3 (the `phoenix/` package layout, particularly the `state/`, `queue/`, `verification/` subdirectories and the migrations table inventory at lines 2279).
3. **`DriftStateUnavailable` already exists** at [phoenix/verification/drift_state.py:27-42](C:\Phoenix\phoenix\verification\drift_state.py). Phase 6b's detector raises it; the gate already handles it (fail-closed per §6.5).
4. **`StateBackend` Protocol already exists** at [phoenix/state/backend_protocol.py](C:\Phoenix\phoenix\state\backend_protocol.py). Phase 6b adds methods; it does not change the two existing methods.
5. **`EventBroker` in-memory impl already exists** at [phoenix/api/event_broker.py](C:\Phoenix\phoenix\api\event_broker.py). Phase 6b adds a NATS-backed alternative; the in-memory impl remains the default for tests and dev.
6. **Working tree clean.** `git -C C:\Phoenix status` is clean (modulo the untracked `.claude/` harness state and the harmless `Ctemp_section4.txt` artifact from a previous session — both ignored by this phase).
7. **Branch.** Work lands on a new branch `phase-6b-infrastructure` cut from `origin/main`. PR #6 against `main` at the end of Step 10.
8. **No OneDrive paths.** Adam's standing rule.

## 2 — Phase-gate review protocol

Phase 6b has **ten steps** (Section 3.1 through 3.10), matching Phase 6a's rhythm. Each step ends with the standard stop gate:

```
=== STEP N COMPLETE — AWAITING ADAM REVIEW ===
```

Same discipline as Phases 0 through 6a. No advancement past a stop gate without explicit Adam approval.

**Standing rule from earlier phases carried forward:** if a step reveals an architectural ambiguity not resolved by the v1 spec, mark it `[OPEN: ...]` and surface to Adam — do not silently invent a resolution. The four open items currently flagged at the bottom of this guide must be resolved before the corresponding step begins.

**Pre-commit gates that must pass at every step boundary:**
- `ruff check .` — clean
- `ruff format --check .` — clean
- `mypy --strict phoenix/` — clean (excluding `vendor/`, `tests/`, `evals/` per `.pre-commit-config.yaml`)
- `pytest tests/unit/test_smoke.py -q` — green

**Full test gate at Step 10 only:** `pytest` (all collected tests across `tests/` and `evals/`) — green.

## 3 — Phase 6b deliverables

### 3.1 — Step 1: Expand the `StateBackend` Protocol

**What lands:**
- [phoenix/state/backend_protocol.py](C:\Phoenix\phoenix\state\backend_protocol.py) gains new method signatures, all additive (the two existing methods are unchanged):
  - `get_solve_cost_record(solve_id: str) -> dict[str, Any] | None` and `put_solve_cost_record(solve_id, record)` per Section 4.7's cost accounting → `solve_cost_ledger` table.
  - `append_audit_event(event: dict[str, Any]) -> None` and `list_audit_events(since_unix: float, limit: int) -> list[dict[str, Any]]` per Decision 16 → `audit_events` table.
  - `enqueue_pending_review(record)` / `list_pending_reviews()` / `resolve_pending_review(review_id, resolution)` per Section 7.7 → `pending_review_queue` table.
  - `get_drift_state_snapshot() -> dict[str, Any]` and `put_drift_state_snapshot(snapshot)` — the durable record of the most recent drift cycle so detectors survive daemon restart.
  - `list_actor_permissions(...)` / `put_actor_permission(...)` siblings to the existing `ActorPermissions` JSON-file registry; the JSON file remains the truth in Phase 6b, but the Protocol surface is extended so Step 2's SQLite impl can shadow-write (verification step).
- The Phase 6a docstring at module top is updated: the line "Phase 6b expands with cost ledger queries, audit-event writes, drift-state reads, etc." becomes a list of the actual new methods landed in this step, with the Phase 6b commit hash.
- No call sites change. Existing safety-gate code keeps calling `get_kill_switch_state` / `set_kill_switch_state` unchanged; Phase 6b's new methods are unused until Step 2 lands the SQLite impl.

**Why an isolated Step 1:** the Protocol shape is the contract two concrete impls (Step 2 SQLite, Step 3 Postgres) must satisfy in parallel. Landing the Protocol first means Steps 2 and 3 are bounded by it. Phase 6a took the same approach (Protocol in Step 4, JSON-file impl in Step 4 too because there was only one impl).

**Verification:**

```powershell
ruff check phoenix/state/backend_protocol.py
mypy --strict phoenix/state/backend_protocol.py
python -c "from phoenix.state.backend_protocol import StateBackend; print(sorted(m for m in dir(StateBackend) if not m.startswith('_')))"
```

The Phase 6a methods (`get_kill_switch_state`, `set_kill_switch_state`) appear in the listing; the new Phase 6b methods also appear; no method is removed or renamed.

```
=== STEP 1 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.2 — Step 2: SQLite backend implementation + migrations

**What lands:**
- `phoenix/state/sqlite_backend.py` — `SQLiteStateBackend` class implementing the expanded `StateBackend` Protocol. Connection lifecycle: one connection per process, opened at startup, closed at shutdown. `PRAGMA journal_mode=WAL` for read concurrency. Path: `~/.phoenix/runtime/state.db` (the same directory Phase 6a uses for `master_key.bin` and JSON files).
- `phoenix/state/migrations/0001_phase6b_initial.py` (or `.sql` — `[OPEN: prefer Python-callable migrations or pure SQL? See open item 1.]`) — initial schema covering all five tables named in architecture §10.3 line 2279: `solve_cost_ledger`, `kill_switch_state`, `actor_permissions`, `audit_events`, `pending_review_queue`. Plus `schema_version` table with one row per applied migration (timestamp + Phoenix release).
- `phoenix/state/migrations/runner.py` — applies pending migrations on startup. Idempotent. Honors `schema_version` so re-running is a no-op.
- The Phase 6a kill-switch implementation in `phoenix/safety/kill_switch.py` gains a *write-through* mode: writes go to both the existing JSON file (Phase 6a-style) AND the SQLite backend in parallel; reads go to SQLite first with JSON-file fallback. This is the migration ramp; full JSON removal is Phase 7. **SAFETY:** dual-write means a power loss between the two writes can desync state. The kill switch must remain *engaged* on read mismatch — fail-closed.

**Verification:**

```powershell
pytest tests/integration/test_sqlite_backend.py -v
python -c "from phoenix.state.sqlite_backend import SQLiteStateBackend; b = SQLiteStateBackend(); print(b.get_kill_switch_state())"
sqlite3 $env:USERPROFILE\.phoenix\runtime\state.db ".tables"   # all five tables present
```

```
=== STEP 2 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.3 — Step 3: Postgres backend implementation

**What lands:**
- `phoenix/state/postgres_backend.py` — `PostgresStateBackend` class implementing the same Protocol as Step 2's SQLite impl. Same schema, same migrations runner (the migration files are SQL-flavor-agnostic where possible; `migrations/runner.py` knows the active dialect and applies dialect-specific variants where required — `INTEGER PRIMARY KEY AUTOINCREMENT` vs `BIGSERIAL`).
- Connection pooling per [OPEN: open item 2 below].
- Postgres-specific migration variants in `phoenix/state/migrations/0001_phase6b_initial.postgres.sql` if pure-SQL; or dialect dispatch inside `0001_phase6b_initial.py` if Python-callable. Either way the *table shapes* are identical to Step 2.

**Verification:** the test in Step 2 (`tests/integration/test_sqlite_backend.py`) is parametrized in Step 9 to run against both backends. Step 3's standalone verification:

```powershell
# Requires a local Postgres reachable; skipped in CI without it (per [OPEN] 2).
$env:PHOENIX_STATE_BACKEND_DSN = "postgresql://phoenix_test:phoenix_test@localhost:5432/phoenix_test"
python -c "from phoenix.state.postgres_backend import PostgresStateBackend; b = PostgresStateBackend(); b.get_kill_switch_state()"
```

```
=== STEP 3 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.4 — Step 4: Backend factory + startup wiring

**What lands:**
- `phoenix/state/factory.py` — `get_state_backend() -> StateBackend` reads either:
  - `$PHOENIX_STATE_BACKEND` env var (`sqlite` | `postgres`), or
  - `~/.phoenix/config.yaml` `state.backend` key (Section 5.4 surfaces this).
  - Default: `sqlite`.
- `phoenix/state/__init__.py` exposes `get_state_backend` at the package level.
- One call site update: `phoenix/api/__main__.py` (or wherever Phoenix daemon startup lives) calls `get_state_backend()` once and stashes the result for the safety gate. The gate's `get_kill_switch_state` / `set_kill_switch_state` paths route through this single backend instance.
- Per Decision 31: the choice is made *at startup* and not switchable at runtime. The factory caches the chosen backend in a module-level singleton.

**Verification:**

```powershell
# Default selects SQLite.
python -c "from phoenix.state import get_state_backend; print(type(get_state_backend()).__name__)"
# → SQLiteStateBackend

# Env-var selects Postgres.
$env:PHOENIX_STATE_BACKEND = "postgres"
$env:PHOENIX_STATE_BACKEND_DSN = "postgresql://..."
python -c "from phoenix.state import get_state_backend; print(type(get_state_backend()).__name__)"
# → PostgresStateBackend
```

```
=== STEP 4 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.5 — Step 5: NATS connection wrapper + embedded runner

**What lands:**
- `phoenix/queue/nats_client.py` — connection management. Async client using `nats-py` (the asyncio NATS client). Singleton per process. Connects to `nats://127.0.0.1:4222` by default, reads `$NATS_URL` for override.
- `phoenix/queue/embedded_runner.py` — launches a NATS subprocess for solo deployments per Decision 33 ("a solo Phoenix install boots two processes: Phoenix itself + NATS"). The runner:
  - Discovers the NATS binary via `$NATS_SERVER_PATH` or PATH lookup. If missing, raises `EmbeddedNATSNotFound` with installation instructions. **[OPEN: bundle NATS binary in the Phoenix release artifact, or require user-installed? See open item 3.]**
  - Boots NATS with `--jetstream --store_dir ~/.phoenix/runtime/nats/` (file-backed JetStream per Decision 32).
  - Manages lifecycle: subprocess started on Phoenix daemon startup, sent SIGTERM on Phoenix shutdown, drained timeout 10s, SIGKILL after.
  - Health check: NATS reports ready when its monitoring port (8222) responds; the embedded runner waits up to 30s for ready before declaring NATS available.
- `phoenix/queue/__init__.py` exposes `connect_nats()` and `start_embedded_nats()`.
- A new bundled launcher `scripts/launch_with_nats.bat` (Windows) demonstrates the two-process model: launches NATS embedded, then Phoenix daemon. **[Step 5 deliverable is the runner module; the launcher script is a thin demo that proves the runner works end-to-end.]**

**Verification:**

```powershell
python -c "import asyncio; from phoenix.queue.embedded_runner import start_embedded_nats, stop_embedded_nats; asyncio.run(start_embedded_nats()); print('NATS up')"
# Confirm process listed:
Get-Process nats-server -ErrorAction SilentlyContinue
```

```
=== STEP 5 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.6 — Step 6: NATS task queue + optional NATS event broker

**What lands:**
- `phoenix/queue/task_queue.py` — task submission and worker dispatch:
  - Subject hierarchy: `phoenix.tasks.submit.<latency_tier>` for ingress, `phoenix.tasks.events.<task_id>` for per-task event streams.
  - JetStream stream config: `STREAM=phoenix-tasks`, `RETENTION=workqueue`, `STORAGE=file`, `MAX_AGE=24h`.
  - Worker pool: a small number of asyncio consumers per Phoenix daemon (configurable; default 4). Consumers are durable per [OPEN] item 4.
- `phoenix/api/event_broker.py` gains a `NATSEventBroker` class that implements the same `emit / get_events / clear` contract as the existing `EventBroker`. Selection of which broker is active is via `$PHOENIX_EVENT_BROKER` env var (`memory` | `nats`); default in Phase 6b stays `memory` to preserve test isolation. The Phase 6a docstring forward-reference ("Phase 6b's NATS JetStream ships durable persistence + cross-process pub/sub") is updated to reflect that the feature is now landed but opt-in.
- The verification gate's emit calls do not change — they call `get_broker().emit(...)` which now dispatches to whichever broker is selected.

**Verification:**

```powershell
$env:PHOENIX_EVENT_BROKER = "nats"
pytest tests/integration/test_nats_queue.py -v
$env:PHOENIX_EVENT_BROKER = "memory"  # reset for test isolation
```

```
=== STEP 6 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.7 — Step 7: Drift detector with three checkers (Decision 17)

**What lands:**
- `phoenix/verification/drift_detector.py` — orchestrator + three checker classes:
  - `Tier1AnalyticalChecker` — runs the five Tier-1 benchmarks (HO-1, ISW-1, H1S-1, RABI-1, SCG-1) currently at `tests/tier1/` and compares against `vendor/calibration_profile.json` baseline. Pass criterion: every benchmark within its profile-defined tolerance.
  - `MLStatisticalChecker` — adapts the dr-frank-and-eddy `ml/drift_ensemble.py` pattern (referenced in architecture line 211; vendored or rewritten per [OPEN] item 5). Watches solver-output distributions against a learned baseline; ensemble vote across multiple lightweight detectors.
  - `CrossVersionChecker` — runs current Phoenix's Tier-1 battery and compares against the *previous release's* recorded Tier-1 results. Previous-release results stored at `~/.phoenix/runtime/calibration_history/<phoenix_version>.json`. The cross-version detector is no-op on the first run (no prior version on disk).
- `DriftDetector` orchestrator owns the cadence:
  - Default cadence: 6 hours (configurable via `$PHOENIX_DRIFT_CADENCE_HOURS`).
  - On every Phoenix daemon startup (one cycle synchronously before serving traffic). **PERF:** this adds ~5-7 minutes to startup per Decision 17; gated behind `$PHOENIX_SKIP_STARTUP_DRIFT_CYCLE=1` for dev mode.
  - On every `requirements.lock` change (the daemon mtime-watches the lock file).
  - Background-thread execution at `threading.Thread` low priority (Python doesn't expose true OS priority on Windows; use `nice` via `psutil` if available, else just a `daemon=True` thread).
- `phoenix/verification/drift_state.py` is *updated, not replaced*:
  - `read_drift_state()` is rewired to consult the `DriftDetector` singleton's most-recent cycle snapshot, with `DriftStateUnavailable` raised when the snapshot is older than `2 * cadence` (stale telemetry).
  - The module-top docstring's "Phase 7's drift detector" / "Phase 5 stub" references are updated to Phase 6b (the stale Phase 7 reference predates the 2026-05-08 6a/6b split).
  - Single-detector firing → `DriftState(state="warning", detector_summaries={...})`.
  - Two-or-more detector firing → still `state="warning"` but `detector_summaries` carries `high_confidence=True`; the verification gate uses this flag for the auto-promote behavior in §6.5.
- Drift cycle results are persisted via the new `put_drift_state_snapshot` Protocol method (Step 1) so detectors survive daemon restart and replay sees the snapshot.

**[OPEN] item 6 (covered in the open items section) decides exact integration with the Phase 7 `drift_feedback` path; Phase 6b does not implement Phase 7's router-feedback.**

**Verification:**

```powershell
# Force a drift cycle now.
python -c "from phoenix.verification.drift_detector import get_detector; get_detector().run_cycle(); print(get_detector().last_snapshot())"
# Verify read_drift_state respects the snapshot.
python -c "from phoenix.verification.drift_state import read_drift_state; print(read_drift_state())"
```

```
=== STEP 7 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.8 — Step 8: `/v1/ws/calibration/drift` endpoint

**What lands:**
- `phoenix/api/routes.py` adds `@app.websocket("/v1/ws/calibration/drift")` async handler per architecture §5.3 line 995.
- Authentication: `Authorization: Bearer <token>` header where `<token>` is minted via the existing `POST /v1/identity/ws-token` endpoint (Phase 6a; [phoenix/api/ws_auth.py](C:\Phoenix\phoenix\api\ws_auth.py)). Token is consumed at WS handshake (single-use). Closure code `1008` for missing/bad token (matches Phase 6a's `/v1/ws/tasks/{task_id}/stream`).
- Event shape: `drift.alert` events emitted whenever the `DriftDetector` reports a state transition (healthy → warning, warning → healthy, or warning → high-confidence-warning). The event payload:
  ```python
  {
      "type": "drift.alert",
      "timestamp_unix": float,
      "from_state": "healthy" | "warning" | "high_confidence_warning",
      "to_state": "healthy" | "warning" | "high_confidence_warning",
      "firing_detectors": list[str],   # subset of ["tier1_analytical", "ml_statistical", "cross_version"]
      "detector_summaries": dict[str, str],
  }
  ```
- Bootstrap-actor parity: same fallback as `/v1/ws/tasks/{task_id}/stream` — when no Authorization header is present and the keystore is available, auto-mint a bootstrap actor (matches [OPEN] item 7's resolution).
- The `EventBroker` (memory or NATS) is the transport: the detector calls `broker.emit("phoenix.drift.alerts", "drift.alert", payload)`; the WS handler subscribes to that channel and forwards to connected clients.

**Verification:**

```powershell
pytest tests/integration/test_ws_calibration_drift.py -v
# Manual smoke (Phase 6a-style):
python -c "
import asyncio, httpx, json
async def go():
    async with httpx.AsyncClient() as c:
        r = await c.post('http://localhost:8003/v1/identity/ws-token', headers={'Phoenix-Actor': '<b64-json>'})
        print(r.json())
asyncio.run(go())
"
# Then connect via wscat / websocat with Authorization: Bearer <token>
```

```
=== STEP 8 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.9 — Step 9: Test suite

**What lands:**
- `tests/integration/test_state_backend.py` — parametrized over `[SQLiteStateBackend, PostgresStateBackend]`; tests:
  - Kill-switch persistence across simulated restart (re-instantiating the backend).
  - All new Phase 6b methods (cost ledger round-trip, audit events append/list, pending review enqueue/resolve, drift state snapshot round-trip, actor permissions round-trip).
  - Migrations idempotency (apply twice, schema_version table has one row per migration).
  - Postgres tests gated behind `$PHOENIX_POSTGRES_TEST_DSN`; CI skip if unset.
- `tests/integration/test_nats_queue.py` — embedded NATS startup/shutdown, JetStream publish/consume, the optional `NATSEventBroker` against the same contract as the in-memory broker (parametrized parity test).
- `tests/integration/test_drift_detector.py`:
  - Each of the three checkers runs in isolation against a known-good baseline → reports healthy.
  - Each checker runs against an injected-drift baseline → reports drift; the orchestrator's snapshot reflects single-detector warning.
  - Two-detector simultaneous fire → high-confidence warning.
  - `DriftStateUnavailable` raised when snapshot is older than `2 * cadence`.
  - Cycle survives daemon restart (snapshot loaded from `StateBackend`).
- `tests/integration/test_ws_calibration_drift.py`:
  - Successful mint + connect + receive a synthetic `drift.alert`.
  - Missing token → 1008.
  - Bad token → 1008.
  - Expired token (60s window) → 1008.
  - Reused token → 1008 (single-use).
- Pre-commit hooks pass across the whole tree.

**Verification:**

```powershell
pytest tests/ -v --tb=short
# Expect: 113 (Phase 6a baseline) + ~25-35 new (Phase 6b) = ~140-150 total, all green.
```

```
=== STEP 9 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.10 — Step 10: Acceptance, version bump, CHANGELOG entry

**What lands:**
- [pyproject.toml:7](C:\Phoenix\pyproject.toml) — `version = "1.0.0.dev6"` → `version = "1.0.0.dev7"`.
- [CHANGELOG.md](C:\Phoenix\CHANGELOG.md) — new section at the top, before the existing `1.0.0.dev6` entry, in the same shape as the Phase 6a entry. Includes:
  - Header: `## [1.0.0.dev7] — 2026-05-DD` (the actual landing date).
  - Locked scope decisions specific to this phase (the four `[OPEN]` items below, with their resolutions recorded).
  - "What landed" enumerating new modules with brief descriptions.
  - "Tests" section with count delta (113 → N).
  - "Bug fixes found during testing" if any surfaced.
  - "Out of scope for Phase 6b (deferred to Phase 7 / v1.x)" matching the section 0 "this guide does NOT cover" list.
- Branch `phase-6b-infrastructure` pushed to origin. PR #6 opened against `main` with:
  - Title: `Phase 6b: Infrastructure (state backends + NATS + drift detector) (1.0.0.dev7)`
  - Body summarizing the locked decisions, the 10 step landings, the test delta, and the acceptance criteria.
- Pre-commit gates green; full pytest green; `phoenix --version` (or equivalent CLI hook) reports `1.0.0.dev7`.

**Verification (end-to-end):**

```powershell
ruff check .
ruff format --check .
mypy --strict phoenix/
pytest tests/ -q
python -c "import phoenix; print(phoenix.__version__)"   # 1.0.0.dev7
Get-Content C:\Phoenix\CHANGELOG.md | Select-Object -First 25   # confirm 6b entry present
gh pr view 6   # confirm PR landed
```

```
=== STEP 10 COMPLETE — AWAITING ADAM REVIEW ===
```

---

## Locked decisions (2026-05-10)

The seven `[OPEN]` items surfaced during BUILDGUIDE authoring were all locked
on 2026-05-10 after a structured option review. Recorded here so future steps
and future readers see why the implementation looks the way it does.

1. **Migration format = Python-callable** (Step 2). Each migration is a `.py`
   file with `apply(conn)` and `revert(conn)`. Trivial migrations bodies are
   `conn.executescript("""<SQL>""")` so SQL is still in plaintext; data
   transforms (e.g., migration #1's kill-switch JSON → SQLite import) live in
   Python. One discipline across SQLite + Postgres backends.

2. **Postgres client = sync `psycopg` wrapped in `asyncio.to_thread`** (Step 3).
   Same pattern as the SQLite path's stdlib `sqlite3` wrap. Symmetry over the
   ~2x async perf `asyncpg` would buy on what is fundamentally a cold
   persistence path. One state-backend pattern at the call sites.

3. **NATS distribution = require user-installed for `1.0.0.dev7`** (Step 5).
   Documented one-line install in the launcher script
   (`winget install nats-io.nats-server` / `brew install nats-server`). Bundle
   is deferred to `1.0.0` when the Phase 10 release-artifact pipeline lands.

4. **JetStream consumer mode = mixed per use case** (Step 6).
   - `phoenix.tasks.submit.*` — durable, no TTL (submissions cannot be lost).
   - `phoenix.tasks.events.<task_id>` — ephemeral (real-time WS updates;
     reconnect replay is uninteresting).
   - `phoenix.drift.alerts` — durable, MAX_AGE=10m (briefly-disconnected ops
     dashboards catch up; longer history lives in the audit log at Phase 7).

5. **ML drift detector = vendor + thin Phoenix adapter** (Step 7). Vendor
   `vendor/ml/drift_ensemble.py` unchanged from `C:\frank-data\` (matches the
   Phase 1 vendoring discipline for `synthesis/`, `wobble/`, etc.).
   `phoenix/verification/drift_detector.py::MLStatisticalChecker` is a
   Phoenix-side wrapper exposing only the methods the orchestrator needs. **At
   Step-7 start, confirm vendorability by reading the frank-data source —** if
   the impl is too tied to its host context, the wrapper layer absorbs the
   delta rather than forcing a rewrite.

6. **Drift forward path = `register_drift_callback(callback)`** (Step 7). The
   `DriftDetector` orchestrator exposes one registration method. Phase 6b
   registers only the verification gate. Phase 7 adds the router as a second
   caller (one new line of Phase 7 code). Forward-compatible without
   over-engineering for a two-caller world.

7. **Drift WS auth = bootstrap-actor parity with Phase 6a** (Step 8). Same
   fallback as `/v1/ws/tasks/{task_id}/stream`: when no `Authorization` header
   is present and the keystore is available, auto-mint the bootstrap actor
   (`adam` / `ash`, both `is_admin=True`). Matches Phase 6a Decision 4 locked
   scope; diverging here would create per-endpoint auth inconsistency with no
   security benefit.

---

## Critical files this phase touches

**New:**
- `phoenix/state/sqlite_backend.py`
- `phoenix/state/postgres_backend.py`
- `phoenix/state/factory.py`
- `phoenix/state/migrations/0001_phase6b_initial.py`
- `phoenix/state/migrations/runner.py`
- `phoenix/queue/nats_client.py`
- `phoenix/queue/task_queue.py`
- `phoenix/queue/embedded_runner.py`
- `phoenix/verification/drift_detector.py`
- `tests/integration/test_state_backend.py`
- `tests/integration/test_nats_queue.py`
- `tests/integration/test_drift_detector.py`
- `tests/integration/test_ws_calibration_drift.py`
- `scripts/launch_with_nats.bat` (demo)

**Modified (additive):**
- [phoenix/state/backend_protocol.py](C:\Phoenix\phoenix\state\backend_protocol.py) — new methods; Phase 6a methods unchanged.
- [phoenix/verification/drift_state.py](C:\Phoenix\phoenix\verification\drift_state.py) — rewire `read_drift_state` to consult the detector; fix stale Phase 7 docstring references; remove the always-healthy stub.
- [phoenix/api/event_broker.py](C:\Phoenix\phoenix\api\event_broker.py) — add `NATSEventBroker` alongside the existing in-memory broker; selection via env var; default `memory` unchanged.
- [phoenix/api/routes.py](C:\Phoenix\phoenix\api\routes.py) — add `/v1/ws/calibration/drift` handler.
- [phoenix/safety/kill_switch.py](C:\Phoenix\phoenix\safety\kill_switch.py) — write-through SQLite mode (dual-write, fail-closed on read mismatch).
- [pyproject.toml:7](C:\Phoenix\pyproject.toml) — version bump.
- [CHANGELOG.md](C:\Phoenix\CHANGELOG.md) — Phase 6b entry.

## Reuse from prior phases

- `StateBackend` Protocol — [phoenix/state/backend_protocol.py](C:\Phoenix\phoenix\state\backend_protocol.py) — Phase 6a seam; Phase 6b drops impls behind it (additive Protocol expansion).
- `DriftStateUnavailable` and `DriftState` dataclass — [phoenix/verification/drift_state.py](C:\Phoenix\phoenix\verification\drift_state.py) — already shipped in Phase 5/6a; Phase 6b populates them with real telemetry.
- `EventBroker` / `TaskEvent` / `get_broker` — [phoenix/api/event_broker.py](C:\Phoenix\phoenix\api\event_broker.py) — Phase 6a contract; Phase 6b adds a NATS impl behind the same surface.
- `WSTokenStore` mint/consume — [phoenix/api/ws_auth.py](C:\Phoenix\phoenix\api\ws_auth.py) — Phase 6a auth; Phase 6b's `/v1/ws/calibration/drift` reuses it directly.
- Phase 6a's 10-step rhythm + stop-gate discipline + pre-commit gates.
- CHANGELOG entry shape from [CHANGELOG.md:18-115](C:\Phoenix\CHANGELOG.md) (Phase 6a entry).

## Verification (end to end, after Step 10)

1. `git -C C:\Phoenix status` clean, on `main`, after PR #6 merges.
2. `pytest tests/ -q` reports ~140-150 passed (113 baseline + 25-35 new), 0 failed, 0 errors.
3. `ruff check .`, `ruff format --check .`, `mypy --strict phoenix/` all clean.
4. `python -c "import phoenix; print(phoenix.__version__)"` → `1.0.0.dev7`.
5. `Get-Content C:\Phoenix\CHANGELOG.md | Select-Object -First 25` shows the Phase 6b section at the top with all 10 steps reflected.
6. Manual end-to-end: start NATS embedded, start Phoenix daemon, POST a `PhysicsTask` from CLI, connect a WS client to `/v1/ws/tasks/{task_id}/stream`, observe task lifecycle events; in parallel connect another WS client to `/v1/ws/calibration/drift`, inject a synthetic drift signal via test hook, observe `drift.alert` event flow.
7. Restart the Phoenix daemon between two solves; the kill-switch state, ActorPermissions, and most-recent drift snapshot all persist (SQLite survives restart).

---

## What this guide deliberately does NOT propose

- No changes to `C:\frank-data\` or its benchmarks (DF&E lives at `C:\frank-data\`; Phoenix vendors a frozen snapshot at `C:\Phoenix\vendor\` and does not modify the source).
- No force-pushes, no destructive history rewrites.
- No removal of the Phase 6a JSON-file kill-switch path until Phase 7 (Step 2 ships dual-write as a migration ramp; full JSON removal is later so we don't lose the fallback while the SQLite path is bedding in).
- No changes to the verification gate's stage 0-6 logic from Phase 6a (Step 7 only changes what `read_drift_state` returns; the gate's consumption of it is the same code path).
- No new runtime dependencies beyond NATS (`nats-py`), Postgres driver (per [OPEN] 2), and possibly `psutil` (for the low-priority thread scheduling on Windows).
