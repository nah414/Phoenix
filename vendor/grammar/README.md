# evolution/knowledge/grammar

## Purpose

Generative grammar layer (Phase 3.2) for the Sanskrit codec. A small
context-free grammar over the codec's already-defined glyph pool so the
evolution lab can **produce** well-formed compressed physics statements
from primitives -- not just compress existing text.

Drives E4's `backend="symbolic"` candidate mode: Qwen3 (or, in Phase 3.2,
the bundled grammar) emits a compressed statement whose glyphs are
structural rather than English; the candidate rides on the existing
solver code so it stays scorable by the Phase E4 sandbox.

## Files

| File | Role |
|---|---|
| `grammar_loader.py` | YAML safe_load + typed `Grammar` / `Production` / `Symbol` + validation (references, productivity, start symbol). `yaml.safe_load` only; Python-object tags raise `GrammarLoadError`. |
| `generator.py` | Deterministic seeded derivation. Depth-aware alternative selection -- once the `max_depth` budget is exhausted the generator prefers terminal-only RHS, falling back to the lowest-non-terminal-count alternative if none exists. `hard_cap` (default 10,000 recursive calls) guards pathological grammars. |
| `parser.py` | Packrat-style recursive-descent parser. Memoises on `(lhs, pos, depth_left)`. Breaks left-recursion without progress via an active-position tracker. Returns a `ParseTree` whose `collect_terminals()` reproduces the input verbatim. |
| `physics_v1.yaml` | Shipping grammar: 13 non-terminals, 51 productions. |
| `__init__.py` | Public API: `load_default_grammar`, `load_grammar`, `load_grammar_from_dict`, `generate`, `parse`, `ParseTree`, `Grammar`, `GrammarLoadError`, `ParseError`, `GenerationError`. |

## YAML schema

Non-terminals are written `"<Name>"`; anything else is a terminal
(verbatim string, spacing significant). Example:

```yaml
grammar: physics_v1
version: 1
start: Statement

productions:
  Statement:
    - ["<Equation>"]
    - ["<Quantifier>", " ", "<Var>", " : ", "<Equation>"]
  Equation:
    - ["<Expr>", " = ", "<Expr>"]
  Expr:
    - ["<ScalarExpr>"]
    - ["<OpExpr>"]
  ScalarExpr:
    - ["⦗E⦘"]
    - ["<ScalarExpr>", " + ", "<ScalarExpr>"]
  # ...
```

Validation errors surface as `GrammarLoadError`:

- non-terminal reference to an undefined LHS,
- `production` map missing,
- any production with an empty RHS,
- grammar with no terminal path from `start` (unproductive),
- non-integer `version` or missing `grammar` / `start` fields,
- YAML parser errors (including `!!python/object:` / `!!python/name:`
  injection attempts, which `safe_load` rejects).

## physics_v1 coverage

The shipping grammar is a Pāṇini-style palette over glyphs the codec
already owns. Every terminal sits inside the escape-target pool, so
`decode(encode(generate(seed, depth), seal=False)) == _normalise(s)` --
round-trip through the codec is an identity.

- **Statement forms:** equation, quantifier + equation, implication
  (`⇒`, `⇔`).
- **Equation operators:** `=`, `≈`, `∝`.
- **Scalar arithmetic:** `+`, `-`, `·`; atoms include `⦗E⦘`, `⦗ϑ⦘`,
  `⦗μ⦘`, the inner product `⟨⦗Ψ⦘|⦗Ψ⦘⟩`, expectation value `⟨ ⦗Ψ⦘ ⟩`,
  integrals `∫ c 𝒹x`, Born probability `ℙ(⦗Ψ⦘)`.
- **Operator algebra:** tensor product `⊗`, direct sum `⊕`, commutator
  `[ , ]`, canonical operators `Ĥ`, `𝒪`, `𝒰`.
- **Vector expressions:** `⦗V⦘`, `⦗𝔸⦘`, plus `∇ ⦗E⦘`.
- **Differential operators:** `∂ₜ`, `∇`, `∇²`.
- **Constants:** `ℏ`, `𝑐`, `𝑘`, `α`, `0`.
- **Quantifiers:** `∀`, `∃`.
- **Variables:** `t`, `𝑥`, `𝐸`, `ψ`.

## Public API

```python
from evolution.knowledge.grammar import (
    generate, parse, load_default_grammar,
    GenerationError, ParseError, GrammarLoadError,
)

grammar = load_default_grammar()
statement = generate(grammar, seed=42, max_depth=6)
tree = parse(grammar, statement)

# statement is deterministic: same (seed, max_depth) → same output.
# tree.collect_terminals() == statement
```

## E4 symbolic-candidate integration

`evolution/algo_lab/candidate_generator.py` accepts `backend="symbolic"`.
On that path the generator:

1. Draws a statement via `grammar_generate(grammar, seed=derived, max_depth=6)`.
2. Round-trip-verifies through `grammar_parse(grammar, statement)` --
   any parse failure aborts candidate creation rather than emitting a
   malformed one.
3. Attaches the seeded solver code for the regime so the candidate
   remains runnable by the E4 sandbox scorer.
4. Records `symbolic_statement`, `symbolic_grammar`, and `symbolic_seed`
   on the `GeneratedCandidate` dataclass; these are persisted into
   `provenance.json` and exposed to the UI.

Seed derivation: `abs(hash((regime, params))) % 2**31` unless the
caller passes an explicit `symbolic_seed`. Same `(regime, params,
symbolic_seed)` → same statement.

## Surfaces

- **REST:** `GET /api/evolution/grammar` (productions),
  `POST /api/evolution/grammar/generate` (sample, self-parse verified),
  `POST /api/evolution/grammar/parse` (arbitrary text → tree or error).
- **CLI:** `evo grammar list | generate [--seed N] [--depth D]
  [--start NT] | parse <text>`.
- **UI:** `GrammarPanel` below `MDLProposalPanel` in the Evolution Lab
  view. Shows metadata (name/version/start/NT-count/production-count),
  two side-by-side demos (generate + parse), and a collapsible rule
  table.

## Invariants (tested in `tests/test_sanskrit_grammar.py`)

1. **Productivity** -- every non-terminal reaches a terminal-only
   expansion in finite depth. Enforced at load time; unproductive
   grammars raise `GrammarLoadError`.
2. **Determinism** -- same `(grammar, seed, max_depth)` → same output
   string and same parse tree.
3. **Bounded generation** -- `max_depth` + `hard_cap` prevent runaway
   recursion; `max_depth=0` still produces a non-empty terminal string
   (base-case selection is guaranteed to terminate by the productivity
   invariant).
4. **Parser round-trip** -- `parse(generate(seed)).collect_terminals()
   == generate(seed)` for all 50 seeds tested.
5. **Codec round-trip** -- `decode(encode(generate(seed), seal=False))
   == _normalise(generate(seed))` for the first 20 seeds. Every
   generator terminal is in the codec's escape-target pool.
6. **E4 compatibility** -- existing `qwen3` / `seeded` / `auto`
   backends unchanged; symbolic fields default to empty strings /
   `None` for non-symbolic candidates.
7. **Security** -- `yaml.safe_load` only; `!!python/object:` and
   `!!python/name:` tags raise `GrammarLoadError`; missing file +
   malformed YAML raise the same error type.
8. **Performance** -- 100 samples at `max_depth=6` complete well under
   one second on dev hardware (budget enforced by `TestPerformance`).

## Tests

`tests/test_sanskrit_grammar.py` (31 tests, 4 tiers): unit (loader +
generator + parser), integration (round-trip + codec seal + E4
symbolic), performance, security. All 31 pass as of Phase 3.2.

## Extensions (post-3.2, once Adam's Chat Projects session lands)

- Richer vocabulary (Dirac equation, Klein-Gordon, gauge covariant
  derivatives).
- Context-sensitive productions (sandhi-style compound forms
  expressible in the grammar rather than as post-hoc codec rules).
- Grammar diff tool so Phase 3.1 MDL-discovered rule mutations can be
  proposed as grammar edits, not just codec-table edits.

Out of scope for Phase 3.2; flagged here as reference for the next
design session.
