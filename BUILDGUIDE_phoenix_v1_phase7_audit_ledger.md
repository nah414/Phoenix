# BUILDGUIDE — Phoenix v1 Phase 7: Audit log + Omega Ledger + drift→router feedback

**Status:** DRAFT — under active design with Adam.
**Authoritative location:** `C:\Phoenix\BUILDGUIDE_phoenix_v1_phase7_audit_ledger.md`
**Architectural reference:** `C:\Phoenix\PHOENIX_ARCHITECTURE_v1.md` (v1 + 2026-05-08 follow-up).
**Phase scope:** Phase 7 only. Phase 8 (admin dev-ops backdoor `/v1/admin/...`), Phase 9 (LoRA + MCP + CLI), Phase 10 (release artifacts), Phase 11 (final acceptance) are separate build guides.
**Date opened:** 2026-05-11.
**Author of record:** Adam (with Claude as design partner).

---

## 0 — What this build guide is

Phase 7's job is to land **audit-grade observability and bit-exact replay** -- the
three commercial-grade capabilities that distinguish Phoenix from a research
prototype per architecture v1 Section 1 Decisions 15-22:

- **Structured audit log** (Decision 16) — every safety-gate decision,
  verification-gate event, request, and override emits a typed structured
  event. Native Phoenix event format; OpenTelemetry export adapter on top.
- **Omega Ledger hashchained provenance store** (Decisions 15 + 19-21) —
  every Phoenix solve produces a tamper-evident ledger entry chained to its
  predecessor. Bit-exact replay path reconstructs any historical solve.
- **OpenTelemetry export adapter** (Decision 22) — opt-in OTLP exporter for
  audit events so regulated users get standards-compliance without a hard
  OTel dependency.
- **Drift → router intelligence feedback** (Section 4.6) — the second
  `DriftDetector.register_drift_callback` caller (the first was the
  verification gate in Phase 6b). A drifted provider gets a lower
  `estimated_fidelity` score in the Router's hardware-intelligence layer.

End-to-end at the end of Phase 7: a regulated user POSTs a `PhysicsTask` with
`reproducibility_mode="strict"`, the solve completes, the Omega Ledger
captures a hashchain entry, the user POSTs to `/v1/tasks/{task_id}/replay`,
Phoenix reads the recorded ledger entry, restores the RNG seeds + FP
environment, re-runs the deterministic portion of the pipeline, and verifies
the result hash matches. Independently, ops observers can subscribe to
audit-event OTLP exports for SIEM integration.

**Phase 7's definition of done:**

- `phoenix/audit/event_format.py` ships typed dataclasses for the native
  Phoenix event format (per Decision 16): `timestamp_unix`, `actor_id`,
  `layer`, `event_type`, `parameters`, `result_hash`, `request_id`.
- `phoenix/audit/emitter.py` + `phoenix/audit/jsonl_writer.py` ship the
  fire-and-forget buffered async writer (default destination
  `~/.phoenix/runtime/audit/events-<date>.jsonl`).
- `phoenix/audit/otel_adapter.py` ships the OpenTelemetry export adapter
  (opt-in via the `otel` extra; pulls in `opentelemetry-sdk` +
  `opentelemetry-exporter-otlp-proto-http`).
- Safety gate (Phase 6a) `Stage 8` and verification gate (Phase 5)
  state-transition events emit through the audit emitter.
- `phoenix/ledger/omega_ledger.py` vendors the `omega/ledger.py` hashchain
  pattern from `C:\frank-data\` and wraps with a thin Phoenix adapter.
- `phoenix/ledger/entry_types.py` ships typed dataclasses for each ledger
  entry kind: `SolveEntry`, `OverrideByOperatorEntry`, `KillSwitchEntry`,
  `EnrollmentEntry`. Per architecture line 2243 the architecture spec
  enumerates these as the v1 entry kinds.
- `StateBackend` Protocol gains `append_ledger_entry` +
  `list_ledger_entries` + `verify_ledger_integrity` methods, with
  SQLite/Postgres impls writing to a new `ledger_entries` table.
- Verification gate (Phase 5 + 6b) wires ledger-entry composition into its
  post-solve path: VerificationProvenance + RoutingProvenance +
  TrinityCoreTrace are stitched into one `SolveEntry` per architecture §6.7.
- `phoenix/ledger/replay_engine.py` ships the replay path: read entry,
  restore deterministic environment (RNG seeds, BLAS threads, FP env),
  re-run pipeline, verify result hash.
- `phoenix/api/routes.py` gains `POST /v1/tasks/{task_id}/replay`,
  `GET /v1/audit/events`, `GET /v1/audit/ledger/verify`.
- `PhysicsTask.tolerance.reproducibility_mode` field honored by daemon for
  `default` / `strict` / `replay` (default ships `default`; strict/replay
  opt-in per request).
- `phoenix/router/intelligence.py` (Phase 4 module) registers as the second
  `DriftDetector.register_drift_callback` caller; drift state influences
  per-provider `estimated_fidelity` scoring per §4.6 Source C.
- Tests: full coverage of audit emit paths, ledger append + verify, replay
  golden-path + failure modes, reproducibility-mode behavior, drift→router
  feedback wiring.
- Pre-commit gates green; full pytest green.
- `pyproject.toml` version bumps `1.0.0.dev7` → `1.0.0.dev8`.
- `CHANGELOG.md` Phase 7 entry in the established shape.

**This guide does NOT cover:**
- Admin dev-ops backdoor endpoints (`/v1/admin/...`) for ledger
  integrity reports, kill-switch admin, etc. (Phase 8 — §8).
- LoRA adapter sandbox, MCP server, CLI commands (Phase 9 — §5.4-5.5, §9).
- OTel adapter to non-OTLP backends (Datadog/Splunk specific shims) — the
  generic OTLP exporter suffices for v1; vendor-specific shims are v1.x.
- Standalone binary, Docker image, cloud-seams concrete impls (Phase 10).
- Final §10.7 acceptance + `1.0.0` release (Phase 11).
- Multi-tenancy at the audit log / ledger layer — Phoenix v1 is
  single-install per architecture Decision 35; Phoenix Cloud handles
  tenant isolation outside the daemon.
- Audit log retention beyond local JSONL (long-term archival is Phoenix
  Cloud's commercial bundle per Decision 35).

## 1 — Prerequisites

Before starting Phase 7:

1. **Phase 6b acceptance.** PR #6 merged to `origin/main` (current tip:
   `2dd47e4`). All 274 tests pass locally with NATS + Postgres enabled.
   `python -c "import phoenix; print(phoenix.__version__)"` reports
   `1.0.0.dev7`.
2. **Architecture sections read fresh.** Section 1 Decisions 15-22, Section
   4.6 (hardware-intelligence Source C — drift feedback), Section 4.8
   (Router under reproducibility-strict + replay), Section 5.2 (the
   `/v1/audit/...` and `/v1/tasks/{task_id}/replay` REST endpoints),
   Section 6.7 (verification gate's provenance composition).
3. **`omega/ledger.py` vendorability confirmed at Step 4 start.** The file
   exists at `C:\frank-data\omega\ledger.py`. Step 4 reads it to confirm
   it's vendorable verbatim (Phase 1 vendoring discipline) or wraps it
   via a thin adapter if it's too tied to its host context.
4. **`phoenix/router/intelligence.py` exists from Phase 4.** Phase 7 Step 9
   adds one method to register on the drift detector; it does not refactor
   Phase 4 code.
5. **Working tree clean.** `git -C C:/Phoenix status` clean on
   `phase-7-audit-ledger` branch (modulo the untracked `.claude/` harness
   state and the harmless `Ctemp_section4.txt` artifact).
6. **No OneDrive paths.** Adam's standing rule.

## 2 — Phase-gate review protocol

Phase 7 has **ten steps** (Section 3.1 through 3.10), matching Phase 6a/6b
rhythm. Each step ends with:

```
=== STEP N COMPLETE — AWAITING ADAM REVIEW ===
```

No advancement past a stop gate without explicit Adam approval. The
`[OPEN: ...]` escalation rule applies for any mid-step architectural
ambiguity not resolved in this BUILDGUIDE.

**Pre-commit gates at every step boundary:**
- `ruff check .` — clean
- `ruff format --check .` — clean
- `mypy --strict phoenix/` — clean
- `pytest tests/unit/test_smoke.py -q` — green

**Full test gate at Step 10 only:** `pytest tests/ -q` — green (with NATS
+ Postgres enabled per the Phase 6b validation pattern, expect
~330-350 passed depending on Phase 7 test count).

## 3 — Phase 7 deliverables

### 3.1 — Step 1: Audit event format + buffered async emitter + JSONL writer

**What lands:**
- `phoenix/audit/event_format.py` — typed `AuditEvent` dataclass per
  Section 1 Decision 16: `timestamp_unix: float`, `actor_id: str`,
  `layer: str` (e.g. `"safety_gate"`, `"verification_gate"`, `"router"`,
  `"api"`), `event_type: str` (dot-namespaced, e.g.
  `"safety.stage5.rate_limit_deducted"`), `parameters: dict[str, Any]`,
  `result_hash: str`, `request_id: str | None`. Plus a `to_json_line`
  serializer that produces one canonical JSON line.
- `phoenix/audit/jsonl_writer.py` — `JSONLWriter` class with async-queue
  fire-and-forget semantics. Writes to
  `~/.phoenix/runtime/audit/events-YYYY-MM-DD.jsonl` (date-stamped per
  locked OPEN-1, see Open Items). Rotates at midnight UTC. PERF: <50 µs
  per emit (just queues; actual write happens on background thread).
- `phoenix/audit/emitter.py` — module-level `get_emitter()` singleton +
  `emit(event)` convenience. Multiple sinks supported (JSONL always on;
  OTel sink registered in Step 3 when env var enables it).
- Module-level singleton + `reset_emitter()` for test isolation.

**Verification:**
```powershell
python -c "from phoenix.audit.emitter import get_emitter; from phoenix.audit.event_format import AuditEvent; import time; get_emitter().emit(AuditEvent(timestamp_unix=time.time(), actor_id='ash', layer='test', event_type='step1.smoke', parameters={'k': 'v'}, result_hash='sha256:000', request_id=None))"
Get-Content "$env:USERPROFILE\.phoenix\runtime\audit\events-$(Get-Date -Format 'yyyy-MM-dd').jsonl" | Select-Object -Last 1
```

```
=== STEP 1 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.2 — Step 2: Wire audit emits across safety gate + verification gate + REST/WS

**What lands:**
- `phoenix/safety/gate.py` Stage 8 (Phase 6a placeholder) now emits a
  structured audit event for every `verify_request` call -- pass or fail.
  Event shape per Section 7's audit-emit clause (line 1524 of architecture):
  `actor_fingerprint`, `capability_checked`, `decision`, `request_id`.
- `phoenix/verification/gate.py` emits `verification.gate.*` events for
  rung selection, axis dispatch, promotion/demotion, completion.
- `phoenix/api/routes.py` emits `api.request.*` events for every
  request: REST `/v1/tasks`, `/v1/identity/ws-token`, WS connect/close
  per Section 5.6's audit-logging clause (line 1080).
- The event broker's `task.*` events (Phase 6a) remain separate from the
  audit log -- those are real-time WS streams; audit is the durable log
  per Section 5.6.

**Verification:** end-to-end test that issues a `POST /v1/tasks`, then
reads the last 5 audit events and confirms the safety-gate stage events
+ verification-gate events landed in the JSONL.

```
=== STEP 2 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.3 — Step 3: OpenTelemetry export adapter

**What lands:**
- `phoenix/audit/otel_adapter.py` — `OTelExporter` that subscribes to the
  emitter's event stream and translates `AuditEvent` instances to
  OpenTelemetry log records, exporting via OTLP per Decision 22.
- `pyproject.toml` adds the `otel` optional extra:
  `opentelemetry-sdk>=1.20,<2.0`,
  `opentelemetry-exporter-otlp-proto-http>=1.20,<2.0` (per locked
  OPEN-2; HTTP/protobuf default).
- Activation via `$PHOENIX_OTEL_ENABLED=1` (default off); endpoint
  configurable via `$OTEL_EXPORTER_OTLP_ENDPOINT` (the standard OTel
  env var).
- mypy override added for `opentelemetry.*` per the psycopg/nats pattern.

**Verification:** with `$PHOENIX_OTEL_ENABLED=1` and a local OTel
collector running, emit an audit event and verify it appears in the
collector's output. Tests use a mock OTLP receiver (no real collector
needed for CI).

```
=== STEP 3 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.4 — Step 4: Vendor `omega/ledger.py` + thin Phoenix wrapper

**What lands:**
- `vendor/omega/__init__.py` (empty, per Phase 1 vendoring pattern).
- `vendor/omega/ledger.py` — vendored verbatim from
  `C:\frank-data\omega\ledger.py` (per locked OPEN-3; if the source is
  too tied to its host context, OPEN-3 falls back to "vendor + thin
  Phoenix adapter that handles the delta").
- `phoenix/ledger/omega_ledger.py` — thin Phoenix adapter:
  `class OmegaLedger`: wraps the vendored hashchain logic;
  `append_entry(entry: LedgerEntry) -> LedgerLink`; `verify_chain() -> ChainVerificationReport`;
  `read_entry(entry_id: str) -> LedgerEntry`.
- `phoenix/ledger/entry_types.py` — typed dataclasses for each entry kind
  enumerated in architecture line 2243: `SolveEntry`, `OverrideByOperatorEntry`,
  `KillSwitchEntry`, `EnrollmentEntry`. Common base: `LedgerEntry` with
  `entry_id`, `entry_kind`, `timestamp_unix`, `actor_id`, `parent_hash`,
  `entry_hash`, `payload`.
- mypy override for `omega.*` per the Phase 6b vendored-modules pattern.

**Verification:** construct an `OmegaLedger`, append 3 `SolveEntry`s,
call `verify_chain()`, assert no integrity violations. Tamper with one
entry's `payload`, re-verify, assert chain-broken report names the bad
link.

```
=== STEP 4 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.5 — Step 5: Extend `StateBackend` Protocol + SQLite/Postgres impls for ledger

**What lands:**
- `phoenix/state/backend_protocol.py` gains 3 new methods (additive,
  matching the Phase 6b additive expansion pattern):
  - `append_ledger_entry(entry_record: dict[str, Any]) -> None`
  - `list_ledger_entries(*, since_unix: float, limit: int) -> list[dict[str, Any]]`
  - `verify_ledger_integrity() -> dict[str, Any]` — walks the chain in
    SQL via window functions, returns a summary
- `phoenix/state/migrations/phase7_ledger.py` — new migration adding the
  `ledger_entries` table (per locked OPEN-4; separate from `audit_events`
  which is the structured-event firehose). Columns: `entry_id PRIMARY KEY`,
  `entry_kind`, `timestamp_unix`, `actor_id`, `parent_hash`, `entry_hash`,
  `payload_json`, plus indexes on `timestamp_unix` and `entry_kind`.
- SQLite + Postgres impls of the 3 new methods (dialect dispatch via the
  Phase 6b migration runner pattern).

**Verification:** parametrized SQLite + Postgres parity test for the 3
new methods, mirroring the Phase 6b Step 9 parity pattern.

```
=== STEP 5 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.6 — Step 6: Wire verification gate to compose + persist ledger entries

**What lands:**
- `phoenix/verification/gate.py` post-solve path now composes a
  `SolveEntry` from:
  - `VerificationProvenance` (Phase 5 — Section 6.7)
  - `RoutingProvenance` (Phase 4 — Section 4)
  - `TrinityCoreTrace` (Phase 2/3 — Section 2)
  - `Result` (top-level value + error_bar + sigma + agreement_type)
- The composed entry's hash is computed over the canonical JSON of all
  four components concatenated (per Section 6.7).
- Entry persisted via `state_backend.append_ledger_entry(entry.to_dict())`
  AND simultaneously appended to the in-memory `OmegaLedger` for
  same-session chain verification (the state backend is the durable
  store; the in-memory ledger is the fast verifier).
- `Result.provenance` gains an `omega_ledger_entry_id` field so callers
  can correlate.

**Verification:** integration test that POSTs a task end-to-end, then
verifies the ledger entry exists, has the correct `parent_hash` pointing
at the prior entry, and `entry_hash` is reproducible from the recorded
`payload`.

```
=== STEP 6 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.7 — Step 7: Reproducibility modes (strict + replay) — RNG/BLAS/FP env helpers

**What lands:**
- `phoenix/_internal/reproducibility.py` — helpers for capturing and
  restoring the deterministic environment per Decision 21:
  - `capture_environment() -> EnvSnapshot` records numpy random state,
    `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `OPENBLAS_NUM_THREADS`,
    floating-point environment (rounding mode, FMA flag where supported).
  - `restore_environment(snap: EnvSnapshot)` reinstates exactly.
  - `with deterministic_environment(snap): ...` context manager for
    scoped restoration.
- `phoenix/trinity/pipeline.py` honors `task.tolerance.reproducibility_mode`:
  - `default` — no changes (current Phase 5 behavior).
  - `strict` — captures EnvSnapshot at solve start; records it in the
    ledger entry. PERF: 15-30% wall-clock cost per Decision 20 (BLAS
    forced to single thread).
  - `replay` — strict mode plus the gate re-runs the pipeline via the
    replay engine before returning. PERF: 2x wall-clock per Decision 19.
- `LedgerEntry.payload` for `SolveEntry` gains `environment_snapshot`.

**Verification:** parametrized over `["default", "strict"]`; a solve in
strict mode produces a ledger entry with a non-null environment_snapshot;
restoring + re-running yields the same `result_hash`.

```
=== STEP 7 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.8 — Step 8: Replay engine + `POST /v1/tasks/{task_id}/replay` endpoint

**What lands:**
- `phoenix/ledger/replay_engine.py`:
  - `replay(entry_id: str) -> ReplayReport` reads the ledger entry,
    restores the EnvSnapshot, re-runs the deterministic portion of the
    pipeline (solver → control; orchestrate reads recorded shots from
    the entry per Decision 20), computes the new `result_hash`, compares
    against the recorded hash.
  - `ReplayReport` dataclass: `original_entry_id`, `replay_entry_id`,
    `hashes_match: bool`, `divergent_layer: str | None`, `wall_clock_ms`.
  - `ReplayDivergence` typed exception when hashes don't match; names
    the divergent layer.
- `phoenix/api/routes.py` adds `POST /v1/tasks/{task_id}/replay` per
  architecture §5.2 line 956. Returns 200 with `ReplayReport` on
  success; 404 if entry missing; 409 if entry's `cloud_shots_recorded`
  is True and the recorded shots cannot be read; 500 with
  `ReplayDivergence` detail on hash mismatch.
- `phoenix/api/routes.py` also adds `GET /v1/audit/events?since=...&limit=...`
  and `GET /v1/audit/ledger/verify` per §5.2.

**Verification:** golden-path replay test (run task, replay, hashes
match). Tamper test (mutate a payload field in the DB, replay, expect
`ReplayDivergence`). Provider-divergence test (entry has
`cloud_shots_recorded=True` but recorded shots missing → expect 409).

```
=== STEP 8 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.9 — Step 9: Drift → router intelligence feedback callback

**What lands:**
- `phoenix/router/intelligence.py` gains a `register_for_drift_updates()`
  function (called from the FastAPI lifespan) that registers the
  router's `on_drift_snapshot(snapshot)` as a second
  `DriftDetector.register_drift_callback` caller -- the OPEN-6 forward-
  compat seam from Phase 6b paying its dividend in exactly one line.
- The router's intelligence layer maintains a per-provider drift
  multiplier (default 1.0; lowered when a provider is named in
  `firing_detectors` of a high-confidence drift snapshot). The
  multiplier scales `estimated_fidelity` in `RoutingDecision` scoring
  per architecture §4.6 Source C ("a provider that drifted three
  releases ago has lower estimated_fidelity than its self-reported
  number").
- `phoenix/api/routes.py` lifespan additionally calls
  `phoenix.router.intelligence.register_for_drift_updates()` at startup.

**Verification:** integration test that constructs a drift snapshot with
`firing_detectors=["ibm_brisbane_drift"]`, fires the callback, asserts
the router's per-provider multiplier for `ibm_brisbane` dropped.

```
=== STEP 9 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.10 — Step 10: Acceptance, version bump, CHANGELOG entry

**What lands:**
- `pyproject.toml` and `phoenix/_internal/version.py` bump
  `1.0.0.dev7` → `1.0.0.dev8`.
- `CHANGELOG.md` Phase 7 entry at the top, same shape as Phase 6b's
  entry: locked decisions, what landed, tests, bug fixes, out-of-scope.
- Test-version assertions in `test_health.py` + `test_smoke.py`
  updated to `1.0.0.dev8`.
- Branch pushed; PR #7 opened against `main`.
- Pre-commit gates green; full pytest green (expect ~330-350 passed
  with NATS + Postgres enabled).

**Verification (end-to-end):**
```powershell
ruff check .; ruff format --check .; mypy --strict phoenix/
$env:PHOENIX_POSTGRES_TEST_DSN = 'postgresql://phoenix_test:phoenix_test@127.0.0.1:5432/phoenix_test'
# (Spawn nats-server on 24222/28222 + set PHOENIX_NATS_TEST_ENABLED=1 + PHOENIX_NATS_URL per Step 10 of Phase 6b)
pytest tests/ -q   # expect green
python -c "import phoenix; print(phoenix.__version__)"   # 1.0.0.dev8
```

```
=== STEP 10 COMPLETE — AWAITING ADAM REVIEW ===
```

---

## Open items (proposed recommendations — to be locked before Step 1 starts)

Six architectural ambiguities surfaced during BUILDGUIDE authoring. My
recommendations are below; lock pending Adam's structured-question review.

1. **`[OPEN: 1]` Audit log JSONL rotation policy.** Step 1.
   - Recommendation: **date-stamped daily files** (`events-YYYY-MM-DD.jsonl`),
     rotated at midnight UTC, no size cap. Keep all files locally;
     archival to long-term storage is Phoenix Cloud's commercial bundle
     (Decision 35). Simpler than size-based rotation; predictable per-day
     file boundary aligns with standard SIEM ingest cadence.

2. **`[OPEN: 2]` OTel exporter protocol.** Step 3.
   - Recommendation: **HTTP/protobuf** (`opentelemetry-exporter-otlp-proto-http`).
     Works through corporate firewalls (port 4318 unprivileged); standard
     OTel collector default. gRPC alternative is `proto-grpc` but adds
     `grpcio` as a transitive dep with native compilation, which complicates
     wheel-only installs.

3. **`[OPEN: 3]` Omega Ledger vendoring approach.** Step 4.
   - Recommendation: **vendor + thin Phoenix adapter**, same as Phase 6b
     OPEN-5 for `ml/drift_ensemble.py`. At Step-4 start, read
     `C:\frank-data\omega\ledger.py` and confirm vendorability. If too
     tied to its host context, the adapter absorbs the delta.

4. **`[OPEN: 4]` Ledger storage placement.** Step 5.
   - Recommendation: **new `ledger_entries` table**, separate from the
     Phase 6b `audit_events` table. Reason: audit_events is the
     structured-firehose (every gate decision, every request, ~thousands
     per day); ledger_entries is only completed solves + admin overrides
     + kill-switch flips (~dozens to hundreds per day, hashchain-worthy).
     Mixing them would make hashchain verification a `WHERE entry_kind IN
     (...)` filter on a huge table; separating keeps the chain walk fast.

5. **`[OPEN: 5]` Reproducibility default mode shipped at v1.** Step 7.
   - Recommendation: **`default`** (current Phase 5 behavior preserved).
     Users opt into `strict` / `replay` per request via
     `task.tolerance.reproducibility_mode`. Reasons: backward compat
     with Phase 5 callers; strict/replay are 15-30%/2x wall-clock more
     expensive and shouldn't be the implicit cost on every solve.

6. **`[OPEN: 6]` Replay engine env-restoration aggressiveness.** Step 7.
   - Recommendation: **restore the standard quartet** — numpy random
     state, `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `OPENBLAS_NUM_THREADS`
     all forced to `1`. PLUS capture+restore `numpy.errstate` (floating-
     point error handling). Skip FMA disable for v1 (FMA detection is
     architecture-dependent and Decision 20 PERF note suggests it's
     optional). Document the residual non-determinism (Intel MKL's
     internal jitting can still vary) as a known v1 limitation in the
     CHANGELOG.

---

## Critical files this phase touches

**New:**
- `phoenix/audit/event_format.py`
- `phoenix/audit/emitter.py`
- `phoenix/audit/jsonl_writer.py`
- `phoenix/audit/otel_adapter.py`
- `phoenix/ledger/omega_ledger.py`
- `phoenix/ledger/entry_types.py`
- `phoenix/ledger/replay_engine.py`
- `phoenix/_internal/reproducibility.py`
- `phoenix/router/intelligence_drift_callback.py` (or add to existing
  `intelligence.py` — Step 9's call)
- `phoenix/state/migrations/phase7_ledger.py`
- `vendor/omega/__init__.py`
- `vendor/omega/ledger.py`
- `tests/integration/test_audit_emit.py`
- `tests/integration/test_omega_ledger.py`
- `tests/integration/test_replay_engine.py`
- `tests/integration/test_drift_router_feedback.py`

**Modified (additive):**
- [phoenix/state/backend_protocol.py](C:\Phoenix\phoenix\state\backend_protocol.py) — 3 new methods.
- [phoenix/state/sqlite_backend.py](C:\Phoenix\phoenix\state\sqlite_backend.py) + [postgres_backend.py](C:\Phoenix\phoenix\state\postgres_backend.py) — concrete impls.
- [phoenix/safety/gate.py](C:\Phoenix\phoenix\safety\gate.py) — Stage 8 audit emit wiring.
- [phoenix/verification/gate.py](C:\Phoenix\phoenix\verification\gate.py) — event emit + ledger composition.
- [phoenix/api/routes.py](C:\Phoenix\phoenix\api\routes.py) — 3 new endpoints + lifespan additions.
- [phoenix/trinity/pipeline.py](C:\Phoenix\phoenix\trinity\pipeline.py) — reproducibility-mode dispatch.
- [phoenix/trinity/data_model.py](C:\Phoenix\phoenix\trinity\data_model.py) — `omega_ledger_entry_id` on `ProvenanceTrace`.
- [pyproject.toml](C:\Phoenix\pyproject.toml) — `[otel]` extra + mypy overrides.
- [CHANGELOG.md](C:\Phoenix\CHANGELOG.md) — Phase 7 entry.
- [tests/integration/test_health.py](C:\Phoenix\tests\integration\test_health.py) + [tests/unit/test_smoke.py](C:\Phoenix\tests\unit\test_smoke.py) — version-string assertions.

## Reuse from prior phases

- `StateBackend` Protocol — Phase 6a seam, additive Phase 6b expansion,
  additive Phase 7 expansion. Same pattern.
- `DriftDetector.register_drift_callback` — Phase 6b OPEN-6 seam; Phase 7
  Step 9 adds the second caller.
- Vendoring discipline (Phase 1) — `vendor/omega/ledger.py` lands the
  same way `vendor/ml/drift_ensemble.py` did in Phase 6b.
- FastAPI lifespan — Phase 6b Step 4 + Step 8 wiring patterns; Phase 7
  Step 9 adds one more call in the same shape.
- Migration runner (Phase 6b Step 2) — Phase 7 Step 5's
  `phase7_ledger.py` migration plugs in via the existing
  `ALL_MIGRATIONS` enumeration in `runner.py`.
- Parametrized state-backend parity tests (Phase 6b Step 9) — Phase 7
  Step 5's new methods get the same parametrization treatment.
- CHANGELOG entry shape — match Phase 6b's `[1.0.0.dev7] — 2026-05-10`
  entry: locked decisions, what landed, tests, bug fixes, out-of-scope.

## Verification (end to end, after Step 10)

1. `git status` clean on `main` after PR #7 merges.
2. `pytest tests/` green with NATS + Postgres enabled (expect
   ~330-350 tests).
3. `ruff check .`, `ruff format --check .`, `mypy --strict phoenix/`
   all clean.
4. `phoenix.__version__ == "1.0.0.dev8"`.
5. `CHANGELOG.md` Phase 7 entry at the top.
6. Manual end-to-end: POST a `strict`-mode task, verify ledger entry
   appears in DB, POST the replay endpoint with that task_id, verify
   `ReplayReport.hashes_match` is True. POST again with a mutated payload
   (via direct DB write), verify `ReplayDivergence` returned with the
   divergent layer named.
7. With `$PHOENIX_OTEL_ENABLED=1` and a local OTel collector, verify
   audit events appear in the collector.
8. Construct a synthetic drift snapshot via the detector's `run_cycle`,
   verify the router's per-provider `estimated_fidelity` for the named
   firing provider drops in the next routing decision.

---

## What this guide deliberately does NOT propose

- No changes to `C:\frank-data\` or its benchmarks. The Omega Ledger
  vendoring is one-way (copy in, never write back).
- No force-pushes, no destructive history rewrites.
- No retroactive ledger entries — the chain starts at the first
  `append_ledger_entry` call after Phase 7 deploys; pre-Phase-7 solves
  exist only in their Phase 5/6a/6b provenance, not in the Omega chain.
- No multi-tenancy at the audit layer — Phoenix v1 is single-install
  (Decision 35); per-tenant audit isolation is Phoenix Cloud's bundle.
- No vendor-specific OTel shims — generic OTLP HTTP/protobuf only.
  Datadog/Splunk-specific exporters are v1.x.
- No CLI commands for replay or audit query — Phase 9 ships those as
  part of the CLI surface. Phase 7 ships only the REST endpoints.
- No admin endpoints — `/v1/admin/ledger/...` and
  `/v1/admin/audit/...` are Phase 8 (Section 8).
- No removal of Phase 6a JSON kill-switch file — same as Phase 6b: the
  JSON path stays as fallback until a future phase promotes SQLite to
  source of truth.
