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

## [1.0.0] — 2026-05-28

Phoenix v1.0 final release. **Content-equivalent to `1.0.0rc1`**: the
1.0.0 git tag is annotated at commit `12c9198` (Phase 12 CI fix; the
last v1.0-line commit before Phase 13 opened the v1.1 development line
on 2026-05-19). No source changes occurred during the two-week rc1
baking period; the 1.0.0 tag formalizes the v1.0 release state.

This entry marks the formal close of the Phoenix v1.0 line. For the
full v1.0 release contents (Trinity Core wiring, the seven wrapping
layers, three-axis wobble verification, hashchained Omega Ledger,
three distribution artifacts, full acceptance battery) see the
`[1.0.0rc1]` entry below. For ongoing v1.1 development on top of v1.0
see `[1.1.0.dev0]` above.

The pyproject.toml version at commit `12c9198` is `1.0.0rc1` — the
lightweight tag accepts this so the v1.1 dev line on `main` is not
disturbed. A future v1.0 maintenance branch (`release/1.0.x`) would
ship a real `1.0.0` pyproject bump if PyPI publication is later
warranted; the GitHub Release at this tag does not require it.

---

## [1.1.0.dev0] — 2026-05-20

### Phase 13 Step 5c: FELM/SAC3 dataset adapters (2026-06-12)

The last corpus-tooling piece: adapters that convert the factual-class source
datasets into our JSONL pairs. Inert (no model calls in the FELM path); the real
data is still Adam-side.

- `cognition_wobble/datasets.py` + `scripts/adapt_dataset.py`:
  - **FELM** (`hkust-nlp/felm`) — each record with a verified error becomes a
    deterministic, pre-labeled **FACTUAL_DISAGREEMENT** pair (original response
    vs. a corrected reconstruction built from FELM's `comment` ground-truth).
    Fully-factual / uncorrectable records are skipped (with reasons).
  - **SAC3** (`intuit/sac3` method output) — pairs the first sampled response
    with each other; the `sc2_vote` consistency vote pre-labels
    **FACTUAL_AGREEMENT** (consistent) / **FACTUAL_DISAGREEMENT** (inconsistent)
    candidates (weaker than FELM's human labels — re-judge/verify). `--no-prelabel`
    emits UNLABELED pairs for the judge.
  - Adapters return per-record skip reasons; the CLI writes via the atomic
    `corpus.write_corpus()` and exits non-zero when nothing is emitted.
- **Tests:** +11 (FELM error→pair, true-segment preservation, skip paths; SAC3
  vote pre-labeling + UNLABELED mode; corpus round-trip; CLI end-to-end +
  malformed-JSON). Verified on Python **3.11** (compile + run) as well as 3.13.

### Phase 13 Step 5c: corpus scoping + generation/labeling tooling (2026-06-12)

**Scaffolds the remaining Step 5c data work** (the labeled corpus itself is still
Adam-side). Production behavior unchanged; nothing here trains or registers a
classifier.

- **Scope + annotation docs:** `docs/planning/STEP5C_CORPUS_PLAN.md` (honest sourcing
  plan — SAC3/FELM/FINCH-ZK yield mostly the two factual classes; the four
  under-represented classes must be Phoenix-generated and every pair labeled) and
  `STEP5C_ANNOTATION_GUIDE.md` (ordered decision procedure + `[ADAM-DECIDE]` boundaries).
- **Corpus audit + confusion reporting:** `scripts/corpus_stats.py` (per-class balance vs
  the ~28 floor, dup detection, feature centroids) and a `--confusion` flag +
  `acceptance.format_confusion_matrix()` (read the confusion matrix before retraining).
- **Pair generation:** `cognition_wobble/generation.py` + `scripts/generate_cognition_pairs.py`
  — run ≥2 providers (injectable; mock-tested) on per-class seed sets → UNLABELED candidate
  pairs; catches `CognitionContentPolicyError` → synthesizes a refusal result.
- **Judge labeling (path B):** `cognition_wobble/annotation.py` +
  `scripts/label_cognition_pairs.py` — reuse `LLMJudgeClassifier` to propose `gold_class`,
  flag the hard four / low-confidence / abstentions `NEEDS-VERIFY`.
- **Embedding model:** `scripts/vendor_embedding_model.py` + loader prefers the vendored
  `all-MiniLM-L6-v2` path; shared `cognition_wobble/provider_factory.py`; benign-but-borderline
  seed prompt sets under `calibration/prompt_seeds/`.
- **Atomic corpus writes:** `corpus.write_corpus()` (temp + `os.replace`) used by both CLIs.
- **Adversarial-review fixes:** out-of-range `provider_index` now skips (not crashes) in
  `generate_pairs`; `_load_seeds` reports malformed JSON with line numbers; `verify_summary`
  always exposes all seven classes; the generate CLI exits non-zero when all specs skip.
  Judge meta-prompt injection-hardening + confidence-clamp signalling were flagged as a
  separate follow-up (the judge is shipped/locked).

**Tests added:** 31 (corpus audit, confusion matrix, generation, annotation, vendored-model
load preference, atomic write). Full cognition suite: 516 passed, 3 skipped.

### Phase 13 Step 5c: cognition-classifier training + eval harness (2026-06-09)

**Scaffolding only — production behavior unchanged.** Builds the
non-corpus-gated half of Step 5c so that, once a real labeled cognition
corpus exists, training a classifier and swapping it in for the shipped
`AlwaysUnclassifiedClassifier` default is mechanical. No model artifact
is committed, the classifier registry default is untouched, and the
**macro-F1 ≥ 0.70** acceptance gate stays unmet until a *real* model
clears it.

**New modules** (under `vendor/cognition_wobble/`, the Phoenix-authored
classifier substrate):
- `corpus.py` — documented **JSONL** corpus schema + `load_corpus()` →
  `list[CalibrationExample]`, with fail-closed validation (unknown
  field, missing field, malformed tool-call, or unrecognized
  `gold_class` stops the load with the offending line number).
- `training.py` — `train_gbm()` trains a lightgbm multiclass GBM over
  the six **graded** classes (gold-`UNCLASSIFIED` rows dropped;
  `UNCLASSIFIED` is the inference-time threshold escape valve, never a
  trained label) and writes the **native-text** artifact + a
  `*.meta.json` sidecar. The artifact loads verbatim through the shipped
  `GBMClassifier(model_path=...)` — matching `lgb.Booster(model_file=…)`
  is what makes the swap plug-and-play (hence `.txt`, not `.joblib`).
  `build_training_matrix()` is dependency-free and testable without the
  `[ml-classifier]` extra.
- `acceptance.py` — `ACCEPTANCE_MACRO_F1 = 0.70`, `check_gate()`, and a
  fixed-width `format_report()`, wrapping the existing `eval.evaluate()`.
- `calibration/synthetic.py` — deterministic **synthetic** corpus
  generator (loudly NOT real data; classes separable by construction so
  the pipeline runs end-to-end without the real corpus).

**New CLIs** (`scripts/`): `train_cognition_classifier.py`,
`evaluate_cognition_classifier.py` (exit 0 = gate PASS, 1 = FAIL),
`gen_synthetic_cognition_corpus.py`.

**Fixture:** `tests/cognition/fixtures/synthetic_corpus.jsonl` (252 rows;
6 graded × 40 + 12 gold-`UNCLASSIFIED`).

**Tests added:** 31 across `test_corpus_loader.py` (loader/validation,
always run), `test_classifier_acceptance.py` (gate/report + dependency-
free training-matrix, always run), and `test_train_cognition_classifier.py`
(training + full `load → train → eval` clearing the gate on a held-out
split; `importorskip lightgbm`). Verified end-to-end against real
lightgbm 4.6 locally; held-out macro-F1 = 1.0 on the synthetic set.

**Still blocked on Adam's real corpus:** the labeled data itself
(≥ 200 examples, ~28+ per graded class), earning the 0.70 gate on real
held-out data, committing `models/gbm_classifier_v1.txt`, and the
registry swap. Procedure documented in
`docs/superpowers/plans/2026-06-09-phase-13-step5c-classifier-training-harness.md`.

### Phase 13.x.9: auto-capture baseline wiring (2026-06-09)

**Wired:** `maybe_auto_capture_baseline()` is now called at the tail of
`DriftDetector.run_cycle`, completing the v1.1.x follow-up deferred at
Phase 13.5. The detector owns a consecutive-healthy counter; after N
consecutive healthy cycles it refreshes the per-version cognition
baseline, then resets the counter (re-baseline every N healthy cycles).

**Opt-in:** off by default. Set `PHOENIX_DRIFT_AUTO_CAPTURE=1` to enable;
`PHOENIX_DRIFT_AUTO_CAPTURE_CYCLES` overrides the threshold (wired-path
default 20 ≈ 5 days at the 6h cadence — intentionally more conservative
than the standalone helper's default of 5, to avoid absorbing slow drift).

**Fail-safe:** capture failures are caught and logged inside `run_cycle`,
never propagated — same contract as snapshot persistence and callbacks.
Deps (provider + per-version baseline + version) flow from
`get_detector()`; in degraded environments (no state backend) auto-capture
stays inert.

**Concurrency hardening** (from an adversarial review of this change):
`CognitionDriftBaseline.write_current` now writes atomically (unique temp
file + `os.replace`), so a reader (e.g. the ML checker reading the baseline
mid-cycle) never observes a truncated file — this also protects the existing
admin recapture path. The post-capture counter reset is now compare-and-swap
(only zeroes the counter if unchanged since the cycle snapshotted it),
avoiding a clobbered increment if an admin force-cycle overlaps the scheduler.
Both are no-ops in the normal single-threaded scheduler path.

**Tests added:** 20 in `tests/integration/test_drift_detector.py`
(11 wiring + 7 env-resolution + 1 get_detector wiring, plus an explicit
get_detector baseline-path isolation) and 1 atomic-write test in
`tests/cognition/test_cognition_drift_baseline.py`. The shipped-helper
tests in `tests/cognition/test_drift_detector_auto_capture.py` are
unchanged.

### Phase 13.x.4: classifier integration for cognition replay (2026-05-28)

Upgrades `phoenix/ledger/cognition_replay.py`'s
`default_compare_cognition_results` from a binary
`{match, divergence}` outcome to a 4-level `CognitionReplayVerdict`
(`bit_exact` / `semantic_match` / `divergence` / `unclassified`)
driven by `CognitionClassifier.classify()`.

**New module:** `phoenix/ledger/cognition_classifier.py` — Protocol
re-export + `set_/get_/reset_cognition_classifier` registry. Ships
with `AlwaysUnclassifiedClassifier` as the ship default (matches the
`NullPromptEncryptor` pattern); ops swap in a real classifier (e.g.,
Phase 13 Step 5b's hybrid GBM+LLM-judge) at daemon startup.

**ComparisonOutcome** gains two optional fields (back-compat: default
`None`): `verdict: CognitionReplayVerdict | None` and
`classification: ClassificationResult | None`.

**Disagreement-type → verdict mapping (locked):**

| `CognitionDisagreementType` | `CognitionReplayVerdict` |
|---|---|
| `FACTUAL_AGREEMENT` | `SEMANTIC_MATCH` |
| `STYLISTIC_DIVERGENCE` | `SEMANTIC_MATCH` |
| `FACTUAL_DISAGREEMENT` | `DIVERGENCE` |
| `INTERPRETIVE_DIVERGENCE` | `DIVERGENCE` |
| `REFUSAL_DIVERGENCE` | `DIVERGENCE` |
| `TOOL_CHOICE_DIVERGENCE` | `DIVERGENCE` |
| `UNCLASSIFIED` | `UNCLASSIFIED` |

**Raise-policy matrix (matches=True ⟺ no raise):**

| Verdict | `temp=0` | `temp>0` |
|---|---|---|
| `BIT_EXACT` | True | True |
| `SEMANTIC_MATCH` | **True (NEW: no raise on classifier-confirmed equivalence)** | True |
| `DIVERGENCE` | False (raise) | **False (NEW: classifier confidence raises even at temp>0)** |
| `UNCLASSIFIED` | False (raise) | True (no raise) |

**Two behavior changes from PR #20** (bolded above):
1. `temp=0` + `SEMANTIC_MATCH` no longer raises (the headline 13.x.4
   feature: classifier-confirmed equivalence is preserved).
2. `temp>0` + `DIVERGENCE` now raises (classifier confidence beats
   the temp>0 hedge).

**Back-compat guarantee:** All existing PR #20 tests pass unchanged.
The `AlwaysUnclassifiedClassifier` ship default produces
`verdict=UNCLASSIFIED`, which maps to the same `matches` semantics as
PR #20's binary outcome.

**Perf optimization:** The bit-exact branch returns *without* calling
the classifier (saves ~100-500ms when hybrid LLM-judge fires).
Pinned via `TestPerfOptimization`.

**Classifier failure fallback:** If `classifier.classify()` raises,
the comparator returns `verdict=UNCLASSIFIED` with `reason` prefix
`classifier_failure: <ExceptionType>(<first 80 chars>)`; logs the
exception at WARNING. Replay does not crash on classifier
malfunction. When `temperature > 0`, the reason still starts with
`non_deterministic_replay:` (the prefix invariant) to preserve PR
#20's substring checks.

**Tests added:** 26 new (4 registry + 1 ComparisonOutcome-defaults + 1
bit-exact-verdict + 7 mapping + 8 raise-policy + 3 error-handling +
1 perf opt + 1 kwarg propagation) in
`tests/cognition/test_cognition_classifier_registry.py` and
`tests/cognition/test_cognition_replay.py`. Total
test_cognition_replay.py count: 50 passing.

**Open follow-ups (deferred to later v1.1.x slots):**
`classifier-version-drift` warning, `hybrid-classifier-LLM-judge-cost`
ceiling.

### Phase 13.x.7: encryption admin CLI + rotate-key endpoint (2026-05-28)

Closes the two ergonomic gaps left by Phase 13.x.6 (PR #21):

- **CLI:** `phoenix admin generate-encryption-key [--name SLUG] [--force] [--keys-dir PATH]`
  generates an age (X25519) keypair, writes identity.txt
  (mode 0o600 on POSIX) + recipients/<name>.pub to the conventional
  Phoenix encryption-keys directory.
- **Admin endpoint:** `POST /v1/admin/encryption/rotate-key` generates
  the keypair in-process (audit-logged), returns paths + fingerprints
  + a `next_step` field reminding the caller that daemon-restart is
  required to pick up the new recipient.

**New shared primitive:** `phoenix/ledger/keygen.py::generate_age_keypair()`
backs both surfaces. Single place for filename convention + POSIX
permission discipline + path-conflict guard (refuses overwrite without
`force=True`).

**New permission:** `ActorPermissions.can_rotate_encryption_key`
(default deny; admin-tier construction grants True). The endpoint
returns 403 if the actor lacks the flag.

**Audit events:**
- `admin.encryption.rotate.success` — `{name, recipient_fingerprint,
  recipient_path, identity_path, force}` on success. Never includes
  the identity secret.
- `admin.encryption.rotate.error.{kind}` — granular per-error event
  types (auth / permission / kill_switch / conflict / keygen /
  pyrage_missing / identity / privilege / rate_limit) for forensic
  observability.

**NOT shipped at 13.x.7** (deferred):
- **Batch decrypt-and-re-encrypt** of existing `ENCRYPTED_OPT_IN`
  ledger rows on rotation. The 13.x.6 README originally framed this
  as part of 13.x.7, but the database-transaction + partial-failure-
  recovery surface is substantially different from key generation;
  it gets its own follow-up slot.
- **`POST /v1/admin/encryption/reload`** zero-downtime encryptor
  reload. Daemon-restart pattern preserved — matches the existing
  `encryptor_from_default_layout()` startup-only loading discipline.
- **Identity revocation / cleanup** of replaced keys.

**Tests added:** ~20 across 4 test files:
- `tests/cognition/test_keygen.py` (9) — primitive happy-path /
  conflict / force / fingerprint / POSIX-mode / name-validation
- `tests/integration/test_admin_encryption_rotate_key.py` (5) —
  endpoint happy-path / default-name / 409 / force / 403
- `tests/cli/test_admin_generate_encryption_key.py` (3) — CLI
  happy-path / conflict / force
- `tests/unit/test_permissions_phase13x7.py` (3) — default-deny /
  explicit-grant / existing-flags-unchanged

### Phase 13.5: cognition drift extension (2026-06-05)

Wires cognition substrate signals into the existing drift detector's
ML Statistical Checker (which had a `feature_provider` seam since
Phase 6b but no provider). 14-dim feature vector covering:

- Classifier verdict distribution (bit_exact / semantic_match /
  divergence / unclassified rates)
- Classifier confidence (mean + p10)
- Cognition wobble disagreement (mean + p90)
- Provider error + refusal rates
- Cognition latency p95
- Prompt disposition mix (HASH_ONLY / VERBATIM / ENCRYPTED_OPT_IN)

**New files:**
- `phoenix/verification/cognition_drift_features.py` —
  `CognitionDriftFeatures` dataclass + `CognitionFeatureProvider` +
  `_VECTOR_FIELDS` ordered tuple (single source of truth for vector
  dimension; module-level assertion catches accidental drift).
- `phoenix/verification/cognition_drift_baseline.py` —
  `CognitionDriftBaseline` with per-Phoenix-version storage + schema
  versioning + weighted-L2 distance.
- `phoenix/admin/cognition_drift_admin.py` — two admin endpoints
  (`POST /v1/admin/drift/cognition-baseline/capture` + `GET
  /v1/admin/drift/cognition-baseline`).

**New permission:** `can_capture_drift_baseline` (default deny;
granted to bootstrap actors).

**Extended:** `MLStatisticalChecker` consumes the baseline + threshold
(`PHOENIX_DRIFT_COGNITION_DISTANCE_THRESHOLD` env-var overrides the
0.5 default). `get_detector()` wires the cognition feature provider
into the ML checker at daemon startup; graceful fallback to default
checker list on wiring failures.

**Auto-capture helper:** `maybe_auto_capture_baseline()` refreshes
the running baseline after N consecutive healthy cycles. Helper is
shipped as a callable; full integration into the `DriftDetector.run_cycle`
loop is a v1.1.x followup.

**Privacy contract:** the feature provider reads only aggregate
fields (verdict, classification, cognition_provenance,
cognition_disagreement_metric, prompt_disposition, axis). It does
NOT access `prompt_verbatim` or `prompt_encrypted` payload fields.
Whitelist enforced by `_extract_aggregate_fields` + pinned by a
dedicated test that asserts the whitelist literal against the
approved frozenset.

**Aggregation rule unchanged.** Decision 17's three-checker
aggregation is preserved; cognition signals roll into the existing
ML checker rather than adding a fourth.

**Tests added:** ~28 across 5 test files (features 10 + baseline 7 +
permission 2 + admin endpoints 5 + ML checker integration 5 +
auto-capture 3 = 32 — actual counts may vary slightly per
implementer adjustments).

**NOT shipped at 13.5** (deferred follow-ups):
- Per-provider drift attribution
- Drift-triggered cognition rerouting (router consumes signal)
- Full integration of `maybe_auto_capture_baseline` into
  `DriftDetector.run_cycle` (helper is shipped; auto-cycle wiring deferred)
  — **shipped in Phase 13.x.9, 2026-06-09**
- Replacing the Tier-1 checker or `ml/drift_ensemble.py`

Phase 13 extends Phoenix from a quantum-only middleware into a hybrid
quantum + classical-cognition substrate. The same audit-grade guarantees
(typed errors, hashchained ledger, three-axis wobble verification,
permission-gated dispatch) now apply to LLM provider calls and MCP-server
tool dispatch. Phoenix can verify cognition outputs the same way it
verifies physics solutions.

This release lands ten build-guide steps on the
`phase-13-cognition-mcp-client` branch (draft PR #15).

### Cognition substrate (Steps 1–5)

- **`phoenix.providers.cognition`** — :class:`CognitionProvider` PEP 544
  Protocol (`complete`, `capabilities`, `fingerprint`) + three concrete
  adapters (`AnthropicProvider`, `OpenAIProvider`, `GoogleProvider`) +
  a `LiteLLMPassthroughProvider` covering ~100 additional models.
  Adapters share a `_CognitionAdapterBase` with retry/backoff +
  cost-estimation seams. Typed errors:
  `CognitionAuthError`, `CognitionRateLimited`, `CognitionUpstreamFailure`,
  `MissingOptionalDependency`, `PricingUnavailable`.

- **Three cognition wobble axes** (`phoenix.verification.axes`):
  :class:`CrossModelAxis` (compare two providers; disagreement → wobble),
  :class:`SelfConsistencyAxis` (same provider, N samples, temperature > 0;
  intra-distribution spread → wobble),
  :class:`PromptPerturbationAxis` (N rephrasings; sensitivity → wobble).
  Each emits :class:`CognitionDisagreementMetric` with semantic-distance +
  classifier-confidence + classifier-version.

- **`vendor/cognition_wobble/`** — Phoenix-authored substrate parallel
  to `vendor/wobble/` for physics. Includes
  :class:`CognitionDisagreementType` enum (7 classes), the
  `CognitionClassifier` Protocol with `GBMClassifier` /
  `LLMJudgeClassifier` / `HybridClassifier` impls (the P13-4 default),
  and a calibration eval framework. The full 200+-example labeled
  corpus + trained GBM model + 22 MB embedding artifact land as
  Adam's calibration data work.

### MCP-client mode (Step 6) — 13-D4 enforced

- **`phoenix.mcp`** — Phoenix-as-MCP-client subsystem with per-server
  admin registration. :class:`MCPServerSpec` rejects `'*'` in
  `allowed_tools` at construction. :class:`MCPServerRegistry` is
  JSON-file-persisted (atomic-write tmp + replace).
  `check_mcp_dispatch()` enforces **no TOFU, no empty-default-allows-all,
  no discovery-based auto-add**. Async :class:`MCPClient` wraps the
  `mcp` SDK (optional extra). Three admin endpoints under
  `/v1/admin/mcp-servers/...`.

### Streaming surface (Step 7)

- :class:`StreamingCognitionProvider` Protocol — `astream()` async
  iterator emitting `StreamTokenDelta` / `StreamToolCallStart` /
  `StreamFinal` events. `AnthropicProvider.astream()` ships;
  OpenAI/Google/LiteLLM streaming follows the same pattern in v1.1.x.
- WebSocket extension to `/v1/ws/tasks/{task_id}/stream` with an
  `events=` filter param; cognition `token.delta` events route through.
- **P13-6 resolved**: `HASH_ONLY` disposition suppresses `token.delta`
  emission (the prompt + raw deltas would be a privacy leak);
  `tool_call` + `tool_result` events still flow. Rate-cap configurable
  via `PHOENIX_STREAM_RATE_CAP` (default 100/sec).

### Privacy controls (Step 8) — 13-D2 enforced

- **Ledger schema v4** migration adds five columns to `ledger_entries`:
  `prompt_disposition` NOT NULL DEFAULT 'HASH_ONLY', `prompt_hash`,
  `prompt_verbatim`, `prompt_encrypted` BLOB, `cognition_provenance_json`.
- **`phoenix.ledger.prompt_disposition`** — canonicalization with
  sorted-keys JSON + whitespace normalization (cross-OS hash equality);
  SHA-256 hex hash. Metadata excluded from the hash so audit-field
  variation doesn't break replay verification.
- **`phoenix.ledger.encryption`** — `PromptEncryptor` Protocol +
  `NullPromptEncryptor` default (raises
  `EncryptedDispositionNotConfigured`). Column ships; KMS ceremony
  lands later.

### Permission registry + admin endpoints (Step 9)

- **`ActorPermissions` extended** with 7 new flags:
  `can_call_cognition` (default True), `can_call_mcp_server`,
  `can_register_mcp_server`, `can_store_prompt_verbatim`,
  `can_store_prompt_encrypted`, `can_store_raw_provider_body`,
  `can_receive_token_stream`. Bootstrap actors get all granted.
- **Safety gate stage 6b** — `verify_request(..., task_kind="cognition")`
  routes through cognition capability checks (skipping the
  frontier-physics check). Each opt-in raises `PermissionDenied` with
  the missing capability name when the gate is closed.
- **Three new admin endpoints**:
  - `POST /v1/identity/permissions/grant-prompt-verbatim` — updates
    registry + appends `PermissionGrantEntry` to the Omega Ledger.
  - `POST /v1/admin/budget/cognition-override` — cognition-specific
    budget bump with three new scope tokens.
  - `GET /v1/admin/audit/cognition-spend` — per-actor cognition spend
    aggregation over rolling window.

### Acceptance + closeout (Step 10)

- Three new `@pytest.mark.acceptance` tests added to the Phase 11
  acceptance battery: `test_cognition_panic_mode.py`,
  `test_mcp_server_panic_mode.py`, `test_long_window_replay_cognition.py`.
- Phoenix wheel size now ~520 KB (added cognition adapters + MCP
  client).

### Locked decisions (2026-05-18)

- **13-D1** — License stays Apache 2.0. Conditionally locked at
  draft-lock pending the frank-data root LICENSE declaration;
  resolved 2026-05-18 with the Apache 2.0 grant on the frank-data repo.
- **13-D2** — `HASH_ONLY` is the load-bearing default for prompt
  storage; `VERBATIM` + `ENCRYPTED_OPT_IN` are explicit opt-ins
  requiring admin grant.
- **13-D3** — Cognition classifier is independent of any specific
  model; the hybrid GBM + LLM-judge architecture (P13-4 default) is
  swappable via the `CognitionClassifier` Protocol.
- **13-D4** — MCP-client mode requires per-server admin
  registration. No TOFU, no empty-default-allows-all, no discovery
  auto-add, no `'*'` in `allowed_tools`.
- **13-D5** — Phase 13 lands as an additive substrate; the existing
  physics surface remains untouched.

### Honesty notes

- **Calibration data is bootstrap-only.** The classifier ships with 14
  hand-crafted examples — enough to exercise the Protocol surface,
  not enough to gate macro-F1 ≥ 0.70 in production. The full corpus
  + trained model land as Adam's data work; the build guide's gate
  is **not** load-bearing for Steps 6-10 (the privacy + permission
  layer is independent of classifier quality).
- **OpenAI / Google / LiteLLM streaming not yet implemented.** Only
  `AnthropicProvider.astream()` ships. The Protocol surface exists
  for the other three; the implementations are v1.1.x follow-up.
- **Encryption ceremony deferred.** Phase 13 ships the
  `prompt_encrypted` column + `PromptEncryptor` Protocol; the actual
  KMS integration + key-rotation ceremony lands later. The default
  `NullPromptEncryptor` raises rather than silently failing.
- **No `solve_entries` table.** The build guide references a
  `solve_entries` table; the actual hashchained provenance table is
  `ledger_entries`. The Phase 13 migration extends `ledger_entries`;
  the naming drift is documented in the migration's module docstring.

### Test surface

- 482 unit + cognition tests pass.
- 678 integration tests pass (31 skipped for Postgres absence).
- mypy --strict clean on 168 source files; ruff check clean.

### Architecture: v1.0 open-tension closeout (2026-05-20, doc-only)

Following Phase 13's merge, Adam reviewed and locked the remaining 14 v1.0 open
tensions catalogued in `PHOENIX_ARCHITECTURE_v1.md` Section 11. Open-tension
count after the closeout: **2** (both from v1.1 perception extension: 11.14.2,
11.14.6 — both correctly stay open until perception build-guide drafting).

Breakdown of the 14 v1.0 resolutions:

- **2 RESOLVED-and-shipped during v1:** 11.1.4 LoRA adapter validation suite
  (`phoenix/adapters/validator.py`, Phase 9); 11.2.1 provider equivalence
  registry (`phoenix/router/equivalence_registry.py`, Phase 4). Both shipped
  during the v1 build pipeline; the doc update reconciles the catalog with
  what's actually in code.
- **10 RESOLVED-as-locked-deferrals to v1.x (Phase 13 hindsight where
  applicable):** 11.1.1 error-bar combiner (keep quadrature), 11.1.2 MPS axis
  (roll into Axis 1 until MPS path ships), 11.1.3 adaptive-depth thresholds
  (keep Section 6.4 defaults), 11.2.3 multi-source vendoring (single-version
  through v1.x), 11.3.2 per-install rung table (bundled with 11.1.3), 11.4.1
  OS keychain attestation (hardening pass), 11.4.2 org permission inheritance
  (Phoenix-Cloud-driven), 11.4.3 org root key rotation (with 11.4.2), 11.5.3
  Prometheus endpoint (Cloud seam when demand emerges), 11.7.1 vendored module
  import paths (verbatim discipline locked; validated through Phase 13's
  `vendor/cognition_wobble/` addition).
- **1 RESOLVED-as-deprecated:** 11.8 translator handler set — grammar-token
  entry path was never built; structured-JSON + LoRA-natural-language are the
  two shipped entry points.
- **3 RESOLVED-as-out-of-scope-for-Section-11:** 11.6.1 Sanskrit memory
  composition (reference-client decision in the reference-client repo); 11.6.2
  reference-client license (Apache 2.0 mirrors 13-D1; further license
  discussion lives in the reference-client repo when it ships); 11.7.2
  launcher icon (reclassified as design-asset work).

Counts add to 16 (2 + 10 + 1 + 3): the 14 official open v1.0 tensions plus
the two previously-uncounted entries (11.7.2 cosmetic, 11.8 build-guide-
territory) that this round formally dispositions for accounting cleanliness.

The Section 11.13 cross-reference table is updated end-to-end; each marker
now shows `**RESOLVED v1**` or `**RESOLVED v1.1**` with the specific
disposition. No code shift; no test impact.

### Phase 13.x.6: encryption ceremony — age-based reference impl

Phase 13 Step 8 shipped the `prompt_encrypted` BLOB column +
`PromptEncryptor` Protocol with `NullPromptEncryptor` as the default.
Phase 13.x.6 ships the **real cryptographic implementation** backed
by [age](https://age-encryption.org) (X25519 + ChaCha20-Poly1305)
via the `pyrage` Rust-bindings Python wrapper.

**Files:**

- `phoenix/ledger/encryption_age.py` (new) — `AgePromptEncryptor`
  implementing the existing `PromptEncryptor` Protocol; typed errors
  `AgeEncryptionError` / `AgeKeyPermissionError` / `AgeKeyLoadError`
  / `AgeDecryptError`; `encryptor_from_default_layout()` convenience
  constructor reading the conventional Phoenix runtime keys directory.
- `phoenix/ledger/README.md` — full ceremony documentation: threat
  model, setup steps, lossless key-rotation flow via multi-recipient
  encryption, audit events, safety invariants.
- `pyproject.toml` — new optional extra `[encryption-age]` depending
  on `pyrage>=1.1,<2.0`. BSD-3-Clause / Apache-2.0 dual-licensed.
- `tests/cognition/test_encryption_age.py` (new) — 20 tests + 1 POSIX-
  only-skip: round-trip, multi-recipient rotation overlap, tamper
  detection, wrong-identity rejection, identity-file permission
  validation, key-load error paths, audit emission on encrypt /
  decrypt / failure, public attributes, default-layout convenience.

**Safety invariants enforced:**

- POSIX identity files MUST have mode `0o600`; loader refuses looser
  permissions with a clear error message including the `chmod 0600`
  fix command. No opt-out.
- Identity contents are NEVER logged. Audit events carry only 16-hex-
  char SHA-256 fingerprints of the public-key strings.
- Failed decrypts are NOT retried. A failed decrypt is structural
  (tampering, wrong key, or malformed ciphertext) — never transient.

**Audit events:**

- `cognition.prompt.encrypted` (success) — `recipient_fingerprints`,
  `plaintext_bytes`, `ciphertext_bytes`.
- `cognition.prompt.decrypted` (success) — `identity_fingerprint`.
- `cognition.prompt.encrypt.failed` / `cognition.prompt.decrypt.failed`
  — fingerprints + `error_type`.

All events land in the existing `phoenix.audit.get_emitter()` sink
(JSONL by default; OpenTelemetry when configured).

**NOT shipped at 13.x.6** (tracked as v1.1.x follow-ups):

- **Phase 13.x.7** — `phoenix admin generate-encryption-key` CLI +
  `POST /v1/admin/encryption/rotate-key` admin endpoint.
- **Phase 13.x.8** — per-actor key isolation.
- **v1.2.x** — `AwsKmsPromptEncryptor` / `GcpKmsPromptEncryptor` /
  `VaultPromptEncryptor` Protocol-conforming plugins for deployments
  needing tamper-evident external audit.

---

## [1.0.0rc1] — 2026-05-14

Phase 12 closes the v1 release-artifact surface. Phoenix's logical
surface stabilized at `1.0.0.dev12` (Phase 11); `1.0.0rc1` is the
release-candidate cut that ships **three distribution artifacts** per
architecture v1 Section 1 Decision 29:

### Three release artifacts

- **pip wheel + sdist** (`phoenix-middleware`). The wheel now
  correctly bundles the six vendored namespace packages (`synthesis`,
  `wobble`, `grammar`, `actor`, `omega`, `ml`) as siblings of
  `phoenix`, so `pip install phoenix-middleware` works in non-editable
  mode (prior phases relied on the sys.path injection that only fires
  in editable installs). Wheel size: ~500 KB. Sdist size: ~540 KB
  (includes `PHOENIX_ARCHITECTURE_v1.md` + `CHANGELOG.md`).

- **Docker image**. Multi-stage `python:3.12-slim` Dockerfile:
  builder stage fetches the nats-server v2.10.22 release with SHA256
  verification + builds the Phoenix wheel; runtime stage drops to a
  non-root `phoenix` user (UID 1000), copies the wheel + nats-server,
  exposes ports 8003 (Phoenix) + 4222 (NATS), `HEALTHCHECK` via httpx
  against `/v1/health`. Target image size: < 300 MB compressed.

- **Nuitka standalone binary** (Linux + Windows). `scripts/build_standalone.py`
  wraps Nuitka with the explicit `--include-package` flags Phoenix needs
  (FastAPI + Uvicorn + Pydantic + the vendored namespace packages
  whose import paths come from the runtime sys.path injection).
  Output: `dist/phoenix-<os>-<arch>(.exe)` -- a self-extracting onefile
  binary with the launcher + daemon + vendor tree.

### Launcher orchestration

`phoenix/launcher.py` is the Nuitka build target and the
`python -m phoenix` entry. Per Section 1 Decision 33, a solo Phoenix
install boots **two processes** (Phoenix daemon + NATS JetStream)
under one launcher. Per Section 11.3.3 RESOLVED, the launcher bundles
the daemon by default; `--external-daemon` + `--external-nats` are
the opt-outs for installs running the components separately.

Five CLI flags:

- `--port` / `--host` -- daemon-side bind (default `127.0.0.1:8003`).
- `--external-daemon` -- skip spawning the daemon; health-probe one
  reachable at `--host:--port` before opening the docs URL.
- `--external-nats` -- skip spawning NATS; the queue module reads
  `$PHOENIX_NATS_URL` (default `nats://127.0.0.1:4222`).
- `--no-browser` -- don't open the docs URL after boot.
- `--version` -- print version + exit 0.

Shutdown: `signal.SIGINT` + `signal.SIGTERM` handlers escalate
`terminate()` (5s grace) -> `kill()` for both NATS + daemon children.

### GitHub Actions CI matrix

`.github/workflows/ci.yml` and `.github/workflows/release.yml` ship
the always-on CI + tag-triggered release pipelines. Matrix:
`ubuntu-latest + windows-latest x py3.11/3.12/3.13` (macOS deferred
to v1.1). Five jobs:

- `lint` -- ruff check + ruff format + mypy strict + shellcheck on
  the bash launchers.
- `tests` -- pytest unit + integration + acceptance (`-m acceptance`
  collects the Section 10.7 panic-mode + long-window-replay battery
  across all OS x Python combinations).
- `build-wheel` -- `python -m build`, smoke-installs in a fresh
  venv, verifies all six vendored namespace imports + the `phoenix`
  console script + `phoenix --version`.
- `build-docker` -- `docker build`, container healthcheck within
  60s, image size < 300 MB sentinel.
- `build-standalone` (matrix: ubuntu + windows) -- install nuitka,
  run `scripts/build_standalone.py`, smoke `--version` on the
  produced binary.

Release workflow additionally pushes the Docker image to
`ghcr.io/nah414/phoenix:<tag>` + `:latest` (smoke-tested before
push) and attaches all artifacts to the GitHub Release via
`softprops/action-gh-release`.

### Distribution + reproducibility docs

- `docs/distribution/README.md` -- three-artifact overview.
- `docs/distribution/install.md` -- step-by-step install for each
  artifact (with pip extras: `[postgres]`, `[nats]`, `[otel]`, `[mcp]`;
  Docker volume mount for state persistence; SmartScreen + glibc-floor
  + NATS-not-bundled caveats on the standalone binary).
- `docs/distribution/run.md` -- runtime topology, port/path tables,
  `--external-daemon` + `--external-nats` semantics, healthcheck
  endpoints, log locations, configuration-file precedence.
- `docs/reproducibility/README.md` -- the cloud-shots-recorded
  asterisk from Section 11 RESOLVED: cloud-quantum shots are
  intrinsically nondeterministic; Phoenix records them in the Omega
  Ledger so post-shot pipeline reproduces bit-exactly under strict /
  replay mode. The `cloud_shots_recorded` provenance field is the
  consumer's explicit hint about which guarantee applies.

### Distribution acceptance battery

`tests/distribution/` under the new `@pytest.mark.distribution`
marker (registered alongside `smoke` + `acceptance`). 18 tests:

- `test_wheel_install.py` (6 tests) -- builds wheel + sdist, installs
  in a fresh venv, asserts wheel + sdist size < 2 MB, asserts all six
  vendored namespace packages resolve from site-packages (not the dev
  tree), asserts the `phoenix` console-script entry-point works.
- `test_docker_smoke.py` (9 tests) -- Dockerfile + .dockerignore
  shape sanity (python:slim base, non-root user, NATS checksum
  verification, EXPOSE 8003 4222, HEALTHCHECK on /v1/health, .dockerignore
  excludes caches + tests). Optional `docker buildx --check` syntax
  validation when docker is locally installed.
- `test_standalone_binary.py` (5 tests) -- build_standalone.py
  loads + constructs the right Nuitka invocation (all six vendored
  namespace packages explicitly --include-package'd). Optional full
  Nuitka compile + binary smoke when Nuitka is locally installed.

Default `pytest tests/` collects but the build steps in CI exercise
the actual artifact builds independently.

### Locked deferrals to v1.0 release prep + v1.1

- **Code signing.** v1.0.rc ships unsigned artifacts; SmartScreen on
  Windows shows the "Unrecognized app" warning on first launch.
  Certificate provisioning + signing pipelines land in v1.0 release
  prep as a separate workstream.
- **macOS standalone binary.** Deferred to v1.1; Apple-Silicon native
  build chain doubles the CI matrix complexity for minimal initial
  user base. Community can contribute via the Apache 2.0 surface.
- **Remote-daemon CLI.** `--external-daemon` is local-only in Phase
  12; the CLI talks to localhost. v1.1 will let `phoenix --rest-url
  https://prod.phoenix.example.com task submit ...` work, turning
  the CLI itself into a "remote" client.
- **NATS bundling in standalone binary.** The Linux + Windows
  standalone binaries do NOT bundle nats-server in v1.0.rc; users
  install nats-server separately if they want the two-process model
  (`winget install NATSAuthors.NATSServer` on Windows, static binary
  from GitHub release on Linux). Bundling lands in v1.0 final once
  signing is in place.

### Version bump

`1.0.0.dev12` -> `1.0.0rc1` (PEP 440 canonical spelling: no period
before `rc`). Bumped in lockstep:

- `pyproject.toml` (`version`, `description`)
- `phoenix/_internal/version.py` (`__version__`)
- `phoenix/state/sqlite_backend.py` (`_DEFAULT_PHOENIX_RELEASE`)
- `phoenix/state/postgres_backend.py` (`_DEFAULT_PHOENIX_RELEASE`)
- `phoenix/verification/drift_detector.py` (`phoenix_release` default)
- `tests/unit/test_smoke.py` (version assertions)
- `tests/integration/test_health.py` (`phoenix_version` + OpenAPI version)
- `tests/integration/test_adapters_step6.py` (mocked response body)
- `README.md` (status paragraph)
- `CHANGELOG.md` (this entry)

`vendor/VENDOR_VERSION.txt` `phoenix_release` field stays at
`1.0.0.dev6` (it represents the Phoenix release that LAST ran the
vendor sync; vendoring is frozen at sync time, not bumped per release).

---

## [1.0.0.dev12] — 2026-05-14

Phase 11 closes v1: compositional acceptance tests + per-directory READMEs.

### Section 10.7 acceptance battery (`@pytest.mark.acceptance`)

Five canonical tests now green under `pytest -m acceptance`:

- **Three panic-mode isolation tests** (Steps 2-4): each fail-closed
  branch exercised in isolation. NATS subsystem unreachable, state
  backend unreachable, drift detector unreachable -- each surfaces
  the typed `QueueUnavailable` / `StateBackendUnavailable` /
  `DriftStateUnavailable` exception cleanly through `pipeline.solve`
  and the front door without partial-state corruption.
- **Combined three-failure panic test** (Step 5): all three
  subsystems simultaneously down. Phoenix refuses-to-start cleanly
  with the kill-switch posture rather than silently degrading. The
  load-bearing acceptance contract from Section 7.6.
- **Long-window bit-exact replay test** (Steps 6-7): hand-built
  `SolveEntry` fixture played back through `pipeline.replay` after
  monkey-patched system clock advances 180 days; the replayed
  `Result.value` matches the recorded value bit-exactly. The
  acceptance contract for Section 1 Decision 15's hashchained ledger
  surviving real time drift.

### Typed-error audit + panic-mode harness scaffolding (Step 1)

Inventoried every fail-closed boundary in Phoenix per OPEN-7 LOCKED:
created missing typed exceptions (`QueueUnavailable`,
`StateBackendUnavailable`) where prior phases relied on generic
`Exception`. The panic-mode test harness at `tests/acceptance/`
exercises each boundary via dependency injection (no global monkey-
patching beyond the clock helper).

### Per-directory READMEs (Steps 8-9)

OPEN-4 LOCKED ("rich-for-top, minimal-for-leaves") audit + fill:

- Every directory under `phoenix/` and `vendor/` has a README.
- Top-level `phoenix/README.md` ships the request-flow ASCII trace
  from `POST /v1/tasks` down through audit + ledger; links to each
  subsystem's README.
- Top-level `vendor/README.md` already documented the vendoring
  contract (Section 10.2 + 11.7.1); per-vendored-package READMEs
  filled for `actor/`, `ml/`, `omega/`, `synthesis/`, and
  `synthesis/quantum/`.
- Repo-root `README.md` rolling ship line bumped to "All v1 phases
  shipped 2026-05-06 → 2026-05-14".

### Version + manifest

- `pyproject.toml`, `phoenix/_internal/version.py`: `1.0.0.dev11` ->
  `1.0.0.dev12`.
- `_DEFAULT_PHOENIX_RELEASE` constants in `sqlite_backend.py`,
  `postgres_backend.py`, and the drift detector's `phoenix_release`
  default kwarg all bumped in lockstep so newly-initialized state
  backends + freshly-spawned drift detectors stamp the right
  release.
- Test version assertions updated: `tests/unit/test_smoke.py`,
  `tests/integration/test_health.py`,
  `tests/integration/test_adapters_step6.py`.

### Out of scope for Phase 11 (locked at draft time)

- Distribution / packaging beyond `pip install phoenix-middleware`
  (PyInstaller binaries, container images, system-package descriptors)
  -- OPEN-5 LOCKED deferral to Phase 12.
- Repo-root README status-table backfill for Phases 6b-10. Phase 11's
  acceptance contract is the ship-line + Phase 11 row; the per-phase
  rows accumulated docs debt while Phase 6b-10 shipped which v1.1 can
  close.

### Refs

- `PHOENIX_ARCHITECTURE_v1.md` Section 7.6 (fail-closed posture),
  Section 10.7 (acceptance criteria), Section 1 Decision 15 (ledger
  replay).
- `BUILDGUIDE_phoenix_v1_phase11_acceptance_composition.md` (all 10
  steps + 7 OPEN items locked at draft).

---

## [1.0.0.dev11] — 2026-05-13

Phase 10 shipped — **cost-ceiling enforcement + Phoenix Cloud
abstraction seams** per architecture v1 Section 4.7 + 10.3.1. Two
tightly-coupled architectural pieces close the biggest remaining
gap in v1's §10.7 acceptance criteria.

1. **Cost-ceiling enforcement (§4.7).** Phase 4 shipped the
   `cost_ceiling_usd` field + `CostCeilingExceeded` error + Stage 2
   per-solve filter. Phase 10 adds the 24h-window accumulators
   (per-actor + per-org), the post-solve accounting writer, the
   verification gate's pre-promotion check with
   `budget_bound_skipped_axis` provenance, and the
   `POST /v1/admin/budget/override` admin endpoint.

2. **Phoenix Cloud abstraction seams (§10.3.1).** Three thin
   `typing.Protocol` definitions (`HttpAuthExtractor`,
   `AuditLogExporter`, `JobBudgetController`) plus a generic
   `CloudSeams` name-keyed registry plus local default
   implementations. The default `LocalJobBudgetController` IS the
   v1 cost-ceiling engine — Phoenix Cloud (a future product) swaps
   in tenant-aware impls via one `register("budget", ...)` call
   with zero changes to Phoenix core.

The two pieces compose because Phase 10's cost-ceiling code lives
**behind the seam from day one**, not retrofitted later.

### Locked scope decisions (2026-05-13)

The six open items surfaced during BUILDGUIDE authoring were
locked at draft time with autonomous-execution defaults (per
Adam's 2026-05-13 direction: "keep building for a while before we
create a PR"). Recorded back into the BUILDGUIDE; summarized here:

1. **OPEN-1 LOCKED**: new migration file
   `phase10_cost_ledger.py` (not extending Phase 6b's initial) so
   replay against a Phase-6b-era backend stays unambiguous.
2. **OPEN-2 LOCKED**: `record_solve_cost` is last-write-wins
   on `request_id` via `INSERT ... ON CONFLICT DO UPDATE`. Single
   writer (Orchestrate post-solve); duplicate writes only on
   retry, where last write IS the authoritative outcome.
3. **OPEN-3 LOCKED**: `budget_bound_skipped_axis` lives on
   `VerificationProvenance` (verification-gate decision, not
   routing-layer). Mixing it onto `RoutingProvenance` would muddy
   the layer boundary.
4. **OPEN-4 LOCKED**: admin override scope = three explicit
   canonical values (`per_solve` / `per_actor_24h` /
   `per_org_24h`). An admin override at the per-org level is
   qualitatively different from a per-actor one; the audit log
   needs to distinguish them.
5. **OPEN-5 LOCKED**: org_id resolution = `actor.org_id` if
   present, else `None`. Solo developers (no org_id) get no
   per-org enforcement; Phoenix Cloud will populate org_id on
   the Actor payload and exercise the per-org path.
6. **OPEN-6 LOCKED**: new `BudgetOverrideEntry` ledger kind
   (distinct from Phase 8's `OverrideByOperatorEntry` for
   HUMAN_REVIEW solve disposition). Sharing a kind would obscure
   the audit story.

### What landed (commits efd4a9f → 9b6b706 → 0722784 → e8a35c3 → d723751 → c9e49fa → ecaec90 → b672620 → 3e58957 → 1eae092 → this commit, 11 commits)

- **BUILDGUIDE drafted + locked (`efd4a9f`).** Six open items
  surfaced and resolved at draft time.
- **Step 1 (`9b6b706`) — cloud_seams Protocol shells + registry.**
  Three `typing.Protocol` definitions, `BudgetDecision` frozen
  dataclass, generic `CloudSeams` name-keyed registry (not
  hardcoded three slots), `UnknownSeam(KeyError)`, module-level
  singleton via `get_seams()` / `reset_seams()`. Step 1 stubs
  registered for all three names; replaced at Steps 4 + 9.
- **Step 2 (`0722784`) — solve_cost_ledger 24h-window + budget_overrides.**
  New migration `phase10_cost_ledger.py` (VERSION 3) extends the
  Phase 6b `solve_cost_ledger` with `org_id` / `reproducibility_mode` /
  `provenance_json` columns and creates the `budget_overrides`
  table. Four new `StateBackend` methods:
  `record_solve_cost` (idempotent on request_id),
  `query_actor_24h_spend` / `query_org_24h_spend`,
  `insert_budget_override` / `list_active_budget_overrides`.
- **Step 3 (`e8a35c3`) — cost-ceiling defaults + resolver.**
  `phoenix/safety/cost_ceilings.py` implements the §4.7 default
  ladder (per-solve $5/$25/$50, per-actor $50/$500/None,
  per-org $2000) plus env-var overrides (`$PHOENIX_PER_SOLVE_CEILING_USD`
  etc.) with invalid-value tolerance.
- **Step 4 (`d723751`) — LocalJobBudgetController default impl.**
  Composes cost_ceilings.resolve_ceilings + state-backend 24h-window
  queries + admin-override list. Stateless; every call resolves
  fresh. `_apply_override` helper enforces §4.7's "override only
  raises" rule via `max(base, override)`.
- **Step 5 (`c9e49fa`) — Router Stage 2 consults the seam.**
  When `task.actor` is set, Router calls
  `cloud_seams.get("budget").check_solve_budget` before per-
  candidate filtering. Seam denial → `CostCeilingExceeded` with
  rationale embedded; seam allowance → effective ceiling =
  min(user, seam). Backward compat: `task.actor=None` skips the
  seam (existing fixtures + tier-1 stay green). Defense-in-depth:
  buggy seam doesn't take down the Router.
- **Step 6 (`ecaec90`) — post-solve accounting hook.**
  `pipeline._record_post_solve_cost` runs after every solve;
  computes actual cost via `router.pricing.estimate_cost_usd` and
  calls `seam.record_solve_cost`. Fire-and-forget: any exception
  swallowed + logged. Skipped when `task.actor=None` or no
  orchestrate provenance.
- **Step 7 (`b672620`) — verification gate pre-promotion check.**
  `_axis_3_would_exceed_ceiling` helper estimates the second
  routing request's cost; if `primary_cost + cheapest_alt > per_solve_ceiling`,
  Axis 3 is skipped, `budget_bound=True`,
  `budget_bound_skipped_axis="cross_provider_axis"`. The user
  still gets their primary-only Result with a clear marker.
- **Step 8 (`3e58957`) — POST /v1/admin/budget/override.**
  New `BudgetOverrideEntry` ledger kind (locked OPEN-6).
  Validates: scope in canonical set, `expires_at > now`,
  `new_ceiling_usd > 0` (override only raises per §4.7).
  Appends ledger entry + writes state-backend row + emits
  `admin.budget.override.success` audit. Non-admin gets 403
  before the registry write.
- **Step 9 (`1eae092`) — LocalHttpAuthExtractor + LocalAuditLogExporter +
  acceptance test.** Real auth/audit seam impls replace the Step 1
  stubs. `tests/integration/test_cloud_seams.py` is the §10.3.1
  acceptance test: 5 tests proving (1) synthesized Actor flows
  through safety gate, (2) audit events fan out to BOTH default
  JSONL + mock cloud sink, (3) tenant-scoped budget denial
  surfaces as CostCeilingExceeded with no tenant-state leak,
  (4) extension discipline accepts `canonical_library` without
  breaking core, (5) v1 defaults satisfy all three Protocols.
- **Step 10 (this commit) — Version bump + CHANGELOG.** Bumps
  `1.0.0.dev10 → 1.0.0.dev11` in pyproject + version.py + the
  three `_DEFAULT_PHOENIX_RELEASE` call sites + drift detector
  default + test version assertions.

### Test coverage

Phase 10 adds 99 tests bringing the suite from 636 (after Phase 9)
to 735 passing + 39 skipped. New test files:

- `test_cloud_seams_step1.py` (19) — Protocol shells + registry
  + singleton lifecycle.
- `test_cost_ledger_step2.py` (15) — record + query round trip,
  last-write-wins, 24h-window semantics, budget-override CRUD.
- `test_cost_ceilings_step3.py` (19) — Section 4.7 default
  ladder, unknown-mode/tier fallbacks, env-var overrides,
  invalid-value tolerance.
- `test_local_budget_controller_step4.py` (20) — happy path,
  per-solve / per-actor-24h / per-org-24h denials, admin tier,
  override only-raises, expired-override invisible.
- `test_router_budget_seam_step5.py` (5) — backward compat,
  per-actor / per-org denials surface via Router, seam-narrows-
  ceiling, buggy seam fault tolerance.
- `test_post_solve_accounting_step6.py` (6) — hook records via
  seam, skips when actor=None or no provenance, buggy seam
  swallowed, e2e through pipeline.solve.
- `test_gate_budget_check_step7.py` (6) — VerificationProvenance
  field + `_axis_3_would_exceed_ceiling` predicate edges.
- `test_admin_budget_override_step8.py` (11) — happy path,
  state-backend row, ledger entry, validation (422 + 400),
  non-admin 403, e2e override-raises-ceiling.
- `test_cloud_seams.py` (5) — §10.3.1 acceptance: 3 seam compose
  tests + extension discipline + Protocol satisfaction regression.

### Limitations explicitly documented

- **Compositional fail-closed test ("panic mode")**: §10.7
  acceptance item; not yet shipped. Phase 11 target.
- **Long-window replay test**: §10.7 acceptance item; not yet
  shipped. Phase 11 target.
- **Distribution artifacts** (pip wheel, Docker image, Nuitka
  binary): release-time work; v1 release candidate.
- **Per-directory READMEs**: §10.7 acceptance item; Phase 11
  docs pass.
- **`phoenix admin pricing-update` CLI**: §11.2.2 disposition
  defers to v1.x. Phase 10 surfaces stale pricing via the
  Result envelope's existing soft-warn path.

---

## [1.0.0.dev10] — 2026-05-12

Phase 9 shipped — **LoRA adapter hot-swap interface + CLI + MCP**
per architecture v1 Section 2.7 + 3.5 + 5.4 + 5.5. Three new
surfaces land on top of the Phase 8 substrate:

1. **LoRA adapter subsystem** — Protocol + reference identity
   adapter + subprocess sandbox + in-process registry + inference-
   time round-trip validator + loader. POST/GET/DELETE
   `/v1/adapters` is the public surface; `POST /v1/admin/adapters/
   {id}/force-revalidate` and `GET .../round-trip-history` are the
   admin surfaces (Phase 8 Step 9's 501 stub is now a real
   handler).
2. **`phoenix` CLI** — `pyproject.toml`'s console-script entry
   wires up. Eight command groups: `task`, `lora`, `identity`,
   `providers` (Step 7) and `audit`, `calibration`, `admin`
   (Step 8), plus `health` (Step 6 smoke) and `mcp serve`
   (Step 9). httpx is now a main dependency.
3. **MCP server** — `phoenix mcp serve` boots an MCP (Model
   Context Protocol) server on stdio per Section 5.5 +
   locked OPEN-3 + OPEN-4. Eight canonical task-lifecycle tools
   bridge IDE-integrated clients (Claude Code, Cursor, Cline)
   into the Phoenix REST surface.

Plus a new `POST /v1/identity/enroll` endpoint (admin-only) that
writes ActorPermissions through the registry and appends an
EnrollmentEntry to the Omega Ledger.

### Locked scope decisions (2026-05-12)

The six open items surfaced during BUILDGUIDE authoring were
locked on 2026-05-12 after Adam reviewed recommendations. Recorded
back into the BUILDGUIDE; summarized here:

1. **OPEN-1 LOCKED: Reference adapter = identity adapter shipped
   as built-in.** `phoenix/adapters/identity_adapter.py` is a
   weights-free echo adapter usable by tests AND by client
   integrators as a starting template.
2. **OPEN-2 LOCKED: Sandbox isolation = subprocess + timeout +
   restricted env + per-call tempdir.** Middle ground; catches the
   80% case (runaway adapters that loop forever) without claiming
   OS-level ACL guarantees Phoenix v1 can't deliver portably.
3. **OPEN-3 LOCKED: MCP transport = stdio only in Phase 9.**
   Covers Claude Code + Cursor + Cline. HTTP+SSE deferred to v1.x.
4. **OPEN-4 LOCKED: MCP SDK = official Anthropic `mcp` SDK.**
   Stable, well-supported. `pyproject.toml` adds `[mcp]` optional
   extra.
5. **OPEN-5 LOCKED: CLI HTTP client = httpx.** Matches FastAPI's
   TestClient internals so the CLI shares one client surface with
   tests. Moved from dev-only to main deps.
6. **OPEN-6 LOCKED: MCP tools = 8 canonical task-lifecycle tools.**
   `phoenix_task_submit / get / replay`, `phoenix_provenance_get`,
   `phoenix_providers_list`, `phoenix_calibration_status`,
   `phoenix_health`, `phoenix_audit_verify`. Adapter / kill-switch
   / enroll deferred to v1.x.

### What landed (commits 4c1c265 → b3b8d9a → f800f8a → this commit, 12 commits)

- **BUILDGUIDE drafted (`4c1c265`) + locked (`b3b8d9a`).** Six
  open items surfaced and resolved at session start.
- **Step 1 (`f800f8a`) — LoRA Protocol + sandbox + identity adapter +
  errors.** `phoenix/adapters/protocol.py` (@runtime_checkable
  Protocol), `sandbox.py` (medium-isolation subprocess sandbox
  with timeout + restricted env + tempdir per locked OPEN-2),
  `identity_adapter.py` (reference echo adapter with constant
  SHA-256 fingerprint per OPEN-1), `errors.py` (5 typed errors
  mapping to HTTP 503/504/412/404/409).
- **Step 2 (`1bb9ace`) — Adapter loader + validator + in-process
  registry.** Thread-safe (RLock) `AdapterRegistry` singleton +
  per-adapter `ValidationHistoryEntry` ring buffer (cap 50).
  `run_round_trip_validation` drives 5 canonical inputs (ASCII,
  whitespace, unicode); always returns an entry (loader checks
  `passed` and raises). `load_adapter(spec)` is the
  spec→import→validate→register chokepoint; file-path specs
  raise NotImplementedError (v1.x).
- **Step 3 (`cdf3476`) — REST adapter endpoints.** POST/GET/
  DELETE `/v1/adapters` wired through the 9-stage safety gate.
  POST/DELETE require `can_load_adapter` / `can_unload_adapter`;
  GET is open. AdapterError family maps to HTTP per Section 2.7.
- **Step 4 (`26fbd79`) — Force-revalidate filler + round-trip-
  history.** Phase 8 501 stub becomes a real handler that drives
  the validator + appends to history. `GET /v1/admin/adapters/
  {id}/round-trip-history` returns the per-adapter ring snapshot.
  Auth chain runs BEFORE registry lookup (prevents "adapter
  exists" leakage via HTTP code).
- **Step 5 (`64a5531`) — `POST /v1/identity/enroll`.** Admin-only
  enrollment of new actor permissions. Writes through the
  permissions registry, appends an `EnrollmentEntry` to the Omega
  Ledger (operator history matters; idempotent on actor_name
  always appends a fresh ledger entry). Cost: `identity_enroll`
  (5 tokens). Validates actor_name regex + rate_limit_tier.
- **Step 6 (`d0ce87f`) — CLI scaffold.** `phoenix/cli/
  config_loader.py` (YAML + env-var overrides, ConfigError on
  malformed), `output_formats.py` (json / text / table / auto
  with TTY detection), `http_client.py` (httpx wrapper with
  actor signing + CLIHTTPError mapping), `entry.py` (argparse
  dispatcher with global flags + group-based subcommand routing).
  `phoenix health` is the Step-6 end-to-end smoke command.
- **Step 7 (`1515457`) — CLI task / lora / identity / providers
  groups.** `phoenix/cli/commands/_shared.py` (spec parsing,
  payload printing, per-user task cache helpers), plus four
  command modules. `task submit` caches the Result envelope so
  `task get` surfaces it offline (Phase 9 v1 has no daemon GET
  endpoint). `identity enroll --permission key=value` with
  boolean coercion. `providers list` routes to
  `/v1/admin/providers/health-history` (the only public read
  surface for the provider registry in v1).
- **Step 8 (`5f6bdb8`) — CLI audit / calibration / admin
  groups.** `audit tail / verify`, `calibration status / run`,
  `admin kill-switch engage|release|status`, `admin health /
  governor / budget`, `admin override <task-id>
  --disposition --reason`. Argparse enforces `--reason` and
  `--disposition` BEFORE dispatch.
- **Step 9 (`00fcad4`) — MCP server + 8 v1 tools.** FastMCP wiring
  (`phoenix/mcp/server.py`) + pure-function tool implementations
  (`tools.py`) registered with typed signatures FastMCP can
  introspect. `phoenix mcp serve` boots stdio transport. The
  `mcp` SDK is an optional `[mcp]` extra so installs without
  IDE integration stay lean. CLIHTTPError translated to an
  in-payload `{error, status_code, body}` blob.
- **Step 10 (this commit) — Version bump + CHANGELOG.** Bumps
  `1.0.0.dev9 → 1.0.0.dev10` in pyproject + version.py + the
  three `_DEFAULT_PHOENIX_RELEASE` call sites + drift detector
  default + test version assertions.

### Test coverage

Phase 9 adds 162 tests bringing the suite from 521 (after Phase 8)
to 636 passing + 39 skipped. New test files:

- `test_adapters_step1.py` (22) — Protocol shape, identity
  round-trip, sandbox timeout / env / tempdir, error family.
- `test_adapters_step2.py` (31) — Registry CRUD, history ring,
  thread-safety, validator pass/fail, loader spec resolution.
- `test_adapters_step3.py` (17) — REST adapter endpoints (POST/
  GET/DELETE), error paths, permission gating, OpenAPI shape.
- `test_admin_adapters.py` (12, rewritten) — force-revalidate
  happy + broken paths, history, permission gating before
  lookup, OpenAPI advertising.
- `test_adapters_step5.py` (16) — Enrollment endpoint happy +
  idempotency + ledger EnrollmentEntry shape + permission gate
  + validation + audit emit.
- `test_adapters_step6.py` (33) — Config loader edges, output
  formats, http_client wiring, main dispatcher exit codes.
- `test_adapters_step7.py` (19) — task / lora / identity /
  providers groups against the in-process FastAPI app.
- `test_adapters_step8.py` (10) — audit / calibration / admin
  groups + kill-switch round trip + argparse required flags.
- `test_adapters_step9.py` (13) — 8 tool implementations,
  FastMCP wiring, end-to-end `call_tool('phoenix_health')`.

### Limitations explicitly documented

- **Real LoRA weights**: not shipped. Phase 9 ships the
  capability (Protocol + sandbox + validator + REST + admin);
  users bring their own weights per Decision 8.
- **WebSocket streaming in the CLI**: `phoenix task stream`
  prints the `ws://...` URL + token-mint hint rather than
  embedding a WS client (httpx doesn't ship sync WS support).
- **File-path adapter specs**: 501 from the REST surface + a
  clear NotImplementedError from the loader. v1.x can layer
  filesystem-discovery on top of the existing spec format
  without breaking the contract.
- **MCP HTTP+SSE transport**: deferred to v1.x per OPEN-3.
  Phase 9 ships stdio only — covers Claude Code / Cursor /
  Cline (the 80% case).

---

## [1.0.0.dev9] — 2026-05-12

Phase 8 shipped — the **admin dev-ops backdoor** under `/v1/admin/...`
per architecture v1 Section 8. Phoenix is now operable without paging
the developer: admins with `is_admin=True` can inspect every subsystem,
engage/release the kill switch, force-cycle the drift detector,
override HUMAN_REVIEW solves, manually quarantine + restore providers,
query the audit log with filter composition, and pull a full ledger
integrity report — all from privileged HTTP endpoints with audit
emit per call and ledger entries for the two architecture-listed
mutations (kill switch + HUMAN_REVIEW override).

The architecturally consequential piece is the verification gate's
auto-enqueue: DEGRADED solves now land in `pending_review_queue`
automatically (locked OPEN-3). The pending-review queue is no longer
dead code Phase 6b shipped without a writer — admin sees real data.

### Locked scope decisions (2026-05-11)

The six open items surfaced during BUILDGUIDE authoring were locked
on 2026-05-11 after Adam reviewed recommendations. Recorded back into
the BUILDGUIDE; summarized here:

1. **OPEN-1 LOCKED: Admin mount path = APIRouter + include_router**
   with `/v1/admin` prefix. One FastAPI app, one OpenAPI schema with
   an `Admin`-tagged section, modular code (each handler group
   registers its own APIRouter that the parent collects).
2. **OPEN-2 LOCKED: `/v1/admin/governor` = psutil-based v1 minimum.**
   CPU%, RAM%, disk%, process RSS populated; GPU/VRAM/NPU/thermal
   fields return `None` until Phase 9+ when the cloud-GPU adapter
   layer matures.
3. **OPEN-3 LOCKED: HUMAN_REVIEW enqueue WIRED in Phase 8.** The
   verification gate's DEGRADED + DEGRADED_BUDGET_BOUND classifications
   now enqueue a `pending_review_queue` row automatically. The user
   still gets their result synchronously; the queue is informational
   v1 (v1.x can promote to hold-for-review semantics).
4. **OPEN-4 LOCKED: Router decision retention = in-process ring buffer.**
   Default 1000 entries, configurable via
   `$PHOENIX_ROUTER_DECISION_LOG_SIZE`. Survives daemon lifetime only;
   audit log captures the canonical durable record via Phase 7 ledger
   entries' `routing_provenance` block.
5. **OPEN-5 LOCKED: Manual quarantine = audit-event only, no ledger
   entry.** Provider state mutations are operational, not audit-grade.
   Section 8.4 reserves ledger entries for kill switch + HUMAN_REVIEW
   override only.
6. **OPEN-6 LOCKED: Adapter `force-revalidate` = 501 stub in Phase 8.**
   Endpoint registered in OpenAPI; handler returns 501 with a "Phase 9"
   message. `round-trip-history` deferred entirely to Phase 9.

### What landed (commits b3599c1 → 6e3daf6 → c03fd3b → this commit, 12 commits)

- **BUILDGUIDE drafted (`b3599c1`) + locked (`03b2702`).** Six open
  items surfaced and resolved at session start.
- **Step 1 (`6e3daf6`) — Admin scaffold + auth + audit decorator.**
  `phoenix/admin/auth.py` (require_admin privilege check),
  `audit_decorator.py` (emit_admin_audit with fire-and-forget contract),
  `errors.py` (5 typed errors with HTTP code mappings),
  `router.py` (APIRouter aggregator + sanity-check `/v1/admin/_ping`).
  Cost catalogue gains `admin.ping` / `admin.read` / `admin.mutate`.
  Pattern: every admin handler runs the 4-layer composition
  `extract_or_bootstrap → verify_request → require_admin →
  emit_admin_audit`.
- **Step 2 (`9e7b504`) — Kill switch engage/release/status.**
  Three endpoints + a `skip_kill_switch_check` flag on
  `verify_request` so admin endpoints stay callable while the
  switch is engaged. Both mutations append a `KillSwitchEntry`
  ledger entry with `transition` discriminator + `engaged_at_unix`
  cross-reference.
- **Step 3 (`9454656`) — Health + governor + inference-status + budget.**
  Four read-only inspection endpoints. `health/detailed` rolls up
  every subsystem with defensive sub-call wrapping (one broken
  subsystem doesn't take down the rollup). `governor` ships
  psutil-based v1 minimum. `inference-status` is a Phase 9
  placeholder shape. `budget` reads `RateLimiter.snapshot()` with
  non-mutating refill projection.
- **Step 4 (`cf1f45d`) — Calibration drill-down + force-cycle.**
  `detail` exposes per-checker state. `history` filters audit_events
  to `drift.*`. `run` body `{wait: bool}` toggles synchronous vs
  daemon-thread execution; non-blocking lock returns 409
  `CalibrationRunInProgress` on concurrent attempts.
- **Step 5 (`347d3b1`) — Verification + pending-review override
  (OPEN-3 WIRING).** Verification gate auto-enqueues DEGRADED +
  DEGRADED_BUDGET_BOUND solves into `pending_review_queue`.
  `tasks-pending-review` lists them; `override/{task_id}` requires
  `can_override_human_review` AND `is_admin`, validates the
  disposition string, appends an `OverrideByOperatorEntry` ledger
  entry. `rung-distribution` histograms `initial_rung` over the
  audit-log window.
- **Step 6 (`c9216db`) — Router decision ring buffer + provider
  health history.** Module-level `collections.deque` (size
  configurable via `$PHOENIX_ROUTER_DECISION_LOG_SIZE`) appended
  to on every `Router.decide()` call. `health-history` filters
  audit events to `provider.health.*`.
- **Step 7 (`02ff88b`) — Provider manual quarantine + restore +
  audit mirror.** Two mutation endpoints (cost `admin.mutate`,
  24h duration cap on quarantine, 404 on unknown provider, 403
  for non-admin). `emit_admin_audit` extended to ALSO write to
  `state_backend.audit_events` so admin actions surface in
  history endpoints (Phase 7's audit emitter was JSONL-only).
- **Step 8 (`6a2e81c`) — Audit replay + ledger integrity report.**
  `audit/replay` accepts filter composition (event_type_prefix,
  actor_id, layer, since/until). `ledger/integrity-report` returns
  both checks + entry_kind histogram + chain_head pointer with
  age_seconds.
- **Step 9 (`c03fd3b`) — Adapter force-revalidate 501 stub.**
  Endpoint registered with full auth chain; success path returns
  501 with "Phase 9" message. `round-trip-history` deferred
  entirely.
- **Step 10 (this commit) — Version bump + CHANGELOG.** Bumps
  `1.0.0.dev8 → 1.0.0.dev9` in pyproject + version.py + the three
  `_DEFAULT_PHOENIX_RELEASE` call sites + drift detector default
  + test version assertions.

### Endpoint surface

Phase 8 ships **15 read + 7 mutation = 22 endpoints** under `/v1/admin/`,
matching the full architecture §8.2 surface:

Read: `_ping`, `health/detailed`, `governor`, `inference-status`,
`budget`, `calibration/detail`, `calibration/history`,
`router/decisions`, `providers/health-history`,
`tasks-pending-review`, `verification/rung-distribution`,
`kill-switch/status`, `audit/replay`, `ledger/integrity-report`.

Mutation: `kill-switch/engage`, `kill-switch/release`,
`calibration/run`, `tasks-pending-review/{task_id}/override`,
`providers/{provider_id}/manual-quarantine`,
`providers/{provider_id}/manual-restore`,
`adapters/{adapter_id}/force-revalidate` (501 stub).

Of the seven mutations, ONLY kill switch + HUMAN_REVIEW override
append Omega Ledger entries per locked OPEN-5 and architecture §8.4.
All seven emit top-priority audit events.

### Tests

- 512 passed, 0 skipped, 0 failed with full infrastructure
  (Postgres on 5432 + NATS on 24222).
- Phase 8 additions vs Phase 7's 402: +110 tests across scaffold (8),
  kill switch (15), health/governor/inference/budget (17),
  calibration (16), verification/override (15), router/health-
  history (12), provider mutate (12), audit replay/integrity (10),
  adapter stub (5).
- Pre-commit gates: ruff (lint + format), mypy --strict, pytest
  smoke (4/4) — all clean.

### Bug fixes found during testing

- **Step 5 `from-import` patching gotcha**: the verification gate's
  `from phoenix.verification.drift_state import read_drift_state`
  creates a local binding in `gate`'s namespace; patching the
  source module didn't affect the gate's already-bound reference.
  Fixed by patching `gate_module.read_drift_state` directly. Same
  gotcha that bit Phase 7 Step 6 — worth documenting because v1.x
  refactors will hit it again.
- **Step 7 audit mirror missing**: `emit_admin_audit` originally
  only wrote to the JSONL writer; the `/v1/admin/*/history`
  endpoints (which read from the SQL `audit_events` table) saw
  empty results when tested end-to-end. Fixed by extending
  `emit_admin_audit` to write to BOTH sinks. Localized to admin
  events for Phase 8; a v1.x cleanup could promote all audit
  events to dual-write.
- **Step 6 ring-buffer threading**: `collections.deque.append` is
  GIL-atomic but iterating during concurrent appends is undefined.
  Added an explicit `threading.Lock` only around the `snapshot()`
  copy operation — the append path stays lock-free.

### Out of scope for Phase 8 (deferred to Phase 9+ / Phase 10 / v1.x)

- **LoRA adapter sandbox + management plane** — Phase 9 (§2.7,
  §5.4-5.5). `POST /v1/admin/adapters/{id}/force-revalidate` ships
  as a 501 stub in Phase 8 so v1 client integrators can see the
  surface; the real handler lands in Phase 9.
  `GET /v1/admin/adapters/{id}/round-trip-history` deferred entirely.
- **MCP server, CLI commands** — Phase 9 (§5.4-5.5, §9).
- **GPU / VRAM / NPU / thermal in `/v1/admin/governor`** — Phase 9+
  when the cloud-GPU adapter layer surfaces them.
- **Cumulative provider spend aggregation in `/v1/admin/budget`** —
  v1.x. Phase 8 ships the endpoint shape with `0.0` placeholders;
  the per-actor / org-level rollup work waits for the
  `solve_cost_ledger` aggregation layer.
- **Persisted router decision log** — v1.x. Phase 8 ships an
  in-process ring buffer (per locked OPEN-4); cross-restart
  persistence is deferred.
- **Promoting all audit events to dual-write (JSONL + SQL)** —
  v1.x cleanup. Phase 8 mirrors only admin emits; verification
  gate + safety gate emits stay JSONL-only.
- **Phoenix Cloud integration** (§8.5) — outside Phoenix process
  boundary; commercial-bundle scope per Decision 35.
- **Manual calibration baseline override** — permanent NO per
  Section 8.4 + Section 11.5.2's resolved disposition.
- **Standalone binary, Docker image, cloud-seams concrete impls** —
  Phase 10.
- **Final §10.7 acceptance + 1.0.0 release** — Phase 11.

---

## [1.0.0.dev8] — 2026-05-11

Phase 7 shipped — the audit-grade observability and bit-exact replay
layer that distinguishes Phoenix from a research prototype. Phase 7
lands a structured audit event format with a JSONL default sink and
an optional OpenTelemetry OTLP exporter; the Omega Ledger hashchained
provenance store with vendored SHA-256 primitives and StateBackend-
backed durable persistence; the verification gate's ledger
composition path that stitches `VerificationProvenance`,
`RoutingProvenance`, and `TrinityCoreTrace` into a `SolveEntry` per
architecture §6.7; reproducibility modes (`strict` + `replay`) that
capture numpy RNG + BLAS env + `PYTHONHASHSEED` for bit-exact
replay; a replay engine + `POST /v1/tasks/{task_id}/replay` endpoint
that re-executes the deterministic pipeline and verifies the
recorded `result_hash`; the drift → router intelligence feedback
callback that lowers `estimated_fidelity` for drifted providers per
§4.6 Source C; and read-only `/v1/audit/events` + `/v1/audit/ledger/verify`
endpoints. End-to-end, a regulated user can now POST a strict-mode
task, get a hashchained ledger entry, and POST to `/replay` to verify
the original solve bit-exactly.

### Locked scope decisions (2026-05-11)

The six open items the BUILDGUIDE drafted were all locked on
2026-05-11 at session start (recorded in the BUILDGUIDE's "Locked
decisions" section):

1. **Audit JSONL rotation = date-stamped daily files.**
   `events-YYYY-MM-DD.jsonl`, rotated at midnight UTC, no size cap.
   Predictable per-day boundary aligns with standard SIEM ingest
   cadence; long-term archival is Phoenix Cloud's commercial bundle
   (Decision 35).
2. **OTel exporter protocol = HTTP/protobuf.**
   `opentelemetry-exporter-otlp-proto-http` over port 4318. Standard
   OTel collector default; works through corporate firewalls. gRPC
   alternative would pull in `grpcio` with native-compile complexity.
3. **Omega Ledger vendoring = vendor + thin adapter (Option B).**
   The upstream `C:\frank-data\omega\ledger.py` is 2666 lines carrying
   DF&E-specific tables (evolution epochs, MDL snapshots, distillation
   chains). Phoenix vendors only the hashchain primitives verbatim
   (`_compute_entry_hash`, `GENESIS_HASH`, `seal`/`verify_chain`/
   `get_entry` core ~210 lines) and drops the DF&E-specific
   superstructure. The slim vendor file documents what was kept and
   what was dropped.
4. **Ledger storage = new `ledger_entries` table.**
   Separate from Phase 6b's `audit_events` firehose. `audit_events`
   is the high-write structured-event store (one row per gate
   decision / WS connect / drift cycle); `ledger_entries` is the
   long-lived hashchained provenance store (one row per solve plus
   state transitions). Different audiences, different retention
   contracts.
5. **Default reproducibility mode = `default` (no env capture).**
   `strict` and `replay` are opt-in per request. `default` keeps the
   Phase 5/6 fast path; `strict` adds 15-30% wall-clock cost per
   Decision 20; `replay` doubles wall-clock per Decision 19.
6. **Replay env restoration = numpy RNG + BLAS thread env vars +
   PYTHONHASHSEED.** Hardware FP environment (rounding mode,
   denormals) is NOT captured — Python's `fpectl` is removed and
   getting it portably requires platform-specific code we'd have to
   maintain forever. Replay tests on x86_64 Linux/macOS/Windows
   show bit-exact match without this.

### What landed (commits 145925d → bdd4805, 11 commits)

- **Step 1 (`1437907`) — audit event format + JSONL writer + emitter
  singleton.** `phoenix/audit/event_format.py` ships the typed
  `AuditEvent` frozen dataclass with stable field order
  (timestamp_unix, actor_id, layer, event_type, parameters,
  result_hash, request_id) and canonical JSON serialization
  (sort_keys=True, ensure_ascii=True). `jsonl_writer.py` ships
  the `JSONLWriter` daemon-thread sink that writes to
  `~/.phoenix/runtime/audit/events-YYYY-MM-DD.jsonl` with midnight-
  UTC rotation, queue-overflow drop tracking, and SIGTERM-safe drain.
  `emitter.py` ships the `AuditEmitter` multi-sink dispatcher with
  per-sink exception isolation. Singleton via `get_emitter()` /
  `reset_emitter()`. 15 unit tests.
- **Step 2 (`5fb6351`) — audit emits across safety + verification +
  REST/WS.** `phoenix/safety/gate.py` Stage 8 (Phase 6a placeholder)
  now emits one `safety.gate.<decision>` event per `verify_request`
  call (allowed / denied.{kill_switch,auth,permission,rate_limit,
  frontier_physics}) via try/except/finally so every code path emits
  exactly once. New `request_id` kwarg threads the front-door UUID
  through. `phoenix/verification/gate.py` emits seven lifecycle
  events parallel to the broker's WS stream (started, solver_complete,
  control_complete, orchestrate_progress, promoted×2, completed).
  `phoenix/api/routes.py` HTTP middleware mints `req_<uuid>` per
  request, stashes on `request.state.request_id`, emits
  `api.request.{start,complete,error}`. WS handlers emit
  `api.ws.{connect_rejected,connect_accepted,closed}` with
  try/finally so all exit paths produce a close record. 12
  integration tests verify shape + request_id propagation.
- **Step 3 (`b4d8610`) — OpenTelemetry export adapter.**
  `phoenix/audit/otel_adapter.py` implements `AuditSink` with lazy
  imports inside `OTelExporter.__init__` (Phoenix install without
  the `[otel]` extra never tries to import `opentelemetry`).
  `from_env()` returns None when `$PHOENIX_OTEL_ENABLED != "1"`,
  preserving import safety. Severity mapping derives WARN/ERROR/INFO
  from substring matches on `event_type`. `[otel]` optional extra
  (`opentelemetry-sdk` + `opentelemetry-exporter-otlp-proto-http`)
  + mypy override matching the psycopg / nats-py pattern. 19 tests
  including lifespan wiring with mocked `LoggerProvider`.
- **Step 4 (`877c978`) — Vendor Omega Ledger (slim profile) + Phoenix
  adapter.** `vendor/omega/__init__.py` (empty) + `vendor/omega/
  ledger.py` (slim, ~210 lines extracted verbatim from the upstream
  2666-line DF&E source). `phoenix/ledger/entry_types.py` ships the
  on-chain `LedgerEntry` shape + four typed payload dataclasses
  (`SolveEntry`, `OverrideByOperatorEntry`, `KillSwitchEntry`,
  `EnrollmentEntry`) with `*_to_ledger_entry` factory converters.
  `omega_ledger.py` (Step-4 form) wraps the vendored substrate;
  Step 6 refactors persistence to StateBackend. `pyproject.toml`
  mypy override for `omega.*`. 22 tests.
- **Step 5 (`25f28c6`) — Extend StateBackend for durable ledger
  storage.** `phoenix/state/migrations/phase7_ledger.py` (VERSION=2)
  creates the `ledger_entries` table with indexes on
  `timestamp_unix` and `entry_kind` for both SQLite + Postgres
  dialects. `StateBackend` Protocol gains 3 additive methods:
  `append_ledger_entry`, `list_ledger_entries(*, since_unix, limit)`,
  `verify_ledger_integrity()` (SQL window-function structural check
  via `LAG(entry_hash, 1, 'GENESIS') OVER (ORDER BY timestamp_unix,
  entry_id)`). 7 new parametrized parity tests.
- **Step 6 (`25188e9`) — Wire verification gate to compose + persist
  ledger entries.** `OmegaLedger` refactored to delegate persistence
  to `StateBackend` (Step 5's `ledger_entries` table) instead of the
  Step 4 vendored SQLite glue. The vendored module's hash primitives
  (`_compute_entry_hash`, `GENESIS_HASH`) are still used verbatim.
  `phoenix/ledger/solve_composer.py` ships
  `compose_and_append_solve_entry` which composes the §6.7 four-
  component payload, computes a replay-invariant `result_hash`,
  snapshots the vendor manifest, and appends via OmegaLedger.
  `phoenix/trinity/data_model.py` `ProvenanceTrace` gains
  `omega_ledger_entry_id: str | None`. Verification gate's
  post-solve path composes inside try/except (audit-path failure
  must NOT block solve response). 27 ledger + composition tests.
- **Step 7 (`6c6c025`) — Reproducibility modes (strict + replay env
  capture).** `phoenix/_internal/reproducibility.py` ships `EnvSnapshot`
  + `capture_environment` / `restore_environment` /
  `deterministic_environment` context manager + `pin_single_thread_blas`.
  `phoenix/trinity/reproducibility_context.py` adds a thread-local
  per-request snapshot scratchpad keyed by `task.request_id` for
  isolating concurrent solves. `phoenix/trinity/pipeline.py` honors
  `task.tolerance.reproducibility_mode`: strict/replay capture →
  stash → pin BLAS → solve → restore in finally. `SolveEntry` payload
  gains `reproducibility_mode` + `environment_snapshot`. 15 tests
  including pipeline env restoration after strict solve.
- **Step 8 (`0f3370e`) — Replay engine + `POST /v1/tasks/{task_id}/replay`
  endpoint.** `phoenix/ledger/replay_engine.py` ships `replay(task_id)`
  + 5 typed exceptions (`ReplayError` base, `LedgerEntryNotFound`,
  `ReplayEntryIncomplete`, `ReplayProviderUnavailable`,
  `ReplayDivergence` with both hashes attached) + `ReplayReport`
  dataclass. Replay reads the entry, validates strict-mode + env
  snapshot present + no cloud shots, reconstructs `PhysicsTask` from
  `task_spec`, sets replay-active flag on the composer (so the
  re-run doesn't append a duplicate entry), restores env via
  `deterministic_environment` context manager, re-runs pipeline,
  computes `result_hash`, compares. `routes.py` `POST /v1/tasks/
  {task_id}/replay` endpoint with full HTTP code mapping (404 / 409
  / 500 / 401 / 403 / 429 / 503). 9 integration tests including
  bit-exact happy path, no chain extension, divergence on tampered
  hash, permission gating.
- **Step 9 (`bdd4805`) — Drift → router intelligence feedback
  callback.** `phoenix/router/intelligence.py` gains the per-provider
  drift multiplier table guarded by an RLock; `on_drift_snapshot`
  callback translates `firing_detectors` named `<provider>_drift`
  into multipliers per the ladder (healthy=1.0, warning=0.90,
  high_confidence_warning=0.70). `estimate_fidelity` scales by the
  multiplier. `register_for_drift_updates()` idempotent registration
  with the singleton `DriftDetector` — the OPEN-6 forward-compat
  seam from Phase 6b paying its dividend in exactly the
  architecture-promised location (second `register_drift_callback`
  caller). 17 integration tests including end-to-end fidelity
  haircut + provider isolation.
- **Step 10 (this commit) — Admin audit endpoints + version bump.**
  `routes.py` adds `GET /v1/audit/events?since_unix&limit` (reads
  the `audit_events` table) + `GET /v1/audit/ledger/verify` (returns
  both the SQL structural report AND the Python crypto walk).
  `pyproject.toml` + `phoenix/_internal/version.py` bump
  `1.0.0.dev7` → `1.0.0.dev8`; `_DEFAULT_PHOENIX_RELEASE` in the
  SQLite + Postgres backends + the drift detector default updated.
  Test-version assertions in `test_smoke.py` + `test_health.py`
  updated.

### Tests

- 402 tests passing with full infrastructure (Postgres + NATS
  enabled). 0 skipped, 0 failed.
- Phase 7 additions vs Phase 6b's 237: +165 tests across audit emit
  (15) + audit wiring (12) + OTel adapter (19) + omega ledger (21) +
  solve ledger composition (6) + state-backend ledger parity (7,
  parametrized SQLite + Postgres = 14 individual runs) +
  reproducibility mode (15) + replay engine (9) + drift router
  feedback (17) + omega ledger plus parity counts shifting under
  the refactor.
- Pre-commit gates: ruff (lint + format), mypy --strict, pytest
  smoke (4/4) — all clean.

### Bug fixes found during testing

- **Step 5 migration reverts**: parity-test fixture teardown was
  reverting Phase 6b's migration but leaving the Phase 7
  `ledger_entries` table behind. Subsequent tests saw stale rows.
  Fixed by reverting in reverse migration order (`phase7_ledger.revert`
  before `phase6b_initial.revert`).
- **Step 6 OmegaLedger refactor**: Step 4's vendored-SQLite path
  was scaffolding; Step 6 redirects persistence to the StateBackend
  introduced in Step 5. The vendored `_compute_entry_hash` +
  `GENESIS_HASH` primitives are still used verbatim. This means
  Postgres installs now get replicated ledger storage for free.
- **Step 8 ledger composer replay-skip**: an early Step 8 draft
  duplicated every replay into the chain. Added a thread-local
  `is_replay_active` flag in `reproducibility_context` so the
  composer can short-circuit the append on replays — replay
  verifies, it doesn't extend the chain.
- **Step 9 register-callback idempotency**: `DriftDetector.register_drift_callback`
  appends unconditionally; lifespan restarts in `TestClient` were
  double-registering the router callback. Made
  `register_for_drift_updates()` itself idempotent via a
  callback-identity check before appending.

### Out of scope for Phase 7 (deferred to Phase 8+ / v1.x)

- **Admin dev-ops backdoor endpoints** (`/v1/admin/ledger/integrity-
  report`, `/v1/admin/calibration/...`, kill-switch admin
  endpoints) — Phase 8 (§8).
- **LoRA adapter sandbox, MCP server, CLI commands** — Phase 9
  (§5.4-5.5, §9).
- **OTel adapter to non-OTLP backends (Datadog/Splunk-specific
  shims)** — the generic OTLP exporter suffices for v1; vendor-
  specific shims are v1.x.
- **Cloud-shot retention** — replay of cloud-quantum solves with
  `cloud_shots_recorded=True` raises `ReplayProviderUnavailable`
  until the shot-retention layer ships. Decision 20 anticipated this:
  cloud shots are intrinsically non-deterministic and recorded once;
  v1 documents the boundary.
- **threadpoolctl live BLAS pool resize** — `pin_single_thread_blas`
  writes `OMP_NUM_THREADS=1` etc. via `os.environ` which affects
  new pools but doesn't resize already-loaded ones. v1.x adds
  threadpoolctl as an opt-in dependency for tighter strict-mode
  pinning.
- **Empirical fidelity from past Results (§4.6 Source C, the other
  half)** — Phase 7 ships the drift-feedback half; the
  measured-fidelities-from-past-Results half lands later when there
  are enough ledger entries to do the regression.
- **Replay long-window test** — the §10.7 acceptance test that
  re-replays a 6+ month old ledger entry across clean Linux + macOS
  containers lands at Phase 11's acceptance.
- **`/v1/admin/...` endpoints** — Phase 8.
- **Standalone binary, Docker image, cloud-seams concrete impls** —
  Phase 10.
- **Final §10.7 acceptance + 1.0.0 release** — Phase 11.

---

## [1.0.0.dev7] — 2026-05-10

Phase 6b shipped — the infrastructure layer that pairs with Phase 6a's
API-side enforcement. Phase 6b lands durable state backends (SQLite
default + Postgres opt-in), NATS JetStream queueing infrastructure,
the three-checker drift detector per Section 1 Decision 17, and the
`/v1/ws/calibration/drift` WebSocket endpoint. Together with Phase 6a,
Phoenix's runtime substrate is now audit-grade durable: every state
change survives daemon restart, the queue is file-backed persistent,
and drift telemetry is observable in real time.

### Locked scope decisions (2026-05-10)

The Phase 6b BUILDGUIDE drafted seven open items; all locked at
session start before any code landed (no silent resolutions in the
implementation):

1. **Migration format = Python-callable.** Each migration is a `.py`
   file with `apply(conn)` / `revert(conn)`. Trivial bodies are
   `conn.executescript("""<SQL>""")` so SQL stays in plaintext; data
   transforms (kill-switch JSON → SQLite import in migration #1) live
   in Python.
2. **Postgres client = sync `psycopg` wrapped in `asyncio.to_thread`.**
   Same pattern as the SQLite path's `sqlite3` (stdlib sync) wrap.
   Symmetry over the ~2x async perf `asyncpg` would buy on what is
   fundamentally a cold persistence path.
3. **NATS distribution = require user-installed for `1.0.0.dev7`.**
   Documented `winget` / `brew` install hint in
   `EmbeddedNATSNotFound`. Bundling deferred to `1.0.0` when the
   Phase 10 release-artifact pipeline lands.
4. **JetStream consumer modes = mixed per use case.**
   - `phoenix.tasks.submit.*` — durable, no TTL.
   - `phoenix.tasks.events.<task_id>` — ephemeral.
   - `phoenix.drift.alerts` — durable, MAX_AGE=10m.
5. **ML drift detector = vendor + thin Phoenix adapter.** Vendored
   `vendor/ml/drift_ensemble.py` unchanged from `C:\frank-data\`;
   `phoenix/verification/drift_detector.py::MLStatisticalChecker`
   wraps it and exposes only the methods the orchestrator needs.
6. **Drift forward path = `register_drift_callback(callback)`.**
   Phase 6b registers only the verification gate's auto-promote
   consumer; Phase 7 adds the router intelligence as a second caller
   in one line.
7. **Drift WS auth = bootstrap-actor parity with Phase 6a.** Same
   fallback as `/v1/ws/tasks/.../stream`: the `ws-token` mint
   endpoint owns the bootstrap fallback; the WS handler is identical.

### What landed (commits d5e976d → a921144, 10 commits)

- **Step 1 (`db78f43`) — `StateBackend` Protocol expansion.**
  11 new method signatures added additively to
  `phoenix/state/backend_protocol.py` covering solve cost ledger,
  audit events, pending-review queue, drift snapshot, and
  ActorPermissions shadow. Phase 6a contract unchanged.
- **Step 2 (`2162c7e`) — SQLite backend + migration + kill-switch
  write-through.** `phoenix/state/sqlite_backend.py` with WAL journal
  mode + RLock; `phoenix/state/migrations/runner.py` +
  `phase6b_initial.py` with 6-table schema (`kill_switch_state`,
  `solve_cost_ledger`, `audit_events`, `pending_review_queue`,
  `actor_permissions`, `drift_state_snapshot` + `schema_version`).
  `kill_switch.py` gains optional `StateBackend` write-through with
  fail-closed merge (`_fail_closed_merge` returns engaged if either
  source is engaged).
- **Step 3 (`4ae595a`) — Postgres backend + dialect dispatch.**
  `phoenix/state/postgres_backend.py` with `ConnectionPool`
  (min_size=1, max_size=10); migration runner dispatches SQLite vs
  Postgres via `isinstance(conn, sqlite3.Connection)`; uses
  `to_regclass('schema_version')` for Postgres existence-check to
  avoid the transaction-abort trap. `[postgres]` optional extra +
  mypy override.
- **Step 4 (`0c62b3d`) — State backend factory + FastAPI lifespan
  startup wiring.** `phoenix/state/factory.py` with env-var dispatch
  (`$PHOENIX_STATE_BACKEND=sqlite|postgres`) + singleton.
  `phoenix/api/routes.py` gains a `lifespan` context manager that
  calls `get_state_backend()` + `set_store_backend()` on enter,
  clears on exit. Per Decision 31, backend choice locked at startup.
- **Step 5 (`ec8393e`) — NATS connection wrapper + embedded runner.**
  `phoenix/queue/nats_client.py` with lazy `import nats`;
  `phoenix/queue/embedded_runner.py` with binary discovery
  (`$NATS_SERVER_PATH` → `shutil.which` → `EmbeddedNATSNotFound`),
  Popen lifecycle, monitor-port readiness poll, SIGTERM → SIGKILL
  drain. `scripts/launch_with_nats.bat` as Windows two-process demo.
  Daemon does **not** auto-launch NATS — launcher script orchestrates.
- **Step 6 (`4fa1ea1`) — NATS task queue + optional `NATSEventBroker`.**
  `phoenix/queue/task_queue.py` with subject constants
  (`SUBMIT_SUBJECT_PREFIX`, `EVENTS_SUBJECT_PREFIX`,
  `DRIFT_ALERTS_SUBJECT`) + `TaskQueue` class declaring streams per
  OPEN-4. `phoenix/api/event_broker.py` adds `BrokerProtocol` +
  `NATSEventBroker` (sync API on daemon-thread asyncio loop;
  fire-and-forget publish + wildcard subscriber feeding local buffer).
  `get_broker()` env-var dispatch via `$PHOENIX_EVENT_BROKER=memory|nats`
  with `memory` default.
- **Step 7 (`dc5820f`) — Drift detector with 3 checkers +
  `register_drift_callback` seam.** `phoenix/verification/drift_detector.py`
  with `Tier1AnalyticalChecker` (5 inline benchmarks: HO-1, ISW-1,
  H1S-1, RABI-1, SCG-1), `MLStatisticalChecker` (vendored
  `predict_scale_separated`), `CrossVersionChecker` (per-version JSON
  history at `~/.phoenix/runtime/calibration_history/`). Aggregation
  per Decision 17 (0 firing → healthy; 1 firing → warning; 2+ →
  high-confidence-warning). Cadence env-var
  `$PHOENIX_DRIFT_CADENCE_HOURS`; snapshot persistence via
  `put_drift_state_snapshot`; rehydration on construction.
  `drift_state.py` rewired with cold-start = healthy + stale = fail-
  closed semantics.
- **Step 8 (`b8205e6`) — `/v1/ws/calibration/drift` endpoint +
  drift-alert bridge.** `phoenix/api/drift_alerts.py` with
  `_DriftAlertEmitter` (transition-detection callback) +
  `install_drift_alert_emitter`. `routes.py` lifespan installs the
  bridge; new `@app.websocket("/v1/ws/calibration/drift")` handler
  mirrors `/v1/ws/tasks/.../stream`'s auth + 1008 close-code shape.
- **Step 9 (`a921144`) — Parametrized parity tests + WS token edge
  cases.** `tests/integration/test_state_backend.py` parametrized
  SQLite + Postgres; `test_broker_parity.py` parametrized memory +
  NATS; `test_drift_ws.py` adds expired-token + reused-token (single-
  use) cases using arithmetic mutation of `issued_at_unix` to avoid
  60-second sleeps.

### Tests

- 237 tests passing (was 113 at end of Phase 6a; +124 from Phase 6b).
  - 28 parametrized state-backend tests (Step 9) covering kill-switch,
    cost ledger, audit events, pending review, drift snapshot,
    ActorPermissions, migration idempotency
  - 14 SQLite-specific tests (Step 2)
  - 9 Postgres-specific tests (Step 3, gated)
  - 10 factory tests (Step 4)
  - 8 NATS runner tests (Step 5)
  - 13 task-queue tests (Step 6)
  - 9 NATSEventBroker dispatch tests (Step 6)
  - 16 broker parity tests (Step 9)
  - 37 drift detector tests (Step 7)
  - 13 drift WS tests (Step 8 + 9)
- 29 skipped (all are env-var-gated): Postgres parametrizations need
  `$PHOENIX_POSTGRES_TEST_DSN`, NATS parametrizations need
  `$PHOENIX_NATS_TEST_ENABLED=1` + `nats-py` installed, real-NATS
  lifecycle test similarly gated.
- Pre-commit hooks: ruff, ruff-format, mypy --strict (81 source files
  clean), pytest smoke (4/4) — all pass.

### Bug fixes found during testing

- **Step 4 test isolation**: `phoenix/safety/kill_switch._STORE`
  module-level singleton leaked across tests because pytest's
  `tmp_path` fixture persists between tests (for debug inspection),
  meaning a test that set `ks._STORE` to a `tmp_path`-backed store
  with engaged state would let the next test's `read_drift_state` see
  that engaged state via the still-pointed `_path`. Fixed via
  `monkeypatch.setattr(ks, "_STORE", None)` in the autouse fixture so
  each test starts with a clean module-level singleton.
- **Step 7 cold-start semantics**: initial implementation raised
  `DriftStateUnavailable` whenever no snapshot existed, breaking 9
  Phase 5 verification-gate tests that exercised the gate on cold
  daemon boot (before any drift cycle had run). Corrected to "no
  snapshot = healthy default" — matches the Phase 5 stub contract
  the verification gate relied on. Stale snapshots (older than
  `2 * cadence`) still fail closed per the Section 6.8 fail-closed
  rule, which is for *telemetry failures*, not startup state.

### Out of scope for Phase 6b (deferred to Phase 7 / Phase 8+ / v1.x)

- **Audit log + OpenTelemetry export** (Phase 7 — Decision 16 + 22).
  `append_audit_event` / `list_audit_events` Protocol methods exist
  but call sites that emit events are Phase 7.
- **Omega Ledger hashchained provenance store** (Phase 7 —
  Decision 15 + 19-21).
- **Drift signals feeding back into routing** (Phase 7 — Section
  4.6's drift→fidelity rescoring). The `register_drift_callback`
  seam is in place; Phase 7 adds the router intelligence as a
  second registered caller.
- **Admin dev-ops backdoor endpoints** `/v1/admin/calibration/...`
  (Phase 8 — Section 8 generally).
- **LoRA adapter sandbox, MCP server, CLI commands** (Phase 9).
- **OpenTelemetry adapter concrete impl, cloud seams concrete impls,
  standalone binary** (Phase 10).
- **Final §10.7 acceptance + release** (Phase 11).
- **Solver-output feature collection for `MLStatisticalChecker`** —
  the `feature_provider` callback is in place; concrete wiring lands
  in a later phase when the verification gate's KPI bundle is
  augmented to capture per-solve features.
- **Drift detector scheduler auto-start at daemon boot** — Phase 6b
  ships the API (`DriftDetector.start_scheduler()`) but the FastAPI
  lifespan does **not** call it. Launcher scripts opt in; this
  protects test environments (a real Tier-1 cycle is seconds, but
  ~5-7 minutes for the full statistical sweep per Decision 17 PERF
  note) and lets ops decide when to start cycling.
- **Phase 6a JSON file removal** — `~/.phoenix/runtime/kill_switch.json`
  and the JSON-file `ActorPermissions` registry remain authoritative
  through Phase 6b. The SQLite backend shadow-writes; Phase 7 promotes
  the backend to source of truth and removes the JSON fallback.
- **NATS binary bundling** — locked OPEN-3 defers to `v1.0.0` when the
  Phase 10 release-artifact pipeline is built. For now,
  `nats-server` is user-installed via `winget` / `brew`.

---

## [1.0.0.dev6] — 2026-05-08

Phase 6a shipped (Phase 6 split into 6a + 6b per locked scope decision
2026-05-08). Phase 6a is the API-side enforcement layer: safety gate
(9-stage pipeline; Section 7.4 stages 0-6 functional); identity layer
(Ed25519 keystore + bootstrap-actor mint); ActorPermissions registry
(JSON-file backed); token-bucket rate limiter; kill switch with refuse-
to-start posture; WebSocket /v1/ws/tasks/{task_id}/stream for verification-
gate events; bearer-token auth via POST /v1/identity/ws-token. Phase 6b
will land the infrastructure layer: SQLite/Postgres state backend, NATS
JetStream queue, drift detector with three checkers,
/v1/ws/calibration/drift endpoint.

### Locked scope decisions (2026-05-08)

1. **Split Phase 6 into 6a + 6b.** 6a = API-side enforcement; 6b =
   infrastructure (state backend + NATS + drift detector). Each phase
   ~10 steps; both fit the established build-guide rhythm.
2. **State at 6a = JSON file + in-memory.** Kill switch +
   ActorPermissions stored in JSON files at ~/.phoenix/runtime/;
   rate-limit buckets in-memory. StateBackend Protocol shipped at 6a;
   SQLite/Postgres concrete impls at 6b.
3. **WebSocket: just /v1/ws/tasks/{task_id}/stream.** /v1/ws/calibration/drift
   defers to 6b with the drift detector; /v1/ws/standing/* defers to v2
   (already 503 in spec).
4. **Actor required with bootstrap-actor fallback.** When no
   Authorization header is present and the keystore is available,
   auto-mint a bootstrap actor (`adam`, admin tier). Preserves dev-mode
   UX while enforcing real Actor verification when a header is present.

### What landed (commits 78a3ed0 → fdce9c6)

- **Identity layer** (`phoenix/identity/{keystore,bootstrap}.py`):
  Ed25519 master key at `~/.phoenix/runtime/master_key.bin` (0600 POSIX);
  `mint_bootstrap_actor` signs via vendored `Actor.sign`;
  `extract_or_bootstrap(authorization | None) -> (Actor, was_bootstrapped)`
  parses `Phoenix-Actor <base64-json>` header or falls back to bootstrap.
- **ActorPermissions** (`phoenix/safety/permissions.py`): 8-flag dataclass
  per Section 7.3 (can_submit_tasks, can_replay_tasks, can_load_adapter,
  can_unload_adapter, frontier_physics, can_override_human_review,
  is_admin, rate_limit_tier). Bootstrap actors `adam`/`ash` get all-True
  + admin tier; others get safe minimum. JSON-file registry with
  threading.RLock.
- **Rate limiter** (`phoenix/safety/rate_limiter.py`): token-bucket per
  Section 7.5 + Decision 23. Tiers: default (cap 100 / refill 1/sec),
  elevated (cap 1000 / refill 16/sec), admin (unlimited). Cost catalogue:
  health=0, tasks_get=1, tasks_submit_r1..r5=5..25, tasks_replay=50,
  adapters_post=10, ws_token=1. RateLimitExceeded carries
  retry_after_seconds for HTTP 429 Retry-After header.
- **Kill switch** (`phoenix/safety/kill_switch.py`): JSON file backend;
  refuse-to-start posture per Section 11.5.1 RESOLVED. KillSwitchEngaged
  exception carries engagement metadata for HTTP 503 detail.
- **StateBackend Protocol** (`phoenix/state/backend_protocol.py`):
  abstract surface for Phase 6b's SQLite/Postgres concrete impls. Phase
  6a methods: get/set kill_switch_state. Phase 6b expands.
- **Safety gate** (`phoenix/safety/{gate,errors}.py`): 9-stage pipeline.
  verify_request runs Stage 0 (kill switch) -> Stages 1+2 (Actor name
  shape, lowercase ASCII) -> Stage 3 (permissions lookup) -> Stage 4
  (capability flag check) -> Stage 5 (rate limit deduct) -> Stage 6
  (frontier-physics authority -- distinct from Phase 2 engine-boundary
  capability check). Stages 7+8 placeholder (Phase 6b/7).
- **Event broker** (`phoenix/api/event_broker.py`): in-memory per-task
  buffer. TaskEvent dataclass (task_id, type, timestamp_unix, payload).
  EventBroker.emit/get_events/clear with FIFO eviction at 1000-event
  cap. Phase 6b NATS JetStream replaces.
- **WebSocket bearer-token auth** (`phoenix/api/ws_auth.py`):
  WSTokenStore.mint (43-char URL-safe random); consume validates
  not-unknown + not-used + not-expired (60s window); single-use.
- **Verification gate emits events** (`phoenix/verification/gate.py`):
  task.started, task.solver.complete, task.control.complete,
  task.orchestrate.progress, task.verification.promoted, task.complete
  emitted into broker. WS clients see real-time progression.
- **routes.py wiring** (`phoenix/api/routes.py`):
  * submit_task gains `authorization: str | None = Header()` parameter.
  * extract_or_bootstrap + verify_request before solve.
  * Maps KillSwitchEngaged -> 503, AuthError/IdentityError -> 401,
    PermissionDenied -> 403, RateLimitExceeded -> 429 with Retry-After,
    FrontierPhysicsRefused -> 403 (gate-layer message).
  * NEW POST /v1/identity/ws-token endpoint; NEW
    @app.websocket("/v1/ws/tasks/{task_id}/stream") async handler.

### Tests

- 113 tests passing (was 91 at end of Phase 5; +22 from Phase 6a).
  - 17 unit tests in `test_identity_safety.py`: identity, permissions,
    rate limiter, kill switch, safety gate.
  - 5 integration tests in `test_ws_endpoint.py`: ws-token mint,
    WS missing/bad token rejection (1008), end-to-end event streaming.
- Pre-commit hooks: ruff, ruff-format, mypy strict, pytest smoke -- all
  4 pass.

### Bug fix found during testing

- `phoenix/safety/permissions.py`: `PermissionsRegistry.set()` acquired
  `threading.Lock` then called `_ensure_loaded()` which acquired the
  same lock -> deadlock on non-reentrant Lock. Switched to
  `threading.RLock`; same-thread re-acquire succeeds. Caught by hung
  test_permissions_registry_round_trip.

### Out of scope for Phase 6a (deferred to 6b / 7 / v1.x)

- SQLite + Postgres concrete impls of StateBackend -- Phase 6b.
- NATS JetStream queue -- Phase 6b.
- Drift detector with three scheduled checkers + /v1/ws/calibration/drift
  endpoint -- Phase 6b.
- /v1/ws/standing/* endpoint (already 503 NotImplementedYet per spec)
  -- v2.
- task.failed event emission via gate-level exception handler -- Phase
  6b.
- /v1/identity/whoami + /v1/identity/permissions endpoints -- Phase 8
  admin.
- OS-keystore bindings (DPAPI / Keychain / libsecret) -- v1.x.
- Per-actor-per-day cumulative cost-ceiling tracking -- Phase 7+.
- Org enrollment ceremony with HKDF subkeys (Section 7.6) -- Phase 6b.
- Replay-mode session pinning (Stage 7) -- Phase 7.
- Audit-event writes to state backend (Stage 8) -- Phase 6b.

### Honesty notes

- Filesystem-backed master key is readable by any process running as the
  same OS user (Section 7.2 honest threat model). v1.x adds DPAPI /
  Keychain / libsecret bindings.
- WebSocket bearer token uses ?token=... query parameter for
  TestClient compatibility; Authorization: Bearer header path lands
  with the Phase 9 production hardening pass.
- WebSocket polling cadence is 100ms; max iteration cap 10 minutes per
  connection. Phase 6b NATS replaces with push-based subscription.
- Bootstrap-actor flow auto-mints `adam` (admin tier) when keystore
  present and no header given. This is intentional dev-mode convenience
  and is documented in `phoenix/identity/bootstrap.py`. Production
  multi-tenant deployments use Phoenix Cloud's `HttpAuthExtractor`
  cloud seam (Decision 35) to override.

### Version + manifest

- `pyproject.toml`, `phoenix/_internal/version.py`: `1.0.0.dev5` ->
  `1.0.0.dev6`.
- `vendor/VENDOR_VERSION.txt` regenerated.

---

## [1.0.0.dev5] — 2026-05-08

Phase 5 shipped. Trinity Core's verification gate is the load-bearing
piece of v1's mandatory three-axis wobble: `VerificationGate.verify(task)`
selects the initial rung from `max_error_bar`, runs Axes 1+2+3 at the
selected depth, reactively promotes when measured disagreement exceeds
half the remaining error budget (max 2 per task), composes the distance
matrix per Section 6.2 DO-NOT-COLLAPSE, classifies the result via the
extended `PhoenixDisagreementType`, and produces the final `Result`
envelope with full `ProvenanceTrace` carrying the new
`VerificationProvenance`.

### Locked scope decisions (2026-05-08)

1. **Static + reactive promotion.** Initial rung from
   `select_initial_rung(max_error_bar)`; reactive promotion when
   axis disagreement > half the remaining error budget (max 2 per
   task per Section 6.4). Demotion telemetry-only. Cost-ceiling
   refuses promotion -> `DEGRADED_BUDGET_BOUND`.
2. **Stub drift state.** `read_drift_state()` returns `DriftState
   (state="healthy")` in v1; the gate's fail-closed wiring (raises
   `DriftStateUnavailable` per Section 6.8) is exercised against the
   stub. Phase 7 swaps for real telemetry.
3. **WebSocket events deferred to Phase 6.** Phase 5 records
   verification.promoted/demoted as in-memory provenance fields;
   Phase 6 wires the WebSocket endpoint that emits them per Section
   5.3.
4. **QHO-only Tier-1 real plumbing.** Eigenstate extraction from the
   solver's high-grid `SolverResult.eigenstates` projects to a 2x2 ρ
   in the lowest 2 energy eigenstates. Real 〈σ_z〉 observable in
   `LocalClassicalSimulator` replaces the trace placeholder. For QHO
   ground state both resolve to mathematically-identical-to-placeholder
   results (the ground state IS the σ_z eigenstate); the data path is
   real for non-QHO future inputs.

### What landed (commits 653c7a9 → e9d9392)

- **`rung_table`** (`phoenix/verification/rung_table.py`):
  `select_initial_rung(max_error_bar) -> RungDepth` per Section 6.4
  thresholds (>1e-2 -> R1, 1e-3..1e-2 -> R2, 1e-4..1e-3 -> R3,
  1e-6..1e-4 -> R4, <=1e-6 -> R5). `next_rung` / `previous_rung`
  walking helpers; `RUNG_WALL_CLOCK_MULTIPLIER` (1x / 1.7x / 3x /
  5.5x / 10x); `estimate_additional_cost_multiplier` for the gate's
  pre-promotion budget check.
- **Drift state stub** (`phoenix/verification/drift_state.py`):
  `DriftStateUnavailable` exception; `DriftState` dataclass;
  `read_drift_state()` always returns `state="healthy"` in Phase 5.
  Phase 7 wires real telemetry from the three drift detectors per
  Section 1 Decision 17.
- **Agreement classifier** (`phoenix/verification/agreement_classifier.py`):
  `PhoenixDisagreementType` enum with 11 values (5 vendored mirrors +
  6 Phoenix extensions per Section 6.2). `classify(axis_results, *,
  max_error_bar, drift_state, budget_bound)` walks the Section 6.2
  decision tree.
- **`CrossProviderAxis`** (Axis 3) (`phoenix/verification/wobble_axis.py`
  + `phoenix/trinity/orchestrate/cross_provider.py`): third concrete
  `WobbleAxis` Protocol impl. R1/R2/R3 skipped; R4+ requires injected
  primary + alternate `Result` envelopes (gate's responsibility per
  Section 6.10); applies_to honors REPLAY mode per Section 6.3.
  `compute_cross_provider_disagreement` metric is
  `|value_primary - value_alternate|`.
- **Real eigenstate plumbing** (`phoenix/trinity/solver/engine.py` +
  `phoenix/trinity/control/engine.py`): `SolverRunResult` adds
  `eigenstates` field (`np.ndarray | None`). `_initial_density_matrix`
  derives ρ from the ground-state eigenvector projected to dim-x-dim
  energy eigenstates basis when `solver_run_result` is provided;
  falls back to |0><0| placeholder. `run_dpd` accepts and threads
  `solver_run_result` kwarg; `CrossControlAxis` uses it.
- **Real σ_z observable**
  (`phoenix/providers/classical/local_simulator.py`):
  `_build_observable(name, dim)` constructs canonical Pauli matrices.
  Default `observable="sigma_z"`; payload can override with
  "sigma_x" / "sigma_y" / "identity". The local sim computes
  `Tr(rho * O)` instead of `Tr(rho)`.
- **`VerificationGate`** (`phoenix/verification/gate.py`):
  `verify(task) -> Result`. Reads drift_state (fail-closed via
  `DriftStateUnavailable` per Section 6.8); selects initial rung; runs
  Axis 1 + reactive promotion + Axis 2 (R3+) + reactive promotion;
  primary orchestrate; alternate orchestrate at R4+ with
  `excluded_providers`-set Router second call (Section 6.10); Axis 3
  on the two Result envelopes; composes distance matrix +
  wobble_score sigma + agreement_type via the classifier; builds full
  `ProvenanceTrace` with `VerificationProvenance`.
- **Pipeline integration**
  (`phoenix/trinity/pipeline.py`): `solve(task)` reduced to
  `_enforce_latency_tier(task); return _get_gate().verify(task)`.
  Module-level `_GATE` singleton.
- **VerificationProvenance** added to `phoenix/trinity/data_model.py`:
  `initial_rung`, `final_rung`, `promotions`, `demotions`,
  `drift_state`, `distance_matrix`, `wobble_score_sigma`,
  `budget_bound`, `phase`. Lands on `ProvenanceTrace`.

### Tests

- 91 tests passing (was 75 at end of Phase 4; +16 from Phase 5).
  - 3 new unit-test files: `test_rung_table.py` (4 tests),
    `test_agreement_classifier.py` (7 tests), `test_verification_gate.py`
    (5 tests).
  - Phase 0/1/2/3/4 baseline tests pass through gate-routed pipeline
    (Steps 6+7 adjusted Phase-3 phase markers and sigma assertions).
- Pre-commit hooks: ruff, ruff-format, mypy strict, pytest smoke -- all
  4 pass.

### Out of scope for Phase 5 (explicit deferrals)

- Drift detector with real telemetry -- Phase 7.
- WebSocket events for verification.promoted/demoted -- Phase 6
  alongside state backend + queue.
- R5 (replicated) replication semantics -- v1.x once per-task budget
  tracking is wired.
- Cost-ceiling per-actor-per-day cumulative tracking -- Phase 7+.
- Real eigenstate plumbing for non-QHO regimes (Pauli, Dirac, etc.) --
  v1.x (Phase 5 Tier-1 plumbing for QHO is enough to ship the gate).
- User-specified observables via task grammar (currently bundle_builder
  doesn't forward `task.metadata["observable"]` -> bundle) -- v1.x.

### Honesty notes

- For QHO ground state, both eigenstate-derived ρ and σ_z observable
  resolve to results mathematically identical to the Phase 3
  placeholders. The ground state IS the σ_z eigenstate in the energy
  basis. Phase 5's win is the data path being real for v1.x non-QHO
  inputs. Cross-control trace distance stays zero on QHO ground state
  -- correct physics, not a bug.
- Axis 3 at R4+ depends on the Router finding an alternate provider
  whose `quantum_technology` is bundle-able. The Phase 4 cloud stubs
  raise NotImplementedError from `bundle_builder` (only `"simulation"`
  is wired); Phase 5's gate catches this and gracefully degrades to
  primary-only result. Axis 3 produces meaningful disagreement when
  Phase 4.5+ wires real cloud providers OR a second simulation
  provider is registered.

### Version + manifest

- `pyproject.toml`, `phoenix/_internal/version.py`: `1.0.0.dev4` ->
  `1.0.0.dev5`.
- `vendor/VENDOR_VERSION.txt` regenerated (`phoenix_release: 1.0.0.dev5`,
  `vendor_synced_at` refreshed).

---

## [1.0.0.dev4] — 2026-05-08

Phase 4 shipped. Trinity Core's pipeline now routes through a real Router
subsystem (Section 4) — the Phase 3 placeholder helper is retired. Six
new modules under `phoenix/router/` plus three quantum provider stubs
under `phoenix/providers/quantum/`. The seven-stage routing algorithm is
fully wired; failover protocol with exponential-backoff quarantine; cost
ceiling enforcement at Stage 2; defense-in-depth frontier-physics
re-check at Stage 4; reproducibility-mode REPLAY pinning at Stage 5.

### Locked scope decisions (2026-05-08)

1. **Stub-only cloud adapters.** IBM/Braket/IonQ stubs ship as
   Protocol-conforming classes that raise `OrchestrateProviderError` on
   `submit`. No cloud SDKs added to `pyproject.toml`. Real
   qiskit-ibm-runtime / amazon-braket-sdk / ionq wiring lands in a
   focused later phase (likely Phase 9 with adapters / MCP) when
   credential management gets tackled deliberately.
2. **Drift drain defers to Phase 7.** Phase 4's intelligence layer uses
   only Source A (static `HardwareParams` from vendored
   `hardware_backends.py`). Sources B (live telemetry) and C (ledger
   history) need Phase 7 backing to be useful.
3. **Equivalence registry shipped.** `phoenix/router/equivalence_registry.py`
   with conservative defaults per Section 4.5 (same `quantum_technology`
   + fidelity within 10%). Section 11.2.1 stays open; v1.x adds
   richer equivalence rules.

### What landed (commits b1780d2 → eb20d36)

- **`RoutingRequest` dataclass + `ReproducibilityMode` enum**
  (`phoenix/router/data_model.py`): forward-compat input shape for
  `Router.decide`. Phase 3 already shipped `RoutingDecision` +
  `ProviderSelection`; Phase 4 adds `RoutingRequest` and the typed
  `ReproducibilityMode` enum honoring DEFAULT and REPLAY (STRICT
  Phase 7).
- **Router error types** (`phoenix/router/errors.py`):
  `NoEligibleProvidersError`, `CostCeilingExceeded`,
  `ReplayProviderUnavailable`, `AllAlternatesExhausted` — each carrying
  the structured context Phase 9's HTTP status mapping needs.
- **Three quantum provider stubs**
  (`phoenix/providers/quantum/{ibm_stub,braket_stub,ionq_stub}.py`):
  IBM Eagle (superconducting), Braket Rigetti Aspen-M-3
  (superconducting), IonQ Forte (trapped_ion). Constructor-overridable
  `available` flag for testing the Stage 3 health filter.
- **`ProviderRegistry`** (`phoenix/router/provider_registry.py`):
  per-process state-of-the-world. `ProviderHealth` enum (HEALTHY /
  DEGRADED / OFFLINE) + `ProviderEntry` mutable dataclass. Mark methods
  for failover. `build_default_registry()` registers the 4 default
  providers (LocalSim + 3 stubs).
- **Pricing v1 data + loader** (`phoenix/router/pricing/pricing_v1.json`
  + `phoenix/router/pricing.py`): static per-provider cost estimates with
  `_metadata` (data_freshness_utc, stale_after_days=90 per Section 11.2.2
  RESOLVED). `load_pricing`, `estimate_cost_usd`, `is_pricing_stale`.
- **Intelligence layer Source A** (`phoenix/router/intelligence.py`):
  `estimate_fidelity` derived from vendored `HardwareParams`
  (gate_error_rate + two_qubit_error_rate via Phase 4 placeholder
  circuit shape: 10 1q + 1 2q gates). `estimate_latency_ms` falls back
  to client's reported value. `estimate_cost_usd` delegates to pricing.
- **Equivalence registry** (`phoenix/router/equivalence_registry.py`):
  conservative defaults per Section 4.5. `is_equivalent` and
  `filter_equivalent_alternates` consumed by Stage 6's alternate
  filtering and Step 8's failover walk.
- **Router decision algorithm** (`phoenix/router/decision.py`):
  `Router.decide(RoutingRequest) -> RoutingDecision` running all seven
  stages (Section 4.4). Stage 1 modality whitelist; Stage 4 frontier
  early raise; Stage 2 cost / latency / fidelity / excluded filters with
  CostCeilingExceeded specialization; Stage 3 health filter; Stage 5
  REPLAY pinning; Stage 6 weighted ranking with deterministic tie-break;
  Stage 7 decision_provenance with per-stage rationale + ranking weights
  + pricing staleness.
- **Failover protocol** (`phoenix/router/failover.py`):
  `FailoverProtocol` class with exponential-backoff quarantine.
  `quarantine` (public; pipeline calls it at the orchestrate boundary),
  `reset_failures` for ops, `attempt_with_failover` for self-contained
  submit-level walks. Defaults: 5 min base, doubles per failure, capped
  at 1 hour.
- **Pipeline integration** (`phoenix/trinity/pipeline.py`): module-level
  Router + FailoverProtocol singletons (lazy via
  `_get_router`/`_get_failover`). `_build_routing_request` translates
  `PhysicsTask` to `RoutingRequest`. `_orchestrate_with_failover` walks
  `decision.primary` + `alternates` on failures, falls back to
  `LocalClassicalSimulator` when `allow_simulator_fallback=True`.
  `solve()`'s Layer 3 replaces the Phase 3 `_build_default_provider_selection`
  helper with real Router routing.

### Tests

- 75 tests passing (was 50 at end of Phase 3; +25 from Phase 4).
  - 4 new unit-test files: `test_provider_registry.py` (4 tests),
    `test_router_decision.py` (7 tests covering all seven stages),
    `test_failover.py` (6 tests including exponential-backoff math and
    simulator fallback walk), `test_intelligence_pricing.py` (8 tests
    combining pricing + intelligence + equivalence).
  - Phase 0/1/2/3 baseline tests pass unchanged through the new
    Router-routed pipeline path.
- Pre-commit hooks: ruff, ruff-format, mypy strict, pytest smoke -- all
  4 pass.

### Out of scope for Phase 4 (explicit deferrals)

- Real cloud SDK wiring (qiskit-ibm-runtime, amazon-braket-sdk, ionq) --
  focused later phase.
- Sources B (live provider telemetry) + C (ledger history) for the
  intelligence layer -- Phase 7 with state backend + Omega Ledger.
- Drift buffer drain scheduler -- Phase 7.
- Phase 9 HTTP status mapping for new Router error types
  (`CostCeilingExceeded` -> 402, `ReplayProviderUnavailable` -> 410,
  `AllAlternatesExhausted` -> 503) -- Phase 9 admin/MCP work.
- Verification gate's secondary routing requests for Axis 3
  (cross-provider wobble) -- Phase 5.
- Cost ceiling per-actor-per-day budget enforcement -- Phase 7+ (needs
  ledger backing for cumulative tracking).
- `phoenix/router/pricing/pricing_v1.json` package_data config so the
  JSON ships in the wheel -- Phase 11 release work.

### Known placeholders (per plan risk register)

All deferrals or known limitations, none blocking:

- Stub adapters' `submit()` raises `OrchestrateProviderError`; failover
  always falls through to `LocalClassicalSimulator` for any cloud-routed
  task. Real cloud calls cost money and need credentials; deferred.
- Phase 4 ranking circuit shape (10 1q + 1 2q gates) is a placeholder;
  Phase 7 wires shot-aware estimates from real KPIBundles.
- Pricing rates are placeholders; ops refresh via `phoenix admin
  pricing-update` (Phase 8 endpoint).
- Drift buffer is unbounded in Phase 4 (R6); Phase 4's Router doesn't
  consume it -- Phase 7 will.

### Version + manifest

- `pyproject.toml`, `phoenix/_internal/version.py`: `1.0.0.dev3` ->
  `1.0.0.dev4`.
- `vendor/VENDOR_VERSION.txt` regenerated (`phoenix_release: 1.0.0.dev4`,
  `vendor_synced_at` refreshed).

---

## [1.0.0.dev3] — 2026-05-08

Phase 3 shipped. Trinity Core's Control and Orchestrate subsystems are
now wired through the pipeline end-to-end. `POST /v1/tasks` returns the
architecturally-correct `Result` envelope (top-level value, error_bar,
sigma, agreement_type, kpi_bundle_orchestrate, three-layer ProvenanceTrace
with cloud_shots_recorded mirror per Section 1 Decision 20). The
`phase_2_solver_only` honesty marker is retired; everything reads
`phase_3_solver_control_orchestrate`. Six of seven Orchestrate modules
ship in Phase 3; `cross_provider.py` and `CrossProviderAxis` (Axis 3)
defer to Phase 5 alongside the verification gate's rung table.

### Locked scope decisions (2026-05-08, executed)

1. Axis 3 fully deferred to Phase 5. No `cross_provider.py`, no
   `CrossProviderAxis` class in Phase 3. Aligns with the orchestrate/README
   timeline.
2. `LocalClassicalSimulator` is the only Phase 3 `BaseProviderClient` impl,
   landing at `phoenix/providers/classical/local_simulator.py`. Phase 4
   adds cloud quantum adapters as siblings.
3. Default verification depth = `R3_TWO_AXES`. The pipeline runs Axis 1 +
   Axis 2 by default. Phase 5's rung-table promotion logic is not in scope.
4. Typed `KPIBundle` introduced; `data_model.py` field types tightened from
   `dict[str, Any]` to `KPIBundle` per Section 2.5.

### What landed (commits 3c910d8 → eb20d36)

- **Typed `KPIBundle`** (`phoenix/trinity/orchestrate/kpi_bundle.py`):
  Phoenix-native frozen dataclass with `fidelity`, `latency_us`,
  `backaction`, `shots_used`, `shot_budget`, `status` per Section 2.5.
  `KPIStatus` enum: `OK` / `WARN` / `FAIL`.
- **Data model tightening** (`phoenix/trinity/data_model.py`):
  `VerifiedAnswer.dpd_result` (`Any` → `DPDResult`),
  `VerifiedAnswer.kpi_bundle_control` and `Result.kpi_bundle_orchestrate`
  (`dict[str, Any]` → `KPIBundle`), `ProvenanceTrace.control` and
  `.orchestrate` (`Any` → typed). New `ControlProvenance` and
  `OrchestrateProvenance` dataclasses.
- **Control engine adapter** (`phoenix/trinity/control/engine.py`):
  `run_dpd(candidate, *, probe_strength=0.1,
  hardware_modality="superconducting") -> ControlRunResult`. Wraps the
  vendored `DPDScheduler.execute()` against a `|0⟩⟨0|` placeholder
  density matrix (Phase 5 wires the real eigenstate). SAFETY: raises
  `ControlVerificationError` on `trace_preservation` drift > 1e-3 or
  positivity violation.
- **Cross-control wobble (Axis 2)** (`phoenix/trinity/control/cross_probe.py`
  + `phoenix/verification/wobble_axis.py`): trace-distance metric
  `T(ρ₁, ρ₂) = (1/2) Σ |λᵢ(ρ₁ - ρ₂)|` per the plan's R1 risk decision.
  `CrossControlAxis` registers as the second concrete `WobbleAxis`
  Protocol impl. R3+ runs eps=0.1 + eps=0.5 sweep; the weak-probe leg
  doubles as the canonical run for the pipeline (PERF win ~1-2 s saved).
  Optional `prior_high_grid_result` constructor injection lets the
  pipeline skip a redundant solver call.
- **Orchestrate scaffolding** (4 new modules under
  `phoenix/trinity/orchestrate/`): `provider_client.py` (BaseProviderClient
  Protocol + ProviderSubmission/RawResult dataclasses + ProviderError
  hierarchy), `bundle_builder.py` (pure translator with deterministic
  16-char SHA-256 bundle hash), `result_extractor.py` (raw result + KPI
  composer), `drift_feedback.py` (in-memory DriftSignal buffer for
  Phase 4's Router intelligence layer to drain).
- **Router data model** (`phoenix/router/data_model.py`):
  `RoutingDecision` + `ProviderSelection` typed dataclasses;
  forward-compat with Phase 4's Router producer.
- **LocalClassicalSimulator** (`phoenix/providers/classical/local_simulator.py`):
  the only Phase 3 concrete `BaseProviderClient`. Synchronous trace
  expectation against the verified ρ; `cloud_shots_recorded=False`.
- **Orchestrate engine** (`phoenix/trinity/orchestrate/engine.py`):
  top-level `orchestrate(verified, selection, ...)` that sequences
  bundle_builder → provider_client.submit → result_extractor →
  drift_feedback and produces `(Result, OrchestrateProvenance)`.
  Quadrature combiner per Section 11.1.1 placeholder; agreement_type
  mapping per the vendored `DisagreementType` enum (HEDGED_CONSENSUS /
  UNKNOWN; Phase 5 extends).
- **Three-layer pipeline** (`phoenix/trinity/pipeline.py`):
  `solve(task) -> Result` (return type promoted from `CandidateAnswer`).
  Default depth `R2_CROSS_PRECISION` → `R3_TWO_AXES`. Layer 1 runs Axis
  1 (cross-precision); Layer 2 runs Axis 2 (cross-control) with
  prior_high_grid_result injection; Layer 3 dispatches Orchestrate via
  default `_build_default_provider_selection(task)` pointing at
  `LocalClassicalSimulator`. Provenance composition stitches all three
  sub-traces into `ProvenanceTrace` with `cloud_shots_recorded` mirror.
- **POST /v1/tasks promotion** (`phoenix/api/routes.py`): response shape
  changes from `candidate_answer`-wrapped to top-level `value`,
  `error_bar`, `sigma`, `agreement_type`, `kpi_bundle_orchestrate`,
  flattened `provenance` with solver/control/orchestrate sub-blocks.
  HTTP 422 added for `ControlVerificationError`; HTTP 502 added for
  `OrchestrateProviderError`.

### Tests

- 50 tests passing (was 34 at end of Phase 2; +16 from Phase 3).
  - 5 new unit-test files: `test_kpi_bundle.py` (3 tests),
    `test_control_engine.py` (3 tests including a synthetic-injection
    `ControlVerificationError` exercise), `test_cross_control_axis.py`
    (4 tests including the prior_high_grid_result PERF path),
    `test_local_simulator.py` (3 tests including unknown bundle_kind
    refusal), `test_orchestrate_engine.py` (3 tests including a
    BrokenProvider stub for failure propagation).
  - Phase 2 `test_pipeline.py` and `test_solve_endpoint.py` adjusted in
    Steps 8+9 to assert the new Result envelope shape.
- Pre-commit hooks: ruff, ruff-format, mypy strict, pytest smoke -- all
  4 pass.

### Out of scope for Phase 3 (explicit deferrals)

- Cross-provider wobble (Axis 3) and `cross_provider.py` -- Phase 5
  alongside the verification gate's rung-table orchestrator.
- Real eigenstate plumbing (replaces the `|0⟩⟨0|` placeholder; surfaces
  non-zero Axis 2 trace distance signal) -- Phase 5.
- Real observable extraction at the local simulator (replaces the trace
  expectation placeholder) -- Phase 5.
- Cloud quantum providers (IBM Eagle / Braket / IonQ) and the Router
  producer -- Phase 4.
- Adaptive rung selection driven by `max_error_bar` -- Phase 5.
- Tasks list / get / replay / approve_promotion / cancel endpoints --
  Phase 3+ once the ledger backs them.
- WebSocket events (Section 5.3) -- Phase 5+ with the gate.
- Actor verification at the front door -- Phase 6.

### Open tensions touching Phase 3 (per plan risk register)

All deferrals or known-placeholders, none blocking:

- **R1 (cross-control metric):** Phase 3 ships trace distance; metric name
  in `CrossControlDisagreement.metric` and `AxisResult.metadata` so Phase
  5's gate composer can introspect or override.
- **R2 (DPD initial ρ placeholder):** `|0⟩⟨0|` in dim=2; Phase 5 wires
  multi-state ρ from the high-grid `SolverRunResult`.
- **R4 (quadrature combiner):** Section 11.1.1 OPEN; Phase 3 ships the v0
  placeholder, per-axis bars recorded in provenance for v1.1 covariance
  refinement.
- **R5 (agreement_type mapping):** Phase 3 maps
  "all axes agree within tolerance" → `HEDGED_CONSENSUS`; Phase 5's
  `agreement_classifier` extends the vendored enum with the
  architecture-spec values.
- **R6 (drift buffer unbounded):** Phase 4's Router intelligence layer
  will drain on schedule; Phase 4 may add ring-buffer semantics.

### Version + manifest

- `pyproject.toml`, `phoenix/_internal/version.py`: `1.0.0.dev2` ->
  `1.0.0.dev3`.
- `vendor/VENDOR_VERSION.txt` regenerated (`phoenix_release: 1.0.0.dev3`,
  `vendor_synced_at` refreshed, `dr_frank_and_eddy_commit` unchanged).

---

## [1.0.0.dev2] — 2026-05-08

Phase 2 shipped. Trinity Core's Solver subsystem is wired through the front
door end-to-end. `POST /v1/tasks` accepts a `SolveRequest`, dispatches via
the vendored `HamiltonianClassifier`, runs cross-precision wobble (Axis 1)
at `RungDepth.R2_CROSS_PRECISION`, and returns a `CandidateAnswer` with
the `phase: phase_2_solver_only` honesty marker. Phase 3 promotes the
return type to a full `Result` envelope once Control + Orchestrate land.

### What landed (commits ba1100d → this release)

- **Trinity Core data model** (`phoenix/trinity/data_model.py`): seven
  frozen dataclasses -- `ToleranceSpec`, `SolverProvenance`,
  `ProvenanceTrace`, `PhysicsTask`, `CandidateAnswer`, `VerifiedAnswer`,
  `Result` -- plus their supporting types. `agreement_type:
  DisagreementType` per the 2026-05-08 drift correction (vendored class
  name, not the v1.0 spec drift `AgreementType`).
- **Latency tier dial** (`phoenix/_internal/latency.py`): `LatencyTier`
  enum with `BATCH_REALTIME` / `STREAMING_REALTIME` /
  `PERCEPTION_REALTIME` plus `LatencyTierNotImplemented` typed exception.
  v1 routes only `BATCH_REALTIME`; the other two are
  defined-but-not-routable per the v1.1 follow-up locked 2026-05-08.
- **`WobbleAxis` Protocol** (`phoenix/verification/wobble_axis.py`): the
  Protocol contract that parameterizes Phase 5's verification gate, plus
  `RungDepth` enum, `AxisResult` dataclass, and the first concrete impl
  `CrossPrecisionAxis`. Perception extension's three axes at Phase 20 plug
  in as additional `WobbleAxis` impls without forking the gate.
- **Solver engine adapter** (`phoenix/trinity/solver/engine.py`): wraps the
  vendored `EquationSolver` registry into Phoenix's `PhysicsTask` ->
  dispatched-solver flow. `pick_solver()` honors `regime_hint` override
  on `PhysicsTask.metadata`; `run_solver()` runs at a specified grid
  resolution and returns a typed `SolverRunResult`. Frontier-physics
  regime gate raises `FrontierPhysicsRefused` for Wheeler-DeWitt /
  Gravitational Decoherence / Semiclassical Gravity without
  `frontier_physics=True` permission (architecture Decision 7).
- **Cross-precision wobble logic** (`phoenix/trinity/solver/cross_precision.py`):
  pure-function `compute_cross_precision_disagreement(low, high)` that
  preserves the full pairwise distance row alongside the scalar per
  Section 6.2's DO-NOT-COLLAPSE invariant.
- **Trinity Core pipeline** (`phoenix/trinity/pipeline.py`): `solve(task)
  -> CandidateAnswer` orchestrates the Solver-only path. Latency-tier
  gate refuses non-routable tiers with typed exceptions naming the
  release that ships support. Reuses Step 4's high-grid `SolverRunResult`
  (stashed in `AxisResult.metadata["high_grid_result"]`) to extract the
  canonical value -- saves one solver invocation per solve.
- **Front-door endpoint** (`phoenix/api/routes.py`): `POST /v1/tasks`
  accepts `SolveRequest`, returns Solver-only response with
  `reproducibility_asterisk`. Status code mapping: 200 success, 400 bad
  latency_tier or no eligible solver, 403 frontier-physics refused, 501
  latency tier defined-but-not-routable.

### Tests

- 34 tests passing (was 19 at end of Phase 1; +15 from Phase 2).
  - Phase 2 unit tests: 3 (CrossPrecisionAxis) + 5 (pipeline) = 8.
  - Phase 2 integration tests: 7 (POST /v1/tasks).
- Pre-commit hooks: ruff, ruff-format, mypy strict, pytest smoke -- all 4 pass.

### Out of scope for Phase 2 (explicit deferrals)

- Cross-control wobble (Axis 2) and cross-provider wobble (Axis 3) full
  impls land in Phase 3 (axis classes) + Phase 5 (gate orchestration).
- Verification gate's full rung table (R1-R5) and adaptive promotion
  logic land in Phase 5.
- Tasks list / get / replay / approve_promotion / cancel endpoints land
  in Phase 3+ once the ledger backs them.
- WebSocket events (Section 5.3) land with the verification gate at Phase 5+.
- Actor-verification at the front door lands in Phase 6.

### Version + manifest

- `pyproject.toml`, `phoenix/_internal/version.py`: `1.0.0.dev1` ->
  `1.0.0.dev2`.
- `vendor/VENDOR_VERSION.txt` regenerated (`phoenix_release: 1.0.0.dev2`,
  `vendor_synced_at: 2026-05-08T18:05:38+00:00`, `dr_frank_and_eddy_commit`
  unchanged at `fa074e5e...`).

---

## [Architecture v1.1 follow-up] — 2026-05-08

Documentation-only follow-up to the 2026-05-07 v1.1 revision. Phoenix-the-package stays at `1.0.0.dev1`; no implementation impact. Captures five architectural decisions Adam approved on 2026-05-08 that future-proof v1 for the perception extension without writing perception code, plus three spec-vs-source drift corrections.

### Architectural future-proofing additions

- **`WobbleAxis` Protocol parameterization (Section 6.3, locks Phase 5 design intent).** The verification gate is parameterized by a list of `WobbleAxis` Protocol implementations rather than hardcoding three named methods. v1 ships three concrete impls (`CrossPrecisionAxis`, `CrossControlAxis`, `CrossProviderAxis`) in `phoenix/verification/wobble_axis.py`. Perception extension's Phase 20 axes (`CrossModalityAxis`, `CrossFrameAxis`, `CrossCanonicalAxis` per the perception plan) plug in as additional `WobbleAxis` impls without forking the gate. Same machinery, different axes. Section 10.3 phoenix/verification/ file list updated with the new `wobble_axis.py` entry.

- **`CloudSeams` generic name-keyed registry (Section 10.3.1).** Refactored from three hardcoded slots (`auth: HttpAuthExtractor`, `audit: AuditLogExporter`, `budget: JobBudgetController`) to a generic dict keyed by name with `register(name, impl)` / `get(name)` / `names()` methods. Default constructor still registers v1's three seams; v1.x extensions register additional seams without core changes. The perception extension's optional fourth seam (`canonical_library` for hosted retention-SLA-bearing canonical-example libraries) plugs in via the same `register()` API. Protocol contracts and SAFETY guarantees unchanged.

- **`LatencyTier` enum (Section 1, post-Decision-28 paragraph).** Three tiers encoded as a single enum in `phoenix/_internal/latency.py`: `BATCH_REALTIME` (v1, routable), `STREAMING_REALTIME` (v2, defined-but-not-routable), `PERCEPTION_REALTIME` (v1.1 perception phase, defined-but-not-routable). v1 routes only `BATCH_REALTIME`; raises typed `LatencyTierNotImplemented` for the other two. Routing layer accepts the tier as a parameter from day one so the perception extension at Phase 12+ doesn't have to retroactively add an enum value or churn callers. **Section 11.14.7 (perception real-time latency tier) RESOLVED** by this enum; open-tension count drops 17 → 16.

- **Front-door namespacing (Section 5).** Decision recorded: `/v1/...` flat with implicit physics semantics. Perception slots in as `/v1/perception/*` sibling (per the perception plan). No spec change required — current spec already commits to this — but recorded for clarity.

- **Strict no-perception-code-in-v1 discipline.** Decision recorded: v1 ships zero perception-shaped code. The v1.1 spec sections (11.14, 10.8) are the only acknowledgments. No empty `phoenix/perception/` or `phoenix/sensors/` directories during v1; perception phase 12 build guide drafts only after v1 Phase 5 milestone per the existing perception plan guardrail.

### Spec-vs-source drift corrections

Three architectural drifts between spec and the actual vendored substrate, surfaced during Phase 1 execution and logged for follow-up:

- **`AgreementType` → `DisagreementType`** (Section 2.2 Result envelope, Section 6.2 vendored types block + Phoenix extension block, prose around line 1165). Spec called the wobble enum `AgreementType`; vendored frank-data has `class DisagreementType(Enum)`. Spec drifted; vendored is source-of-truth per Section 11.7.1's verbatim-through-v1 disposition. Phase 1 tests already use the vendored name; spec now follows reality. Field name `agreement_type` kept (it describes the semantic concept); type renamed to `DisagreementType` (matches the vendored class).
- **`DPDEngine` → `DPDScheduler`** (Section 0 Control description, Section 10.3 phoenix/trinity/control/engine.py description). Same shape: spec drifted from vendored `class DPDScheduler` in `synthesis/core/dpd_engine.py`.
- **`ProbeType.STRONG` → `ProbeType.STRONG_PROJECTIVE`** (Phase 1 build guide content was updated during Phase 1 execution; no architecture spec drift to correct since the spec uses prose "strong projective", not the enum value name). Logged for completeness.

### Spec consistency cleanup (bonus)

- **Section 0 Orchestrate paragraph** updated to reflect the 2026-05-06 SynQc-greenfield revision. The 2026-05-06 commit updated Section 2.5 (Orchestrate as greenfield) but missed Section 0's intro paragraph, which still claimed "Orchestrate vendors the SynQc TDS Core framework." Now correctly describes Orchestrate as greenfield Phoenix code with seven Phoenix-native modules and SynQc as design reference.

### README count update

- README "Documents" section: tension count updated 17 → 16 (with 11.14.7 resolution noted).

### Process notes

- Five architectural decisions and three drift corrections all approved by Adam on 2026-05-08 via a structured-options review of the v1.1 follow-up scope.
- Seven decision points in total: A (verification gate parameterization), B (cloud seams generic registry), C (API namespacing), D (`LatencyTier` enum), E (strict no-perception-code), F (spec drifts), G (commit shape: two commits — this is the second).
- v1.1 is now a two-step revision: 2026-05-07 captured the perception extension scope and 7 tensions; 2026-05-08 locked the v1-side future-proofing and resolved the 7th tension. v1's Phase 0 → Phase 11 build pipeline remains unchanged.

---

## [Architecture v1.1] — 2026-05-07

Architecture-only revision; no package version bump. Phoenix-the-package stays at `1.0.0.dev1`. This entry documents the v1.0 → v1.1 spec revision triggered by the perception harness extension plan locking.

### Added

- **`PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md`** — extension plan locked at v1, 692 lines. All 21 open questions from the v0 draft resolved with Adam's recorded dispositions. Plan positions the perception harness as a v1.x extension landing at Phase 12 onwards (after v1 ships at Phase 11), reusing 70-80% of v1's substrate (vendored Sanskrit codec, grammar substrate, wobble framework, Actor authentication, Omega Ledger pattern, cloud seams). Six Sanskrit techniques scoped: kāraka, chanda, vivakṣā, anuvṛtti, paribhāṣā, lakṣaṇa-lakṣya. Plus Penrose spatial tilings and the IP-defensible novel temporal pulse-coding work (Phase 16).
- **`PHOENIX_ARCHITECTURE_v1.md` Section 11.14** — new tension category for perception extension. Seven entries: 11.14.1 (placement, RESOLVED v1.1), 11.14.2 (substrate vendoring scope, build-guide territory), 11.14.3 (sensor ingest layer placement, RESOLVED v1.1), 11.14.4 (canonical example library storage, RESOLVED v1.1), 11.14.5 (Penrose hardware integration, RESOLVED v1.1 with Q5.2 scalability constraint), 11.14.6 (perception verification axes count, deferred v1.x perception milestone), 11.14.7 (perception real-time latency tier, recommended for documentation).
- **`PHOENIX_ARCHITECTURE_v1.md` Section 10.8** — perception harness extension acceptance criterion. v1.1 acceptance now includes: all perception phases (12-22) shipped per the plan; Tier-1 perception calibration battery passing for supported weather modes; three-axis perception wobble verification producing typed Results; Penrose pulse-train simulator demonstrating ≥20% reconstruction-error reduction at 20% rain corruption; Phase 16 Q5.2 scalability gate (mock hardware driver swap test); front-door endpoints (REST, WebSocket, CLI, MCP) exercising the perception pipeline end-to-end; cross-protocol audit-log correlation working.
- **`PHOENIX_ARCHITECTURE_v1.md` document header** — v1.1 revision date (2026-05-07) and revision summary added to the status line and date timeline.
- **`PHOENIX_ARCHITECTURE_v1.md` Section 0** — new v1.1 transition paragraph documenting the perception extension's positioning as v1.x extension and the documentation-only nature of the revision.
- **`README.md` Documents section** — new "Future extension planning (not part of locked v1)" subsection pointing to `PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md`. Status table row added: "Perception harness extension | LOCKED v1 plan — awaiting Phase 11 release for Phase 12 build guide drafting".

### Changed

- **`PHOENIX_ARCHITECTURE_v1.md` Section 11.9** (Summary of dispositions) — updated to reflect the 7 new tensions added in v1.1. Open-tension count: 14 (v1.0) → 17 (v1.1: 14 + 3 unresolved from v1.1's 11.14.x catalog).

### Critical scalability constraint locked (Q5.2 expansion)

Adam's Q5.2 disposition added a binding architectural principle to Phase 16 that did not exist in the v0 plan: the Penrose temporal pulse-coding simulator must be architected so hardware integration in Phoenix v2 lands as a new driver implementing the same Protocol interfaces, not as a rewrite. Phase 16's deliverables now explicitly include `LidarTransmitter`, `LidarReceiver`, and `InterferenceModel` Protocols defined in `phoenix/perception/penrose/temporal/interfaces.py`. Phase 16's stop gate explicitly tests Protocol compatibility via a mock hardware driver swap. This constraint is binding from Phase 16 day one, not a v2 retrofit, and is recorded in both the perception plan v1 (Section 5, Phase 16) and the architecture v1.1 (Section 11.14.5).

### Process notes

- 21 open questions from `PHOENIX_PERCEPTION_HARNESS_PLAN_v0.md` Section 12 reviewed by Adam on 2026-05-07; all 21 dispositions recorded in v1's Section 12. Adam approved all 21 of Claude's recommendations, with one explicit expansion on Q5.2 (the scalability-on-top constraint).
- v0 of the plan (`PHOENIX_PERCEPTION_HARNESS_PLAN_v0.md`) preserved as historical record alongside v1; may be deleted at Adam's discretion.
- v1.1 is a documentation-only revision. v1.0's load-bearing structure (seven layers, three peer engines in Trinity Core, mandatory three-axis quantum wobble, hashchained provenance, Phoenix Cloud commercial path, all v1 acceptance criteria from Section 10.7) is unchanged. No v1 implementation impact; Phase 0 → Phase 11 build pipeline proceeds unchanged. Perception extension work begins at Phase 12 only after v1 reaches its Phase 5 verification-gate milestone.

---

## [1.0.0.dev1] — 2026-05-06

Phase 1 lands. The vendored substrate from `dr-frank-and-eddy` is real,
calibrated, and exercisable through Phoenix's package boundary. Trinity
Core's Solver and Control subsystems now have the substrate they need
when Phases 2–3 wire them through the pipeline; Orchestrate stays
greenfield per the 2026-05-06 architecture revision.

### Added

- **Step 0 — frank-data housekeeping.** Adam's lab bench at
  `C:\frank-data\` cleaned: 4 untracked items moved to git
  (DrFrankEddy_Capabilities_Overview_for_Ash.md, evolution/candidates/epoch_0001/),
  `electron-debug.log*` added to `.gitignore`. Commit `fa074e5` on
  `nah414/dr-frank-and-eddy/wave-a-through-f-merge`. Prep for the
  vendor source clone.
- **Step 1 — vendor source workspace.** `C:\Phoenix-vendor-source\frank-data\`
  cloned at commit `fa074e5` (sibling of `C:\Phoenix\`, never inside).
  `Phoenix-vendor-source/` added to Phoenix `.gitignore` defensively.
- **Step 2 — vendor sync infrastructure.** `scripts/vendor_sync.py`
  (~370 lines, type-annotated, mypy-strict-clean) + `scripts/vendor_manifest.json`.
  Eight typed errors, four frozen dataclasses, five CLI modes
  (default / --validate-only / --dry-run / --target / --update-version-manifest /
  --generate-calibration). Admin-gate placeholder via `PHOENIX_ADMIN_OVERRIDE=1`
  env var (Phase 6 replaces with safety-gate Actor check).
- **Step 3 — frank-data substrate vendored.** 56 files / 379 KB copied
  per the manifest's 11 mappings: `vendor/synthesis/equations/` (29
  files, 12 solvers + base + registry + llm_context + 12 specs +
  README + __init__), `vendor/synthesis/core/` (6: dpd_engine,
  lindblad_rk4, probe_model, hardware_backends + README + __init__),
  `vendor/synthesis/quantum/tensor_lindblad.py`, `vendor/grammar/`
  (11: 6 grammar files + 5 codec files), `vendor/actor/actor.py`,
  `vendor/wobble/` (6 files). `pyproject.toml` `[tool.ruff]` gains
  `extend-exclude = ["vendor"]` so vendored substrate keeps upstream
  formatting verbatim.
- **Step 4 — sys.path injection.** `phoenix/__init__.py` defines
  `_inject_vendor_path()` that appends `C:\Phoenix\vendor\` to
  `sys.path` on package load. Vendored modules now import at their
  upstream paths (`from synthesis.equations.base import EquationSolver`).
  `[[tool.mypy.overrides]]` block silences mypy on `synthesis.*`,
  `wobble.*`, `grammar.*`, `actor.*` (the vendored code has no type
  stubs and is excluded from analysis already).
- **Step 5 — calibration profile generation.** `vendor/calibration_profile.json`
  (3.9 KB) ships with the source-side calibration suite results: 32/32
  tests passing in 1.4 seconds, all module-level physical constants
  captured (HBAR, M_ELECTRON, MU_BOHR, C_LIGHT, G_NEWTON, EV_TO_JOULE),
  source commit + branch + ISO timestamp. `vendor/VENDOR_VERSION.txt`'s
  `calibration_profile_hash` field now populated.
- **Step 6 — Tier-1 + invariants + DPD test infrastructure.** 13 new
  Phoenix-side tests across three directories:
  - `tests/tier1/`: 5 nominal Tier-1 benchmarks (HO-1 QHO, ISW-1 PIB,
    H1S-1 Dirac, RABI-1 Pauli/Zeeman, SCG-1 weak-field gravity).
  - `tests/invariants/`: 4 grammar invariants (load, safe-load
    discipline, generate-then-parse round-trip, bounded generation).
  - `tests/dpd/`: 4 DPDScheduler structural tests.
  - Runtime deps grow: `numpy>=1.26,<3.0`, `scipy>=1.11,<2.0`,
    `pyyaml>=6.0,<7.0`.
- **Step 7 — Phase 1 acceptance + push.** Version bumps `1.0.0.dev0` →
  `1.0.0.dev1` in `pyproject.toml`, `phoenix/_internal/version.py`,
  test assertions. `vendor/VENDOR_VERSION.txt` regenerated with
  `phoenix_release: 1.0.0.dev1`. `/v1/health` end-to-end check confirms
  daemon serves dev1 with full vendor manifest.

### Changed

- `phoenix.__version__`: `1.0.0.dev0` → `1.0.0.dev1`.
- `vendor/VENDOR_VERSION.txt`: all four hash fields populated (was
  Phase 0 placeholder with empty values).
- `tests/unit/test_smoke.py::test_internal_version_module`: now asserts
  `vendor_synced_at`, `dr_frank_and_eddy_commit`, `calibration_profile_hash`
  are non-empty (Phase 0 had asserted them empty as the placeholder).
- `tests/integration/test_health.py::test_health_returns_200_and_expected_shape`:
  same flip on the `/v1/health` response shape assertions.

### Open architectural drifts (logged for follow-up before Phase 5)

Three spec-vs-source naming drifts surfaced during Phase 1 execution
when the test code touched the actual vendored API:

1. **`AgreementType` (spec §6.2) vs `DisagreementType` (vendored).**
   The architecture spec names the wobble enum `AgreementType` with
   extended physics-wobble values; the actual vendored `wobble/disagreement_types.py`
   has `class DisagreementType(Enum)` (the upstream cognition-wobble name).
2. **`DPDEngine` (spec) vs `DPDScheduler` (vendored).** Spec references
   `DPDEngine`; the actual vendored class in `synthesis/core/dpd_engine.py`
   is `DPDScheduler`.
3. **`ProbeType.STRONG` (spec implied) vs `ProbeType.STRONG_PROJECTIVE`
   (vendored).** The vendored enum value spells out `STRONG_PROJECTIVE`,
   `WEAK_MEASUREMENT`, `ANCILLA_BASED`, `NONE`.

Phase 1's Phoenix-side tests use the real (vendored) class names and
pass. A single spec-drift-correction commit before Phase 5 (verification
gate work) will resolve all three: rename in spec, alias on Phoenix
side, or accept upstream names as authoritative.

### Acceptance

Phase 1 acceptance (build guide §3.7):
- ✅ `vendor/` populated with frank-data content; `VENDOR_VERSION.txt`
  has all four fields (phoenix_release, vendor_synced_at,
  dr_frank_and_eddy_commit, calibration_profile_hash) with real values.
- ✅ `vendor/calibration_profile.json` exists, hash matches `VENDOR_VERSION.txt`.
- ✅ `python -c "from synthesis.equations.base import EquationSolver"` works.
- ✅ `pytest tests/tier1/`: 5/5 (HO-1, ISW-1, H1S-1, RABI-1, SCG-1).
- ✅ `pytest tests/invariants/`: 4/4 grammar invariants.
- ✅ `pytest tests/dpd/`: 4/4 DPD structural tests.
- ✅ `pytest tests/`: 19/19 combined.
- ✅ `pre-commit run --all-files`: ruff, ruff-format, mypy strict, smoke -- all 4 Passed.
- ✅ `python -m phoenix.api --port 8003`: daemon boots; `GET /v1/health`
  returns `phoenix_version=1.0.0.dev1` and the full vendor manifest.
- ✅ `git status`: working tree clean after Step 7 commit.

### Process notes

- 7 phase-gated commits + 1 housekeeping commit in `frank-data`. Each
  Phoenix-side step ended at `=== STEP N COMPLETE — AWAITING ADAM REVIEW ===`;
  no auto-advancement.
- Build-guide sequencing fix: Phase 1's Step 4 (sys.path) and Step 5
  (calibration generation) both surfaced live in execution rather than
  ahead of time -- they were `[OPEN: ...]` items in the Phase 1 build
  guide that resolved as the code was written.

---

## [1.0.0.dev0] — 2026-05-06

The repository skeleton lands. No physics yet; this release is the foundation
that subsequent phases build on. All eight Phase 0 build-guide steps executed
through phase-gated review with Adam; final acceptance verified end-to-end.

### Added

- **Architecture specification** at v1 (`PHOENIX_ARCHITECTURE_v1.md`, ~2,900
  lines covering Trinity Core's three subsystems, the seven wrapping layers,
  mandatory three-axis wobble verification, hashchained Omega Ledger
  provenance, end-to-end cost-ceiling enforcement, the Phoenix Cloud
  commercial path, and 14 catalogued open design tensions).
- **Phase 0 build guide** (`BUILDGUIDE_phoenix_v1_phase0_skeleton.md`)
  directing the eight-step skeleton work with phase-gated reviews between
  each step.
- **Top-level scaffolding** (Step 1): `pyproject.toml` with pinned upper
  bounds and `>=3.11,<3.14` Python constraint; `requirements.lock`
  placeholder for Phase 1's `uv` lockfile; `.gitignore`, `.gitattributes`,
  `.pre-commit-config.yaml` (ruff + mypy strict + smoke-test), `CHANGELOG.md`.
- **`phoenix/` package skeleton** (Step 2): 26 directories with `__init__.py`,
  one per architectural Section. Two non-empty: `phoenix/__init__.py` exports
  `__version__`; `phoenix/_internal/version.py` defines the constant +
  `read_vendor_version()`.
- **`vendor/` scaffold** (Step 3): directory + `VENDOR_VERSION.txt`
  placeholder with `phoenix_release: 1.0.0.dev0` and four hash fields empty
  (Phase 1 vendor sync populates).
- **Launcher chain** (Step 4): `scripts/launch.bat`, `scripts/launch.sh`,
  `scripts/create_shortcut.ps1` — Phoenix daemon on port 8003 (port 8002
  reserved for dr-frank-and-eddy).
- **29 per-section READMEs** (Step 5): 21 across `phoenix/`+`vendor/` +
  8 in the `evals/` audit/debug scaffold (audit, ledger, replay, drift,
  routing, cost_ceiling, frontier_physics).
- **Test infrastructure + FastAPI daemon** (Steps 6+7 combined due to
  inter-step dependency): `tests/unit/test_smoke.py` (3 tests),
  `tests/integration/test_health.py` (2 tests using FastAPI TestClient),
  `phoenix/api/routes.py` (FastAPI app exposing `GET /v1/health`),
  `phoenix/api/__main__.py` (`python -m phoenix.api --port 8003`),
  `phoenix/api/error_envelope.py` (typed envelope dataclass per §5.2).

### Changed

- **LICENSE**: switched from MIT (auto-generated at repo creation) to Apache 2.0
  per architecture v1 Decision 34 — open source plus the patent grant as
  belt-and-suspenders against future patent claims on calibration methodology.
- **README**: expanded from the one-line repo-creation placeholder to a
  project-shaped overview with the v1 status table and pointers to the
  architecture spec and the Phase 0 build guide.

### Fixed

- **PEP 440 version compliance**: setuptools rejected the originally-chosen
  `1.0.0-phase0` literal as not-PEP-440. All artifacts now use `1.0.0.dev0`
  (Phase 0 development pre-release per PEP 440); subsequent phases use
  `1.0.0.dev1`, `1.0.0.dev2`, etc.
- **Python version constraint**: widened from `>=3.11,<3.13` to `>=3.11,<3.14`
  to accommodate the actual development environment (Python 3.13.9). The
  upper-bound discipline from the dep-tightening pass is preserved (3.14
  stays gated until validated).

### Acceptance

Phase 0 acceptance criteria from build guide §3.8 (15 items):

- ✅ `python -c "import phoenix; print(phoenix.__version__)"` returns `1.0.0.dev0`.
- ✅ `pytest tests/` passes 5/5 (3 unit + 2 integration via FastAPI TestClient).
- ✅ `pytest evals/ --collect-only` exits 5 ("no tests collected") — expected
  Phase 0 state since `evals/` ships placeholder READMEs only.
- ✅ `python -m phoenix.api --port 8003` boots the daemon; `/v1/health`
  responds with the expected contract; `/v1/openapi.json` serves OpenAPI 3.1
  with the matching version.
- ✅ All 29 READMEs present and non-empty.
- ✅ `pre-commit install` placed the hook; `pre-commit run --all-files` clean
  on all four hooks (ruff, ruff-format, mypy strict, pytest smoke-test).
- ✅ Working tree clean after commits.
- ⏸ Interactive: browser at `http://127.0.0.1:8003/docs` (Adam-driven).
- ⏸ Interactive: `scripts/launch.bat` end-to-end (Adam-driven).
- ⏸ Interactive: `scripts/create_shortcut.ps1` + double-click flow (Adam-driven).

### Process notes

- Step 6 and Step 7 were combined into a single commit because the build
  guide's sequencing was wrong: the integration test depends on the daemon
  code. Documented in the commit message; a future build-guide revision
  should re-sequence (or merge) the two steps.

### Architecture revision (2026-05-06, post Phase 0)

- **`PHOENIX_ARCHITECTURE_v1.md` revised** to remove the v0 spec's
  internal contradiction around SynQc TDS Core. Decision 37 originally
  said "code skeleton, not literal git fork" but other places in the
  spec (§1 Decision 4, §2.5, §10.2) said SynQc was vendored verbatim
  alongside frank-data. The revised spec aligns with Decision 37: SynQc
  is a *design reference* for Trinity Core's Orchestrate subsystem;
  Orchestrate is greenfield Phoenix code under
  `phoenix/trinity/orchestrate/`, not vendored.
- **Affected sections:** §1 Decisions 4, 5, 7, 9, 37 (reworded);
  §2.5 (rewritten — Orchestrate as greenfield with Phoenix-native
  module breakdown: bundle_builder, provider_client, result_extractor,
  drift_feedback, cross_provider, kpi_bundle, engine);
  §10.1 (drops `vendor/synqc_tds/` from directory tree);
  §10.2 (drops the SynQc TDS vendoring table; updates VENDOR_VERSION
  format to remove `synqc_tds_commit` field);
  §10.3 (`phoenix/trinity/orchestrate/` description specifies the
  seven Phoenix-native modules);
  §10.4 (vendor sync script takes only frank-data as input);
  preamble adds a v1-revision transition note.
- **Phase 0 README updates:** `phoenix/trinity/orchestrate/README.md`,
  `phoenix/trinity/README.md`, `phoenix/providers/README.md`,
  `vendor/README.md` updated to match the revised spec.
- **`vendor/VENDOR_VERSION.txt`** drops the `synqc_tds_commit` field.
- **Phase 0 build guide** stale `synqc_tds_commit:` example updated.
- **Phase 1 build guide drafted** at `BUILDGUIDE_phoenix_v1_phase1_vendor_sync.md`
  reflecting the simpler frank-data-only scope (8 phase-gated steps,
  vs. 9 in the original draft that included a SynQc-vendoring step).
- **Discovery driver:** Phase 1 build-guide drafting against actual
  source state (the SynQc zip in Adam's Downloads) found that SynQc's
  module structure (`backend/synqc_backend/` FastAPI service) didn't
  match the v0 spec's named files (`scheduler.py`, `probes/`,
  `demod.py`, `adapt.py`). Live reads beat memory.

The architecture's load-bearing structure (seven layers, three peer
engines, mandatory three-axis wobble, hashchained provenance, Phoenix
Cloud commercial path, fourteen open tensions, all v1 acceptance
criteria) is unchanged. The revision narrows the substrate that
Phoenix vendors and clarifies that Orchestrate is Phoenix-native code
informed by SynQc patterns, not vendored from SynQc.
