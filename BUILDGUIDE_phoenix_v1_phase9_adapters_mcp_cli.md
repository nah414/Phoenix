# BUILDGUIDE — Phoenix v1 Phase 9: LoRA adapters + CLI + MCP

**Status:** DRAFT — under active design with Adam.
**Authoritative location:** `C:\Phoenix\BUILDGUIDE_phoenix_v1_phase9_adapters_mcp_cli.md`
**Architectural reference:** `C:\Phoenix\PHOENIX_ARCHITECTURE_v1.md` (Section 2.7 LoRA hot-swap, Section 5.4 CLI, Section 5.5 MCP, Section 3.5 adapter sandbox).
**Phase scope:** Phase 9 only. Phase 10 (release artifacts — standalone binary, Docker, cloud-seams), Phase 11 (final acceptance) are separate build guides.
**Date opened:** 2026-05-12.
**Author of record:** Adam (with Claude as design partner).

---

## 0 — What this build guide is

Phase 9's job is to land **the three consumer-facing surfaces** that
make Phoenix usable from outside the REST endpoint:

- **LoRA hot-swap interface** (Section 2.7 + 3.5) — the
  `LoRAAdapter` Protocol, the adapter registry + loader, the
  inference-time validator, and the subprocess sandbox.
- **CLI** (Section 5.4) — the `phoenix` command-line tool, a thin
  REST wrapper covering the canonical task lifecycle, adapter
  management, identity, providers, audit, calibration, and admin.
- **MCP server** (Section 5.5) — stdio-transport MCP server with
  the 8 v1 task-lifecycle tools the architecture names. Lets
  agentic IDEs (Claude Code, Cursor, Cline) and the reference
  admin client (Section 9) drive Phoenix.

End-to-end at the end of Phase 9: a user can `pip install
phoenix-middleware`, run `phoenix task submit task.yaml`, get a
result, `phoenix task replay <id> --mode=strict` to verify bit-
exact reproducibility, and Claude Code can integrate Phoenix as
an MCP server with the same surface.

**Phase 9's definition of done:**

- `phoenix/adapters/protocol.py` ships the `LoRAAdapter` Protocol
  per Section 2.7 with `encode_to_grammar` / `decode_from_grammar`
  / `fingerprint` methods + capability declarations.
- `phoenix/adapters/sandbox.py` ships subprocess isolation per the
  locked OPEN-2 choice (medium isolation: subprocess + temp working
  dir + restricted env + timeout).
- `phoenix/adapters/loader.py` ships adapter discovery + load
  orchestration with the in-process registry.
- `phoenix/adapters/validator.py` ships inference-time round-trip
  validation per Section 2.7.
- `phoenix/adapters/identity_adapter.py` (per locked OPEN-1) ships
  the reference "identity" adapter — a weights-free echo adapter
  that exercises the load + validate + register chain end-to-end.
- REST endpoints land: `POST /v1/adapters`, `GET /v1/adapters`,
  `DELETE /v1/adapters/{adapter_id}`.
- Phase 8 Step 9's 501 stub at
  `POST /v1/admin/adapters/{id}/force-revalidate` is filled in
  with the real handler. `GET /v1/admin/adapters/{id}/round-trip-history`
  is registered for the first time.
- `POST /v1/identity/enroll` ships — the missing REST endpoint
  for Section 7.3's enrollment flow. Writes an `EnrollmentEntry`
  to the Omega Ledger.
- `phoenix/cli/entry.py` ships the `phoenix` command. Subcommand
  groups: `task`, `lora`, `identity`, `providers`, `audit`,
  `calibration`, `admin`. Output formats per Section 5.4:
  `--output=json|text|table` with TTY-detection default.
- `phoenix/cli/config_loader.py` reads `~/.phoenix/config.yaml`.
- `phoenix/mcp/server.py` + `tools.py` ship the FastMCP-based
  server with the 8 v1 task-lifecycle tools.
- `pyproject.toml` version bump `1.0.0.dev9` → `1.0.0.dev10`.
- `CHANGELOG.md` Phase 9 entry in the established shape.
- Pre-commit gates green; full pytest green with Phase 7 + 8
  infra (Postgres + NATS) running.

**This guide does NOT cover:**

- **The reference admin client** (Section 9 architecture) — that's
  a separate `phoenix-reference-client` package living in its own
  repo per Section 9.6. Phoenix v1 Phase 9 ships the MCP *server*
  surface; the reference client is downstream.
- **Standalone Nuitka binary** (Section 5.4 mentions) — that's a
  Phase 10 release-artifact deliverable.
- **Sanskrit memory tools** in MCP (the 7 vendored tools mentioned
  in `phoenix/mcp/README.md`) — per locked OPEN-6, deferred to v1.x.
  Phase 9 ships only the 8 canonical task-lifecycle tools.
- **HTTP+SSE MCP transport** — per locked OPEN-3, Phase 9 ships
  stdio only. HTTP+SSE for browser-based MCP clients is v1.x.
- **Real LoRA weights** — Phase 9 ships the Protocol + sandbox +
  the identity-adapter reference; users bring their own LoRA
  weights per Section 1 Decision 8 ("v1 capability, not v1
  content").
- **Phase 11 §10.7 acceptance + `1.0.0` release** — separate.

## 1 — Prerequisites

Before starting Phase 9:

1. **Phase 8 acceptance.** PR #8 merged to `origin/main` (current
   tip: `2afbee9`). All 512 tests pass locally with NATS + Postgres
   enabled. `python -c "import phoenix; print(phoenix.__version__)"`
   reports `1.0.0.dev9`.
2. **Architecture sections read fresh.** Section 2.7 (LoRA
   Protocol), Section 3.5 (adapter sandbox), Section 5.4 (CLI
   surface), Section 5.5 (MCP surface), Section 7.3 (identity +
   enrollment).
3. **Phase 1-8 substrate available.** Phase 9 is wiring + thin
   handlers + a new sandbox layer over Phase 1-8 substrate.
4. **Working tree clean.** `git status` clean on
   `phase-9-adapters-mcp-cli` branch.
5. **No OneDrive paths.** Adam's standing rule.

## 2 — Phase-gate review protocol

Phase 9 has **ten steps** matching the Phase 6a/6b/7/8 rhythm. Each
step ends with:

```
=== STEP N COMPLETE — AWAITING ADAM REVIEW ===
```

No advancement past a stop gate without explicit Adam approval.
The `[OPEN: ...]` escalation rule applies for any mid-step
architectural ambiguity not resolved in this BUILDGUIDE.

**Pre-commit gates at every step boundary:**

- `ruff check .` — clean
- `ruff format --check .` — clean
- `mypy --strict phoenix/` — clean
- `pytest tests/unit/test_smoke.py -q` — green

**Full test gate at Step 10:** `pytest tests/ -q` — green with full
infra running (Postgres + NATS).

## 3 — Phase 9 deliverables

### 3.1 — Step 1: LoRA Protocol + sandbox + identity adapter

**What lands:**
- `phoenix/adapters/protocol.py` — `LoRAAdapter` Protocol per
  Section 2.7. Fields: `name`, `version`, `base_model_fingerprint`,
  `capabilities`. Methods: `encode_to_grammar(natural_language)`,
  `decode_from_grammar(tokens)`, `fingerprint()`.
- `phoenix/adapters/sandbox.py` — subprocess sandbox per locked
  OPEN-2 (medium isolation):
  * Launches the adapter handler in a subprocess via
    `subprocess.run` with `timeout=`.
  * Restricted env: only `PATH`, `TMPDIR`/`TEMP`, no
    `PHOENIX_*` env vars leaked through.
  * Working directory set to a per-call tempdir
    (`tempfile.mkdtemp()`), cleaned up on exit.
  * `AdapterTimeoutError` (504) on timeout.
- `phoenix/adapters/errors.py` — `AdapterError` base +
  `AdapterValidationError`, `AdapterTimeoutError`,
  `AdapterVersionMismatch`, `AdapterNotLoaded`.
- `phoenix/adapters/identity_adapter.py` — per locked OPEN-1: the
  reference identity adapter. Trivial implementation: encode/decode
  are pass-through (`return input`); fingerprint is constant. Lets
  tests exercise load + validate + register without LoRA weights.

**Verification:** unit tests for the Protocol's structural shape +
sandbox timeout enforcement + identity adapter round-trip.

```
=== STEP 1 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.2 — Step 2: Adapter loader + validator + in-process registry

**What lands:**
- `phoenix/adapters/registry.py` — module-level `AdapterRegistry`
  singleton. Methods: `register(adapter)`, `unregister(adapter_id)`,
  `get(adapter_id)`, `list_adapters()`. Thread-safe via internal
  lock.
- `phoenix/adapters/loader.py` — `load_adapter(path_or_spec)` 
  orchestration. Discovers the adapter (file path → import),
  runs validation, registers on success.
- `phoenix/adapters/validator.py` — inference-time round-trip
  validation. Takes a small fixed set of canonical grammar
  statements (3-5 round-trips), invokes adapter via the sandbox,
  verifies decode(encode(x)) == x for each. Raises
  `AdapterValidationError` (503) on failure.

**Verification:** unit tests: identity adapter passes validation;
a deliberately-broken stub adapter fails validation with
`AdapterValidationError`.

```
=== STEP 2 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.3 — Step 3: REST adapter management endpoints

**What lands:**
- `phoenix/api/routes.py` adds:
  * `POST /v1/adapters` — body `{spec: str}` (path or module
    spec). Loads + validates + registers. Returns adapter metadata.
    Cost: `adapters_post` (10 tokens, already in catalogue).
  * `GET /v1/adapters` — lists registered adapters. Cost:
    `tasks_get`.
  * `DELETE /v1/adapters/{adapter_id}` — unregisters. Requires
    `can_unload_adapter`. Cost: `adapters_post`.
- All three go through the safety gate with appropriate
  `requires_capability` checks (`can_load_adapter` for POST,
  `can_unload_adapter` for DELETE).

**Verification:** integration tests: POST identity adapter →
GET shows it → DELETE removes it → GET shows empty list. 403
when actor lacks `can_load_adapter`.

```
=== STEP 3 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.4 — Step 4: Phase 8 force-revalidate filler + round-trip-history

**What lands:**
- `phoenix/admin/adapters_admin.py`: replace the 501 stub with
  the real handler. Reads the loaded adapter from the registry,
  re-runs the inference-time validation suite, returns the result
  with `validation_passed: bool` + per-test breakdown. Raises
  `AdapterNotLoaded` (404) when the adapter isn't loaded.
- Add `GET /v1/admin/adapters/{id}/round-trip-history` — returns
  per-adapter validation history. Reads from a new in-memory
  ring buffer in `phoenix/adapters/registry.py` that the
  validator appends to on every run.

**Verification:** integration tests: load identity adapter → force-
revalidate succeeds → round-trip-history shows the entry. 404 for
unloaded adapter.

```
=== STEP 4 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.5 — Step 5: POST /v1/identity/enroll endpoint

**What lands:**
- `phoenix/api/routes.py` adds `POST /v1/identity/enroll` --
  body: `{actor_name, permissions: dict}`. Admin-only
  (`is_admin` required since enrollment is a high-trust
  operation). Sets the new actor's permissions via the
  `PermissionsRegistry`, appends an `EnrollmentEntry` ledger
  entry (Phase 7 Step 4 type), emits audit.
- Idempotent on `actor_name`: re-enrolling overwrites permissions
  but still records a new ledger entry (operator history matters).

**Verification:** integration test: enroll "bob" with
`can_submit_tasks=True`, verify the permission lookup works, verify
the ledger has the `EnrollmentEntry` row.

```
=== STEP 5 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.6 — Step 6: CLI scaffold + config + output formats

**What lands:**
- `phoenix/cli/entry.py` — `main()` entry point. Argparse-based
  command dispatcher. Subcommand groups created but most are stubs;
  Step 7-8 fill them.
- `phoenix/cli/config_loader.py` — loads `~/.phoenix/config.yaml`
  with env-var override. Honors `$PHOENIX_REST_URL`,
  `$PHOENIX_REPRODUCIBILITY_MODE`. Returns a typed `CLIConfig`
  dataclass.
- `phoenix/cli/output_formats.py` — `render(payload, format)`
  with `json | text | table` output modes. TTY-detection default
  (`json` when piped, `text` when interactive).
- `phoenix/cli/http_client.py` — thin wrapper around `httpx`
  (locked OPEN-5) that handles actor signing + base URL
  resolution.
- `pyproject.toml` adds `httpx>=0.27,<0.29` to main deps (was
  dev-only).

**Verification:** unit tests: config loader parses fixture YAML;
output formats produce correct shapes; http_client signs requests.

```
=== STEP 6 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.7 — Step 7: CLI command groups — task / lora / identity / providers

**What lands:**
- `phoenix/cli/commands/task.py`:
  * `phoenix task submit <task-spec>` → POST /v1/tasks
  * `phoenix task get <task-id>` → GET /v1/tasks/{id}/provenance
    (will need `provenance` REST endpoint — for v1 we'll just
    show the response from the original POST since GET /v1/tasks/{id}
    isn't implemented; CLI surfaces the cached value via local
    state).
  * `phoenix task replay <task-id> --mode=strict` → POST
    /v1/tasks/{id}/replay
  * `phoenix task stream <task-id>` → WebSocket subscribe
- `phoenix/cli/commands/lora.py`:
  * `phoenix lora load <adapter-path>` → POST /v1/adapters
  * `phoenix lora list` → GET /v1/adapters
  * `phoenix lora unload <adapter-id>` → DELETE /v1/adapters/{id}
- `phoenix/cli/commands/identity.py`:
  * `phoenix identity show` → reads keystore + permissions
  * `phoenix identity enroll <actor> --permission=...` → POST
    /v1/identity/enroll (admin-only; the CLI surfaces the
    permissions denial cleanly)
- `phoenix/cli/commands/providers.py`:
  * `phoenix providers list` → GET providers from the admin
    surface (or read from the local registry if we add a
    public endpoint).

**Verification:** integration tests via CliRunner; each command
returns expected output shape.

```
=== STEP 7 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.8 — Step 8: CLI command groups — audit / calibration / admin

**What lands:**
- `phoenix/cli/commands/audit.py`:
  * `phoenix audit tail` → GET /v1/audit/events (paginated streaming)
  * `phoenix audit verify` → GET /v1/audit/ledger/verify
- `phoenix/cli/commands/calibration.py`:
  * `phoenix calibration status` → GET /v1/calibration/status
    (READ-ONLY for any actor; alternative: surface via
    /v1/admin/calibration/detail with admin auth)
  * `phoenix calibration run` → POST /v1/admin/calibration/run
    (admin only)
- `phoenix/cli/commands/admin.py`:
  * `phoenix admin kill-switch engage|release|status` → 3 admin
    endpoints
  * `phoenix admin health` → GET /v1/admin/health/detailed
  * `phoenix admin governor` → GET /v1/admin/governor
  * `phoenix admin budget` → GET /v1/admin/budget
  * `phoenix admin override <task-id> --disposition=...` → admin
    override endpoint

**Verification:** integration tests for each subcommand.

```
=== STEP 8 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.9 — Step 9: MCP server + 8 v1 tools

**What lands:**
- `phoenix/mcp/server.py` — FastMCP server (using the official
  `mcp` SDK per locked OPEN-4). stdio transport per locked OPEN-3.
- `phoenix/mcp/tools.py` — 8 tool registrations per Section 5.5:
  * `phoenix_task_submit`
  * `phoenix_task_get`
  * `phoenix_task_replay`
  * `phoenix_provenance_get`
  * `phoenix_providers_list`
  * `phoenix_calibration_status`
  * `phoenix_health`
  * `phoenix_audit_verify`
- Each tool is a thin wrapper that calls the REST API via
  `httpx`. Actor signing on every tool call (`actor_payload` HMAC
  per Section 5.6).
- `pyproject.toml` adds `mcp>=1.0,<2.0` as a new `[mcp]` optional
  extra (matches the `[otel]`/`[nats]`/`[postgres]` pattern).
- `phoenix.cli.commands.mcp_server` — `phoenix mcp serve` boots
  the MCP server on stdio for IDE integration.

**Verification:** integration tests: spawn the MCP server in a
subprocess; send canonical tool requests; verify responses.

```
=== STEP 9 COMPLETE — AWAITING ADAM REVIEW ===
```

### 3.10 — Step 10: Acceptance, version bump, CHANGELOG

**What lands:**
- `pyproject.toml` and `phoenix/_internal/version.py` bump
  `1.0.0.dev9` → `1.0.0.dev10`.
- `CHANGELOG.md` Phase 9 entry at the top.
- Test-version assertions in `test_health.py` + `test_smoke.py`
  updated.
- `_DEFAULT_PHOENIX_RELEASE` constants updated.
- Branch pushed; PR #9 opened against `main`.
- Full pytest green (expect ~570-600 tests).

```
=== STEP 10 COMPLETE — AWAITING ADAM REVIEW ===
```

---

## Open items to lock before Step 1

Six open items surfaced during BUILDGUIDE authoring. Lock with Adam
before any code lands.

1. **`[OPEN-1]` Reference adapter shipping path.**
   - **a)** Ship `phoenix/adapters/identity_adapter.py` as a
     built-in identity (echo) adapter. Real-world value (clients
     can copy as a starting point) + test value (exercises load +
     validate + register without LoRA weights).
   - **b)** Keep test-only in `tests/_fixtures/`.
   **Recommendation:** **(a)**. Documentation that compiles is
   high value.

2. **`[OPEN-2]` Sandbox isolation level.**
   - **a)** Subprocess + timeout only.
   - **b)** Subprocess + timeout + restricted env + per-call
     tempdir working directory.
   - **c)** Full OS-level ACLs / firewall rules.
   **Recommendation:** **(b)**. (a) is too weak; (c) is platform-
   specific complexity Phoenix v1 doesn't need. v1.x can layer
   stricter isolation if a real threat model emerges.

3. **`[OPEN-3]` MCP transport.**
   - **a)** stdio only in Phase 9; HTTP+SSE deferred to v1.x.
   - **b)** Both stdio + HTTP+SSE in Phase 9.
   **Recommendation:** **(a)**. stdio covers Claude Code + Cursor
   + Cline (the 80% case). HTTP+SSE adds transport complexity
   v1 doesn't need.

4. **`[OPEN-4]` MCP SDK choice.**
   - **a)** Official Anthropic `mcp` Python SDK
     (https://github.com/modelcontextprotocol/python-sdk).
   - **b)** Hand-roll the JSON-RPC over stdio.
   **Recommendation:** **(a)**. The official SDK is stable,
   well-supported, and matches the broader MCP ecosystem.

5. **`[OPEN-5]` CLI HTTP client.**
   - **a)** `requests` (sync).
   - **b)** `httpx` (already a dev dependency).
   - **c)** stdlib `urllib`.
   **Recommendation:** **(b)**. `httpx` is already pulled in for
   dev; promote to a main dep. Supports both sync (CLI) and async
   (future v1.x async CLI) idioms.

6. **`[OPEN-6]` Sanskrit memory MCP tools.**
   - **a)** Vendor the 7 tools from
     `C:\frank-data\frankenstein\mcp_server\server.py` in Phase 9.
   - **b)** Defer to v1.x.
   **Recommendation:** **(b)**. Phase 9 ships the 8 canonical
   task-lifecycle tools per Section 5.5. The Sanskrit memory
   tools are vendored substrate for the reference admin client
   (Section 9), not v1 core.

---

## Where you (Adam) shape the design

These are the calls I want input on rather than inventing answers for:

1. **Six open items above** — recommendations attached; please
   confirm or override before Step 1 lands.
2. **OPEN-2 (sandbox isolation)** is the security-posture call.
   Worth a deliberate yes/no on whether v1 ships subprocess+tempdir
   isolation or invests in tighter OS-level ACLs.
3. **OPEN-3 (MCP transport)** affects what kinds of integrators
   can use Phoenix's MCP server out of the box. Worth a deliberate
   decision about who Phoenix's MCP audience is in v1.

These belong in the BUILDGUIDE's "open items" section so you decide
once, on paper, before code lands.

---

## What I am NOT proposing

- No changes to `C:\frank-data\` (DF&E) or its benchmark shell.
- No force-pushes, no destructive history rewrites.
- No new substrate layers in Phase 9 — adapters/cli/mcp are
  consumers of the substrate Phase 1-8 built.
- No standalone binary or Docker image — those are Phase 10.
- No reference admin client (Section 9 architecture) — that's a
  separate package per Section 9.6.
- No real LoRA weights — Phoenix v1 ships the interface; users
  bring their own.
