# BUILDGUIDE — Phoenix v1.1 Phase 13: Cognition substrate + MCP-client mode

> **▲ NOTE FOR CLAUDE CODE — READ BEFORE STARTING**
>
> This build guide reflects design decisions locked with Adam on
> 2026-05-18. **Section 0 below imports those decisions verbatim from
> `DESIGN_DECISIONS_PHASE13_LOCKED_2026-05-18.md`.** Read Section 0
> in full before implementing any step.
>
> If a step in this guide appears to contradict Section 0, **stop and
> surface to Adam** — do not silently reconcile. Mark `[OPEN: ...]`
> and ask.
>
> Cross-session Claude context lives in `E:\CLAUDE_NOTES.md` — read
> the recent entries before starting (no build guides there per
> Adam's rule; the notes are open expression / hand-off context only).
>
> Standing rules from Phase 9–12 (phase gates, no OneDrive paths, live
> reads beat memory, PERF/SAFETY callouts, per-section README updates)
> carry forward unchanged. They are restated in Section 6 of this guide.

**Status:** DRAFT — design locked 2026-05-18; awaiting 1.0.0 final ship before implementation begins.
**Authoritative location:** `C:\Phoenix\BUILDGUIDE_phoenix_v1_phase13_cognition_mcp_client.md`
**Architectural reference:** `PHOENIX_ARCHITECTURE_v1.md` (v1.1, locked 2026-05-07):
- Section 1 Decision 24 (v1.1 cognition substrate scope)
- Section 4.2 (provider adapter pattern)
- Section 5.5 (MCP transport)
- Section 6.3 (`WobbleAxis` Protocol)
- Section 10.3.1 (`CloudSeams` registry pattern)
- Section 7.4 (safety gate 9-stage pipeline)
- Section 9 (reference admin client as canonical cognition consumer)

**Phase scope:** Phase 13 only. Lands as v1.1 work after `1.0.0` final release. Runs as a parallel track to the perception harness's Phase 12 build-guide drafting.
**Date opened:** 2026-05-18.
**Author of record:** Adam (with Claude as design partner).
**Companion docs:**
- `DESIGN_DECISIONS_PHASE13_LOCKED_2026-05-18.md` — five locked decisions; Section 0 below imports them.
- `PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md` — parallel v1.x extension; no overlap with Phase 13.

---

## 0 — Design Decisions Locked 2026-05-18

This section restates the five decisions locked during the Adam ↔
Claude design session on 2026-05-18. The canonical record is
`DESIGN_DECISIONS_PHASE13_LOCKED_2026-05-18.md`; this section MUST
match. If you find drift between this section and the decisions
doc, the decisions doc wins — surface to Adam.

- **13-D1 — License: CONDITIONALLY LOCKED on Apache 2.0.** Revisit only if
  Adam files a patent on Phoenix methodology before launch. Dependency check:
  SynQc TDS Core verified MIT (Apache-2.0 compatible); **frank-data root
  LICENSE pending Adam declaration — blocker before Phase 13 implementation
  start.**
- **13-D2 — Privacy posture: hash-only default + opt-in verbatim.**
  Omega Ledger gains `prompt_disposition` column: `HASH_ONLY` (default),
  `VERBATIM` (opt-in), `ENCRYPTED_OPT_IN` (column shipped, key-mgmt
  ceremony deferred to first commercial customer).
- **13-D3 — Cognition disagreement classifier is independent from
  physics.** Distinct taxonomy, distinct training data, distinct
  evaluator. Step 5 of this guide owns it. Calibration set ≥ 200
  paired examples; `UNCLASSIFIED` escape hatch mandatory.
- **13-D4 — MCP allowlist: per-server explicit registration.** No
  TOFU, no empty-default-allows-all, no discovery-based auto-add.
  Admin endpoint shape in Step 6.
- **13-D5 — Sequencing: 1.0.0 final first, then Phase 13 parallel
  with perception harness Phase 12.**

Full rationale for each: see `DESIGN_DECISIONS_PHASE13_LOCKED_2026-05-18.md`.

---

## 1 — What this build guide is

Phase 13's job is to land the **two substrates that close the
universal-model-integration gap** in Phoenix's provider surface, plus
the verification, ledger, and safety scaffolding those substrates
require.

The two substrates:

- **Cognition substrate** — native adapters for the top cloud LLM
  providers (Anthropic Claude, OpenAI GPT, Google Gemini), wired
  through the existing `ProviderRegistry` and `Router` patterns.
  Adds an optional `[litellm]` pip extra for long-tail coverage
  (Ollama, vLLM, AWS Bedrock, Azure, Mistral, Cohere, xAI,
  Perplexity, and ~130 other providers via LiteLLM's unified API).
- **MCP-client mode** — `phoenix/mcp/client.py` lets Phoenix call
  out to any registered MCP server (stdio or HTTP+SSE), enabling
  customer-sandboxed models, local LLM servers, and the broader MCP
  ecosystem to plug into Phoenix without writing a Phoenix-specific
  adapter. **Per-server explicit registration only** (13-D4).

Beyond the substrates, Phase 13 ships:

- Three new `WobbleAxis` implementations for cognition: cross-model
  agreement, self-consistency, prompt-perturbation.
- An independent cognition disagreement classifier (Section 13-D3).
- Pricing v2 schema covering per-token LLM costs, prompt-cache
  discounts, and batch-API discounts. Wires through the existing
  Phase 10 cost-ceiling engine.
- Streaming-token events on the `/v1/ws/tasks/{task_id}/stream`
  surface, extending the Phase 6a WebSocket protocol.
- Privacy controls (13-D2): hash-only default ledger entries,
  opt-in verbatim path, encrypted-at-rest column for the future
  enterprise path.
- Permission-registry extensions: new capabilities for cognition
  dispatch, MCP-client dispatch, and prompt-verbatim storage.
- New admin endpoints for MCP server registration and per-server
  budget management.

**Phase 13's definition of done:**

- `phoenix/providers/cognition/` ships the `CognitionProvider`
  Protocol and three concrete adapters (Anthropic, OpenAI, Google).
- `phoenix/providers/cognition/litellm_passthrough.py` ships behind
  the optional `[litellm]` extra.
- `phoenix/pricing/v2/` ships the LLM-aware pricing schema with
  per-1M-token input/output rates, prompt-cache discount factor,
  batch-API discount factor, vision-token multipliers.
- `phoenix/verification/axes/` ships `CrossModelAxis`,
  `SelfConsistencyAxis`, `PromptPerturbationAxis` as
  `WobbleAxis` Protocol impls.
- `vendor/cognition_wobble/` ships the independent disagreement
  classifier (Step 5; substantial sub-deliverable).
- `phoenix/mcp/client.py` ships the MCP-client mode with explicit
  per-server registration.
- Omega Ledger schema migration to v4 (adds `prompt_disposition`,
  `prompt_hash`, `prompt_verbatim`, `prompt_encrypted`,
  `cognition_provenance_json`).
- New REST endpoints: `POST/GET/DELETE /v1/admin/mcp-servers/{name}`,
  `POST /v1/admin/budget/cognition-override`,
  `POST /v1/identity/permissions/grant-prompt-verbatim`.
- WebSocket event schema extended with `token.delta`,
  `cognition.tool_call`, `cognition.tool_result` event types.
- `pyproject.toml` version bump `1.0.0` → `1.1.0.dev0`.
- `CHANGELOG.md` Phase 13 entry in the established shape.
- Pre-commit gates green; full pytest green with cognition mocks,
  MCP-client mocks, classifier eval set, Postgres + NATS infra
  running.

**This guide does NOT cover:**

- **Customer-key-management ceremony for `ENCRYPTED_OPT_IN`** — the
  column ships, the key-management protocol (Section 7.6-style
  enrollment with HKDF subkeys) lands when the first commercial
  customer requires it. Per locked OPEN-13.
- **Federated MCP server discovery** (e.g., via Anthropic's MCP
  registry) — per locked 13-D4, only per-server explicit
  registration. Discovery-based auto-add is deferred unless and
  until 13-D4's re-lock trigger fires.
- **Conversation state management** — Phoenix is per-task; multi-
  turn conversation state stays the caller's responsibility.
- **Custom guardrails / content safety** — Phoenix routes through
  cognition providers' native safety; dedicated content-safety
  layers (Lakera, Llama Guard) are out of scope. Integration via
  MCP-client mode is the future path.
- **Frontier-model fine-tuning APIs** (OpenAI fine-tuning,
  Anthropic API for Anthropic-side trained adapters) — Phase 13
  ships inference adapters only; training-time API surface deferred
  to v1.2+.
- **A2A protocol support** — Phase 13 does not implement
  agent-to-agent delegation. A2A integration is a v1.2 candidate
  per separate roadmap discussion.
- **The reference admin client (Section 9 architecture)** — that
  ships in its own `phoenix-reference-client` repo per Section 9.6.
  Phase 13 makes it *possible*; the reference client is downstream.

## 2 — Prerequisites

Before starting Phase 13:

1. **`1.0.0` final shipped.** Code signing in place; macOS standalone
   binary built; NATS bundling resolved; README status-table backfill
   complete; CHANGELOG `1.0.0` entry merged to `origin/main`. Per
   13-D5 sequencing.
2. **Architecture sections read fresh.** Section 1 Decision 24 (v1.1
   cognition scope), Section 4.2 (provider pattern), Section 5.5
   (MCP transport), Section 6.3 (`WobbleAxis` Protocol), Section
   10.3.1 (`CloudSeams` registry), Section 7.4 (safety gate),
   Section 9 (reference admin client).
3. **Design decisions doc read fresh.**
   `DESIGN_DECISIONS_PHASE13_LOCKED_2026-05-18.md` — all five
   decisions, the re-lock-trigger table, and the dependency-check
   note on frank-data + SynQc TDS Core licenses.
4. **Dependency-check resolved.**
   - SynQc TDS Core: ✅ MIT, Apache-2.0 compatible (verified 2026-05-18).
   - frank-data: **MUST have a root `LICENSE` file declaring Apache 2.0 (or
     compatible) before Step 1 begins.** Without it, Phoenix cannot validly
     redistribute frank-data substrate under Apache 2.0. No silent
     advancement past this prereq.
5. **Phase 1-12 substrate available.** Phase 13 is wiring + new
   substrates over Phase 1-12 substrate; no Phase 1-12 code is
   rewritten.
6. **Working tree clean** on `phase-13-cognition-mcp-client` branch.
7. **No OneDrive paths.** Adam's standing rule.
8. **Live reads beat memory.** Vendored API names are source-of-
   truth. Read `vendor/wobble/`, `vendor/wobble/detector.py`,
   `phoenix/verification/wobble_axis.py`, `phoenix/providers/`
   fresh before Step 1.

## 3 — Phase-gate review protocol

Phase 13 has **ten steps** matching the Phase 9–12 rhythm. Each step
ends with:

```
=== STEP N COMPLETE — AWAITING ADAM REVIEW ===
```

No advancement past a stop gate without explicit Adam approval. The
`[OPEN: ...]` escalation rule applies for any mid-step architectural
ambiguity not resolved in this guide or the design-decisions doc.

**Pre-commit gates at every step boundary:**

- `ruff check .` — clean
- `ruff format --check .` — clean
- `mypy --strict phoenix/` — clean
- `pytest tests/unit/test_smoke.py -q` — green

**Full test gate at Step 10:** `pytest tests/ -q` — green with full
infra running (Postgres + NATS + cognition-mock + MCP-mock servers).

**Step 5 (cognition classifier) special gate:** Step 5 additionally
requires the classifier's calibration-eval report green per the
acceptance criteria stated inline in Step 5. If Step 5 splits into
5a + 5b mid-execution, each sub-step ends with its own stop gate.

---

## 4 — Phase 13 deliverables

### 4.1 — Step 1: CognitionProvider Protocol + pricing v2 schema

**What lands:**

- `phoenix/providers/cognition/__init__.py` — package marker.
- `phoenix/providers/cognition/protocol.py` — `CognitionProvider`
  Protocol parallel to `BaseProviderClient`. Methods:
  - `complete(prompt: Prompt, *, max_tokens: int, temperature: float,
    tools: list[Tool] | None, stream: bool) -> CognitionResult`
  - `capabilities() -> CognitionCapabilities`
  - `fingerprint() -> str` (for ledger provenance; includes provider
    name + model version + canonicalized request shape)
- `phoenix/providers/cognition/capabilities.py` — `CognitionCapabilities`
  dataclass: `streaming`, `tool_use`, `vision`, `max_context_tokens`,
  `supports_prompt_cache`, `supports_batch`.
- `phoenix/providers/cognition/types.py` — `Prompt`, `Tool`,
  `CognitionResult`, `TokenUsage`, `ToolCall`, `ToolResult` dataclasses.
  `CognitionResult` carries `text`, `tool_calls`, `usage`, `latency_ms`,
  `provider_fingerprint`, `prompt_cache_hit`.
- `phoenix/pricing/v2/schema.py` — pricing v2 record shape:
  `provider`, `model`, `usd_per_1m_input_tokens`,
  `usd_per_1m_output_tokens`, `prompt_cache_discount_factor`,
  `batch_discount_factor`, `vision_token_multiplier`.
- `phoenix/pricing/v2/cognition_pricing.json` — initial pricing
  table for Anthropic + OpenAI + Google (current published rates).
- `phoenix/pricing/v2/loader.py` — `load_cognition_pricing()`
  returns a dict keyed by `(provider, model)`.
- `pricing_update.py` (existing) extended to refresh v2 alongside v1.

**Verification:** unit tests for Protocol structural shape; pricing
loader returns expected rates; `CognitionCapabilities` round-trips
through `dataclasses.asdict` cleanly.

**`[OPEN: P13-1]`** — should `CognitionResult` carry the raw provider
response body (for debugging) or only the canonicalized Phoenix view?
Tension: raw body is invaluable for diagnosing provider regressions;
storing it duplicates payload and may include vendor-specific data
that complicates 13-D2 hash-only semantics. Default: store
canonicalized only; raw body available behind a `task.options.
preserve_raw_provider_body=True` flag with `can_store_raw_provider_body`
permission. Surface to Adam.

```
=== STEP 1 COMPLETE — AWAITING ADAM REVIEW ===
```

### 4.2 — Step 2: Three concrete cognition adapters (Anthropic, OpenAI, Google)

**What lands:**

- `phoenix/providers/cognition/anthropic.py` — `AnthropicProvider`
  implementing `CognitionProvider`. Wraps the `anthropic` Python SDK
  (pinned ≥0.40, <0.60). Maps the messages API to Phoenix's
  `Prompt` + `Tool` types; surfaces prompt-cache hits via
  `capabilities().supports_prompt_cache=True` and the
  `CognitionResult.prompt_cache_hit` field; canonical models:
  `claude-sonnet-4-7-20260418`, `claude-opus-4-7-20260315`,
  `claude-haiku-4-5-20251001` (pulled fresh from Anthropic's models
  endpoint at provider init; static fallback list shipped).
- `phoenix/providers/cognition/openai.py` — `OpenAIProvider`. Wraps
  the `openai` Python SDK (pinned ≥1.50, <2.0). Maps to chat-
  completions API; handles tool-use schema differences from
  Anthropic's; handles structured-outputs (`response_format`); batch
  API exposed via `capabilities().supports_batch=True`.
- `phoenix/providers/cognition/google.py` — `GoogleGeminiProvider`.
  Wraps `google-generativeai` SDK. Note long-context support
  (`max_context_tokens` up to 2M for Gemini 1.5 Pro / 3M for
  Gemini 2.x as of 2026); the capability advertise must be live-
  read from the model metadata, not hardcoded.
- `phoenix/providers/cognition/errors.py` — `CognitionError` base +
  `CognitionAuthError` (401), `CognitionRateLimitError` (429, with
  `Retry-After`), `CognitionContextLengthError` (413),
  `CognitionContentPolicyError` (refusal),
  `CognitionTimeoutError` (504), `CognitionUnavailable` (503,
  failover-eligible).
- Retry-backoff strategy shared across all three: exponential
  backoff with jitter on 429s and 5xx; immediate raise on 4xx
  non-429; max 3 retries before failover.
- `phoenix/providers/registry.py` extended to register cognition
  providers alongside quantum/classical. The `Router.decide`
  algorithm gains a `task.kind == "cognition"` branch.

**Verification:**
- Unit tests with mocked HTTP responses for each adapter.
- Integration tests using each provider's official test/mock surface
  if available; live tests gated by `PHOENIX_LIVE_COGNITION=1`
  environment flag, off by default.
- Error mapping coverage: each typed exception has a unit test.

**SAFETY:** API keys are read from environment variables only
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`) — never
from config files, never logged, never serialized into the ledger
or audit. Provider authentication failures yield typed
`CognitionAuthError` with no key material in the message.

**`[OPEN: P13-2]`** — should the three adapters share a base class
or stay independent? Tension: base-class deduplicates retry logic
and error mapping; independence preserves the freedom to absorb
provider-specific quirks (Anthropic's prompt caching, OpenAI's
structured-outputs nesting, Gemini's safety settings). Default:
shared `_CognitionAdapterBase` abstract class for retry + error
mapping; provider-specific code lives in subclasses. Surface to Adam.

```
=== STEP 2 COMPLETE — AWAITING ADAM REVIEW ===
```

### 4.3 — Step 3: LiteLLM passthrough provider (optional [litellm] extra)

**What lands:**

- `pyproject.toml` adds optional extra:
  ```toml
  [project.optional-dependencies]
  litellm = ["litellm >= 1.50, < 2.0"]
  ```
- `phoenix/providers/cognition/litellm_passthrough.py` —
  `LiteLLMPassthroughProvider` implementing `CognitionProvider`.
  Construction takes a `litellm_model` string (e.g.,
  `"ollama/llama3:8b"`, `"bedrock/anthropic.claude-3-sonnet"`,
  `"openai/gpt-5"`). Routes through `litellm.completion()`.
  Capabilities advertised via `litellm.get_model_info(...)`.
- Import gating: module imports `litellm` lazily inside provider
  methods; module-level import succeeds without `litellm`
  installed, but constructing a `LiteLLMPassthroughProvider`
  without the extra raises `MissingOptionalDependency` (501)
  with the install instruction in the message.
- `phoenix/providers/cognition/litellm_passthrough.py` ships
  pricing-table delegation: pricing comes from `litellm`'s
  built-in cost catalogue when available, falls back to a
  Phoenix v2 entry if the user registers one, otherwise raises
  `PricingUnavailable` (the router skips the candidate).
- Per-provider passthrough budget: each LiteLLM model registered
  in the v2 pricing table gets its own row; unregistered
  passthrough models fail the Stage 2 ceiling check by default.

**Verification:**
- Unit test that import succeeds without `litellm`.
- Unit test that construction raises `MissingOptionalDependency`
  without the extra.
- Mocked `litellm.completion` for the integration path.
- License check note in test: LiteLLM is MIT, Apache-2.0
  compatible.

**Honesty note:** LiteLLM is a large dependency (~50 MB installed).
The `[litellm]` extra is opt-in deliberately. Users who only need
Anthropic + OpenAI + Google should not be forced to pull it in.

```
=== STEP 3 COMPLETE — AWAITING ADAM REVIEW ===
```

### 4.4 — Step 4: Three new WobbleAxis implementations (raw distances only)

**What lands:**

- `phoenix/verification/axes/cross_model.py` — `CrossModelAxis`
  implementing `WobbleAxis` Protocol. Dispatches the same prompt
  to two or more cognition providers (configurable via task
  options; default: primary + cheapest secondary from the same
  capability tier). Returns raw `DisagreementMetric` with
  `distance` (semantic-similarity score, 0..1), `provenance`
  (list of `(provider, model, latency_ms, token_usage)`),
  `responses` (list of `CognitionResult` references in ledger).
- `phoenix/verification/axes/self_consistency.py` —
  `SelfConsistencyAxis`. Dispatches the same prompt to the same
  provider multiple times with varied seeds/temperatures.
  Default `n=3`, temperatures `[0.0, 0.5, 0.7]`. Returns the
  pairwise distance matrix.
- `phoenix/verification/axes/prompt_perturbation.py` —
  `PromptPerturbationAxis`. Generates semantically-equivalent
  rewrites of the prompt (initial implementation: paraphrase
  via a lightweight LLM call to the same primary provider; later
  may use embedding-space neighbor sampling). Dispatches each
  perturbation, returns pairwise distances.
- All three register with the verification gate via the existing
  `WobbleAxis` Protocol locked in the 2026-05-08 v1.1 follow-up.
  The gate's adaptive-depth logic treats them as additional
  candidates; the user's `max_error_bar` parameter governs
  whether they run.

**Distance metric:** initial implementation uses sentence-embedding
cosine similarity via `sentence-transformers` (pinned model:
`all-MiniLM-L6-v2`, 22 MB). The model is shipped in
`vendor/cognition_wobble/embeddings/` as part of Step 5's
classifier work; Step 4 depends on Step 5 for the model
artifact, OR Step 4 ships first with a stub distance metric
(exact-string match) and Step 5 swaps in semantic distance.
**`[OPEN: P13-3]`** — which ordering? Default: ship Step 4 with
exact-string match; Step 5 owns the embedding model and swaps
in semantic distance as part of its classifier work. Surface to
Adam.

**Raw distances only at Step 4.** Step 4 does not classify
disagreements; it emits `DisagreementMetric.disagreement_type =
PhoenixDisagreementType.COGNITION_UNCLASSIFIED` for all cognition-
axis findings. The classifier (Step 5) replaces `UNCLASSIFIED`
with a real class.

**Verification:** unit tests for each axis with mocked cognition
providers; deterministic tests using fixed seeds for self-
consistency; the embedding model is vendored and pinned so
distance results are reproducible.

```
=== STEP 4 COMPLETE — AWAITING ADAM REVIEW ===
```

### 4.5 — Step 5: Cognition disagreement classifier (independent from physics)

**This step is substantial and may split into 5a + 5b mid-execution.**
Surface to Adam if it does. Per 13-D3, the cognition classifier MUST
be independent from the vendored physics-disagreement classifier:
distinct taxonomy, distinct training data, distinct evaluator.

**What lands:**

- `vendor/cognition_wobble/__init__.py` — vendor namespace marker.
- `vendor/cognition_wobble/disagreement_types.py` — `CognitionDisagreementType`
  enum (independent from `vendor/wobble/disagreement_types.py`):
  - `FACTUAL_AGREEMENT`
  - `STYLISTIC_DIVERGENCE`
  - `FACTUAL_DISAGREEMENT`
  - `INTERPRETIVE_DIVERGENCE`
  - `REFUSAL_DIVERGENCE`
  - `TOOL_CHOICE_DIVERGENCE`
  - `UNCLASSIFIED` (escape hatch — emitted whenever classifier
    confidence below threshold)
- `vendor/cognition_wobble/embeddings/` — pinned `sentence-
  transformers` model (`all-MiniLM-L6-v2`, 22 MB). Used by Step 4's
  distance metric and Step 5's classifier features.
- `vendor/cognition_wobble/calibration/` — calibration eval set.
  **Minimum 200 paired examples** spanning all seven taxonomy
  classes (~28 per class minimum). Sources: SAC3, FELM, FINCH-ZK,
  and Phoenix-generated examples covering Phoenix-specific cases
  (physics-prompt LLM responses, tool-use scenarios). Each example
  carries: `(prompt, response_a, response_b, gold_class,
  source_dataset, annotation_notes)`.
- `vendor/cognition_wobble/classifier.py` — the classifier itself.
  Initial architecture: feature engineering (semantic-distance
  score, length ratio, refusal-pattern match, tool-call equality,
  presence of factual claims via NER) feeding a calibrated
  gradient-boosted classifier (`xgboost` pinned, or `lightgbm`).
  **`[OPEN: P13-4]`** — should the classifier itself be an LLM-as-
  judge call (zero training data, but ongoing inference cost), a
  trained gradient-boosted model (one-time training, fast
  inference, requires labeled data), or a hybrid (GBM for fast
  classification, LLM-as-judge for `UNCLASSIFIED` cases)? Default
  for first cut: hybrid. GBM does the primary classification; cases
  scoring below confidence threshold escalate to LLM-as-judge for
  one extra inference. Surface to Adam.
- `vendor/cognition_wobble/eval.py` — calibration evaluator.
  Reports per-class precision/recall/F1 against the calibration
  set. Target accuracy: macro-F1 ≥ 0.70 across the six classified
  classes (UNCLASSIFIED is not graded — it's the escape valve).
  **Step 5 acceptance gate: macro-F1 ≥ 0.70.** Below that, Step 5
  does not pass; either the classifier improves or the threshold
  for `UNCLASSIFIED` rises to surface raw distance scores more
  often. Either outcome surfaces to Adam.
- `phoenix/verification/axes/*.py` (Step 4 outputs) updated to
  call the classifier and populate
  `DisagreementMetric.disagreement_type` with a real class instead
  of `UNCLASSIFIED`. The `UNCLASSIFIED` value remains as the
  classifier's confidence-too-low escape hatch.
- `phoenix/verification/cognition_classifier_provenance.py` —
  records classifier inputs, output class, confidence score, and
  classifier version into the ledger entry so replay can verify
  classification stability across classifier upgrades.

**Verification:**
- Calibration eval set passes the macro-F1 ≥ 0.70 acceptance gate.
- Per-class precision/recall reported in the build-step summary.
- Adversarial test cases (deliberately ambiguous prompts) verify
  `UNCLASSIFIED` is emitted with `classifier_confidence < threshold`
  rather than a forced class.
- Ledger replay against a fixture verifies classifier output is
  reproducible (deterministic for non-LLM-judge cases; the
  LLM-judge escalation path is documented as non-deterministic
  and gated by `replay_mode='loose'`).

**Honesty notes:**
- Classifier accuracy is bounded by the calibration set's
  coverage. Phase 13 ships with the minimum viable set; later
  phases may expand it as production traffic surfaces classes
  the initial set under-represents.
- The taxonomy locked in 13-D3 is a v1.1 default; v1.2 may add
  classes (e.g., `CITATION_DIVERGENCE`, `NUMERIC_PRECISION_
  DIVERGENCE`) as patterns emerge.
- Per Adam's 2026-05-18 emphasis: "This must be done carefully to
  give us the most accurate answers as possible." If care
  requires the step to split (separate eval-set construction phase
  from classifier-training phase), do it. Surface to Adam.

```
=== STEP 5 COMPLETE — AWAITING ADAM REVIEW ===
```

### 4.6 — Step 6: MCP-client mode + per-server registration

**What lands:**

- `phoenix/mcp/client.py` — `MCPClient` class wrapping the official
  MCP Python SDK (`mcp` package, pinned ≥1.0, <2.0). Supports both
  `stdio` and `http+sse` transports. Methods:
  - `connect(transport_spec) -> MCPSession`
  - `list_tools() -> list[ToolSpec]` (per-session)
  - `call_tool(name, args) -> ToolResult`
  - `close()` (releases transport resources cleanly)
- `phoenix/mcp/client_provider.py` — `MCPClientProvider` implementing
  `CognitionProvider` *and* a separate `MCPToolProvider` Protocol
  for non-LLM MCP servers (e.g., GitHub MCP, Postgres MCP). The
  dual-shape handles both "remote LLM behind MCP" and "remote tool
  set" cleanly.
- `phoenix/mcp/server_registry.py` — `MCPServerRegistry`. Module-level
  singleton (per Phase 6a pattern). Methods:
  - `register(name, spec)` — persists to `~/.phoenix/runtime/
    mcp_servers.json` (matches Phase 6a JSON-file backing) and to
    the Phase 6b state backend (SQLite/Postgres).
  - `unregister(name)` — immediate revocation.
  - `get(name) -> MCPServerSpec | None`
  - `list_servers() -> list[MCPServerSpec]`
  - All registry-mutating operations require admin Actor signature.
- `phoenix/mcp/server_spec.py` — `MCPServerSpec` frozen dataclass:
  `name`, `transport`, `endpoint`, `auth_config` (provider-specific,
  serialized as opaque dict; never logged), `allowed_tools` (list
  of strings; `["*"]` is explicitly forbidden and raises at
  registration), `max_budget_usd_per_day`, `audit_export_policy`,
  `prompt_disposition_override`.
- New REST endpoints in `phoenix/api/routes.py`:
  - `POST /v1/admin/mcp-servers/{name}` — register (admin only;
    requires `can_register_mcp_server`).
  - `GET /v1/admin/mcp-servers` — list (admin only).
  - `GET /v1/admin/mcp-servers/{name}` — fetch single (admin or
    delegated read).
  - `DELETE /v1/admin/mcp-servers/{name}` — de-register (admin
    only; immediate effect — any in-flight calls to the server
    receive `MCPServerRevoked` 503 on next round-trip).
- Dispatch path in `phoenix/router/decide.py` extended: when a task
  references `mcp_server: <name>` in its routing hint, Router
  consults `MCPServerRegistry.get(name)`:
  - Not registered → `MCPServerNotRegistered` (403).
  - Tool not in `allowed_tools` → `MCPToolNotAllowed` (403).
  - Per-server daily budget exceeded → `MCPServerBudgetExceeded`
    (429 with `Retry-After: <seconds-until-reset>`).
- Per-server budget accumulator: extends the Phase 10 cost-ledger
  schema with `mcp_server_name` column; the existing 24h-window
  query gains an `mcp_server_name` filter.

**Verification:**
- Unit tests for `MCPServerRegistry` round-trip (register → get →
  unregister).
- Integration tests against a local mock MCP server (stdio
  transport). Test fixtures include: a registered server, an
  unregistered server, a registered server with restrictive
  `allowed_tools`, a registered server with daily budget already
  exhausted.
- Negative tests: dispatching to an unregistered name MUST return
  403 before any network call; verify no socket open attempt in
  the test.

**SAFETY:** Per 13-D4, this step bears the largest threat-surface
expansion in Phoenix. Audit every code path that could call
`MCPServerRegistry.get()` and verify the call site enforces the
allowlist before any network operation. The default empty registry
state MUST result in zero possible network calls to any MCP
server.

**`[OPEN: P13-5]`** — should `allowed_tools` support glob patterns
(e.g., `"github.*"` to allow all GitHub MCP tools)? Tension:
convenience vs. precise-allowlist discipline. Default: exact-match
only in Phase 13; glob support is a v1.2 candidate behind a
`can_register_glob_pattern` permission. Surface to Adam.

```
=== STEP 6 COMPLETE — AWAITING ADAM REVIEW ===
```

### 4.7 — Step 7: Streaming-token surface on WebSocket

**What lands:**

- `phoenix/api/ws_events.py` extended with three new event types:
  - `token.delta` — incremental token from a cognition provider.
    Payload: `{ task_id, axis_id, provider, model, delta_text,
    cumulative_tokens, cost_so_far_usd }`. Emitted only when the
    task's `options.stream_tokens=True` and the actor has
    `can_receive_token_stream` permission.
  - `cognition.tool_call` — emitted when a cognition provider
    requests a tool call. Payload: `{ task_id, axis_id, provider,
    model, tool_name, tool_args }`.
  - `cognition.tool_result` — emitted when Phoenix returns a tool
    result to the cognition provider. Payload: `{ task_id, axis_id,
    tool_name, result_summary, latency_ms }`.
- `phoenix/providers/cognition/*.py` adapters extended to support
  streaming: when `stream=True` and the provider supports it, the
  adapter yields token deltas via an internal async generator;
  Phoenix's event broker (Phase 6b NATS) fans these out to WS
  subscribers.
- WebSocket subscription extended: `GET /v1/ws/tasks/{task_id}/stream?
  events=token.delta,cognition.*` lets the client filter event types.
  Existing event types (`task.started`, `task.complete`, etc.) remain
  the default subscription.
- Backpressure: NATS JetStream's per-subject consumer pull rate
  governs streaming; slow consumers see dropped `token.delta` events
  with a `token.dropped` summary event at the end (containing total
  dropped count). Verification gate inputs are NEVER dropped —
  those go via a separate higher-priority subject.

**Verification:**
- Unit tests with mocked streaming adapters; verify event order and
  payload shapes.
- Integration test: client subscribes, dispatches a streaming
  cognition task, receives `token.delta` events in order followed
  by `task.complete`.
- Backpressure test: slow consumer (artificial delay) verifies
  `token.dropped` summary at end.

**PERF:** Streaming adds per-token NATS publish overhead. Default
publish rate cap: 100 token.delta events/second per task (above
which Phoenix batches deltas into chunks). Tunable via
`PHOENIX_STREAM_RATE_CAP` env var.

**`[OPEN: P13-6]`** — should `token.delta` payloads be hashed or
suppressed entirely when `prompt_disposition=HASH_ONLY` (13-D2)?
The user has opted out of verbatim prompt storage; presumably
they don't want token-by-token reconstruction either. Default:
when `HASH_ONLY`, `token.delta` events are NOT emitted to WS
subscribers; the consumer receives only the final
`task.complete` with the aggregate result. Streaming requires
either `VERBATIM` or a new `STREAM_ONLY` disposition that
streams to one specific subscriber session but does not persist
to the ledger. Surface to Adam.

```
=== STEP 7 COMPLETE — AWAITING ADAM REVIEW ===
```

### 4.8 — Step 8: Privacy controls — hash-only default ledger + opt-in verbatim

**What lands:**

- Omega Ledger schema migration to v4 via new migration file
  `phoenix/state/migrations/phase13_prompt_disposition.py`. Adds
  columns to `solve_entries`:
  - `prompt_disposition TEXT NOT NULL DEFAULT 'HASH_ONLY'`
  - `prompt_hash TEXT NOT NULL` (SHA-256 of canonicalized prompt)
  - `prompt_verbatim TEXT NULL` (filled only when disposition=VERBATIM)
  - `prompt_encrypted BLOB NULL` (filled only when disposition=
    ENCRYPTED_OPT_IN; Phase 13 ships the column, key-mgmt deferred)
  - `cognition_provenance_json TEXT NULL` (model fingerprint,
    temperature, top_p, response_format, tool list hashes, classifier
    output)
- `phoenix/ledger/prompt_disposition.py` — canonicalization +
  hashing helpers. Canonical form for prompts is JSON with sorted
  keys, normalized whitespace, normalized message content per the
  Anthropic Prompt format spec. Hash is hex-encoded SHA-256.
- `phoenix/ledger/encryption.py` — encrypted-at-rest skeleton. Phase
  13 ships the column and the encrypt/decrypt helper Protocol; the
  default impl raises `EncryptedDispositionNotConfigured` because
  the key-management ceremony lands later. Tests verify the
  ceremony point is well-marked.
- Provenance: every cognition-axis ledger entry records
  `prompt_disposition`. Replay strict-mode behavior:
  - `prompt_disposition=HASH_ONLY` → strict replay verifies hash
    matches; cannot regenerate output (the prompt isn't stored).
    Documented as expected.
  - `prompt_disposition=VERBATIM` → strict replay re-invokes the
    cognition provider with the stored prompt. For deterministic
    providers (temperature=0, fixed seed where supported), output
    is bit-exact. For non-deterministic providers, replay records
    the new output alongside the original and flags
    `non_deterministic_replay`.
  - `prompt_disposition=ENCRYPTED_OPT_IN` → replay requires the
    decryption key; without it, falls back to HASH_ONLY semantics.

**Verification:**
- Unit tests for canonicalization + hashing (idempotency,
  whitespace invariance, key-order invariance).
- Migration test: existing v3 ledger entries auto-default to
  HASH_ONLY when migrated to v4.
- Replay tests for all three dispositions.
- Negative test: attempting to write a VERBATIM entry without
  `can_store_prompt_verbatim` permission raises 403.

**SAFETY:** Per 13-D2, the default `HASH_ONLY` posture is the
load-bearing privacy guarantee. Any code path that stores a
prompt in any form other than the SHA-256 hash MUST check the
actor's `can_store_prompt_verbatim` (or `can_store_prompt_encrypted`)
permission first. Audit this at Step 10's acceptance battery.

```
=== STEP 8 COMPLETE — AWAITING ADAM REVIEW ===
```

### 4.9 — Step 9: Permission registry extensions + admin endpoints

**What lands:**

- `phoenix/safety/permissions.py` extended with new capabilities:
  - `can_call_cognition` (default: granted to all actors in
    `default` tier and above) — required to dispatch a cognition
    task.
  - `can_call_mcp_server` — base capability; specific server
    access controlled by per-server registration (Step 6).
  - `can_register_mcp_server` (admin tier only) — required for the
    `POST /v1/admin/mcp-servers/{name}` endpoint.
  - `can_store_prompt_verbatim` (default: NOT granted; explicit
    opt-in only) — required to dispatch a task with
    `prompt_disposition=VERBATIM`.
  - `can_store_prompt_encrypted` (default: NOT granted) — required
    for `ENCRYPTED_OPT_IN` disposition.
  - `can_store_raw_provider_body` (default: NOT granted) — gates
    P13-1 raw-provider-body storage.
  - `can_receive_token_stream` (default: granted) — required to
    subscribe to `token.delta` events.
- New admin endpoints:
  - `POST /v1/identity/permissions/grant-prompt-verbatim` — grants
    `can_store_prompt_verbatim` to a target actor. Requires admin
    Actor + writes a `PermissionGrantEntry` to the Omega Ledger
    with reason + duration. Optional `expires_at` for time-bound
    grants.
  - `POST /v1/admin/budget/cognition-override` — cognition-specific
    budget override (parallel to Phase 10's
    `POST /v1/admin/budget/override` for solves). Same three scopes
    (per_solve / per_actor_24h / per_org_24h), separately tracked
    because cognition cost patterns differ from solve costs.
  - `GET /v1/admin/audit/cognition-spend` — admin view of
    cognition-specific spending; complements existing audit
    endpoints.
- Permission-check enforcement: the safety gate's 9-stage pipeline
  (Section 7.4) gains a new stage 6b for cognition-specific
  permission checks, between the existing Stage 6 (per-actor caps)
  and Stage 7 (frontier-physics primary check). The frontier-
  physics check is irrelevant for cognition tasks; the gate routes
  cognition tasks through 6b instead of 7.

**Verification:**
- Unit tests for each new permission.
- Integration test: actor without `can_store_prompt_verbatim`
  attempting a VERBATIM task gets 403; the rejection writes an
  audit entry.
- Admin-endpoint tests: grant flow round-trips (grant → check →
  use), revocation flow (un-grant → next call returns 403).

```
=== STEP 9 COMPLETE — AWAITING ADAM REVIEW ===
```

### 4.10 — Step 10: Acceptance battery + version bump + CHANGELOG

**What lands:**

- `tests/cognition/` — full cognition test surface:
  - `test_adapter_anthropic.py`, `test_adapter_openai.py`,
    `test_adapter_google.py` — mocked HTTP + error mapping +
    capability advertisement.
  - `test_litellm_passthrough.py` — import gating + dispatch via
    mocked `litellm.completion`.
  - `test_cross_model_axis.py`, `test_self_consistency_axis.py`,
    `test_prompt_perturbation_axis.py` — wobble axis behavior.
  - `test_cognition_classifier.py` — classifier accuracy on
    held-out portion of calibration set (macro-F1 reported in
    test output; gate at ≥ 0.70).
  - `test_streaming_tokens.py` — WS event stream shape +
    backpressure behavior.
- `tests/mcp_client/`:
  - `test_server_registry.py` — registration / revocation flows.
  - `test_dispatch.py` — allowlist enforcement; per-server budget
    enforcement; revocation immediate effect.
  - `test_threat_surface.py` — adversarial tests: unregistered
    server, disallowed tool, exhausted budget, transport-spec
    injection attempts. ALL must yield typed exceptions before
    any network call.
- `tests/privacy/`:
  - `test_prompt_disposition.py` — all three dispositions
    round-trip through the ledger correctly.
  - `test_permission_enforcement.py` — opt-in gates work end-to-end.
- `tests/distribution/` extended with:
  - `test_wheel_includes_cognition.py` — wheel install resolves
    `phoenix.providers.cognition`.
  - `test_wheel_litellm_extra.py` — `pip install
    phoenix-middleware[litellm]` installs LiteLLM.
  - Docker image healthcheck verifies cognition imports succeed.
- `@pytest.mark.acceptance` battery extension (Phase 11 contract):
  three new acceptance tests added to the existing five-test set:
  - `test_cognition_panic_mode.py` — cognition provider unreachable
    → `CognitionUnavailable` 503; failover to alternate provider
    if registered; refuse-to-complete if no alternatives.
  - `test_mcp_server_panic_mode.py` — registered MCP server
    unreachable → server quarantined; admin override re-enables
    after the operator confirms availability.
  - `test_long_window_replay_cognition.py` — VERBATIM-disposition
    task replayed after 30-day clock advance; for a deterministic
    provider (temperature=0), output matches bit-exact.
- Version bump in lockstep across the same surface as `1.0.0rc1`'s
  version bump (Phase 12 changelog Step 10):
  `1.0.0` → `1.1.0.dev0`.
- `pyproject.toml` description updated to reflect the v1.1
  cognition + MCP-client extension.
- `CHANGELOG.md` `[1.1.0.dev0]` entry in the Phase 12 shape:
  what landed, locked-scope decisions, deferrals, honesty notes.
- README status table updated with Phase 13 row (also closes the
  Phase 6b–10 backfill debt left over from Phase 11).

**Verification:**
- `pytest tests/ -m "not acceptance"` green.
- `pytest tests/ -m acceptance` green (5 v1 acceptance tests + 3
  new Phase 13 acceptance tests).
- `pytest tests/distribution/ -q` green with the `[litellm]` extra
  installed.
- Pre-commit gates: ruff + ruff-format + mypy-strict + smoke green.

**Final stop gate:**

```
=== STEP 10 COMPLETE — PHASE 13 COMPLETE — AWAITING ADAM REVIEW ===
```

---

## 5 — Open items for Adam review

This guide ships with six `[OPEN: ...]` items. The
build-guide-defaults are listed; Adam may override any of them at
draft-lock time (the Phase 10 pattern). All defaults respect the
five 2026-05-18 locks.

| ID | Topic | Default | Step |
|---|---|---|---|
| P13-1 | Raw provider response body storage | Canonicalized only; raw body behind `preserve_raw_provider_body` flag + `can_store_raw_provider_body` permission | Step 1 |
| P13-2 | Shared base class for cognition adapters | `_CognitionAdapterBase` for retry + error mapping; provider quirks in subclasses | Step 2 |
| P13-3 | Step 4 / Step 5 ordering re. embedding model | Step 4 ships with exact-string match; Step 5 owns the embedding model and upgrades Step 4's metric | Step 4 + 5 |
| P13-4 | Classifier architecture | Hybrid: GBM primary, LLM-as-judge for `UNCLASSIFIED` escalation | Step 5 |
| P13-5 | `allowed_tools` glob support | Exact-match only in Phase 13; globs deferred to v1.2 | Step 6 |
| P13-6 | Token streaming under HASH_ONLY disposition | Suppress `token.delta` when HASH_ONLY; require VERBATIM (or future STREAM_ONLY) for streaming consumers | Step 7 |

Resolve at draft-lock time per the Phase 10 pattern, or carry to
implementation as `[OPEN: ...]` markers and surface at the relevant
step's stop gate.

## 6 — Standing rules carried forward (from Phases 0–12)

1. **Phase gates with explicit Adam review.** `=== STEP N COMPLETE ===`
   ends every step. No silent advancement.
2. **Stop and ask on architectural ambiguity.** If a step reveals a
   question this guide and the design-decisions doc don't answer,
   mark `[OPEN: ...]` and surface to Adam — do not invent a
   resolution.
3. **PERF and SAFETY callouts inline.** Performance and safety
   implications flagged at the point they arise, not buried in
   end-of-step notes.
4. **Per-section READMEs.** Every directory under `phoenix/` and
   `vendor/` gets its README updated when this phase changes its
   contents. New directories (`phoenix/providers/cognition/`,
   `phoenix/mcp/`, `vendor/cognition_wobble/`,
   `phoenix/verification/axes/`, `phoenix/pricing/v2/`) get fresh
   READMEs.
5. **Launcher updated only when startup behavior changes.** Phase 13
   does not change startup behavior. Launcher stays as-is.
6. **No OneDrive paths.** All Phoenix paths under `C:\Phoenix\`.
   Tooling that tries to redirect to OneDrive is refused.
7. **Live reads beat memory.** Vendored API names and provider SDK
   shapes are source-of-truth. Read fresh before each step.
8. **2026-05-08 v1.1 follow-up commitments honored.** `WobbleAxis`
   Protocol stays the axis-dispatch surface; `CloudSeams` registry
   stays generic name-keyed; `LatencyTier` enum unchanged
   (cognition tasks default to `BATCH_REALTIME`); front-door
   endpoints stay under `/v1/...` flat (cognition surface is
   `/v1/admin/mcp-servers/*` and `/v1/admin/budget/cognition-*`,
   sibling to existing flat structure).
9. **The Claude Code header block stays at the top of this file.**
   Per Adam's 2026-05-18 request. If this guide is materially
   updated, the header note's date is updated.

## 7 — What's next after Phase 13

Following Phase 13's `=== STEP 10 COMPLETE ===` review and merge,
the v1.1 work surface looks like:

- **Phase 14 (candidate) — A2A protocol support.** Agent-to-agent
  delegation per the Linux Foundation Agentic AI Foundation spec.
  Builds on Phase 13's MCP-client mode; the patterns are
  complementary (MCP = how agents call tools; A2A = how agents
  delegate to other agents). Scoped if Adam decides A2A demand
  justifies the build.
- **Perception harness Phase 12 (separate track).** Per
  `PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md`, building in parallel
  with Phase 13. No overlap.
- **`phoenix-reference-client` (separate repo).** The Section 9
  reference admin client, now able to dispatch through Phase 13's
  cognition substrate as its canonical demonstration.
- **Customer-key-management ceremony for `ENCRYPTED_OPT_IN`.**
  Per 13-D2 deferred path — lands when the first commercial
  customer requires it.
- **Calibration set expansion** for the cognition disagreement
  classifier. The 200-example minimum is v1.1 scope; v1.2 expands
  as production traffic surfaces under-represented classes.
- **Patent decision (potentially license re-lock).** Per 13-D1
  revisit trigger — if Adam files a patent before launch, the
  license decision re-opens before publication.

```
=== BUILD GUIDE COMPLETE — AWAITING ADAM REVIEW ===
```
