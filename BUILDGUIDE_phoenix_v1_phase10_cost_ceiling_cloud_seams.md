# Build Guide — Phoenix v1 Phase 10: Cost-ceiling enforcement + Phoenix Cloud abstraction seams

**Status:** DRAFT under active design with Adam.
**Authoritative location:** `C:\Phoenix\BUILDGUIDE_phoenix_v1_phase10_cost_ceiling_cloud_seams.md`
**Architectural reference:** `C:\Phoenix\PHOENIX_ARCHITECTURE_v1.md` (Section 4.7 cost estimation + Section 10.3.1 Phoenix Cloud abstraction seams + Section 10.7 acceptance criteria).
**Phase scope:** Phase 10 only — NO Phase 11 compositional fail-closed test, NO long-window replay test, NO distribution artifacts.
**Date opened:** 2026-05-13.
**Author of record:** Adam (with Claude as design partner).

---

## 0 — What this build guide is

Phase 10 lands two tightly-coupled architectural pieces that together close the
biggest remaining gap in v1's acceptance criteria:

1. **Cost-ceiling enforcement (Section 4.7).** Phoenix already ships
   `cost_ceiling_usd` on RouterRequest, the `CostCeilingExceeded` error, and the
   Router's Stage 2 cost filter at the per-solve level. What's missing is the
   24-hour-window accumulators (per-actor + per-org), the `solve_cost_ledger`
   state backend table, the post-solve accounting writer, the verification
   gate's pre-promotion cost check with `budget_bound_skipped_axis` provenance,
   and the `POST /v1/admin/budget/override` admin endpoint.

2. **Phoenix Cloud abstraction seams (Section 10.3.1).** Three thin
   `typing.Protocol` definitions plus a generic `CloudSeams` name-keyed
   registry plus local default implementations that satisfy the Protocols
   without any cloud dependency. Phoenix Cloud (a separate future product)
   replaces the impls without modifying Phoenix code; v1 ships the seams now
   so the contract is locked.

The two pieces compose because the default `JobBudgetController` impl IS the
v1 cost-ceiling enforcement engine. Building them as a unit avoids a double
refactor — Phase 10's cost-ceiling code is written behind the seam from day
one, not retrofitted later.

**This guide does NOT cover:**
- Compositional fail-closed test ("panic mode") — Phase 11.
- Long-window replay test — Phase 11.
- Distribution artifacts (pip wheel, Docker image, Nuitka binary) — release-time.
- Per-directory README pass — Phase 11 docs pass.
- Per-install pricing-data refresh CLI (`phoenix admin pricing-update`) —
  deferred to v1.x; current pricing JSON staleness warning is enough for v1.

---

## 1 — Prerequisites

- Phase 9 merged onto `main` (PR #9 disposition: see git log).
- `pytest -m smoke` green.
- `python -m pytest --ignore=tests/tier1 -q` green (636 passing + 39 skipped
  at the Phase 9 tip).
- Clean working tree.
- No OneDrive paths.

---

## 2 — Phase-gate review protocol

Each of the 10 steps below ends with the canonical stop-gate marker:

```
=== STEP N COMPLETE — AWAITING ADAM REVIEW ===
```

Adam reviews the diff + verification at each gate before the next step begins.
Architectural ambiguities discovered mid-step trip the `[OPEN: ...]` escalation
discipline — drop the marker into the BUILDGUIDE and surface to Adam rather
than guessing.

Per Adam's 2026-05-13 direction ("keep building for a while before we create a
PR"): Phase 10 stays on the same `phase-9-adapters-mcp-cli` branch as Phase 9
(commits stack on top of `b3599e0`). PR ceremony is deferred to whenever Adam
asks for it — most likely after Phase 11 closes. The branch will be
re-titled/re-bodied or split when the time comes.

---

## 3 — Phase 10 deliverables

### 3.1 — Step 1: Cloud seams Protocol shells + registry

**What lands:**
- `phoenix/_internal/cloud_seams.py`:
  - `HttpAuthExtractor` Protocol (single `extract_actor(request) -> Actor` method).
  - `AuditLogExporter` Protocol (`export(event) -> None` + `flush(timeout_s) -> bool`).
  - `JobBudgetController` Protocol (`check_solve_budget(...) -> BudgetDecision` +
    `record_solve_cost(...) -> None`).
  - `BudgetDecision` frozen dataclass (`allowed: bool`, `remaining_usd: float`,
    `rationale: str`, `ceiling_applied_usd: float`).
  - `CloudSeams` generic name-keyed registry with `register(name, impl)`,
    `get(name)`, `names()` methods.
  - `UnknownSeam(KeyError)` error.
  - Module-level singleton (`get_seams() / reset_seams()` pattern matching
    Phases 6b/7/8/9).
- Phase 10 Step 1 ships **stub default impls** that raise `NotImplementedError`
  with a helpful "Phase 10 Step N lands the real impl" message. Steps 4 + 9
  replace the stubs with real local impls.

**Verification:** unit tests: registry register/get/replace, BudgetDecision
shape, UnknownSeam on unknown name, stubs raise NotImplementedError when
called.

```
=== STEP 1 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.2 — Step 2: `solve_cost_ledger` table — schema + StateBackend methods

**What lands:**
- New schema migration: `phoenix/state/migrations/phase10_cost_ledger.py`.
  - SQLite + Postgres DDL for `solve_cost_ledger` table:
    - `request_id` (PK), `actor_name`, `org_id` (nullable, defaults to
      `<actor_name>` for single-actor installs), `timestamp_unix`,
      `actual_cost_usd`, `reproducibility_mode`, `provenance_json`.
  - Indexes on `(actor_name, timestamp_unix)` and `(org_id, timestamp_unix)`
    for the 24h-window queries.
- Three new methods on `StateBackend` Protocol (and SQLite + Postgres impls):
  - `record_solve_cost(*, request_id, actor_name, org_id, timestamp_unix,
    actual_cost_usd, reproducibility_mode, provenance_json) -> None`.
  - `query_actor_24h_spend(actor_name, *, as_of_unix) -> float` — sums
    `actual_cost_usd` where `actor_name` matches AND `timestamp_unix >=
    as_of_unix - 86400`.
  - `query_org_24h_spend(org_id, *, as_of_unix) -> float` — same shape on
    `org_id`.

**Verification:** integration tests against both SQLite + Postgres backends:
record + query round trip, 24h-window cut-off correctness, idempotent
recording (same request_id is a no-op or last-write-wins per locked OPEN-2),
empty actor returns 0.0.

```
=== STEP 2 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.3 — Step 3: Cost-ceiling defaults + resolver

**What lands:**
- `phoenix/safety/cost_ceilings.py`:
  - `_DEFAULT_PER_SOLVE_CEILINGS` dict: `{"default": 5.0, "strict": 25.0,
    "replay": 50.0}` (Section 4.7).
  - `_DEFAULT_PER_ACTOR_24H_CEILINGS` dict: `{"default": 50.0, "elevated":
    500.0, "admin": None}` (None = no ceiling).
  - `_DEFAULT_PER_ORG_24H_CEILING_USD = 2000.0`.
  - `resolve_ceilings(*, reproducibility_mode, actor_tier) -> CeilingTriple`
    dataclass with per_solve / per_actor_24h / per_org_24h fields.
  - Env-var override hooks (`$PHOENIX_PER_SOLVE_CEILING_USD`,
    `$PHOENIX_PER_ACTOR_24H_CEILING_USD`, `$PHOENIX_PER_ORG_24H_CEILING_USD`)
    so installs can tune ceilings without code changes.

**Verification:** unit tests for default resolution at every (mode, tier)
combination + env-var override path + invalid-value rejection.

```
=== STEP 3 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.4 — Step 4: Default `LocalJobBudgetController` impl

**What lands:**
- `phoenix/_internal/cloud_seams.py` gains `LocalJobBudgetController` class
  implementing the Protocol:
  - `check_solve_budget(actor, estimated_cost_usd, reproducibility_mode)`:
    - Resolves ceilings via `cost_ceilings.resolve_ceilings`.
    - Queries `state_backend.query_actor_24h_spend` and `query_org_24h_spend`.
    - Returns `BudgetDecision(allowed=..., remaining_usd=...,
      rationale=..., ceiling_applied_usd=...)`.
    - When denied, the rationale names WHICH ceiling tripped (per-solve vs
      per-actor-24h vs per-org-24h).
  - `record_solve_cost(actor, request_id, actual_cost_usd, provenance)`:
    - Resolves `org_id` from the actor (Section 7.3's actor identity carries
      `org_id`; default impl reads from `actor.org_id` if present, else
      defaults to `actor.name`).
    - Calls `state_backend.record_solve_cost`.
- `CloudSeams.__init__` registers `LocalJobBudgetController` under the
  `"budget"` key by default.

**Verification:** integration tests: budget denial per ceiling type + happy
path budget allowance + record_solve_cost writes to state backend + 24h
window correctness across multiple recorded costs.

```
=== STEP 4 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.5 — Step 5: Router Stage 2 uses JobBudgetController

**What lands:**
- `phoenix/router/decision.py` Stage 2 cost filter:
  - Before the existing per-candidate `cost_ceiling_usd` filter, call
    `cloud_seams.get("budget").check_solve_budget(actor, max_estimated_cost,
    reproducibility_mode)`. The Router has access to the user's
    `cost_ceiling_usd` AND the seam-resolved ceiling — uses
    `min(user_ceiling, seam_ceiling)` as the effective threshold.
  - When the seam denies, raise `CostCeilingExceeded` with the seam's
    rationale embedded.
- Router signature: existing `decide()` is augmented with an `actor` kwarg
  (default `None` for backward compat — when `None`, the seam is NOT
  consulted, matching existing test fixtures' behavior). Production callers
  in `phoenix/api/routes.py` pass `actor=request_actor`.

**Verification:** existing Router tests stay green (actor=None path
unchanged). New tests: actor-tier denial path (default-tier actor over
$50/24h ceiling), per-solve denial path, per-org denial path. Rationale
present in raised exception's message.

```
=== STEP 5 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.6 — Step 6: Post-solve accounting (Orchestrate → ledger)

**What lands:**
- `phoenix/trinity/orchestrate/engine.py`: after the
  `RoutingDecision.execute()` returns a `ProviderRawResult` with cost info,
  call `cloud_seams.get("budget").record_solve_cost(actor, request_id,
  actual_cost_usd, provenance)`.
- `KPIBundle_orchestrate` gains an `actual_cost_usd: float` field (currently
  the price model lives in the routing decision; this surfaces the realised
  cost so the ledger entry can carry it for audit).
- Defensive: if `record_solve_cost` raises, log + swallow (post-solve
  accounting is informational; a ledger write failure must not corrupt the
  Result envelope).

**Verification:** integration test: end-to-end POST `/v1/tasks` → assertion
that `state_backend.query_actor_24h_spend(actor.name)` increased by the
solve's realised cost. Local-simulator solves have `actual_cost_usd=0.0` so
the accumulator stays at 0; tests use a stub provider that returns a non-
zero cost.

```
=== STEP 6 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.7 — Step 7: Verification gate pre-promotion cost check

**What lands:**
- `phoenix/verification/gate.py`: when considering promotion from R_n →
  R_n+1, estimate the next axis run's cost. If `current_cost + estimated_next
  > per_solve_ceiling`:
  - Skip promotion.
  - Classify as `DEGRADED_BUDGET_BOUND` (already exists).
  - Set new `budget_bound_skipped_axis: str` field on
    `VerificationProvenance` naming which axis was skipped (e.g.,
    `"cross_provider_axis"`).
- `VerificationProvenance` dataclass gains `budget_bound_skipped_axis: str |
  None = None`.

**Verification:** unit test: synthetic promotion scenario where ceiling
truncation triggers; verify classification + provenance field; verify Result
still ships (not raised).

```
=== STEP 7 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.8 — Step 8: `POST /v1/admin/budget/override` endpoint

**What lands:**
- New admin module `phoenix/admin/budget_override.py`:
  - `POST /v1/admin/budget/override` accepts `{actor_name: str,
    new_ceiling_usd: float, expires_at_unix: float, scope: str ("per_solve" |
    "per_actor_24h" | "per_org_24h")}`.
  - Validates: `new_ceiling_usd > 0` (overrides only grant more, never less
    per Section 4.7); `expires_at_unix > now`; scope is one of the three
    canonical scopes.
  - Writes a new `budget_overrides` row to the state backend (table created
    in Step 2's migration as a separate small table:
    `actor_name`, `scope`, `new_ceiling_usd`, `expires_at_unix`,
    `created_by`, `created_at_unix`).
  - Appends a top-priority audit event (`admin.budget.override`) and an
    Omega Ledger entry (new entry kind `budget_override` in
    `phoenix/ledger/entry_types.py`).
- `cost_ceilings.resolve_ceilings` consults the override table for the actor
  before falling back to defaults.
- Admin-only via the standard `require_admin` privilege check.

**Verification:** integration tests: admin override grants temporary bump;
ceiling reverts after `expires_at_unix`; non-admin gets 403;
`new_ceiling_usd <= 0` returns 400; ledger entry appended.

```
=== STEP 8 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.9 — Step 9: Local auth + audit seams + `test_cloud_seams.py`

**What lands:**
- `phoenix/_internal/cloud_seams.py` gains `LocalHttpAuthExtractor`
  (wraps existing `phoenix.identity.bootstrap.extract_or_bootstrap` against
  the standard `Authorization` header).
- `phoenix/_internal/cloud_seams.py` gains `LocalAuditLogExporter` (wraps
  the existing `phoenix.audit.get_emitter`; `flush()` calls
  emitter.close-and-reopen).
- Both registered by `CloudSeams.__init__`.
- `tests/integration/test_cloud_seams.py` — the v1 acceptance test:
  - **Test 1:** swap in a mock `HttpAuthExtractor` that returns a
    synthesized Actor; verify a request flows through the safety gate
    cleanly.
  - **Test 2:** swap in a mock `AuditLogExporter` that records to a list;
    verify Phoenix audit events arrive at BOTH the local default impl AND
    the mock (multi-sink pattern).
  - **Test 3:** swap in a mock `JobBudgetController` that always denies;
    verify the user gets `CostCeilingExceeded` with the mock's rationale,
    AND that no tenant-scoped state leaks from the mock into Phoenix's
    audit log.
  - **Test 4:** verify `register("canonical_library", ...)` succeeds and
    `get("canonical_library")` returns the registered impl (extension
    discipline check).

**Verification:** the four tests above + a regression test that base
Phoenix behaviour is unchanged when no overrides happen.

```
=== STEP 9 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.10 — Step 10: Acceptance, version bump, CHANGELOG

**What lands:**
- `pyproject.toml` + `phoenix/_internal/version.py` bump
  `1.0.0.dev10` → `1.0.0.dev11`.
- `CHANGELOG.md` Phase 10 entry at the top.
- Test-version assertions in `test_health.py` + `test_smoke.py` + Step 6
  CLI test updated.
- `_DEFAULT_PHOENIX_RELEASE` constants in SQLite + Postgres backends + drift
  detector default updated.
- Full pytest green.

```
=== STEP 10 COMPLETE — AWAITING ADAM REVIEW ===
```

---

## 4 — Open items (resolved 2026-05-13)

Per Adam's autonomous-execution direction this session, I'm locking the six
OPEN items below with recommended defaults. Each is reversible — if Adam
flags a different choice at any stop-gate, the relevant step rolls back.

### 4.1 — OPEN-1 LOCKED — Schema migration file = new `phase10_cost_ledger.py`

**Choice:** New migration file rather than extending `phase6b_initial.py`.
Phase 6b shipped on a specific dev cycle; rewriting that migration would
make replay against a Phase 6b-era state backend ambiguous. Phase 10 ships
its own additive migration. The migration runner already handles ordered
application.

### 4.2 — OPEN-2 LOCKED — `record_solve_cost` idempotency = last-write-wins

**Choice:** Re-recording the same `request_id` overwrites the row. Rationale:
the post-solve accounting path is the SINGLE writer (Orchestrate after
provider execution); duplicate writes would only happen if Orchestrate
retried after a transient failure, in which case the last write IS the
authoritative result. Stricter idempotency would require a UUID-on-write
discipline that adds complexity without protecting against any realistic
threat. Insertion uses `INSERT OR REPLACE` on SQLite and `ON CONFLICT
(request_id) DO UPDATE` on Postgres.

### 4.3 — OPEN-3 LOCKED — `budget_bound_skipped_axis` lives on VerificationProvenance

**Choice:** New field on VerificationProvenance (not RoutingProvenance).
Rationale: the decision to skip an axis is a verification-gate decision,
not a routing decision. RoutingProvenance already carries
`pricing_data_staleness_days`; mixing in a verification-gate-owned field
would muddy the layer boundary. The Result-envelope flattening at the API
surface still surfaces both fields side-by-side.

### 4.4 — OPEN-4 LOCKED — Admin override scope = three explicit scopes

**Choice:** `scope: "per_solve" | "per_actor_24h" | "per_org_24h"` rather
than a single "ceiling" field. Rationale: an admin override at the per-org
level is qualitatively different from a per-actor override (one operator
making the call vs one team having extra headroom); the audit log needs
to distinguish them. The three scopes mirror Section 4.7's three default
ceilings. Adding a fourth scope (e.g., per-install) is a v1.x ask.

### 4.5 — OPEN-5 LOCKED — `org_id` resolution = `actor.org_id` if present, else `actor.name`

**Choice:** When the Actor payload carries an `org_id` field (Phoenix Cloud
will populate it), use that. Otherwise default to the actor's own name,
which means single-actor installs naturally have a per-org ceiling that
equals the per-actor ceiling — no regression for solo developers, no
special-casing in Phoenix code. Mock impls in the cloud_seams test can
synthesize Actors with explicit `org_id` to exercise the per-org code path.

### 4.6 — OPEN-6 LOCKED — Ledger entry kind for budget override = `budget_override`

**Choice:** New `BudgetOverrideEntry` typed payload in
`phoenix/ledger/entry_types.py` with `entry_kind="budget_override"`. Fields:
`granted_actor_name`, `granted_by`, `scope`, `new_ceiling_usd`,
`expires_at_unix`, `rationale: str | None`. Phase 8's `OverrideByOperatorEntry`
is for HUMAN_REVIEW solve overrides — semantically different from budget
overrides; sharing a kind would obscure the audit story. Matches the locked-
scope discipline of Phase 7 (one kind per architectural decision).

---

## 5 — Verification (end-to-end)

After Step 10 completes:

1. Full `pytest --ignore=tests/tier1 -q`: expect ~660 passing.
2. Smoke gate green.
3. ruff / ruff-format / mypy strict clean.
4. End-to-end manual: submit a task that would exceed the default per-solve
   ceiling at `replay` mode → `CostCeilingExceeded` (402) with rationale
   naming "per_solve" scope.
5. Admin override → ceiling temporarily raised → submit succeeds → expires →
   ceiling reverts → next solve at the same cost is denied again.
6. `test_cloud_seams.py` four-test suite green (Adam's request: the
   compose-without-modifying-Phoenix guarantee from Section 10.3.1).

---

## 6 — What I am NOT proposing

- No PR creation after Phase 10 (per Adam's 2026-05-13 direction).
- No changes to `C:\frank-data\` (DF&E) or its benchmark shell.
- No reduction in existing test coverage; the budget-controller default-on
  behavior at runtime preserves all current test fixtures (Router's actor=None
  path explicitly skips the seam check).
- No `phoenix admin pricing-update` CLI in Phase 10 — soft-warn-on-stale-
  pricing already lives in Section 4.7's routing decision provenance. v1.x
  ships the refresh command.
