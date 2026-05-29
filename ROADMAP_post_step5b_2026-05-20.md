# Phoenix v1.1 Roadmap — Post Step 5b (2026-05-20)

**Status of writing:** synthesis after a codebase-wide audit on 2026-05-20, in response to Adam's "review and analyze the entire codebase so that you can get a good idea of where we are and where our roadmap should point" ask. Conversational chat-side synthesis preserved here for future-Claude reference.

**Authoritative location at commit time:** `C:\Phoenix\.claude\worktrees\phase-13-cognition-mcp-client\ROADMAP_post_step5b_2026-05-20.md`.

---

## Status update (2026-05-28) — 8 days later

Each major item below the original 2026-05-20 snapshot annotated here:

### State items

- **Phase 12 / `1.0.0rc1` / main at `12c9198`** — STILL ACCURATE in fact; **v1.0.0 final tag created 2026-05-28** at `12c9198` ([GitHub Release Latest](https://github.com/nah414/Phoenix/releases/tag/1.0.0)). The `1.0.0rc1` artifact is now content-equivalent to `1.0.0`.
- **Phase 13 implementation (Steps 1-5b in flight on `phase-13-cognition-mcp-client`)** — DONE for the original scope. All 10 Phase 13 steps shipped on `main` via PR #15 merge 2026-05-19 at `1.1.0.dev0`. Phase 13.x sub-improvements followed: `.x.1` / `.x.2` / `.x.3` / `.x.6` merged via 2026-05-27 PR sweep; `.x.4` (classifier integration for cognition replay) merged 2026-05-28 via PR #22 (squash-merge as commit `6d2c043`).
- **Step 5c data work deferred** — STILL DEFERRED. Adam-side research; no deadline; corpus rebalancing + GBM training + macro-F1 verification all pending.
- **PR #13 (Phase 13 docs conditional lock on frank-data LICENSE)** — MERGED 2026-05-18 (commit `07e4fc0`).
- **PR #14 (README phases 6b-12 status backfill)** — MERGED 2026-05-18 (commit `ba8745e`).
- **PR #44 on dr-frank-and-eddy (Apache 2.0 LICENSE)** — STATUS UNCHANGED in Adam's working memory; LICENSE not yet declared (informal-loosening pattern continues; Phase 13 shipping anyway). Re-confirm with Adam when convenient.
- **Test surface 878 + 203 cognition** — SUPERSEDED. The cognition substrate landed on main; Phase 13.x.4's PR #22 adds 21 tests on top.
- **Architectural debt 16 OPEN markers + 3 perception** — DOWN TO 2. PR #16 (`arch v1.1 second-round`, merged 2026-05-28) locked 14 of the 16 v1.0 Section 11 tensions; only the 2 perception items (11.14.2 + 11.14.6) remain open.

### "What's healthy" items

- All three observations (Trinity Core / Phase 13 build-guide discipline / Step 4→5b→5c fallback chain) — STILL TRUE.

### "What's accumulating risk" items

- **`phoenix/admin/` under-tested relative to scope** — STILL TRUE. No focused admin-tests sweep happened. Phase 13.x.4 added one admin endpoint reference but no new admin-suite coverage.
- **`phoenix/verification/drift_detector.py` placeholder stubs** — STILL TRUE. The "Phase 13.5 or absorbed into Step 10" framing remains the recommended path. Distinct concept from Phase 13.x.5 (which is an unallocated sub-improvement numbering slot, audited 2026-05-28).
- **Carried `[OPEN: ...]` markers** — LARGELY RESOLVED via PR #16 (2026-05-28 second-round resolution): error-combiner quadrature, state migrations format, and adaptive-depth formula all locked or deferred-with-disposition.
- **`phoenix/queue/` 13K LOC integration-only** — STILL TRUE. No dedicated unit-test sweep happened.

### "What's theoretical, not load-bearing yet" items

- **13-D5 parallel-with-perception framing** — STILL NOT CONSTRAINING. Perception code is still zero LOC.
- **`1.0.0` final label gated on Adam-side actions** — DONE 2026-05-28. The lightweight-tag option was chosen (no PyPI publish, no Docker rebuild for `:1.0.0` — the existing `:1.0.0rc1` image is content-equivalent).

### Recommended sequencing — what's been done

| Order | What | Status |
|---|---|---|
| 1 | Phase 13 Step 6: MCP-client mode | **DONE** (merged in PR #15) |
| 2 | Admin tests paired with Steps 6/8/9 | **NOT DONE** — still gap |
| 3 | Empirically test quadrature error-combiner | **DEFERRED via PR #16** — locked as v1.1.x revisit, not actively empirical |
| 4 | Step 5c corpus rebalancing + training | **STILL PENDING** (Adam-side research) |
| 5 | 1.0.0 closeout | **DONE 2026-05-28** (lightweight tag) |
| 6 | Drift detector real implementation | **STILL PENDING** — natural next focus session |

### Step 9+ architectural decision (cross-enum mapping)

- The Path A vs Path B recommendation (extend `PhoenixDisagreementType` vs separate-enums-with-mapping) — STATUS UNCERTAIN. Phase 13 main shipped Step 9 (`grant-prompt-verbatim` / `cognition-budget-override` / `cognition-spend audit` admin endpoints); whether the cross-enum mapping decision was made and which path was taken needs a quick code spot-check. Worth a separate audit if Phase 13.5 / drift detector work begins, since drift detector consumes cognition outputs.

### Push-back-and-flag items

- All three observations (13-D5 framing not constraining / Step 5c feature additions wait for rebalance / drift_detector aged into tech debt) — STILL VALID.

### Net read

The original 2026-05-20 sequencing held remarkably well: items 1, 5 done; items 2, 4, 6 still pending in roughly their original framing. The biggest unanticipated win was PR #16's resolution of 14 v1.0 OPEN markers (was framed as a deferred-with-context item; got actively dispositioned). The biggest unanticipated work was the Phase 13.x.N sub-improvement series (.x.1 through .x.4 + .x.6), which added a layer of refinement to Phase 13's substrate that this doc didn't anticipate.

---

## State (2026-05-20)

**Phase 12 shipped at package version `1.0.0rc1`**; main is at commit `12c9198`.

**Phase 13 implementation** (branch `phase-13-cognition-mcp-client`, draft PR [phoenix#15](https://github.com/nah414/Phoenix/pull/15)): 7 commits in flight covering Steps 1-5b. Step 5c data work deferred (200+ examples, trained GBM model, 22 MB embedding artifact, macro-F1 ≥ 0.70 verification); Steps 6-10 unblocked since none depend on Step 5b's macro-F1 gate.

**Other open work:**
- PR [phoenix#13](https://github.com/nah414/Phoenix/pull/13) — Phase 13 docs conditional lock on frank-data LICENSE.
- PR [phoenix#14](https://github.com/nah414/Phoenix/pull/14) — README phases 6b-12 status backfill.
- PR [dr-frank-and-eddy#44](https://github.com/nah414/dr-frank-and-eddy/pull/44) — Apache 2.0 LICENSE for frank-data (resolves 13-D1 dep check for the Adam-authored side; SynQc TDS Core MIT verified separately).

**Test surface:** 878 tests in `tests/` (pre-Phase-13) + 203 cognition tests on the Phase 13 branch.

**Architectural debt:** 16 unresolved `[OPEN: ...]` markers across `PHOENIX_ARCHITECTURE_v1.md` + 3 unresolved perception-extension tensions from the 2026-05-07 v1.1 follow-up.

---

## Key observations from the audit

### What's healthy

- **Trinity Core, safety/, audit/, ledger/ subsystems** are well-tested and structurally sound.
- **Phase 13 build-guide discipline** is holding: each step ends with a `=== STEP N COMPLETE ===` stop gate; no silent merges; OPEN items surfaced at gates.
- **The Step 4 → 5b → 5c fallback chain** (exact-string distance → semantic distance with model fallback → full embedding model on commit) is a clean opt-in upgrade path.

### What's accumulating risk

1. **`phoenix/admin/` is under-tested relative to scope.** Four integration tests covering kill-switch + budget + health combined. Phase 13 Steps 6, 8, and 9 each add new admin endpoints (MCP-server registration, prompt-disposition admin, prompt-verbatim grant). The gap compounds unless tests land alongside each step's endpoint additions.

2. **`phoenix/verification/drift_detector.py` ships with placeholder stubs.** Module-level comment marks "Phase 5 expansion" but it's now Phase 13. With Step 4 cognition axes + Step 8's prompt_disposition columns, the drift detector has real signal shapes to consume — worth a focused phase (Phase 13.5 or absorbed into Step 10) rather than letting drift keep drifting.

3. **Carried `[OPEN: ...]` markers worth resolving on this architectural cycle:**
   - **Error-combiner quadrature formula** (Section 2.3) — Step 4 added 3 cognition axes; combined with 3 physics axes, the combiner is now testable on 6+ axis inputs. Empirical validation possible.
   - **State migrations format** (Phase 6b OPEN) — carried 3+ phases; Phase 13's v3→v4 migration adds without resolving. Worth a 5-minute lock decision.
   - **Adaptive-depth formula** (Section 1 Decision 14) — not published; cognition axes need a depth-decision rule too.

4. **`phoenix/queue/` has 13K LOC tested only via integration.** No dedicated unit-test module for `embedded_runner.py` or `nats_client.py`. Risk is small (the integration tests cover the happy paths) but the embedded-runner's goroutine-like semantics deserve direct unit coverage.

### What's theoretical, not load-bearing yet

- **13-D5 "parallel with perception harness Phase 12"** — perception has zero code; Phase 12 build-guide drafting hasn't started. Phase 13 has Adam's full attention until perception genuinely activates.
- **The `1.0.0` final label** is gated on Adam-side actions (signing certs, macOS box, NATS-bundle verification) + the frank-data LICENSE which is now in flight (PR #44). The rc1 substrate is already shipping-grade.

---

## Recommended sequencing

| Order | Track | What | Why now |
|---|---|---|---|
| **1** | Phase 13 forward | **Step 6: MCP-client mode + per-server registration.** | Doesn't depend on Step 5c. Substantial new surface (registry + admin endpoints + dispatch). Highest velocity. |
| **2** | Hardening (in-flight) | **Admin tests paired with Steps 6/8/9.** | Gap closes cheaply when paired with the step that adds the endpoint; expensive retroactive coverage later. |
| **3** | Carried OPEN | **Empirically test the quadrature error-combiner.** | 6+ axes now feed the combiner; the OPEN from v1.0 Section 2.3 can be validated or revised. |
| **4** | Step 5c (your timeline) | **Corpus rebalancing + training + macro-F1 verification.** | Adam-side research work; not gating other Phase 13 steps. See "Step 5c specifics" below. |
| **5** | 1.0.0 closeout | **Cert procurement + macOS box + NATS bundle verification.** | Mostly Adam-side. rc1 substrate is shipping-grade. |
| **6** | Carried OPEN | **Drift detector real implementation.** | Tech debt with growing weight. Cognition_wobble provenance gives real signals. |

---

## Step 5c specifics (the original ask)

The diagnosis Adam confirmed: **class imbalance dragging macro-F1**. Targeted next moves:

1. **Audit the corpus class distribution.** SAC3 + FELM + FINCH-ZK skew heavy toward FACTUAL_AGREEMENT / FACTUAL_DISAGREEMENT. Likely starting distribution: ~80/80/10/10/10/10 across the six graded classes. Goal: ~28+ per class with ≥3 examples in each "easy" sub-category for the under-represented four.

2. **Phoenix-generation targets for the under-represented four:**
   - **REFUSAL_DIVERGENCE** — pair refusal outputs with successful outputs on the same prompt (capability/policy gray areas, jailbreak-adjacent prompts that trigger refusal on one provider not another).
   - **TOOL_CHOICE_DIVERGENCE** — same query with vs. without tool-use enabled; or with different tool sets exposed.
   - **INTERPRETIVE_DIVERGENCE** — ambiguous-by-design prompts (polysemy: "best language", "handle this case", context-free pronouns).
   - **STYLISTIC_DIVERGENCE** — same fact in casual vs. formal framing (deliberately generated by asking different providers to render the same fact in different registers).

3. **Read the confusion matrix BEFORE retraining.** `CalibrationReport.confusion_matrix` tells you which class pairs the classifier confuses:
   - `FACTUAL_AGREEMENT ↔ STYLISTIC_DIVERGENCE` confusion → features don't separate "same facts" from "same wording" (need a factual-overlap feature distinct from text-overlap).
   - `FACTUAL_DISAGREEMENT ↔ INTERPRETIVE_DIVERGENCE` confusion → need a topical-overlap feature.
   - High `REFUSAL_DIVERGENCE` precision but low recall → refusal patterns too narrow; expand `_REFUSAL_PATTERNS` in `features.py`.

4. **Feature additions to consider AFTER corpus rebalancing:**
   - **Named-entity overlap fraction** — fraction of named entities shared between responses. spaCy small English model is fast enough.
   - **Numerical agreement detector** — when both responses contain numbers, do they match within tolerance? Strong FACTUAL_DISAGREEMENT signal.
   - Both slot into `extract_features()` without retraining the schema — new keys appended to `FEATURE_NAMES`.

5. **The morning Claude's "inflection mismatch" lesson translates:** run per-class audits on real (not synthetic) LLM responses. The substrate-fidelity issue surfaces only when the audit hits production-shaped data.

---

## Step 9+ architectural decision worth pre-locking

Cross-enum mapping from `CognitionDisagreementType` (the cognition substrate's native 7-class enum) to `PhoenixDisagreementType` (Phoenix's union enum) needs to happen when the verification gate gains its `task.kind == "cognition"` branch (Step 9+).

**Recommendation: Path A** — extend `PhoenixDisagreementType` with the six cognition values. Mirrors the existing extension pattern (PhoenixDisagreementType already mirrors `wobble.disagreement_types.DisagreementType`). Less indirection at the gate boundary. 30-second decision lock so Step 9 has a clear target.

**Alternative: Path B** — keep enums separate, add an explicit mapping function at the axis layer. More flexibility for v1.2's cognition-only consumers but adds a translation hop.

---

## What I'd push back on or flag

1. **The 13-D5 "parallel with perception Phase 12" framing isn't currently constraining.** Perception has no code. Phase 13 has full attention; this isn't wrong to acknowledge.
2. **For Step 5c specifically:** don't add NER overlap + numerical-agreement features before the corpus rebalance. Additional features won't help if the training set is 80/80/10/10/10/10. Get the distribution right first.
3. **The `drift_detector.py` "Phase 5 expansion" comment has aged into real tech debt.** Worth a dedicated phase or absorption into Step 10's acceptance battery.

---

*Companion doc to [`BUILDGUIDE_phoenix_v1_phase13_cognition_mcp_client.md`](BUILDGUIDE_phoenix_v1_phase13_cognition_mcp_client.md) + [`DESIGN_DECISIONS_PHASE13_LOCKED_2026-05-18.md`](DESIGN_DECISIONS_PHASE13_LOCKED_2026-05-18.md). Drafted by the Claude Code session that closed Steps 1-5b on `phase-13-cognition-mcp-client`.*
