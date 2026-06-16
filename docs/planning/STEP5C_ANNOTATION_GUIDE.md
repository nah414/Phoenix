# Step 5c Cognition Corpus — Annotation Guide

**Purpose.** The source of truth for assigning `gold_class` to a pair `(prompt, response_a,
response_b)`. Consistent labels are what let macro-F1 reach 0.70 — inconsistent boundary
calls cap the ceiling no matter how good the features are. This guide gives an **ordered
decision procedure** plus per-class rules and the hard-boundary cases.

The seven classes are defined canonically in
`vendor/cognition_wobble/disagreement_types.py`; this guide is the *operational* version for
a human (or LLM-judge) labeler.

> **`[ADAM-DECIDE]` markers** flag the few places where the line is a genuine judgment call.
> How you draw these lines determines label consistency, so they're yours to lock. Defaults
> are proposed; change them and the whole corpus should follow the locked version.

---

## The decision procedure (apply in order — first match wins)

Order matters: it resolves most boundary ambiguity by precedence. Walk the checks top to
bottom and assign the first class that matches.

1. **Refusal asymmetry?** Did *exactly one* response decline on safety / policy / capability
   grounds (the other answered)? → **`REFUSAL_DIVERGENCE`**.
2. **Tool divergence?** Did the two responses invoke *different* tools, or did one call a
   tool while the other did not? → **`TOOL_CHOICE_DIVERGENCE`**.
3. **Different question?** Did the two responses answer *different readings* of the prompt
   (one answers A, the other answers B)? → **`INTERPRETIVE_DIVERGENCE`**.
4. **Same load-bearing facts?**
   - **Yes**, and presentation is also similar → **`FACTUAL_AGREEMENT`**.
   - **Yes** on the facts, but presentation differs substantially (formality, length,
     framing) → **`STYLISTIC_DIVERGENCE`**.
   - **No** — they assert a *conflicting verifiable claim* on the same question →
     **`FACTUAL_DISAGREEMENT`**.
5. **None of the above / insufficient signal** → **`UNCLASSIFIED`** (see "When to abstain").

> **`[ADAM-DECIDE]` #1 — precedence order.** The default puts refusal and tool checks *before*
> the factual axis (a pair where one refuses *and* the facts differ is labeled
> `REFUSAL_DIVERGENCE`, because the refusal is the load-bearing event for a downstream
> consumer that needs an answer). If you'd rather the factual axis win in that overlap, say so
> and this order flips.

---

## Class definitions, rules, and examples

### `FACTUAL_AGREEMENT`
Both responses assert the same load-bearing claims. Surface phrasing may vary.
- *Rule:* the verifiable claims are equivalent **and** there's no large stylistic gap.
- *Example:* "Paris." / "The capital of France is Paris."

### `STYLISTIC_DIVERGENCE`
Same facts; differ in presentation (formality, detail level, framing).
- *Rule:* claims equivalent, but one is terse/casual and the other verbose/formal (or
  otherwise framed very differently).
- *Example:* "Stuff falls because Earth pulls it down." / a formal Newton-vs-Einstein
  paragraph saying the same thing.

### `FACTUAL_DISAGREEMENT`
The responses answer the **same** question with a **conflicting verifiable claim**.
- *Rule:* a date, value, or boolean fact differs; both clearly addressed the same question.
- *Example:* "WWII ended in 1944." / "WWII ended in 1945."

### `INTERPRETIVE_DIVERGENCE`
The responses read the prompt **differently** — one answers question A, the other B.
- *Rule:* the disagreement is about *what was asked*, not the answer.
- *Example:* prompt "What's the best language?" → one answers programming languages, the
  other spoken languages.

### `REFUSAL_DIVERGENCE`
Exactly one response declined on safety / policy / capability grounds.
- *Rule:* asymmetric refusal. (If *both* refused, it's not divergence — see abstain.)
- *Example:* "I can't help with that." / a substantive answer.

### `TOOL_CHOICE_DIVERGENCE`
Different tool choices, or one called a tool and the other did not.
- *Rule:* the divergence is about *which capability to invoke*, not the final text.
- *Example:* one emits a `get_weather` tool call; the other answers from prior knowledge.

### `UNCLASSIFIED` (gold)
Reserved for genuinely unclassifiable pairs — used to test that the classifier *abstains*.
These rows are excluded from the graded macro-F1.

---

## Hard boundaries (the cases that drag macro-F1)

These are the confusions the roadmap predicts and the confusion matrix will show. Lock the
rule so labeling is consistent.

### `FACTUAL_AGREEMENT` ↔ `STYLISTIC_DIVERGENCE`
Both assert the same facts. The question is *how different is the presentation?*
- *Default rule:* label `STYLISTIC_DIVERGENCE` when the two responses differ by a clear
  register/length step (e.g., one-liner vs. multi-sentence formal exposition); otherwise
  `FACTUAL_AGREEMENT` for minor surface variation (word order, "Paris" vs "It's Paris").
- > **`[ADAM-DECIDE]` #2 — the threshold.** Where exactly does "minor surface variation"
  > become "stylistic divergence"? A usable proxy: length ratio < ~0.5 (one response less
  > than half the other) *or* an obvious formality shift ⇒ `STYLISTIC`. Confirm/adjust this
  > proxy; it should match how the `length_ratio` feature actually separates the two.

### `FACTUAL_DISAGREEMENT` ↔ `INTERPRETIVE_DIVERGENCE`
Both have divergent content. The question is *same question or different question?*
- *Default rule:* if both responses are clearly answering the **same** question but assert
  conflicting facts → `FACTUAL_DISAGREEMENT`. If they're answering **different** questions
  (different reading) → `INTERPRETIVE_DIVERGENCE`. When unsure whether it's "different facts"
  vs "different question," ask: *could both be simultaneously true under different readings?*
  If yes → `INTERPRETIVE`; if they can't both be right about the same thing → `FACTUAL`.
- > **`[ADAM-DECIDE]` #3 — the tie-break.** The "could both be true under different readings"
  > test is the proposed tie-break. Confirm it, or supply your own.

### Refusal/Tool overlaps
Covered by the precedence order (`[ADAM-DECIDE]` #1). A pair where one refuses *and* the
other calls a tool is `REFUSAL_DIVERGENCE` under the default order.

---

## When to abstain (`UNCLASSIFIED`)

Label `UNCLASSIFIED` when:
- Both responses are empty / contentless ("ok" / "?").
- The prompt is meaningless and neither response engages.
- **Both** responses refused (symmetric — no *divergence*).
- You genuinely can't tell which class fits after the decision procedure.

Aim for ~10–15 gold-`UNCLASSIFIED` rows total — enough to test abstention, not so many they
crowd the graded classes. They are **not** training targets (the trainer drops them).

---

## Quality checklist (before committing the corpus)

- [ ] Run `python scripts/corpus_stats.py --corpus <file>` — every graded class ≥ 28, no
      gross duplicates, feature centroids look separable.
- [ ] The four hard classes (`STYLISTIC`, `INTERPRETIVE`, `REFUSAL`, `TOOL_CHOICE`) were
      hand-verified (these are where an LLM-judge bootstrap is least reliable).
- [ ] `source_dataset` is set on every row; `annotation_notes` explains any non-obvious call.
- [ ] The three `[ADAM-DECIDE]` rules are locked and applied uniformly.
