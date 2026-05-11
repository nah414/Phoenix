# BUILDGUIDE — Phoenix v1 Phase 8: Admin dev-ops backdoor

**Status:** DRAFT — under active design with Adam.
**Authoritative location:** `C:\Phoenix\BUILDGUIDE_phoenix_v1_phase8_admin_devops.md`
**Architectural reference:** `C:\Phoenix\PHOENIX_ARCHITECTURE_v1.md` (Section 8 + 2026-05-06 follow-ups).
**Phase scope:** Phase 8 only. Phase 9 (LoRA + MCP + CLI), Phase 10 (release artifacts), Phase 11 (final acceptance) are separate build guides.
**Date opened:** 2026-05-11.
**Author of record:** Adam (with Claude as design partner).

---

## 0 — What this build guide is

Phase 8's job is to land the **dev-ops backdoor** — Phoenix's privileged
operational surface for inspection, diagnostics, and the seven explicit
manual interventions per architecture v1 Section 8.

The dev-ops layer ships a single principle: **Phoenix is operable
without paging the developer.** Read-only inspection is broad and free;
mutation is narrow and audit-weighted. The kill switch (Section 8.3) is
the load-bearing piece — Phoenix runs in production environments where
"stop accepting *normal* work now" is the difference between a tidy
incident and a corrupted ledger.

End-to-end at the end of Phase 8: an admin with `is_admin=True` can:
- inspect Phoenix's full operational state without restarting the daemon;
- engage and release the kill switch with persistent state across
  process restarts;
- manually quarantine and restore providers based on out-of-band
  knowledge;
- override `HUMAN_REVIEW` results from the verification gate with a
  recorded operator disposition;
- pull the full hashchain integrity report + admin-scoped audit replay.

**Phase 8's definition of done:**

- `phoenix/admin/` package with seven module files (one per
  endpoint group) per the README inventory.
- `phoenix/admin/auth.py` — `require_admin(actor)` dependency that
  inserts after `verify_request` and refuses non-`is_admin` actors
  with HTTP 403 `AdminPrivilegeRequired`.
- `phoenix/admin/audit_decorator.py` — wraps every admin handler so
  each call emits a top-priority `admin.<endpoint>.<outcome>` audit
  event with operator identity + parameters + result hash. Two of
  the seven mutations (kill switch engage/release, operator override)
  also land as Omega Ledger entries per the architecture spec.
- Kill switch fully wired: `engage` / `release` / `status`
  endpoints + ledger entries (`KillSwitchEntry` from Phase 7 Step 4
  is reused) + refuse-to-start posture (already shipped in Phase 6b;
  Phase 8 just adds the API).
- Read endpoints for every observable subsystem Phase 1-7 produced
  state for: detailed health, calibration history + detail, router
  decisions log, provider health history, verification rung
  distribution, pending-review queue, audit replay, ledger integrity
  report.
- Mutation endpoints for the seven architecture-listed actions
  (kill-switch ×2, manual quarantine/restore, calibration run,
  HUMAN_REVIEW override, adapter force-revalidate). The
  adapter mutation ships as a 501 stub since LoRA adapter management
  is Phase 9 — the route is registered so OpenAPI advertises it.
- `pyproject.toml` version bump `1.0.0.dev8` → `1.0.0.dev9`.
- `CHANGELOG.md` Phase 8 entry in the established shape.
- Pre-commit gates green; full pytest green with the Phase 7 infra
  stack (Postgres + NATS) running.

**This guide does NOT cover:**

- LoRA adapter sandbox + management endpoints (Phase 9 — §5.4-5.5).
  The `POST /v1/admin/adapters/{id}/force-revalidate` route ships
  as a 501 stub since the adapter management plane lands in Phase 9.
  `GET /v1/admin/adapters/{id}/round-trip-history` is deferred
  entirely to Phase 9.
- MCP server, CLI commands (Phase 9).
- Standalone binary, Docker image, cloud-seams concrete impls
  (Phase 10).
- Final §10.7 acceptance + `1.0.0` release (Phase 11).
- Phoenix Cloud integration (Section 8.5) — the
  outside-Phoenix-process layer described in Section 8.5 is a
  Phoenix Cloud (commercial bundle) concern, not v1 Phase 8.

## 1 — Prerequisites

Before starting Phase 8:

1. **Phase 7 acceptance.** PR #7 merged to `origin/main` (current
   tip: `56cd3c9`). All 402 tests pass locally with NATS + Postgres
   enabled. `python -c "import phoenix; print(phoenix.__version__)"`
   reports `1.0.0.dev8`.
2. **Architecture sections read fresh.** Section 8.1-8.9 covering
   why dev-ops is its own layer, the endpoint surface, kill-switch
   semantics, the read-only-by-default principle, observability
   beyond admin endpoints, failure modes, performance budget, and
   cross-layer interactions.
3. **Phase 1-7 substrate available.** Phase 8 is wiring + thin
   handlers over substrate Phase 1-7 already built — the safety
   gate, the audit emitter, the Omega Ledger, the state backend's
   `pending_review_queue` + `kill_switch_state` tables, the drift
   detector's `force_cycle` API, the provider registry's
   `mark_degraded` / `mark_healthy` methods. No new substrate
   layers land in Phase 8.
4. **Working tree clean.** `git -C C:/Phoenix status` clean on
   `phase-8-admin-dev-ops` branch.
5. **No OneDrive paths.** Adam's standing rule.

## 2 — Phase-gate review protocol

Phase 8 has **ten steps** (Section 3.1 through 3.10), matching
Phase 6a/6b/7 rhythm. Each step ends with:

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

**Full test gate at Step 10 only:** `pytest tests/ -q` — green
(with NATS + Postgres enabled per the Phase 7 validation pattern,
expect ~430-450 passed depending on Phase 8 test count).

## 3 — Phase 8 deliverables

### 3.1 — Step 1: Admin scaffold + auth + audit decorator

**What lands:**
- `phoenix/admin/auth.py` — `require_admin(actor)` helper. Verifies
  `actor.is_admin` via the permissions registry. Raises a typed
  `AdminPrivilegeRequired` (Phoenix-side) that the routes layer
  translates to HTTP 403.
- `phoenix/admin/audit_decorator.py` — `with_admin_audit(layer,
  event_type_prefix)` decorator factory. Wraps each admin handler
  so every call emits a top-priority
  `admin.<endpoint>.<outcome>` audit event. Captures: operator
  actor name + fingerprint, request_id, endpoint path, request
  parameters (with PII redaction), response status, wall_clock_ms.
- `phoenix/admin/errors.py` — typed admin errors
  (`AdminPrivilegeRequired`, `QuarantineDurationExceeded`,
  `TaskNotPendingReview`, `CalibrationRunInProgress`,
  `AdapterNotLoaded`) shared across the admin handlers. Routes
  map each to an HTTP status code.
- `phoenix/api/routes.py` registers an `/v1/admin/...` sub-router
  (or adds routes inline — `[OPEN-1]`). Every admin handler
  composes `require_admin` + `with_admin_audit` + the handler body.

**Verification:** unit test that a non-admin actor calling an
admin endpoint gets 403 `AdminPrivilegeRequired`; admin call
emits the audit event with the right shape.

```
=== STEP 1 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.2 — Step 2: Kill switch endpoints

**What lands:**
- `phoenix/admin/kill_switch.py`:
  - `POST /v1/admin/kill-switch/engage` — body: `{rationale: str?}`.
    Writes the state-backend row (already-existing Phase 6b
    schema), emits top-priority audit, appends a
    `KillSwitchEntry` (Phase 7 Step 4 type) to the Omega Ledger
    with `transition="engaged"`. Returns the current state.
  - `POST /v1/admin/kill-switch/release` — body: `{rationale: str?}`.
    Mirror image; ledger entry tags `transition="released"` and
    carries the `engaged_at_unix` cross-reference.
  - `GET /v1/admin/kill-switch/status` — returns the persisted state.
- Section 7.4 stage 0 (kill-switch check) is already enforced by
  the safety gate; Phase 8 just adds the API to manage the state.

**Verification:** end-to-end test: admin engages, subsequent
`/v1/tasks` returns 503; admin releases, `/v1/tasks` resumes 200.
The ledger has two new entries (engage + release) chained
correctly.

```
=== STEP 2 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.3 — Step 3: System health + budget + governor + inference-status

**What lands:**
- `phoenix/admin/health.py`:
  - `GET /v1/admin/health/detailed` — extends `/v1/health` with
    per-subsystem rollup: Trinity Core readiness flag, drift
    detector state, NATS queue depth (when broker is NATS), state
    backend last-write latency, audit emitter pending queue size,
    Omega Ledger entry count, provider registry health summary.
  - `GET /v1/admin/governor` — system resource snapshot. v1 Phase 8
    ships psutil-based: CPU% (1-second sample), RAM% (used /
    available), disk% on the state-backend mount, process RSS. GPU
    / VRAM / NPU / thermal fields ship as `None` until Phase 9+
    when the cloud-GPU adapter layer matures and surfaces them
    (`[OPEN-2]` confirms the v1 scope).
  - `GET /v1/admin/inference-status` — placeholder shape per
    architecture line 1694. Phase 9 (LoRA adapter sandbox) fills
    it in; v1 Phase 8 ships an empty-shape stub returning
    `{adapters: [], queue_depth: 0, last_round_trip_at: null}`.
  - `GET /v1/admin/budget` — per-actor and org-level token-bucket
    state from `RateLimiter.snapshot()`. Cumulative provider spend
    fields default to `0.0` (the Phase 7 solve cost ledger has
    the data but no aggregation layer; v1.x aggregates).

**Verification:** smoke test hits each of the four endpoints
with an admin actor and asserts the response shape contract.

```
=== STEP 3 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.4 — Step 4: Calibration drill-down + force-cycle

**What lands:**
- `phoenix/admin/calibration.py`:
  - `GET /v1/admin/calibration/detail` — full drift detector
    state. Per-checker last-run timestamp, threshold configuration,
    most-recent raw output, decision history.
  - `GET /v1/admin/calibration/history` — drift cycle history from
    the audit log filtered to `drift.*` event types over a
    configurable time window.
  - `POST /v1/admin/calibration/run` — body: `{wait: bool = false}`.
    Calls `DriftDetector.force_cycle()` (Phase 6b shipped this
    method already). When `wait=true`, blocks until the cycle
    completes (typical 5-7 minutes per Decision 17 PERF note);
    when false, returns immediately with the cycle ID. Raises
    `CalibrationRunInProgress` (HTTP 409) if a cycle is already
    running.

**Verification:** test forces a cycle, polls
`/v1/admin/calibration/detail`, sees the new run reflected.

```
=== STEP 4 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.5 — Step 5: Verification + pending-review override

**What lands:**
- `phoenix/admin/verification_inspect.py`:
  - `GET /v1/admin/tasks-pending-review` — calls
    `state_backend.list_pending_reviews()`. Per-task `task_id`,
    `actor_id`, `result` snapshot, `agreement_type`, queued
    timestamp.
  - `POST /v1/admin/tasks-pending-review/{task_id}/override` —
    body: `{disposition: "ship-as-degraded" | "reject" |
    "re-run-with-tighter-bounds", reason: str}`. Requires
    `can_override_human_review` on top of `is_admin`. Writes the
    resolution row, appends an `OverrideByOperatorEntry` to the
    Omega Ledger, emits the audit event.
  - `GET /v1/admin/verification/rung-distribution` — aggregates
    rung-selection over the recent window from the audit log
    (`verification.gate.started` events carry `initial_rung`).
- **Wiring gap to fix in Step 5:** Phase 6b shipped
  `enqueue_pending_review` on the state backend but the
  verification gate doesn't currently enqueue HUMAN_REVIEW
  results; it returns `DEGRADED` inline. Per `[OPEN-3]`,
  Phase 8 decides whether to wire the enqueue (so the queue is
  real) or to treat the queue as forever-empty for v1.

**Verification:** if `[OPEN-3]` resolves to "wire the enqueue",
test that a verification gate run producing HUMAN_REVIEW lands
in the queue and the override endpoint resolves it.

```
=== STEP 5 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.6 — Step 6: Router decision log + provider health history

**What lands:**
- `phoenix/admin/router_inspect.py`:
  - `GET /v1/admin/router/decisions?limit=N` — returns the last
    N `RoutingDecision` records with full `decision_provenance`
    (Section 4.4). Reads from a new in-process ring buffer
    populated by the router's `decide()` method.
  - `GET /v1/admin/providers/health-history?provider_id&since_unix`
    — per-provider mark_degraded / mark_healthy events from the
    audit log.
- `phoenix/router/decision.py` gains a ring-buffer side-effect:
  every successful `decide()` appends the decision to a thread-safe
  bounded deque (default 1000 entries). Per `[OPEN-4]`, the
  retention size is a constant for v1 (no admin endpoint to
  configure it).

**Verification:** test submits 5 tasks, then GETs
`/v1/admin/router/decisions?limit=10` and verifies the decisions
appear in order with full provenance.

```
=== STEP 6 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.7 — Step 7: Provider manual quarantine + restore

**What lands:**
- `phoenix/admin/router_inspect.py` (extended):
  - `POST /v1/admin/providers/{provider_id}/manual-quarantine` —
    body: `{duration_seconds: int, reason: str}`. Calls
    `provider_registry.mark_degraded(provider_id)` and schedules
    an auto-restore via the FastAPI background task system. Caps
    `duration_seconds` at 86400 (24 hours per the architecture
    spec's policy default); raises
    `QuarantineDurationExceeded` (HTTP 400) over the cap.
  - `POST /v1/admin/providers/{provider_id}/manual-restore` —
    immediately calls `mark_healthy(provider_id)` and cancels
    any pending auto-restore task.
- Both mutations write top-priority audit events with the
  operator's actor identity. Per `[OPEN-5]`, manual
  quarantine/restore does NOT land in the Omega Ledger (only
  kill switch + HUMAN_REVIEW override write ledger entries per
  architecture spec — manual provider state is operational, not
  audit-grade).

**Verification:** test quarantines a provider, verifies it's
marked DEGRADED, calls restore, verifies it's HEALTHY again.

```
=== STEP 7 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.8 — Step 8: Audit replay + ledger integrity report

**What lands:**
- `phoenix/admin/audit_replay.py`:
  - `GET /v1/admin/audit/replay?since_unix&until_unix&event_type&actor_id&limit`
    — admin-scoped audit query. Beyond `/v1/audit/events`, this
    surface exposes events that aren't visible to non-admins:
    rate-limit denials of OTHER actors, signature-verification
    failures, kill-switch transitions, and admin-action audit
    events themselves. Filters compose; default returns the last
    100 events of any kind.
  - `GET /v1/admin/ledger/integrity-report` — the full
    end-to-end hashchain check. Calls
    `state_backend.verify_ledger_integrity()` (SQL structural) and
    `OmegaLedger.verify_chain()` (Python crypto). Adds a per-link
    tag-distribution histogram: how many `solve` entries vs.
    `override_by_operator` vs. `kill_switch` vs. `enrollment` over
    the configured window. Includes a chain-head pointer so ops
    can detect if the chain has frozen.

**Verification:** test that admin audit replay surfaces rate-
limit denials that `/v1/audit/events` filters out; integrity
report after a Phase 7 strict solve + replay shows the chain
valid with tag counts.

```
=== STEP 8 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.9 — Step 9: Adapter force-revalidate stub + miscellaneous

**What lands:**
- `phoenix/admin/adapters_admin.py`:
  - `POST /v1/admin/adapters/{id}/force-revalidate` — registered
    as an endpoint but returns HTTP 501
    `Not Implemented: LoRA adapter management lands in Phase 9`
    per `[OPEN-6]`. Phoenix Cloud / Phase 9 wires the real handler.
- `GET /v1/admin/adapters/{id}/round-trip-history` —
  deferred entirely to Phase 9; not registered in Phase 8's
  OpenAPI surface.
- OpenAPI tagging: every admin endpoint gets the `Admin` tag so
  `/v1/openapi.json` cleanly separates user-facing from privileged.

**Verification:** OpenAPI schema test asserts every admin endpoint
has the `Admin` tag; force-revalidate returns 501 with the
expected detail.

```
=== STEP 9 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.10 — Step 10: Acceptance, version bump, CHANGELOG entry

**What lands:**
- `pyproject.toml` and `phoenix/_internal/version.py` bump
  `1.0.0.dev8` → `1.0.0.dev9`.
- `CHANGELOG.md` Phase 8 entry at the top, same shape as Phase 7's
  entry: locked decisions, what landed, tests, bug fixes,
  out-of-scope.
- Test-version assertions in `test_health.py` + `test_smoke.py`
  updated to `1.0.0.dev9`.
- `_DEFAULT_PHOENIX_RELEASE` in SQLite + Postgres backends + drift
  detector updated to `1.0.0.dev9`.
- Branch pushed; PR #8 opened against `main`.
- Pre-commit gates green; full pytest green (expect ~430-450
  passed with NATS + Postgres enabled).

**Verification (end-to-end):**
```powershell
ruff check .; ruff format --check .; mypy --strict phoenix/
$env:PHOENIX_POSTGRES_TEST_DSN = 'postgresql://phoenix_test:phoenix_test@127.0.0.1:5432/phoenix_test'
$env:PHOENIX_NATS_TEST_ENABLED = '1'; $env:PHOENIX_NATS_URL = 'nats://127.0.0.1:24222'
pytest tests/ -q   # expect green
python -c "import phoenix; print(phoenix.__version__)"   # 1.0.0.dev9
```

```
=== STEP 10 COMPLETE — AWAITING ADAM REVIEW ===
```

---

## Open items to lock before Step 1

Six open items surfaced during BUILDGUIDE authoring. Lock with Adam
before any code lands. Locked decisions are recorded back into the
BUILDGUIDE's "Locked decisions" section so future readers see why
the implementation looks the way it does.

1. **`[OPEN-1]` Admin endpoint mount path.** Three options:
   a. **Inline in `phoenix/api/routes.py`** — register each admin
      route with the existing FastAPI `app` instance, just prefixed
      `/v1/admin/...`. Simplest; admin routes live alongside user
      routes in the same OpenAPI schema (separated by tag).
   b. **Separate FastAPI sub-app `mount("/v1/admin", admin_app)`**
      — keeps admin OpenAPI schema fully separate; downsides: two
      schema endpoints to manage, two lifespans.
   c. **APIRouter with `include_router(admin_router, prefix="/v1/admin")`**
      — middle ground. One FastAPI app + one OpenAPI schema with
      `Admin`-tagged section. Modular code (each admin handler
      group registers its own APIRouter that the parent collects).
   **Recommendation:** option (c). One schema, one lifespan,
   clean code organization, idiomatic FastAPI.

2. **`[OPEN-2]` `governor` endpoint scope for v1.** Three options:
   a. **psutil-based v1 minimum** — CPU%, RAM%, disk%, process RSS.
      GPU/VRAM/NPU/thermal fields return `None`. Minimum viable;
      ops can integrate Phoenix's `/governor` with their own
      hardware monitoring stack.
   b. **Vendor dr-frank-and-eddy's `/api/governor` implementation**
      — pull in the existing `nvidia-ml-py` / `pynvml` /
      Windows-WMI thermals code from `C:\frank-data\`. More
      hardware coverage but adds platform-specific dependencies
      that need [extras] gating.
   c. **Defer entirely to Phase 9** — `governor` endpoint not
      registered in Phase 8; Phase 9 ships it alongside the
      cloud-GPU adapter layer.
   **Recommendation:** option (a). v1 minimum gets the endpoint
   shape correct; v1.x or Phase 9 layers GPU telemetry. Lighter
   dependency footprint.

3. **`[OPEN-3]` HUMAN_REVIEW enqueue wiring.** Two options:
   a. **Wire the enqueue in Phase 8.** Verification gate's
      DEGRADED + HUMAN_REVIEW classifications enqueue a row in
      `pending_review_queue` instead of returning inline. The
      override endpoint at Step 5 resolves real queue entries.
      Workflow becomes: user submits → DEGRADED HUMAN_REVIEW →
      task waits for admin override → admin POSTs override →
      ledger entry written.
   b. **Treat the queue as forever-empty for v1 Phase 8.** Ship
      `/v1/admin/tasks-pending-review` as an empty list; the
      override endpoint returns `TaskNotPendingReview`. Wiring
      the enqueue is deferred to v1.x or Phase 9 when the human-
      in-loop workflow gets explicit product attention.
   **Recommendation:** option (a). The architecture spec (Section
   7.7) clearly anticipates the queue is real. Phase 6b shipped
   the state-backend methods; not wiring them is "ship dead code,
   admit it ships dead code". The wiring is small.

4. **`[OPEN-4]` Router decision retention.** Two options:
   a. **In-process ring buffer, size 1000 (default), config via
      env `$PHOENIX_ROUTER_DECISION_LOG_SIZE`.** Bounded memory;
      survives only daemon lifetime; ample for ops debugging.
   b. **Persist decisions to `state_backend.append_audit_event`**
      with `event_type="router.decision"`. Survives restart;
      queryable via `/v1/audit/events`; slightly more write
      pressure per solve.
   **Recommendation:** option (a) for v1 Phase 8. The audit log
   already records `verification.gate.*` events including
   provider choice via `routing_provenance` on the ledger entry.
   Adding another write path for the same data is premature; the
   ring buffer is enough for "what just happened" ops debugging.
   v1.x can promote to persisted if a real need emerges.

5. **`[OPEN-5]` Manual quarantine ledger entries.** Two options:
   a. **No ledger entry; audit-event only.** Manual quarantine
      is operational, not audit-grade. The audit emit captures
      operator identity + rationale for the audit trail; the
      ledger doesn't grow with every provider-state toggle.
   b. **Add a `ProviderStateOverrideEntry` ledger kind.** Every
      quarantine/restore appends a hashchain entry alongside the
      audit event. Heavier; matches kill-switch precedent.
   **Recommendation:** option (a). The architecture spec Section
   8.4 explicitly names "the entire admin mutation surface" and
   lists kill switch + HUMAN_REVIEW override as the only
   mutations that write ledger entries. Provider state is
   operational state, not provenance.

6. **`[OPEN-6]` Adapter `force-revalidate` route in Phase 8.** Two
   options:
   a. **Register as 501 stub in Phase 8.** Phase 9 fills the
      implementation. OpenAPI advertises the endpoint; clients
      get a clear error message until Phase 9.
   b. **Defer entirely to Phase 9.** Endpoint not registered;
      OpenAPI doesn't advertise it. Phase 9 introduces the
      route.
   **Recommendation:** option (a). Advertising the surface
   early lets v1 client integrators see the full admin shape and
   handle the 501 gracefully. Matches the architecture spec's
   "Section 8.2 names the endpoint" framing.

---

## Where you (Adam) shape the design

These are the calls I want input on rather than inventing answers for:

1. **Six open items above** — recommendations attached; please confirm
   or override before Step 1 lands.
2. **HUMAN_REVIEW enqueue wiring (OPEN-3)** is the highest-stakes
   call — it makes the queue real and changes the user-visible
   behavior of DEGRADED-classified solves. Worth a deliberate yes/no.
3. **`governor` scope (OPEN-2)** affects the optional-extra surface;
   nvml + WMI thermals would justify a `[governor]` extra. Worth a
   deliberate decision about Phoenix's hardware-telemetry posture.

These belong in the BUILDGUIDE's "open items" section so you decide
once, on paper, before code lands.

---

## What I am NOT proposing

- No changes to `C:\frank-data\` (DF&E) or its benchmark shell.
- No force-pushes, no destructive history rewrites.
- No new substrate layers — Phase 8 is wiring + handlers over
  Phase 1-7 substrate.
- No LoRA adapter, MCP, or CLI surface — Phase 9 owns those.
- No Phoenix Cloud (Section 8.5) work — that's outside-Phoenix-
  process, commercial-bundle scope.
- No changes to the safety gate or verification gate (Phase 6a/7
  contracts are locked); only new admin handlers added.
