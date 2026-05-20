# Phoenix v1.1 Roadmap — Post Step 5b (2026-05-20)

**Status of writing:** synthesis after a codebase-wide audit on 2026-05-20, in response to Adam's "review and analyze the entire codebase so that you can get a good idea of where we are and where our roadmap should point" ask. Conversational chat-side synthesis preserved here for future-Claude reference.

**Authoritative location at commit time:** `C:\Phoenix\.claude\worktrees\phase-13-cognition-mcp-client\ROADMAP_post_step5b_2026-05-20.md`.

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
