# Phase 13 PR Sweep — Design

**Date:** 2026-05-27
**Author:** Adam (with Claude as design partner)
**Status:** DRAFT — awaiting Adam review
**Type:** sequencing/execution plan (not a phase build guide)

---

## 1 — Context

Phase 13 (cognition substrate + MCP-client mode) merged its 10-step main
line on 2026-05-19 as `phoenix-middleware 1.1.0.dev0` (PR #15). Between
2026-05-20 and 2026-05-24 the work spawned six follow-up PRs that have
been sitting CI-green awaiting review:

| PR | Branch | Type | Risk |
|----|--------|------|------|
| #17 | `housekeeping-readme-phase13` | doc-only — adds Phase 13 row to README status table | None |
| #16 | `arch-v1-open-tensions` | doc-only — locks 14 v1.0 Section 11 tensions; fixes 11.9 accounting | None |
| #18 | `phase-13x-mcp-budget-audit` (.x.1) | code — MCPClient.call_tool enforces dispatch gates internally (defense-in-depth) | Low |
| #19 | `phase-13x-streaming` (.x.2) | code — streaming impls for OpenAI / Google / LiteLLM cognition adapters | Low |
| #20 | `phase-13x-replay-cognition` (.x.3) | code — cognition replay engine (binary comparator + classifier hook) | Medium |
| #21 | `phase-13x-encryption-age` (.x.6) | code — encryption ceremony (age-based reference impl for ENCRYPTED_OPT_IN disposition) | Medium |

CI status as of 2026-05-27: all 6 PRs pass the full 9-check matrix (Docker
build, wheel build, lint/mypy/shellcheck, 3.11/3.12/3.13 × ubuntu/windows
tests). Sourcery reviews pass on the 4 code PRs (#18-21).

The 13.x.4 and 13.x.5 numbering slots were reserved-and-skipped; an
exhaustive search of the Phase 13 buildguide, decisions doc, all commits,
and `E:\CLAUDE_NOTES.md` found zero references. Treated as cosmetic gap;
no action required.

## 2 — Goal

Drive all six CI-green Phase 13 follow-up PRs through human review and
merge to `main`, in a risk-ladder order that builds shared review context
before the higher-risk surfaces.

## 3 — Out of scope

- **13-D1 frank-data LICENSE declaration.** Adam-side action; tracked
  separately. Phase 13 dev0 already shipped under informal-loosening, so
  the LICENSE is not a blocker for these merges. Surfaced as a parallel
  item but not driven by this plan.
- **1.0.0 final ship.** v1.0.0rc1 is the current high-water on the v1.0
  line; promotion to `1.0.0` is its own decision and not coupled to
  these v1.1-line follow-ups.
- **Phase 13.x.4 / .x.5.** Numbering gap is cosmetic; no missing work.
- **Phase 14+ planning.** Out of scope.
- **Perception harness Phase 12 buildguide drafting.** Out of scope
  (parallel track per 13-D5).

## 4 — Merge sequence and per-PR review pattern

### Sequence (risk-ladder)

1. **PR #17** (housekeeping-readme-phase13)
2. **PR #16** (arch-v1-open-tensions)
3. **PR #18** (.x.1 — mcp-budget-audit)
4. **PR #19** (.x.2 — streaming)
5. **PR #20** (.x.3 — replay-cognition)
6. **PR #21** (.x.6 — encryption-age)

Rationale: doc-only first to warm up the merge tooling and validate no
rebase conflicts between adjacent PRs. Then code PRs in numerical order
(.x.1 → .x.2 → .x.3 → .x.6). Encryption-age last because it is the most
novel surface (security ceremony, key handling) and benefits from the
prior 5 merges being in `main` for context.

### Per-PR review pattern (applied to all 6)

For each PR in sequence:

1. **Diff fetch.** `gh pr diff <N>` to load the full diff.
2. **Three-axis review** — concise bullets across:
   - **Correctness** — does it do what the commit message claims?
   - **Boundary discipline** — does it stay within its declared file scope,
     or does it leak into adjacent layers?
   - **Test coverage** — are the new code paths exercised by the tests
     in the same PR?
3. **Surface concerns to Adam** — any concerns get raised before merge.
   No concerns = "ready to merge, recommend approve."
4. **Adam decision** — approve and merge, request changes, or skip.
5. **Post-merge verification** — confirm `main` still green; rebase the
   remaining PRs only if a conflict is reported by GitHub.

Acceptance criteria for "review complete": one of {approve, request
changes, skip} signal from Adam on each PR.

## 5 — Acceptance criteria for "all Phase 13 items shipped"

The plan is **complete** when:

- All 6 PRs have a terminal state recorded (merged, closed, or
  explicitly deferred with rationale).
- `main` is green in CI after the last merge.
- `docs/superpowers/specs/2026-05-27-phase-13-pr-sweep-design.md` plus a
  short execution log (which PRs merged, which deferred, any notable
  findings) is committed.

The plan is **NOT** required to:

- Resolve 13-D1 LICENSE (Adam-side, parallel).
- Cut a new version bump beyond `1.1.0.dev0` (the .x sub-improvements
  are dev-line refinements; a `1.1.0.dev1` bump is an option but not
  required by this plan — surfaces as an [OPEN] item below).

## 6 — Open tensions

- **[OPEN: version-bump-policy]** — Each .x merge ships a code change but
  the version stays `1.1.0.dev0`. Acceptable for the dev line but means
  there's no version pin that captures "the dev0 line *after* .x.1-.x.3
  + .x.6 sub-improvements." Defer the decision to Adam at the end of the
  sweep; default if not raised is "no bump, stay at dev0."
- **[OPEN: rebase-strategy]** — If PR #18 conflicts with PR #17 after
  #17 merges, options are (a) rebase-on-merge, (b) Adam rebases locally
  and force-pushes, (c) merge-commit. Plan default is (a) since the PRs
  are 1 commit each; Adam can override per-PR.

## 7 — Risks and mitigations

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| A .x merge introduces a regression CI didn't catch | Low | Per-PR review surface; post-merge `main` CI confirms. Revert is one `gh pr revert` away. |
| Rebase conflict cascades across PRs | Low (small diffs) | Sequence is doc-only-first; code PRs touch different file areas (MCP, streaming, replay, encryption are orthogonal). |
| Encryption-age PR has a security concern that needs deeper review than 3-5 bullets | Medium | If concerns surface, escalate to a dedicated review session — do not merge under time pressure. |
| Session token-exhaustion before all 6 merge | Low | Each PR is small; review-and-merge per PR is bounded work. If we run out, the merged PRs stay merged and unmerged ones stay open. |
| 13-D1 LICENSE escalation lands mid-sweep | Low | Surface to Adam if frank-data LICENSE gets declared during the sweep; doesn't block continuing. |

## 8 — Execution log placeholder

To be appended at sweep end:

```
PR #17: [outcome]
PR #16: [outcome]
PR #18: [outcome]
PR #19: [outcome]
PR #20: [outcome]
PR #21: [outcome]
main green after sweep: [Y/N + CI run URL]
Notable findings: [...]
Version bump decision: [...]
```
