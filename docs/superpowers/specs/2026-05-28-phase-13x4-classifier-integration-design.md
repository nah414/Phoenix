# Phase 13.x.4 — Classifier integration for cognition replay — Design

**Date:** 2026-05-28
**Author:** Adam (with Claude as design partner)
**Status:** DRAFT — awaiting Adam review
**Type:** v1.1 sub-improvement design (Phase 13.x track)

**Architectural reference:**
- `PHOENIX_ARCHITECTURE_v1.md` Section 6.7 (Omega Ledger), Section 7.4 (verification gate cognition path)
- `BUILDGUIDE_phoenix_v1_phase13_cognition_mcp_client.md` Step 5 (classifier Protocol) and §4.8 (replay disposition contract)
- `phoenix/ledger/cognition_replay.py` line 427 TODO (planned 13.x.4 hook)

**Companion work (already shipped on `main`):**
- PR #20 (`ad35b99`) — cognition replay engine with binary comparator + classifier hook
- Phase 13 Step 5a/5b — `cognition_wobble.classifier` Protocol + GBM / LLM-judge / hybrid impls
- Phase 13 Step 8 (privacy controls) — Omega Ledger schema v4 with `prompt_disposition` + cognition_classifier_provenance

---

## 1 — Context

PR #20 shipped the cognition replay engine with a deliberately binary
comparator: `default_compare_cognition_results` returns
`ComparisonOutcome(matches: bool, reason: str)`. The binary outcome
conflates two distinct cases on text-differs replays:

1. The classifier could confirm the replay is semantically equivalent
   to the original (e.g., model version updated; same facts, different
   surface phrasing). PR #20's binary outcome flags this as `matches=False`
   under `temperature=0` — a false-positive divergence.
2. The classifier could confirm the replay is genuinely divergent
   (different facts, different tool, different refusal). Same outcome
   shape as case 1; same false-positive risk under `temperature>0`,
   where PR #20 returns `matches=True` regardless.

Phase 13.x.4 adds the classifier integration that distinguishes these
cases. The 13.x.4 upgrade is **additive**: existing PR #20 callers see
their tests pass unchanged because the ship-default classifier
(`AlwaysUnclassifiedClassifier`) produces verdict-equivalent outcomes
to PR #20's binary path.

The classifier itself (`CognitionClassifier` Protocol +
hybrid GBM/LLM-judge impls) is already shipped at Phase 13 Step 5.
13.x.4 is the wiring + the verdict-mapping policy + the raise-or-not
matrix.

## 2 — Goal

Upgrade `default_compare_cognition_results` from a binary
`{match, divergence}` outcome to a 4-level verdict
`{bit_exact, semantic_match, divergence, unclassified}` driven by
`CognitionClassifier.classify()`, preserving PR #20's existing tests
without modification and exposing the classifier output via the
`ComparisonOutcome.classification` field.

## 3 — Out of scope

- **Verification gate's cognition path.** The gate already uses the
  classifier directly via Step 5. 13.x.4 does not refactor that path.
- **Admin endpoint for replay-with-classifier-verdict.** PR #20 left
  the admin endpoint as a noted follow-up; that ships in a later
  v1.1.x slot (likely 13.x.4-admin or separate).
- **Omega Ledger schema for classifier verdict.** The ledger entry's
  `cognition_classifier_provenance_json` field already records the
  classifier output at original-solve time (Phase 13 Step 8). 13.x.4
  does not extend the schema for replay-time classification (that's
  a separate v1.1.x slot if needed).
- **Production training of the GBM classifier.** Step 5b ships the
  hybrid GBM+LLM-judge impl with calibrated thresholds. 13.x.4 wires
  it into replay; calibration retraining is out of scope.
- **Phase 13.x.5 / .x.7 / .x.8 follow-ups.** Out of scope for this
  spec.

## 4 — API surface

### 4.1 New module `phoenix/ledger/cognition_classifier.py`

Registry + ship default. Pattern mirrors `phoenix/ledger/encryption.py`
(Protocol registry + `set_/get_/reset_` triad).

```python
from cognition_wobble.classifier import (
    CognitionClassifier,
    ClassificationResult,
    AlwaysUnclassifiedClassifier,
)

_classifier: CognitionClassifier = AlwaysUnclassifiedClassifier()

def get_cognition_classifier() -> CognitionClassifier: ...
def set_cognition_classifier(c: CognitionClassifier) -> None: ...
def reset_cognition_classifier() -> None: ...  # test helper
```

Thread-safety: module-global reference write is atomic under GIL;
matches `encryption.py` pattern. Not lock-protected (read-dominated).

### 4.2 New enum `CognitionReplayVerdict`

In `phoenix/ledger/cognition_replay.py`:

```python
class CognitionReplayVerdict(Enum):
    BIT_EXACT      = "bit_exact"
    SEMANTIC_MATCH = "semantic_match"
    DIVERGENCE     = "divergence"
    UNCLASSIFIED   = "unclassified"
```

### 4.3 Extended `ComparisonOutcome`

```python
@dataclass(frozen=True)
class ComparisonOutcome:
    matches: bool                                          # unchanged
    reason: str                                            # unchanged
    verdict: CognitionReplayVerdict | None = None          # NEW
    classification: ClassificationResult | None = None     # NEW
```

Custom comparators (callers replacing `default_compare_cognition_results`)
keep `verdict` and `classification` at `None`; mypy stays happy via
default values. The default comparator populates both.

### 4.4 Extended function signatures

```python
def replay_cognition_entry(
    entry_id: str,
    *,
    provider_factory: CognitionProviderFactory | None = None,
    comparator: CognitionResultComparator | None = None,
    classifier: CognitionClassifier | None = None,         # NEW
    state_backend: StateBackend | None = None,
) -> CognitionReplayReport: ...

def default_compare_cognition_results(
    original_payload: dict[str, Any],
    replayed: CognitionResult,
    *,
    classifier: CognitionClassifier | None = None,         # NEW
) -> ComparisonOutcome: ...
```

When `classifier=None`: falls through to `get_cognition_classifier()`.

## 5 — Decision flow

`default_compare_cognition_results` becomes:

```
1. Resolve effective classifier:
      classifier = kwarg_classifier or get_cognition_classifier()
2. Determine temperature from provenance (existing PR #20 logic).
3. Compute text_match + tool_calls_match (existing PR #20 logic).
4. IF text_match AND tool_calls_match:
      verdict       = BIT_EXACT
      matches       = True
      classification= None        # no classifier call (perf optimization)
      reason        = "bit_exact: text + tool_calls match (temperature=...)"
      return
5. ELSE (text or tool_calls differ):
      try:
          classification = classifier.classify(
              prompt=reconstructed_prompt,
              responses=[original_cognition_result, replayed],
          )
      except Exception as exc:
          # Defense: classifier failure → UNCLASSIFIED, not a crash.
          log.warning("classifier.classify() raised", exc_info=True)
          return ComparisonOutcome(
              matches=False if temperature == 0.0 else True,
              reason=f"classifier_failure: {type(exc).__name__}({str(exc)[:80]})",
              verdict=CognitionReplayVerdict.UNCLASSIFIED,
              classification=None,
          )
      verdict = MAP_DISAGREEMENT_TYPE_TO_VERDICT[classification.disagreement_type]
      matches = compute_matches(verdict, temperature)
      reason  = compute_reason(verdict, classification, temperature, text_match, tool_calls_match)
      return ComparisonOutcome(
          matches=matches,
          reason=reason,
          verdict=verdict,
          classification=classification,
      )
```

### 5.1 Disagreement-type → verdict mapping (locked)

| `CognitionDisagreementType` | `CognitionReplayVerdict` |
|---|---|
| `FACTUAL_AGREEMENT` | `SEMANTIC_MATCH` |
| `STYLISTIC_DIVERGENCE` | `SEMANTIC_MATCH` |
| `FACTUAL_DISAGREEMENT` | `DIVERGENCE` |
| `INTERPRETIVE_DIVERGENCE` | `DIVERGENCE` |
| `REFUSAL_DIVERGENCE` | `DIVERGENCE` |
| `TOOL_CHOICE_DIVERGENCE` | `DIVERGENCE` |
| `UNCLASSIFIED` | `UNCLASSIFIED` |

### 5.2 Raise-policy matrix (`compute_matches`)

| Verdict | `temperature=0` | `temperature>0` |
|---|---|---|
| `BIT_EXACT` | `matches=True` | `matches=True` |
| `SEMANTIC_MATCH` | `matches=True` (**NEW vs PR #20**) | `matches=True` |
| `DIVERGENCE` | `matches=False` (raise) | `matches=False` (raise) (**NEW behavior change vs PR #20**) |
| `UNCLASSIFIED` | `matches=False` (raise) | `matches=True` (no raise; benefit of doubt under uncertainty) |

**Two behavior changes vs PR #20** documented in CHANGELOG entry:
1. `temp=0 + SEMANTIC_MATCH` no longer raises (classifier confirms equivalence).
2. `temp>0 + DIVERGENCE` now raises (classifier confidence beats temp>0 hedge).

### 5.3 Classifier-failure fallback

When `classifier.classify()` raises any exception:
- Log at `WARNING` with `exc_info=True` for forensic detail.
- Return `verdict=UNCLASSIFIED`, `classification=None`.
- `reason` prefixed `classifier_failure: ` so ops can distinguish from
  classifier-explicitly-returned-UNCLASSIFIED.
- `matches` follows the raise-policy matrix at `verdict=UNCLASSIFIED`
  for the recorded temperature.

### 5.4 Defense against invalid classifier output

If `classification.disagreement_type` is not a member of
`CognitionDisagreementType` (impossible per the enum's runtime check,
but defense-in-depth for Protocol violations), treat as `UNCLASSIFIED`
with `reason="classifier_invalid_result: <repr of bad value>"`.

## 6 — Out-of-scope alternatives considered

- **Provenance-driven classifier loading.** Read `classifier_version`
  from the entry's cognition_classifier_provenance_json; load that
  exact classifier impl via a version-keyed registry. Rejected for
  13.x.4: requires building the versioned classifier registry which
  is its own design problem. Slot is reserved for 13.x.4-followup
  if needed.
- **Configurable per-call temp>0 policy.** Adding a kwarg like
  `temp_gt_zero_policy: Literal["raise_on_divergence", "never_raise"]`.
  Rejected: pushes the decision to every call site; the recommended
  policy is the right default. Ops can override by passing a
  comparator that wraps `default_compare_cognition_results`.
- **Add `classifier` as required-on-construction (no registry).**
  Cleaner type story but verbose for ops. Hybrid pattern (kwarg +
  global fallback) supports both styles.

## 7 — Open tensions

- **[OPEN: classifier-version-drift]** — If `set_cognition_classifier`
  swaps the classifier impl mid-day, replays before and after the swap
  may produce different verdicts on the same entry. The entry's
  `cognition_classifier_provenance_json` records the *original-solve*
  classifier version, not the replay-time one. Whether 13.x.4 should
  surface a `classifier_version_changed` warning is left as a v1.1.x
  follow-up; the spec does not require it.
- **[OPEN: hybrid-classifier-LLM-judge-cost]** — Phase 13 Step 5b's
  hybrid impl can escalate to LLM-as-judge on low-confidence GBM
  outputs. This adds ~100-500ms latency. 13.x.4 surfaces the
  classifier output (`classification.escalated_to_judge` is True when
  escalation fires) but does NOT add per-replay cost limits. Cost-
  ceiling enforcement for classifier calls is a v1.1.x follow-up.

## 8 — Acceptance criteria

The 13.x.4 implementation is **complete** when:

1. `phoenix/ledger/cognition_classifier.py` ships the registry +
   re-exports the Protocol + ship default.
2. `phoenix/ledger/cognition_replay.py` extends `ComparisonOutcome`
   with `verdict` + `classification` fields (both optional, default
   `None`).
3. `default_compare_cognition_results` and `replay_cognition_entry`
   gain the `classifier` kwarg.
4. The decision flow (§5) is implemented per the mapping table (§5.1)
   and raise-policy matrix (§5.2).
5. `tests/cognition/test_cognition_classifier_registry.py` ships 4
   tests covering set/get/reset/isolation.
6. `tests/cognition/test_cognition_replay.py` gains 3 new test
   classes (TestVerdictMapping ×7, TestRaisePolicyMatrix ×8,
   TestClassifierErrorHandling ×3) plus 1 perf-optimization test.
7. All ~23 existing PR #20 tests pass **unchanged**.
8. CHANGELOG entry documents the two behavior changes from §5.2.
9. mypy --strict passes; ruff check clean.

## 9 — Risks and mitigations

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Existing PR #20 callsites break on `verdict=None` lookups | Low | New fields default to None; existing tests don't reference them; type system catches incompatible callers at mypy time. |
| Classifier failure during replay surprises ops | Low | Fallback to UNCLASSIFIED + WARNING log + reason-prefix distinguishes crash from low-confidence. |
| Mapping table drift (e.g., new disagreement_type added later) | Medium | TestVerdictMapping pins the table; a new disagreement_type without a mapping entry causes a KeyError that the perf-optimization test catches via `MAP_DISAGREEMENT_TYPE_TO_VERDICT[<unknown>]`. |
| Behavior change in §5.2 surprises a downstream consumer | Medium | CHANGELOG entry documents the two changes; opt-in via `classifier=None` (ship default) preserves binary outcome via AlwaysUnclassifiedClassifier; consumers who opt-in via `set_cognition_classifier` accept the new behavior. |
| Classifier hot path latency regression on replay | Low | Bit-exact early return; TestPerfOptimization pins it. Hybrid classifier's LLM-judge escalation is opt-in. |

## 10 — File-level summary

**New files:**
- `phoenix/ledger/cognition_classifier.py` (~80 lines: registry + Protocol re-export)
- `tests/cognition/test_cognition_classifier_registry.py` (~80 lines: 4 tests)

**Modified files:**
- `phoenix/ledger/cognition_replay.py` (+~80 lines: enum, ComparisonOutcome fields, classifier kwarg, decision-flow refactor)
- `tests/cognition/test_cognition_replay.py` (+~250 lines: TestVerdictMapping ×7, TestRaisePolicyMatrix ×8, TestClassifierErrorHandling ×3, TestPerfOptimization ×1, _StubClassifier + _FailingClassifier helpers)
- `CHANGELOG.md` (1 entry documenting 13.x.4 + the two behavior changes)

**Total new tests:** ~23.
