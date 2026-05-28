# Phase 13.x.4 Classifier Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `CognitionClassifier` into `default_compare_cognition_results` to produce a 4-level `CognitionReplayVerdict` (bit_exact / semantic_match / divergence / unclassified) while preserving all ~23 existing PR #20 tests unchanged.

**Architecture:** Build the registry module first (isolated unit), then extend the comparator's decision flow one component at a time (verdict enum → ComparisonOutcome fields → bit-exact early-return → classifier path → raise-policy matrix → error fallback → kwarg threading). Each component is its own TDD task: failing test → implementation → passing test → commit.

**Tech Stack:** Python 3.11–3.13, pytest, dataclasses, PEP 544 Protocol. Uses existing vendored `cognition_wobble.classifier` Protocol shipped at Phase 13 Step 5.

**Spec:** `docs/superpowers/specs/2026-05-28-phase-13x4-classifier-integration-design.md` (committed `465044d`).

**Branch:** `phase-13x-classifier-integration` (matches the existing `phase-13x-<descriptor>` convention from .x.1/.x.2/.x.3/.x.6).

---

## Task 0: Pre-flight + working branch

**Files:**
- Read-only: git status, main CI, working tree.
- Create: working branch `phase-13x-classifier-integration`.

- [ ] **Step 1: Verify working tree clean (modulo known stray file)**

Run: `git status --short`
Expected output: only `?? "C\357\200\272temp_section4.txt"` (the known Windows path-encoding artifact, pre-existing). If anything else appears, stop and surface to Adam.

- [ ] **Step 2: Verify on main + synced with origin**

Run: `git fetch origin && git status -b --short`
Expected output: `## main...origin/main` (no ahead/behind).

- [ ] **Step 3: Confirm main CI green**

Run: `gh run list --branch main --limit 1 --json status,conclusion --jq '.[] | "status=\(.status) conclusion=\(.conclusion)"'`
Expected output: `status=completed conclusion=success`.

- [ ] **Step 4: Create and check out the working branch**

Run:
```bash
git checkout -b phase-13x-classifier-integration
git status -b --short
```
Expected output: `## phase-13x-classifier-integration`.

- [ ] **Step 5: No commit** (branch creation only)

---

## Task 1: New module `phoenix/ledger/cognition_classifier.py` with registry + 4 tests

**Files:**
- Create: `phoenix/ledger/cognition_classifier.py`
- Test: `tests/cognition/test_cognition_classifier_registry.py`

- [ ] **Step 1: Write the failing tests for the registry**

Create `tests/cognition/test_cognition_classifier_registry.py`:

```python
"""Tests for ``phoenix.ledger.cognition_classifier`` registry (Phase 13.x.4).

Coverage:

- Ship default is :class:`AlwaysUnclassifiedClassifier`.
- :func:`set_cognition_classifier` updates the global.
- :func:`reset_cognition_classifier` restores the ship default.
- The autouse fixture isolates tests from each other.
"""

from __future__ import annotations

import pytest

from cognition_wobble.disagreement_types import CognitionDisagreementType
from phoenix.ledger.cognition_classifier import (
    AlwaysUnclassifiedClassifier,
    ClassificationResult,
    get_cognition_classifier,
    reset_cognition_classifier,
    set_cognition_classifier,
)


class _StubClassifier:
    """A test stub that returns a fixed version + FACTUAL_AGREEMENT."""

    version: str = "stub-test-v1"

    def classify(self, prompt, responses, *, confidence_threshold=0.5):
        del prompt, responses, confidence_threshold
        return ClassificationResult(
            disagreement_type=CognitionDisagreementType.FACTUAL_AGREEMENT,
            confidence=1.0,
            classifier_version=self.version,
            feature_values={},
            escalated_to_judge=False,
        )


@pytest.fixture(autouse=True)
def _reset_classifier():
    """Every test starts and ends with the ship default."""
    reset_cognition_classifier()
    yield
    reset_cognition_classifier()


class TestCognitionClassifierRegistry:
    def test_get_returns_ship_default_AlwaysUnclassifiedClassifier(self) -> None:
        c = get_cognition_classifier()
        assert isinstance(c, AlwaysUnclassifiedClassifier)
        assert c.version == "stub-always-unclassified-v1"

    def test_set_then_get_returns_custom(self) -> None:
        stub = _StubClassifier()
        set_cognition_classifier(stub)
        retrieved = get_cognition_classifier()
        assert retrieved is stub
        assert retrieved.version == "stub-test-v1"

    def test_reset_restores_ship_default(self) -> None:
        set_cognition_classifier(_StubClassifier())
        reset_cognition_classifier()
        assert isinstance(get_cognition_classifier(), AlwaysUnclassifiedClassifier)

    def test_isolation_between_tests(self) -> None:
        # If isolation works, the ship default is in place at test start.
        assert isinstance(get_cognition_classifier(), AlwaysUnclassifiedClassifier)
```

- [ ] **Step 2: Run tests → expect ImportError**

Run: `pytest tests/cognition/test_cognition_classifier_registry.py -v`
Expected: FAIL with `ImportError: No module named 'phoenix.ledger.cognition_classifier'`.

- [ ] **Step 3: Create the module**

Create `phoenix/ledger/cognition_classifier.py`:

```python
"""Cognition classifier registry + ship default (Phase 13.x.4).

Per Phase 13.x.4 design spec (2026-05-28): the cognition classifier is
pluggable via a global registry that mirrors
:mod:`phoenix.ledger.encryption`'s pattern. The ship default is
:class:`AlwaysUnclassifiedClassifier` from
:mod:`cognition_wobble.classifier`; ops swap in a real classifier
(typically Phase 13 Step 5b's hybrid GBM+LLM-judge) at daemon
startup via :func:`set_cognition_classifier`.

The registry is **read-dominated**: ops call
:func:`set_cognition_classifier` once at daemon startup; every replay
read calls :func:`get_cognition_classifier`. The module-global write
is atomic under the GIL (single reference assignment); the pattern
does not need explicit locking.

Test-isolation: :func:`reset_cognition_classifier` restores the ship
default. The :mod:`tests.cognition.test_cognition_classifier_registry`
autouse fixture pattern is the recommended way to keep tests isolated.
"""

from __future__ import annotations

# Re-exports from vendor so callers import from one place.
from cognition_wobble.classifier import (
    AlwaysUnclassifiedClassifier,
    ClassificationResult,
    CognitionClassifier,
    DEFAULT_CONFIDENCE_THRESHOLD,
)

__all__ = [
    "AlwaysUnclassifiedClassifier",
    "ClassificationResult",
    "CognitionClassifier",
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "get_cognition_classifier",
    "reset_cognition_classifier",
    "set_cognition_classifier",
]


# Module-level singleton. Read-dominated; reference write is GIL-atomic.
_classifier: CognitionClassifier = AlwaysUnclassifiedClassifier()


def get_cognition_classifier() -> CognitionClassifier:
    """Return the currently registered classifier.

    Ships with :class:`AlwaysUnclassifiedClassifier`. Daemon startup
    swaps in a real classifier via :func:`set_cognition_classifier`.
    """
    return _classifier


def set_cognition_classifier(c: CognitionClassifier) -> None:
    """Register ``c`` as the active classifier.

    Typically called once at daemon startup with a real classifier
    impl. Reference assignment is atomic under the GIL; no lock
    needed.
    """
    global _classifier
    _classifier = c


def reset_cognition_classifier() -> None:
    """Restore the ship default :class:`AlwaysUnclassifiedClassifier`.

    Test helper. Production should not call this; daemon shutdown
    discards the module anyway.
    """
    global _classifier
    _classifier = AlwaysUnclassifiedClassifier()
```

- [ ] **Step 4: Run tests → expect 4 PASS**

Run: `pytest tests/cognition/test_cognition_classifier_registry.py -v`
Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add phoenix/ledger/cognition_classifier.py tests/cognition/test_cognition_classifier_registry.py
git commit -m "phase 13.x.4 step 1: cognition_classifier registry + ship default"
```

---

## Task 2: Add `CognitionReplayVerdict` enum + mapping dict to `cognition_replay.py`

**Files:**
- Modify: `phoenix/ledger/cognition_replay.py` (type additions only)

- [ ] **Step 1: Verify existing PR #20 tests still pass (baseline)**

Run: `pytest tests/cognition/test_cognition_replay.py -v --no-header 2>&1 | tail -5`
Expected: 23 passed (or whatever the existing PR #20 test count is). Record this number — Task 11 verifies it stays at this count + new tests.

- [ ] **Step 2: Add the enum + mapping dict (no behavior change; just types)**

Edit `phoenix/ledger/cognition_replay.py`. Near the top of the file (after the existing imports, before the typed-errors section), add:

```python
# ---------------------------------------------------------------------------
# Phase 13.x.4 — verdict enum + mapping dict.

from enum import Enum

from cognition_wobble.disagreement_types import CognitionDisagreementType


class CognitionReplayVerdict(Enum):
    """The four-level outcome of a cognition replay comparison.

    Per Phase 13.x.4 design spec:

    - :attr:`BIT_EXACT` — text + tool_calls byte-for-byte match; no
      classifier call needed (perf optimization).
    - :attr:`SEMANTIC_MATCH` — text differs but classifier confirms
      semantic equivalence (e.g., model-version update producing
      equivalent prose).
    - :attr:`DIVERGENCE` — classifier confident the outputs differ in
      substance (factual disagreement, refusal asymmetry, tool-choice
      divergence, etc.).
    - :attr:`UNCLASSIFIED` — classifier could not confidently
      categorize (or the classifier crashed; see
      :class:`CognitionReplayDivergence` reason prefix).
    """

    BIT_EXACT = "bit_exact"
    SEMANTIC_MATCH = "semantic_match"
    DIVERGENCE = "divergence"
    UNCLASSIFIED = "unclassified"


# Locked mapping from classifier output to verdict. Per Phase 13.x.4
# design spec §5.1: the six classified disagreement types collapse
# to SEMANTIC_MATCH or DIVERGENCE; UNCLASSIFIED stays as its own
# verdict (the calibrated escape hatch).
MAP_DISAGREEMENT_TYPE_TO_VERDICT: dict[
    CognitionDisagreementType, CognitionReplayVerdict
] = {
    CognitionDisagreementType.FACTUAL_AGREEMENT: CognitionReplayVerdict.SEMANTIC_MATCH,
    CognitionDisagreementType.STYLISTIC_DIVERGENCE: CognitionReplayVerdict.SEMANTIC_MATCH,
    CognitionDisagreementType.FACTUAL_DISAGREEMENT: CognitionReplayVerdict.DIVERGENCE,
    CognitionDisagreementType.INTERPRETIVE_DIVERGENCE: CognitionReplayVerdict.DIVERGENCE,
    CognitionDisagreementType.REFUSAL_DIVERGENCE: CognitionReplayVerdict.DIVERGENCE,
    CognitionDisagreementType.TOOL_CHOICE_DIVERGENCE: CognitionReplayVerdict.DIVERGENCE,
    CognitionDisagreementType.UNCLASSIFIED: CognitionReplayVerdict.UNCLASSIFIED,
}
```

Then add these to the `__all__` list at the bottom of the file:

```python
__all__ = [
    # ... (existing entries) ...
    "CognitionReplayVerdict",
    "MAP_DISAGREEMENT_TYPE_TO_VERDICT",
]
```

- [ ] **Step 3: Run existing tests → expect same count PASS as Step 1**

Run: `pytest tests/cognition/test_cognition_replay.py -v --no-header 2>&1 | tail -5`
Expected: same passed count as Step 1 (no behavior change).

- [ ] **Step 4: Verify the enum + mapping are importable**

Run: `python -c "from phoenix.ledger.cognition_replay import CognitionReplayVerdict, MAP_DISAGREEMENT_TYPE_TO_VERDICT; print(len(MAP_DISAGREEMENT_TYPE_TO_VERDICT))"`
Expected: `7` (one entry per CognitionDisagreementType value).

- [ ] **Step 5: Commit**

```bash
git add phoenix/ledger/cognition_replay.py
git commit -m "phase 13.x.4 step 2: CognitionReplayVerdict enum + mapping dict"
```

---

## Task 3: Extend `ComparisonOutcome` with optional `verdict` + `classification` fields

**Files:**
- Modify: `phoenix/ledger/cognition_replay.py` (`ComparisonOutcome` dataclass)
- Test: `tests/cognition/test_cognition_replay.py` (add backward-compat assertions)

- [ ] **Step 1: Write the failing test (defaults to None)**

Edit `tests/cognition/test_cognition_replay.py`. Add a new test method to the existing `TestComparisonOutcome` class:

```python
    def test_outcome_new_fields_default_to_none(self) -> None:
        """Phase 13.x.4: verdict + classification default to None for
        backward-compat with PR #20 callsites that don't populate them."""
        outcome = ComparisonOutcome(matches=True, reason="x")
        assert outcome.verdict is None
        assert outcome.classification is None
```

- [ ] **Step 2: Run test → expect AttributeError**

Run: `pytest tests/cognition/test_cognition_replay.py::TestComparisonOutcome::test_outcome_new_fields_default_to_none -v`
Expected: FAIL with `AttributeError: 'ComparisonOutcome' object has no attribute 'verdict'`.

- [ ] **Step 3: Extend the dataclass**

In `phoenix/ledger/cognition_replay.py`, locate the existing `ComparisonOutcome` dataclass and add the two new optional fields:

```python
@dataclass(frozen=True)
class ComparisonOutcome:
    """The result of comparing an original cognition entry to a replay.

    Fields (Phase 13.x.3 original):
        matches: True iff the replay matches the original per the
            comparison policy chosen by the caller.
        reason: Free-form description of WHY they match or differ.

    Fields (Phase 13.x.4 additions; optional for back-compat):
        verdict: The 4-level :class:`CognitionReplayVerdict` produced
            by the default comparator when a classifier is available.
            ``None`` for custom comparators that don't classify.
        classification: The full :class:`ClassificationResult` from
            the classifier, when one was called. ``None`` when the
            comparator returned BIT_EXACT (no classifier call) or
            when a custom comparator chose not to populate it.
    """

    matches: bool
    reason: str
    verdict: CognitionReplayVerdict | None = None
    classification: ClassificationResult | None = None
```

You'll also need to add the import for `ClassificationResult` near the top of the file:

```python
from phoenix.ledger.cognition_classifier import ClassificationResult
```

- [ ] **Step 4: Run all tests → expect existing tests + new test PASS**

Run: `pytest tests/cognition/test_cognition_replay.py -v --no-header 2>&1 | tail -5`
Expected: existing count + 1 PASSED (the new `test_outcome_new_fields_default_to_none` test passes, and all PR #20 tests pass unchanged because the new fields are optional with default `None`).

- [ ] **Step 5: Commit**

```bash
git add phoenix/ledger/cognition_replay.py tests/cognition/test_cognition_replay.py
git commit -m "phase 13.x.4 step 3: ComparisonOutcome gains optional verdict + classification fields"
```

---

## Task 4: Implement bit-exact early return — populate verdict; no classifier call

**Files:**
- Modify: `phoenix/ledger/cognition_replay.py` (`default_compare_cognition_results` bit-exact branch)
- Test: `tests/cognition/test_cognition_replay.py` (add bit-exact verdict assertion)

- [ ] **Step 1: Write the failing test**

Add to `tests/cognition/test_cognition_replay.py` in the existing `TestDefaultComparator` class:

```python
    def test_bit_exact_populates_verdict_bit_exact(self) -> None:
        """Phase 13.x.4: bit-exact comparison sets verdict=BIT_EXACT."""
        payload = {
            "cognition_provenance": {"temperature": 0.0},
            "result_text": "answer",
            "result_tool_calls": [],
        }
        replayed = CognitionResult(
            text="answer",
            tool_calls=[],
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            latency_ms=0.0,
            provider_fingerprint="x",
        )
        outcome = default_compare_cognition_results(payload, replayed)
        assert outcome.matches is True
        assert outcome.verdict == CognitionReplayVerdict.BIT_EXACT
        assert outcome.classification is None  # No classifier call on bit-exact.
```

Add the import at the top of the test file (with existing imports):

```python
from phoenix.ledger.cognition_replay import CognitionReplayVerdict  # NEW
```

- [ ] **Step 2: Run test → expect FAIL**

Run: `pytest tests/cognition/test_cognition_replay.py::TestDefaultComparator::test_bit_exact_populates_verdict_bit_exact -v`
Expected: FAIL with `assert None == CognitionReplayVerdict.BIT_EXACT` (verdict isn't set).

- [ ] **Step 3: Update the bit-exact path in `default_compare_cognition_results`**

In `phoenix/ledger/cognition_replay.py`, locate the existing `default_compare_cognition_results` function. Find the bit-exact branch (which currently returns `ComparisonOutcome(matches=True, reason=f"bit_exact: ...")`).

Update it to populate the `verdict`:

```python
    # Deterministic case (temperature == 0): demand bit-exact text +
    # tool_calls. Usage is informational only.
    if text_match and tool_calls_match:
        return ComparisonOutcome(
            matches=True,
            reason=(
                f"bit_exact: text + tool_calls match "
                f"(temperature={temperature}; usage drift not compared)"
            ),
            verdict=CognitionReplayVerdict.BIT_EXACT,
            classification=None,  # No classifier call on bit-exact (perf opt).
        )
```

(Note: keep the reason string literal `bit_exact:` so PR #20's `assert outcome.reason.startswith("bit_exact")` test continues to pass.)

- [ ] **Step 4: Run all tests → expect existing + new PASS**

Run: `pytest tests/cognition/test_cognition_replay.py -v --no-header 2>&1 | tail -5`
Expected: existing count + 2 PASSED (the new bit-exact verdict test + Task 3's test, plus all PR #20 tests).

- [ ] **Step 5: Commit**

```bash
git add phoenix/ledger/cognition_replay.py tests/cognition/test_cognition_replay.py
git commit -m "phase 13.x.4 step 4: bit-exact branch populates verdict=BIT_EXACT"
```

---

## Task 5: TestVerdictMapping — 7 tests + classifier-driven mapping in comparator

**Files:**
- Modify: `phoenix/ledger/cognition_replay.py` (`default_compare_cognition_results` text-differs branch)
- Test: `tests/cognition/test_cognition_replay.py` (add `TestVerdictMapping` class + helpers)

- [ ] **Step 1: Add the `_StubClassifier` and `_FailingClassifier` test helpers**

Edit `tests/cognition/test_cognition_replay.py`. Near the top of the file (after the existing `_FakeBackend` / `_FakeProvider` helpers), add:

```python
# ---------------------------------------------------------------------------
# Phase 13.x.4 test helpers: stub classifiers.


class _StubClassifier:
    """Classifier stub: returns a fixed CognitionDisagreementType every time.

    Used by TestVerdictMapping + TestRaisePolicyMatrix to exercise
    the comparator's classifier-driven path without invoking the
    real GBM/LLM-judge classifier.
    """

    version: str = "stub-fixed-v1"

    def __init__(
        self,
        *,
        returns: CognitionDisagreementType,
        confidence: float = 0.95,
    ) -> None:
        self._returns = returns
        self._confidence = confidence

    def classify(
        self,
        prompt,
        responses,
        *,
        confidence_threshold: float = 0.5,
    ):
        from phoenix.ledger.cognition_classifier import ClassificationResult

        del prompt, responses, confidence_threshold
        return ClassificationResult(
            disagreement_type=self._returns,
            confidence=self._confidence,
            classifier_version=self.version,
            feature_values={},
            escalated_to_judge=False,
        )


class _FailingClassifier:
    """Classifier stub that raises on classify(). For error-handling tests."""

    version: str = "stub-failing-v1"

    def __init__(self, *, exception: Exception) -> None:
        self._exception = exception

    def classify(self, prompt, responses, *, confidence_threshold=0.5):
        del prompt, responses, confidence_threshold
        raise self._exception
```

Also add the imports near the top of the test file (with the existing imports):

```python
from cognition_wobble.disagreement_types import CognitionDisagreementType  # NEW
```

- [ ] **Step 2: Write the 7 failing tests in `TestVerdictMapping` class**

Add to `tests/cognition/test_cognition_replay.py`:

```python
# ---------------------------------------------------------------------------
# Phase 13.x.4: TestVerdictMapping — pin the disagreement_type → verdict
# mapping table from design spec §5.1.


class TestVerdictMapping:
    """Each test pins one row of the §5.1 mapping table.

    The classifier returns a single CognitionDisagreementType; the
    comparator runs (text differs, classifier called); the outcome's
    verdict matches the locked mapping.
    """

    def _make_payload_and_replayed(self) -> tuple[dict[str, Any], CognitionResult]:
        """Text-differs payload so the comparator goes through the
        classifier branch."""
        return (
            {
                "cognition_provenance": {"temperature": 0.0},
                "result_text": "original",
                "result_tool_calls": [],
            },
            CognitionResult(
                text="replayed (DIFFERENT)",
                tool_calls=[],
                usage=TokenUsage(input_tokens=1, output_tokens=1),
                latency_ms=0.0,
                provider_fingerprint="x",
            ),
        )

    def test_factual_agreement_maps_to_semantic_match(self) -> None:
        classifier = _StubClassifier(returns=CognitionDisagreementType.FACTUAL_AGREEMENT)
        payload, replayed = self._make_payload_and_replayed()
        outcome = default_compare_cognition_results(payload, replayed, classifier=classifier)
        assert outcome.verdict == CognitionReplayVerdict.SEMANTIC_MATCH

    def test_stylistic_divergence_maps_to_semantic_match(self) -> None:
        classifier = _StubClassifier(returns=CognitionDisagreementType.STYLISTIC_DIVERGENCE)
        payload, replayed = self._make_payload_and_replayed()
        outcome = default_compare_cognition_results(payload, replayed, classifier=classifier)
        assert outcome.verdict == CognitionReplayVerdict.SEMANTIC_MATCH

    def test_factual_disagreement_maps_to_divergence(self) -> None:
        classifier = _StubClassifier(returns=CognitionDisagreementType.FACTUAL_DISAGREEMENT)
        payload, replayed = self._make_payload_and_replayed()
        # Note: this verdict=DIVERGENCE under temp=0 → matches=False → raises.
        # We catch the raise to assert verdict; raise-policy is tested in
        # TestRaisePolicyMatrix.
        outcome = default_compare_cognition_results(payload, replayed, classifier=classifier)
        assert outcome.verdict == CognitionReplayVerdict.DIVERGENCE

    def test_interpretive_divergence_maps_to_divergence(self) -> None:
        classifier = _StubClassifier(returns=CognitionDisagreementType.INTERPRETIVE_DIVERGENCE)
        payload, replayed = self._make_payload_and_replayed()
        outcome = default_compare_cognition_results(payload, replayed, classifier=classifier)
        assert outcome.verdict == CognitionReplayVerdict.DIVERGENCE

    def test_refusal_divergence_maps_to_divergence(self) -> None:
        classifier = _StubClassifier(returns=CognitionDisagreementType.REFUSAL_DIVERGENCE)
        payload, replayed = self._make_payload_and_replayed()
        outcome = default_compare_cognition_results(payload, replayed, classifier=classifier)
        assert outcome.verdict == CognitionReplayVerdict.DIVERGENCE

    def test_tool_choice_divergence_maps_to_divergence(self) -> None:
        classifier = _StubClassifier(returns=CognitionDisagreementType.TOOL_CHOICE_DIVERGENCE)
        payload, replayed = self._make_payload_and_replayed()
        outcome = default_compare_cognition_results(payload, replayed, classifier=classifier)
        assert outcome.verdict == CognitionReplayVerdict.DIVERGENCE

    def test_unclassified_stays_unclassified(self) -> None:
        classifier = _StubClassifier(returns=CognitionDisagreementType.UNCLASSIFIED)
        payload, replayed = self._make_payload_and_replayed()
        outcome = default_compare_cognition_results(payload, replayed, classifier=classifier)
        assert outcome.verdict == CognitionReplayVerdict.UNCLASSIFIED
```

- [ ] **Step 3: Run the new tests → expect FAIL (no classifier path implemented)**

Run: `pytest tests/cognition/test_cognition_replay.py::TestVerdictMapping -v`
Expected: 7 FAIL. The current text-differs path doesn't accept or call a classifier; the `classifier` kwarg either errors or is ignored.

- [ ] **Step 4: Implement the classifier-driven text-differs branch**

Edit `phoenix/ledger/cognition_replay.py`. Update the `default_compare_cognition_results` function. Specifically:

1. Add the `classifier` kwarg to the signature.
2. Resolve the effective classifier (kwarg OR global).
3. Refactor the text-differs branch to call the classifier and use `MAP_DISAGREEMENT_TYPE_TO_VERDICT`.

The full updated function (replacing the existing body) is:

```python
def default_compare_cognition_results(
    original_payload: dict[str, Any],
    replayed: CognitionResult,
    *,
    classifier: CognitionClassifier | None = None,
) -> ComparisonOutcome:
    """Default comparator for VERBATIM-disposition cognition replays.

    **Policy (Phase 13.x.4 four-level verdict, locked 2026-05-28):**

    The 4-level :class:`CognitionReplayVerdict` ladder:

    - ``BIT_EXACT`` — text + tool_calls match byte-for-byte. No
      classifier call (perf optimization).
    - ``SEMANTIC_MATCH`` — text differs but classifier confirms
      equivalence (e.g., model version producing equivalent prose).
    - ``DIVERGENCE`` — classifier confident the outputs differ in
      substance.
    - ``UNCLASSIFIED`` — classifier could not confidently categorize
      OR the classifier itself raised (reason carries the prefix
      ``classifier_failure:`` in the latter case).

    The ``matches`` field follows §5.2's raise-policy matrix:

    +----------------+---------+---------+
    | verdict        | temp=0  | temp>0  |
    +================+=========+=========+
    | BIT_EXACT      | True    | True    |
    | SEMANTIC_MATCH | True    | True    |
    | DIVERGENCE     | False   | False   |
    | UNCLASSIFIED   | False   | True    |
    +----------------+---------+---------+

    Args:
        original_payload: The cognition entry's parsed payload dict.
        replayed: The fresh :class:`CognitionResult` from re-invoking
            the provider.
        classifier: Optional :class:`CognitionClassifier` to use for
            the text-differs case. When ``None``, falls through to
            :func:`get_cognition_classifier` (which returns the ship
            default :class:`AlwaysUnclassifiedClassifier` until
            ``set_cognition_classifier`` is called).

    Returns:
        :class:`ComparisonOutcome` with ``matches``, ``reason``,
        ``verdict``, and ``classification`` populated.
    """
    from phoenix.ledger.cognition_classifier import get_cognition_classifier

    # Resolve effective classifier (kwarg overrides global).
    effective_classifier = classifier if classifier is not None else get_cognition_classifier()

    # Determine non-determinism from the recorded sampling params.
    provenance = original_payload.get("cognition_provenance") or {}
    try:
        temperature = float(provenance.get("temperature", 0.0))
    except (TypeError, ValueError):
        temperature = 0.0

    # Pull original artifacts. Missing keys → empty defaults so the
    # comparator never raises KeyError on partial entries.
    original_text = str(original_payload.get("result_text", ""))
    original_tool_calls_raw = original_payload.get("result_tool_calls") or []

    # Normalize the replayed tool_calls.
    replayed_tool_calls = [
        {
            "call_id": tc.call_id,
            "name": tc.name,
            "arguments": tc.arguments,
        }
        for tc in replayed.tool_calls
    ]

    text_match = replayed.text == original_text
    tool_calls_match = list(original_tool_calls_raw) == replayed_tool_calls

    # Bit-exact branch: early return, no classifier call.
    if text_match and tool_calls_match:
        return ComparisonOutcome(
            matches=True,
            reason=(
                f"bit_exact: text + tool_calls match "
                f"(temperature={temperature}; usage drift not compared)"
            ),
            verdict=CognitionReplayVerdict.BIT_EXACT,
            classification=None,
        )

    # Text-differs branch: invoke the classifier (with defensive try).
    # Reconstruct prompt for classifier from the canonical form in payload.
    try:
        canonical_json = str(original_payload.get("prompt_verbatim", "") or "{}")
        prompt = _reconstruct_prompt_from_canonical(canonical_json)
    except Exception:
        # If reconstruction fails, classifier still gets *some* prompt;
        # use an empty Prompt as fallback so classify() can run.
        from phoenix.providers.cognition.types import Prompt

        prompt = Prompt(system=None, messages=[])

    # Reconstruct original CognitionResult for the classifier.
    original_response = _payload_to_cognition_result(original_payload)

    # Classify (defense: catch any exception, fall back to UNCLASSIFIED).
    classification: ClassificationResult | None
    try:
        classification = effective_classifier.classify(
            prompt=prompt,
            responses=[original_response, replayed],
        )
        verdict = MAP_DISAGREEMENT_TYPE_TO_VERDICT.get(
            classification.disagreement_type,
            CognitionReplayVerdict.UNCLASSIFIED,  # Defense-in-depth.
        )
    except Exception as exc:
        log.warning(
            "default_compare_cognition_results: classifier.classify() raised",
            exc_info=True,
        )
        return _build_unclassified_outcome(
            text_match=text_match,
            tool_calls_match=tool_calls_match,
            original_text=original_text,
            replayed_text=replayed.text,
            original_tool_calls_n=len(list(original_tool_calls_raw)),
            replayed_tool_calls_n=len(replayed_tool_calls),
            temperature=temperature,
            reason_prefix=f"classifier_failure: {type(exc).__name__}({str(exc)[:80]})",
        )

    # Compute matches per §5.2 raise-policy matrix.
    matches = _compute_matches(verdict, temperature)

    # Build reason. PRESERVE existing PR #20 substring patterns:
    #   "text differs (...)"          when text doesn't match
    #   "tool_calls differ (...)"     when tool_calls don't match
    #   "non_deterministic_replay"    when temperature > 0
    #   "temperature=X.X"             when temperature > 0
    # plus add the 13.x.4 verdict + classifier info.
    reason_parts: list[str] = [f"verdict={verdict.value}"]
    if not text_match:
        reason_parts.append(
            f"text differs (original_len={len(original_text)}, "
            f"replayed_len={len(replayed.text)})"
        )
    if not tool_calls_match:
        reason_parts.append(
            f"tool_calls differ (original_n={len(list(original_tool_calls_raw))}, "
            f"replayed_n={len(replayed_tool_calls)})"
        )
    if temperature > 0.0:
        reason_parts.append(
            f"non_deterministic_replay: temperature={temperature}"
        )
    reason_parts.append(
        f"classifier: version={classification.classifier_version} "
        f"confidence={classification.confidence:.2f}"
    )
    reason = "; ".join(reason_parts)

    return ComparisonOutcome(
        matches=matches,
        reason=reason,
        verdict=verdict,
        classification=classification,
    )


def _compute_matches(
    verdict: CognitionReplayVerdict,
    temperature: float,
) -> bool:
    """Phase 13.x.4 raise-policy matrix (§5.2):

    +----------------+---------+---------+
    | verdict        | temp=0  | temp>0  |
    +================+=========+=========+
    | BIT_EXACT      | True    | True    |
    | SEMANTIC_MATCH | True    | True    |
    | DIVERGENCE     | False   | False   |
    | UNCLASSIFIED   | False   | True    |
    +----------------+---------+---------+
    """
    if verdict in (
        CognitionReplayVerdict.BIT_EXACT,
        CognitionReplayVerdict.SEMANTIC_MATCH,
    ):
        return True
    if verdict is CognitionReplayVerdict.DIVERGENCE:
        return False
    if verdict is CognitionReplayVerdict.UNCLASSIFIED:
        return temperature > 0.0
    # Unreachable per the 4-value enum; defense-in-depth.
    return False


def _build_unclassified_outcome(
    *,
    text_match: bool,
    tool_calls_match: bool,
    original_text: str,
    replayed_text: str,
    original_tool_calls_n: int,
    replayed_tool_calls_n: int,
    temperature: float,
    reason_prefix: str,
) -> ComparisonOutcome:
    """Build an UNCLASSIFIED outcome (classifier failure or invalid result).

    Preserves PR #20's reason substrings so existing tests pass.
    """
    parts: list[str] = [reason_prefix]
    parts.append(f"verdict={CognitionReplayVerdict.UNCLASSIFIED.value}")
    if not text_match:
        parts.append(
            f"text differs (original_len={len(original_text)}, "
            f"replayed_len={len(replayed_text)})"
        )
    if not tool_calls_match:
        parts.append(
            f"tool_calls differ (original_n={original_tool_calls_n}, "
            f"replayed_n={replayed_tool_calls_n})"
        )
    if temperature > 0.0:
        parts.append(f"non_deterministic_replay: temperature={temperature}")
    return ComparisonOutcome(
        matches=_compute_matches(CognitionReplayVerdict.UNCLASSIFIED, temperature),
        reason="; ".join(parts),
        verdict=CognitionReplayVerdict.UNCLASSIFIED,
        classification=None,
    )


def _payload_to_cognition_result(payload: dict[str, Any]) -> CognitionResult:
    """Reconstruct a CognitionResult from a cognition ledger payload.

    Used by the classifier-driven comparator path: classify() expects
    a list of responses; we feed it [original, replayed].
    """
    from phoenix.providers.cognition.types import ToolCall, TokenUsage

    text = str(payload.get("result_text", ""))
    tc_raw = payload.get("result_tool_calls") or []
    tool_calls = [
        ToolCall(
            call_id=str(tc.get("call_id", "")),
            name=str(tc.get("name", "")),
            arguments=tc.get("arguments", {}),
        )
        for tc in tc_raw
    ]
    usage_raw = payload.get("result_usage") or {}
    usage = TokenUsage(
        input_tokens=int(usage_raw.get("input_tokens", 0)),
        output_tokens=int(usage_raw.get("output_tokens", 0)),
        cached_input_tokens=int(usage_raw.get("cached_input_tokens", 0)),
    )
    provenance = payload.get("cognition_provenance") or {}
    fingerprint = f"{provenance.get('provider_id', '')}|{provenance.get('model', '')}|replay"
    return CognitionResult(
        text=text,
        tool_calls=tool_calls,
        usage=usage,
        latency_ms=0.0,
        provider_fingerprint=fingerprint,
        prompt_cache_hit=None,
    )
```

Also add the `CognitionClassifier` import to the typing-import block at the top:

```python
from phoenix.ledger.cognition_classifier import (
    CognitionClassifier,
    ClassificationResult,
)
```

- [ ] **Step 5: Run the new tests → expect 7 PASS**

Run: `pytest tests/cognition/test_cognition_replay.py::TestVerdictMapping -v`
Expected: 7 PASSED.

- [ ] **Step 6: Run all tests → expect existing + new all PASS**

Run: `pytest tests/cognition/test_cognition_replay.py -v --no-header 2>&1 | tail -5`
Expected: existing PR #20 count + 7 (TestVerdictMapping) + 1 (Task 3) + 1 (Task 4) PASSED.

- [ ] **Step 7: Commit**

```bash
git add phoenix/ledger/cognition_replay.py tests/cognition/test_cognition_replay.py
git commit -m "phase 13.x.4 step 5: TestVerdictMapping (7 tests) + classifier-driven path"
```

---

## Task 6: TestRaisePolicyMatrix — 8 tests + verify `_compute_matches` correctness

**Files:**
- Test: `tests/cognition/test_cognition_replay.py` (add `TestRaisePolicyMatrix` class)
- (No production code changes — `_compute_matches` was added in Task 5)

- [ ] **Step 1: Write the 8 failing tests for the raise-policy matrix**

Add to `tests/cognition/test_cognition_replay.py`:

```python
# ---------------------------------------------------------------------------
# Phase 13.x.4: TestRaisePolicyMatrix — pin the §5.2 raise-policy
# matrix (4 verdicts × 2 temperature regimes).


class TestRaisePolicyMatrix:
    """Each test pins one cell of the §5.2 matrix.

    +----------------+---------+---------+
    | verdict        | temp=0  | temp>0  |
    +================+=========+=========+
    | BIT_EXACT      | True    | True    |
    | SEMANTIC_MATCH | True    | True    |
    | DIVERGENCE     | False   | False   |
    | UNCLASSIFIED   | False   | True    |
    +----------------+---------+---------+

    Tests build a text-differs payload and feed a stub classifier
    returning the disagreement_type that maps to the test's verdict.
    """

    @staticmethod
    def _payload_at(temperature: float, *, text_differs: bool = True) -> dict[str, Any]:
        return {
            "cognition_provenance": {"temperature": temperature},
            "result_text": "original",
            "result_tool_calls": [],
        }

    @staticmethod
    def _replayed(text: str = "different") -> CognitionResult:
        return CognitionResult(
            text=text,
            tool_calls=[],
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            latency_ms=0.0,
            provider_fingerprint="x",
        )

    # ----- BIT_EXACT row -----

    def test_bit_exact_temp_zero_matches_true(self) -> None:
        """Bit-exact at temp=0 → matches=True. (Covered by Task 4 too;
        re-asserted here for matrix completeness.)"""
        payload = self._payload_at(0.0)
        # bit-exact: replayed text == original text
        replayed = self._replayed("original")
        outcome = default_compare_cognition_results(payload, replayed)
        assert outcome.verdict == CognitionReplayVerdict.BIT_EXACT
        assert outcome.matches is True

    def test_bit_exact_temp_high_matches_true(self) -> None:
        payload = self._payload_at(0.7)
        replayed = self._replayed("original")
        outcome = default_compare_cognition_results(payload, replayed)
        assert outcome.verdict == CognitionReplayVerdict.BIT_EXACT
        assert outcome.matches is True

    # ----- SEMANTIC_MATCH row -----

    def test_semantic_match_temp_zero_matches_true(self) -> None:
        """Phase 13.x.4 behavior change: classifier confirms equivalence
        → no raise even at temp=0."""
        classifier = _StubClassifier(returns=CognitionDisagreementType.FACTUAL_AGREEMENT)
        payload = self._payload_at(0.0)
        outcome = default_compare_cognition_results(
            payload, self._replayed(), classifier=classifier
        )
        assert outcome.verdict == CognitionReplayVerdict.SEMANTIC_MATCH
        assert outcome.matches is True

    def test_semantic_match_temp_high_matches_true(self) -> None:
        classifier = _StubClassifier(returns=CognitionDisagreementType.STYLISTIC_DIVERGENCE)
        payload = self._payload_at(0.7)
        outcome = default_compare_cognition_results(
            payload, self._replayed(), classifier=classifier
        )
        assert outcome.verdict == CognitionReplayVerdict.SEMANTIC_MATCH
        assert outcome.matches is True

    # ----- DIVERGENCE row -----

    def test_divergence_temp_zero_matches_false(self) -> None:
        classifier = _StubClassifier(returns=CognitionDisagreementType.FACTUAL_DISAGREEMENT)
        payload = self._payload_at(0.0)
        outcome = default_compare_cognition_results(
            payload, self._replayed(), classifier=classifier
        )
        assert outcome.verdict == CognitionReplayVerdict.DIVERGENCE
        assert outcome.matches is False

    def test_divergence_temp_high_matches_false(self) -> None:
        """Phase 13.x.4 behavior change: confident classifier divergence
        raises even at temp>0."""
        classifier = _StubClassifier(returns=CognitionDisagreementType.FACTUAL_DISAGREEMENT)
        payload = self._payload_at(0.7)
        outcome = default_compare_cognition_results(
            payload, self._replayed(), classifier=classifier
        )
        assert outcome.verdict == CognitionReplayVerdict.DIVERGENCE
        assert outcome.matches is False

    # ----- UNCLASSIFIED row -----

    def test_unclassified_temp_zero_matches_false(self) -> None:
        """Preserves PR #20: classifier unsure at temp=0 → raise."""
        classifier = _StubClassifier(returns=CognitionDisagreementType.UNCLASSIFIED)
        payload = self._payload_at(0.0)
        outcome = default_compare_cognition_results(
            payload, self._replayed(), classifier=classifier
        )
        assert outcome.verdict == CognitionReplayVerdict.UNCLASSIFIED
        assert outcome.matches is False

    def test_unclassified_temp_high_matches_true(self) -> None:
        """Preserves PR #20: classifier unsure at temp>0 → no raise
        (benefit of doubt under double uncertainty)."""
        classifier = _StubClassifier(returns=CognitionDisagreementType.UNCLASSIFIED)
        payload = self._payload_at(0.7)
        outcome = default_compare_cognition_results(
            payload, self._replayed(), classifier=classifier
        )
        assert outcome.verdict == CognitionReplayVerdict.UNCLASSIFIED
        assert outcome.matches is True
```

- [ ] **Step 2: Run the new tests → expect 8 PASS (since `_compute_matches` was implemented in Task 5)**

Run: `pytest tests/cognition/test_cognition_replay.py::TestRaisePolicyMatrix -v`
Expected: 8 PASSED.

- [ ] **Step 3: Run all tests to verify nothing regressed**

Run: `pytest tests/cognition/test_cognition_replay.py -v --no-header 2>&1 | tail -5`
Expected: existing count + 8 (matrix) + 7 (mapping) + 1 (Task 3) + 1 (Task 4) PASSED.

- [ ] **Step 4: Commit**

```bash
git add tests/cognition/test_cognition_replay.py
git commit -m "phase 13.x.4 step 6: TestRaisePolicyMatrix (8 tests)"
```

---

## Task 7: TestClassifierErrorHandling — 3 tests for fallback paths

**Files:**
- Test: `tests/cognition/test_cognition_replay.py` (add `TestClassifierErrorHandling` class)
- (No production code changes — error handling was implemented in Task 5)

- [ ] **Step 1: Write the 3 failing tests**

Add to `tests/cognition/test_cognition_replay.py`:

```python
# ---------------------------------------------------------------------------
# Phase 13.x.4: TestClassifierErrorHandling — defense paths.


class TestClassifierErrorHandling:
    @staticmethod
    def _payload() -> dict[str, Any]:
        return {
            "cognition_provenance": {"temperature": 0.0},
            "result_text": "original",
            "result_tool_calls": [],
        }

    @staticmethod
    def _replayed() -> CognitionResult:
        return CognitionResult(
            text="different",
            tool_calls=[],
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            latency_ms=0.0,
            provider_fingerprint="x",
        )

    def test_classifier_raises_falls_back_to_unclassified(self) -> None:
        """classifier.classify() exception → verdict=UNCLASSIFIED with
        reason prefix 'classifier_failure:'."""
        boom = _FailingClassifier(exception=RuntimeError("model file corrupt"))
        outcome = default_compare_cognition_results(
            self._payload(), self._replayed(), classifier=boom
        )
        assert outcome.verdict == CognitionReplayVerdict.UNCLASSIFIED
        assert outcome.reason.startswith("classifier_failure: RuntimeError(")
        assert "model file corrupt" in outcome.reason
        assert outcome.classification is None  # Classifier raised; no result.

    def test_classifier_returns_unclassified_explicitly_respected(self) -> None:
        """Classifier explicitly returns UNCLASSIFIED → verdict=UNCLASSIFIED
        with reason WITHOUT classifier_failure prefix (the classifier ran
        successfully; it just didn't know)."""
        classifier = _StubClassifier(returns=CognitionDisagreementType.UNCLASSIFIED)
        outcome = default_compare_cognition_results(
            self._payload(), self._replayed(), classifier=classifier
        )
        assert outcome.verdict == CognitionReplayVerdict.UNCLASSIFIED
        assert not outcome.reason.startswith("classifier_failure:")
        assert outcome.classification is not None
        assert outcome.classification.classifier_version == "stub-fixed-v1"

    def test_classifier_returns_unmapped_type_treated_as_unclassified(self) -> None:
        """If the classifier returns a disagreement_type not in the mapping,
        the lookup falls back to UNCLASSIFIED (defense-in-depth).

        We simulate this by patching MAP_DISAGREEMENT_TYPE_TO_VERDICT to be
        empty for the duration of the test, then invoking with FACTUAL_AGREEMENT
        (which would normally map to SEMANTIC_MATCH). The lookup uses
        ``.get(<key>, UNCLASSIFIED)``, so an empty dict produces UNCLASSIFIED.
        """
        import phoenix.ledger.cognition_replay as cr

        original = cr.MAP_DISAGREEMENT_TYPE_TO_VERDICT
        cr.MAP_DISAGREEMENT_TYPE_TO_VERDICT = {}  # type: ignore[misc]
        try:
            classifier = _StubClassifier(returns=CognitionDisagreementType.FACTUAL_AGREEMENT)
            outcome = default_compare_cognition_results(
                self._payload(), self._replayed(), classifier=classifier
            )
            assert outcome.verdict == CognitionReplayVerdict.UNCLASSIFIED
        finally:
            cr.MAP_DISAGREEMENT_TYPE_TO_VERDICT = original  # type: ignore[misc]
```

- [ ] **Step 2: Run the new tests → expect 3 PASS (error handling implemented in Task 5)**

Run: `pytest tests/cognition/test_cognition_replay.py::TestClassifierErrorHandling -v`
Expected: 3 PASSED.

- [ ] **Step 3: Run all tests to verify nothing regressed**

Run: `pytest tests/cognition/test_cognition_replay.py -v --no-header 2>&1 | tail -5`
Expected: existing count + 8 + 7 + 1 + 1 + 3 PASSED.

- [ ] **Step 4: Commit**

```bash
git add tests/cognition/test_cognition_replay.py
git commit -m "phase 13.x.4 step 7: TestClassifierErrorHandling (3 tests)"
```

---

## Task 8: TestPerfOptimization — bit-exact case skips classifier (1 test)

**Files:**
- Test: `tests/cognition/test_cognition_replay.py` (add `TestPerfOptimization` class)

- [ ] **Step 1: Write the failing test**

Add to `tests/cognition/test_cognition_replay.py`:

```python
# ---------------------------------------------------------------------------
# Phase 13.x.4: TestPerfOptimization — verify bit-exact case bypasses
# the classifier call.


class TestPerfOptimization:
    def test_bit_exact_does_not_call_classifier(self) -> None:
        """Bit-exact comparison must NOT call classifier.classify().

        The early return on text_match + tool_calls_match prevents the
        classifier call (saving ~100-500ms when hybrid LLM-judge fires).
        This test pins that perf optimization as an automated invariant.
        """
        from unittest.mock import MagicMock

        spy_classifier = MagicMock()
        spy_classifier.version = "spy-v1"
        # Build a bit-exact payload.
        payload = {
            "cognition_provenance": {"temperature": 0.0},
            "result_text": "hello",
            "result_tool_calls": [],
        }
        replayed = CognitionResult(
            text="hello",  # bit-exact match with original
            tool_calls=[],
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            latency_ms=0.0,
            provider_fingerprint="x",
        )
        outcome = default_compare_cognition_results(
            payload, replayed, classifier=spy_classifier
        )
        assert outcome.verdict == CognitionReplayVerdict.BIT_EXACT
        spy_classifier.classify.assert_not_called()
```

- [ ] **Step 2: Run → expect PASS (Task 4's bit-exact early return already implements this)**

Run: `pytest tests/cognition/test_cognition_replay.py::TestPerfOptimization -v`
Expected: 1 PASSED.

- [ ] **Step 3: Commit**

```bash
git add tests/cognition/test_cognition_replay.py
git commit -m "phase 13.x.4 step 8: TestPerfOptimization (1 test)"
```

---

## Task 9: Thread classifier kwarg through `replay_cognition_entry`

**Files:**
- Modify: `phoenix/ledger/cognition_replay.py` (`replay_cognition_entry` signature + internal call sites)
- Test: `tests/cognition/test_cognition_replay.py` (add classifier-propagation test)

- [ ] **Step 1: Write the failing test**

Add to `tests/cognition/test_cognition_replay.py`:

```python
# ---------------------------------------------------------------------------
# Phase 13.x.4: classifier kwarg propagates through replay_cognition_entry.


class TestClassifierKwargPropagation:
    def test_replay_passes_classifier_to_comparator(self) -> None:
        """When replay_cognition_entry is called with a classifier kwarg,
        it propagates to the comparator (default OR custom)."""
        prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])
        # Build VERBATIM payload where text DIFFERS so the comparator
        # actually invokes the classifier.
        payload = _build_verbatim_payload(prompt=prompt, result_text="original", temperature=0.0)
        backend = _FakeBackend(rows=[_build_row(entry_id="c1", payload=payload)])

        canned = CognitionResult(
            text="DIFFERENT",
            tool_calls=[],
            usage=TokenUsage(input_tokens=10, output_tokens=20),
            latency_ms=0.0,
            provider_fingerprint="x",
        )

        # FACTUAL_AGREEMENT classifier → verdict=SEMANTIC_MATCH → no raise.
        classifier = _StubClassifier(returns=CognitionDisagreementType.FACTUAL_AGREEMENT)

        report = replay_cognition_entry(
            "c1",
            state_backend=_as_backend(backend),
            provider_factory=_make_factory(canned),
            classifier=classifier,
        )
        assert report.comparison_outcome is not None
        assert report.comparison_outcome.verdict == CognitionReplayVerdict.SEMANTIC_MATCH
        assert report.comparison_outcome.matches is True
        assert report.comparison_outcome.classification is not None
        assert report.comparison_outcome.classification.classifier_version == "stub-fixed-v1"
```

- [ ] **Step 2: Run → expect FAIL (replay_cognition_entry doesn't accept classifier kwarg yet)**

Run: `pytest tests/cognition/test_cognition_replay.py::TestClassifierKwargPropagation -v`
Expected: FAIL with `TypeError: replay_cognition_entry() got an unexpected keyword argument 'classifier'`.

- [ ] **Step 3: Add the kwarg + propagate to the comparator**

Edit `phoenix/ledger/cognition_replay.py`. Update `replay_cognition_entry`'s signature + the two internal call sites (`_replay_verbatim` and `_replay_encrypted`):

```python
def replay_cognition_entry(
    entry_id: str,
    *,
    provider_factory: CognitionProviderFactory | None = None,
    comparator: CognitionResultComparator | None = None,
    classifier: CognitionClassifier | None = None,  # NEW (Phase 13.x.4)
    state_backend: StateBackend | None = None,
) -> CognitionReplayReport:
```

Then inside the function, after resolving `compare` and before the disposition branches:

```python
    # Phase 13.x.4: wrap the comparator so the classifier kwarg flows through.
    # Custom comparators that don't take a classifier kwarg are still supported
    # because we only inject the kwarg into the default_compare_cognition_results
    # path; custom comparators receive only (original_payload, replayed).
    if comparator is None:
        # Bind the classifier into a partial so the comparator call site
        # below sees the standard CognitionResultComparator signature.
        from functools import partial

        compare = partial(default_compare_cognition_results, classifier=classifier)
    else:
        compare = comparator
```

(Note: the existing `compare = comparator if comparator is not None else default_compare_cognition_results` line gets replaced with the block above.)

The `_replay_verbatim` and `_replay_encrypted` functions don't need signature changes — they receive `compare` as a parameter and call `compare(payload, replayed)` which now has the classifier closed over.

- [ ] **Step 4: Run the new test → expect PASS**

Run: `pytest tests/cognition/test_cognition_replay.py::TestClassifierKwargPropagation -v`
Expected: 1 PASSED.

- [ ] **Step 5: Run all tests → expect existing + new PASS**

Run: `pytest tests/cognition/test_cognition_replay.py -v --no-header 2>&1 | tail -5`
Expected: existing PR #20 count + 21 NEW (1 + 1 + 7 + 8 + 3 + 1 + 1) PASSED.

- [ ] **Step 6: Commit**

```bash
git add phoenix/ledger/cognition_replay.py tests/cognition/test_cognition_replay.py
git commit -m "phase 13.x.4 step 9: replay_cognition_entry threads classifier kwarg"
```

---

## Task 10: Add CHANGELOG entry + full pre-PR validation

**Files:**
- Modify: `CHANGELOG.md` (add `## [Phase 13.x.4]` entry under existing `## [1.1.0.dev0]` section)
- Run: full validation matrix (pytest, mypy --strict, ruff)

- [ ] **Step 1: Add the CHANGELOG entry**

Edit `CHANGELOG.md`. Find the `## [1.1.0.dev0] — 2026-05-20` heading. Add a new sub-heading IMMEDIATELY ABOVE the existing entry's first paragraph (so the 13.x.4 work nests under the v1.1 dev line):

```markdown
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

**Back-compat guarantee:** All ~23 existing PR #20 tests pass
unchanged. The `AlwaysUnclassifiedClassifier` ship default produces
`verdict=UNCLASSIFIED`, which maps to the same `matches` semantics as
PR #20's binary outcome.

**Perf optimization:** The bit-exact branch returns *without* calling
the classifier (saves ~100-500ms when hybrid LLM-judge fires).
Pinned via `TestPerfOptimization`.

**Classifier failure fallback:** If `classifier.classify()` raises,
the comparator returns `verdict=UNCLASSIFIED` with `reason` prefix
`classifier_failure: <ExceptionType>(<first 80 chars>)`; logs the
exception at WARNING. Replay does not crash on classifier
malfunction.

**Tests added:** 21 new (4 registry + 7 mapping + 8 raise-policy + 3
error-handling + 1 perf opt) in
`tests/cognition/test_cognition_classifier_registry.py` and
`tests/cognition/test_cognition_replay.py`.

**Open follow-ups (deferred):** `classifier-version-drift` warning,
`hybrid-classifier-LLM-judge-cost` ceiling. Both are v1.1.x slots.

```

- [ ] **Step 2: Run the full test suite**

Run: `pytest tests/ -v --no-header 2>&1 | tail -10`
Expected: all tests passing. The new 21 + the pre-existing PR #20 ~23 + everything else.

- [ ] **Step 3: Run mypy --strict on the touched modules**

Run: `mypy phoenix/ledger/cognition_classifier.py phoenix/ledger/cognition_replay.py --strict`
Expected: `Success: no issues found in 2 source files`.

- [ ] **Step 4: Run ruff check**

Run: `ruff check phoenix/ledger/cognition_classifier.py phoenix/ledger/cognition_replay.py tests/cognition/test_cognition_classifier_registry.py tests/cognition/test_cognition_replay.py`
Expected: `All checks passed!`.

- [ ] **Step 5: Run ruff format check (no changes needed)**

Run: `ruff format --check phoenix/ledger/cognition_classifier.py phoenix/ledger/cognition_replay.py tests/cognition/test_cognition_classifier_registry.py tests/cognition/test_cognition_replay.py`
Expected: `X files already formatted` (no changes).

- [ ] **Step 6: Commit**

```bash
git add CHANGELOG.md
git commit -m "phase 13.x.4 step 10: CHANGELOG entry + full validation"
```

---

## Task 11: Push branch + open PR + final summary

**Files:**
- Read-only: gh pr create, branch push.

- [ ] **Step 1: Push the branch to origin**

Run: `git push -u origin phase-13x-classifier-integration`
Expected: `* [new branch] phase-13x-classifier-integration -> phase-13x-classifier-integration`.

- [ ] **Step 2: Create the PR**

Run:
```bash
gh pr create --title "phase 13.x.4: classifier integration for cognition replay" --body "$(cat <<'EOF'
## Summary

Wires `CognitionClassifier` into `phoenix/ledger/cognition_replay.py`'s `default_compare_cognition_results` to produce a 4-level `CognitionReplayVerdict` (bit_exact / semantic_match / divergence / unclassified) per the Phase 13.x.4 design spec.

## What ships

- **New module:** `phoenix/ledger/cognition_classifier.py` (registry + Protocol re-export; ship default `AlwaysUnclassifiedClassifier`).
- **`ComparisonOutcome`** gains optional `verdict` + `classification` fields (back-compat: default None).
- **`replay_cognition_entry`** and **`default_compare_cognition_results`** gain `classifier: CognitionClassifier | None = None` kwarg.
- Locked **disagreement_type → verdict mapping** and **raise-policy matrix** per design spec §5.1 and §5.2.
- **Classifier failure fallback:** `classifier.classify()` raising falls back to `UNCLASSIFIED` with `classifier_failure:` reason prefix; replay doesn't crash.

## Two behavior changes from PR #20

1. `temp=0 + SEMANTIC_MATCH` no longer raises (classifier-confirmed equivalence).
2. `temp>0 + DIVERGENCE` now raises (classifier confidence beats the temp>0 hedge).

## Back-compat

All ~23 existing PR #20 tests pass **unchanged**. The `AlwaysUnclassifiedClassifier` ship default reproduces PR #20's binary `matches` semantics for callers that don't opt into classifier wiring.

## Tests added

21 new across 5 test classes:
- `TestCognitionClassifierRegistry` (4) — set/get/reset/isolation
- `TestVerdictMapping` (7) — one per CognitionDisagreementType
- `TestRaisePolicyMatrix` (8) — 4 verdicts × 2 temp regimes
- `TestClassifierErrorHandling` (3) — fallback paths
- `TestPerfOptimization` (1) — bit-exact skips classifier
- `TestClassifierKwargPropagation` (1) — kwarg flows through replay_cognition_entry

## Spec / plan

- Design: `docs/superpowers/specs/2026-05-28-phase-13x4-classifier-integration-design.md` (committed `465044d`)
- Plan: `docs/superpowers/plans/2026-05-28-phase-13x4-classifier-integration.md`

## Test plan

- [ ] Reviewer confirms `pytest tests/ -v` all green on this branch
- [ ] Reviewer confirms `mypy --strict` clean on the two touched modules
- [ ] Reviewer eyeballs the §5.2 raise-policy matrix and the two behavior changes
- [ ] Reviewer eyeballs the classifier failure fallback (verdict=UNCLASSIFIED + reason prefix)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL printed to stdout.

- [ ] **Step 3: Watch the PR's CI to first signal**

Run: `gh pr checks <pr-number> 2>&1 | head -3` (replace `<pr-number>` with the PR number from Step 2).
Expected: at least one check entry appearing (CI started or queued).

- [ ] **Step 4: Final summary message**

Summarize for Adam:
1. PR URL.
2. Branch + new commits (use `git log main..phase-13x-classifier-integration --oneline`).
3. Test counts: 21 new tests; ~23 existing PR #20 tests preserved unchanged.
4. CI initial state (pass/pending/fail per the most recent check).
5. Next-session candidates beyond merge: 13.x.5 audit, 13.x.7 / .x.8 planning, memory refresh, README v1.0-status update.

---

## Self-review

**Spec coverage:**
- §1 Context — covered by plan header + Task 0 framing.
- §2 Goal — covered by plan header `Goal`. ✓
- §3 Out of scope — verification gate / admin endpoint / ledger schema / training / 13.x.5/7/8 not touched. ✓
- §4 API surface — Task 1 (cognition_classifier.py), Task 2 (enum + mapping), Task 3 (ComparisonOutcome), Task 5 (function signatures). ✓
- §5 Decision flow — Task 4 (bit-exact branch), Task 5 (classifier-driven branch), Task 5 (compute_matches), Task 7 (error fallback). ✓
- §5.1 mapping — Task 5 TestVerdictMapping pins all 7 rows. ✓
- §5.2 matrix — Task 6 TestRaisePolicyMatrix pins all 8 cells. ✓
- §5.3 classifier failure — Task 7 TestClassifierErrorHandling covers exception path. ✓
- §5.4 invalid result — Task 7 test_classifier_returns_unmapped_type_treated_as_unclassified. ✓
- §6 out-of-scope alternatives — not implementing; explicitly listed in spec. ✓
- §7 open tensions — surfaced; deferred. ✓
- §8 acceptance — all 9 criteria mapped to tasks: #1→Task 1, #2→Task 3, #3→Task 5+9, #4→Tasks 4-7, #5→Task 1, #6→Tasks 5-7, #7→Tasks 3-9, #8→Task 10, #9→Task 10. ✓
- §9 risks — perf-opt risk addressed by Task 8; behavior-change risk addressed by Task 10 CHANGELOG. ✓
- §10 file growth — matches Task 1 (~80 new) + Task 5 (~80 added to cognition_replay.py) + Tasks 1+3-9 (~250 added to test_cognition_replay.py) + Task 10 (CHANGELOG). ✓

**Placeholder scan:**
- No "TBD" / "TODO" / "implement later" / "add appropriate error handling".
- `<pr-number>` in Task 11 Step 3 is a runtime template field (PR number is assigned at PR creation in Step 2), not a deferred decision.

**Type/method consistency:**
- `CognitionReplayVerdict` enum: members are `BIT_EXACT` / `SEMANTIC_MATCH` / `DIVERGENCE` / `UNCLASSIFIED` consistently throughout (Tasks 2, 4, 5, 6, 7, 8, 9). ✓
- `MAP_DISAGREEMENT_TYPE_TO_VERDICT` dict: defined in Task 2, used in Task 5. ✓
- `default_compare_cognition_results` signature consistent across Tasks 5, 6, 7, 8, 9 (kwarg `classifier: CognitionClassifier | None = None`). ✓
- `replay_cognition_entry` signature: existing kwargs preserved, new `classifier` added in Task 9. ✓
- `_StubClassifier(returns=...)` test helper: defined in Task 5, used in Tasks 6, 7, 9 with the same constructor signature. ✓
- `_FailingClassifier(exception=...)` test helper: defined in Task 5, used in Task 7. ✓
- `ComparisonOutcome` field names (`verdict`, `classification`): consistent across Tasks 3, 4, 5, 6, 7, 8, 9. ✓
- `_compute_matches` / `_build_unclassified_outcome` / `_payload_to_cognition_result` helpers: defined in Task 5, used by the comparator there; no later task assumes a different name. ✓

All cross-task references check out. Plan complete.
