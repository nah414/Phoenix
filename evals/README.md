# evals/ — audit and debugging evaluations

This directory contains evaluations that verify *holistic correctness* of Phoenix
across subsystems. Distinct from `tests/`:

- `tests/` checks specific behavior of specific code (a function returns X for input Y).
- `evals/` checks audit-shaped properties of the system (every required event reaches the
  audit log; the ledger hashchain remains valid under all operations; replay produces
  bit-exact match; drift detectors fire when expected).

Both are pytest-collected. Phase 0 ships placeholder subdirectories; later phases
populate as their subsystems land.

## Subdirectories

- `audit/` — audit-log correctness (architecture v1 §16). Phase 7+.
- `ledger/` — Omega Ledger hashchain integrity (§15). Phase 7+.
- `replay/` — strict/replay reproducibility (§19-21). Phase 7+.
- `drift/` — drift detector behavior (§17). Phase 7+.
- `routing/` — routing decision provenance (§4). Phase 4+.
- `cost_ceiling/` — cost-ceiling enforcement (§4.7). Phase 4+.
- `frontier_physics/` — frontier-physics gating refusal (§7.4 step 6). Phase 7+.

## Running evals

```bash
pytest evals/                           # all evals
pytest evals/audit/                     # one subsystem
pytest evals/ -m "not slow"             # exclude slow evals (marker added in Phase 4+)
```

## Why a separate directory?

Mixing audit-shaped evals into `tests/` makes the test suite harder to scan. When
an audit eval fails, the failure mode is "the system is producing wrong audit
trail" — operationally distinct from "this function has a bug." Keeping them in
their own directory makes that distinction visible to anyone reading the repo.

The split also supports Phoenix v1 Section 10.7's two new acceptance tests added
in the 2026-05-06 tightening pass: the compositional fail-closed "panic mode"
test and the long-window six-month replay test. Both are eval-shaped (they
verify holistic system properties under failure or under time) and live here
once their underlying subsystems land.

## Recent changes

- 2026-05-06 — Phase 0 (BUILDGUIDE_phoenix_v1_phase0_skeleton.md): scaffold created as placeholder structure with seven subdirectory READMEs naming what will populate them.
