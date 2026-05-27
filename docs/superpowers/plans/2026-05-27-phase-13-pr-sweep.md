# Phase 13 PR Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drive all six CI-green Phase 13 follow-up PRs (#16, #17, #18, #19, #20, #21) through human review and merge to `main` in risk-ladder order, with per-PR review surface to Adam.

**Architecture:** Sequential merge orchestration. Each task is one PR; each task follows the same 7-step loop (fetch diff → three-axis review → surface to Adam → await decision → execute decision → post-merge CI check → rebase remaining PRs if needed). Doc-only PRs run first to warm the tooling; code PRs run in numerical order; encryption-age (most novel surface) runs last.

**Tech Stack:** `gh` CLI for PR ops; `git` for branch/merge; Phoenix CI matrix (GitHub Actions: Docker build, wheel build, ruff/mypy/shellcheck, 3.11/3.12/3.13 × ubuntu/windows tests); Sourcery for AI code review.

**Spec:** `docs/superpowers/specs/2026-05-27-phase-13-pr-sweep-design.md` (committed `cc15d80`).

---

## Task 0: Pre-flight

**Files:**
- Read-only: working-tree status, `main` HEAD, all 6 PR CI states.

- [ ] **Step 1: Confirm working tree is clean (modulo known stray file)**

Run: `git status --short`
Expected: only `?? "C\357\200\272temp_section4.txt"` (known Windows path-encoding artifact, pre-existing). If anything else appears, surface to Adam before proceeding.

- [ ] **Step 2: Confirm we are on `main` and synced with `origin/main`**

Run: `git fetch origin && git status -b --short`
Expected: `## main...origin/main` with no `[ahead]` or `[behind]` markers.

- [ ] **Step 3: Re-confirm all 6 PRs are still CI-green**

Run: `for pr in 17 16 18 19 20 21; do echo "=== PR #$pr ==="; gh pr checks $pr --required 2>&1 | tail -3; done`
Expected: every PR shows all checks `pass`. If any have flipped to `fail` since the audit, surface to Adam before proceeding — do not merge a non-green PR.

- [ ] **Step 4: Confirm no PR has new commits since the spec was written**

Run: `for pr in 17 16 18 19 20 21; do echo -n "PR #$pr: "; gh pr view $pr --json commits --jq '.commits | length'; done`
Expected: each PR shows `1` commit (or `2` for #16 which had 2 commits per audit). If anyone pushed updates, surface to Adam.

- [ ] **Step 5: No commit (no changes to commit at this step)**

---

## Task 1: Merge PR #17 (housekeeping-readme-phase13)

**Files:**
- PR diff scope: `README.md` (status-table row addition + ancillary refresh).

- [ ] **Step 1: Fetch diff**

Run: `gh pr diff 17`
Expected: diff contains README.md only. If other files appear, stop and surface to Adam.

- [ ] **Step 2: Three-axis review**

Apply the per-PR review pattern from spec §4:
- **Correctness:** Does the new Phase 13 status row accurately reflect what shipped in `1.1.0.dev0` (cognition substrate + MCP-client mode)?
- **Boundary discipline:** Does the diff stay inside README, or does it leak into other docs?
- **Test coverage:** N/A — doc-only.

Write findings as 3-5 bullets.

- [ ] **Step 3: Surface to Adam**

Present the review bullets. Wait for one of: `approve`, `request changes`, `skip`.

- [ ] **Step 4: Execute Adam's decision**

If approve: `gh pr merge 17 --squash --delete-branch`
If request changes: `gh pr comment 17 --body "<changes>"` then mark PR-as-deferred in execution log.
If skip: mark PR-as-skipped in execution log; proceed.

Expected on merge: `Squashed and merged as <sha>. Deleted branch housekeeping-readme-phase13`.

- [ ] **Step 5: Post-merge CI sanity check on `main`**

Run: `git fetch origin && git log origin/main -1 --oneline` to confirm the squash landed.
Then: `gh run list --branch main --limit 1` to see if `main` has a CI run triggered. If a run is in progress, note the run ID for monitoring; if it finishes during this session, confirm green. (Doc-only PR → main CI should be near-instant since lint+smoke is the dominant cost.)

- [ ] **Step 6: Rebase check for remaining PRs**

Run: `for pr in 16 18 19 20 21; do echo -n "PR #$pr mergeable: "; gh pr view $pr --json mergeable --jq '.mergeable'; done`
Expected: `MERGEABLE` for all. If any show `CONFLICTING`, surface to Adam and resolve before continuing.

- [ ] **Step 7: Commit (none — merge is the commit)**

The squash-merge IS the commit on main. No local commit step required.

---

## Task 2: Merge PR #16 (arch-v1-open-tensions)

**Files:**
- PR diff scope: `PHOENIX_ARCHITECTURE_v1.md` (lock 14 v1.0 Section 11 tensions; fix 11.9 accounting). 2 commits.

- [ ] **Step 1: Fetch diff**

Run: `gh pr diff 16`
Expected: diff contains `PHOENIX_ARCHITECTURE_v1.md` only. If other files appear, stop and surface to Adam.

- [ ] **Step 2: Three-axis review**

- **Correctness:** Are the 14 tension resolutions consistent with prior locked decisions in Sections 1-10? Is the 11.9 accounting fix a real correction (re-categorizing 11.7.1 from "out-of-scope" to "locked-deferral") and not a silent scope change?
- **Boundary discipline:** Diff stays inside architecture doc Section 11 (and 11.9)?
- **Test coverage:** N/A — doc-only.

Write 3-5 bullets.

- [ ] **Step 3: Surface to Adam**

Present review bullets. Wait for `approve` / `request changes` / `skip`.

- [ ] **Step 4: Execute Adam's decision**

If approve: `gh pr merge 16 --squash --delete-branch`
If request changes: `gh pr comment 16 --body "<changes>"`; mark deferred.
If skip: mark skipped.

- [ ] **Step 5: Post-merge sanity check**

Run: `git fetch origin && git log origin/main -1 --oneline`
Then: `gh run list --branch main --limit 1`

- [ ] **Step 6: Rebase check**

Run: `for pr in 18 19 20 21; do echo -n "PR #$pr mergeable: "; gh pr view $pr --json mergeable --jq '.mergeable'; done`
Expected: all `MERGEABLE`.

- [ ] **Step 7: No local commit (merge is the commit)**

---

## Task 3: Merge PR #18 (.x.1 mcp-budget-audit)

**Files:**
- PR diff scope: `phoenix/mcp/client.py` and related tests (defense-in-depth dispatch gates inside `MCPClient.call_tool`). First code PR.

- [ ] **Step 1: Fetch diff**

Run: `gh pr diff 18`
Expected: changes scoped to MCP client + tests; no leaks into other Phoenix subsystems (router, verification gate, etc.).

- [ ] **Step 2: Three-axis review**

- **Correctness:** Does `MCPClient.call_tool` enforce the same dispatch gates the outer admin endpoint enforces? Is it actually defense-in-depth (duplicate enforcement) and not a *replacement* of the outer gate?
- **Boundary discipline:** Changes stay inside `phoenix/mcp/`? Does it import anything from outside MCP that would create new coupling?
- **Test coverage:** Are the new gate-enforcement code paths exercised by tests added in the same PR? Specifically: does at least one test exercise the case where the outer gate is bypassed and the inner gate must fire?

Write 3-5 bullets.

- [ ] **Step 3: Surface to Adam**

Present review bullets. Wait for `approve` / `request changes` / `skip`.

- [ ] **Step 4: Execute Adam's decision**

If approve: `gh pr merge 18 --squash --delete-branch`
If request changes: `gh pr comment 18 --body "<changes>"`; mark deferred.
If skip: mark skipped.

- [ ] **Step 5: Post-merge sanity check**

Run: `git fetch origin && git log origin/main -1 --oneline`
Then: `gh run list --branch main --limit 1` and note the run ID. For code PRs, the full CI matrix runs — confirm or defer the green-check to background monitoring.

- [ ] **Step 6: Rebase check**

Run: `for pr in 19 20 21; do echo -n "PR #$pr mergeable: "; gh pr view $pr --json mergeable --jq '.mergeable'; done`
Expected: all `MERGEABLE`. If any conflict, the diff areas should be different (streaming vs replay vs encryption are orthogonal) so a true conflict is suspicious — surface to Adam.

- [ ] **Step 7: No local commit (merge is the commit)**

---

## Task 4: Merge PR #19 (.x.2 streaming)

**Files:**
- PR diff scope: cognition adapter files (OpenAI / Google / LiteLLM) + tests, adding `astream`-style streaming implementations.

- [ ] **Step 1: Fetch diff**

Run: `gh pr diff 19`
Expected: changes scoped to cognition provider adapters + tests. The Anthropic adapter already shipped `astream` in Phase 13 Step 7 — confirm this PR doesn't re-touch it.

- [ ] **Step 2: Three-axis review**

- **Correctness:** Do the three new streaming impls follow the same `astream` Protocol shape that the Anthropic adapter established in Step 7? Do they suppress `token.delta` under HASH_ONLY disposition (per buildguide table P13-6)?
- **Boundary discipline:** Each adapter's streaming code stays in its own file? No shared streaming utility that creates cross-adapter coupling unless intentional?
- **Test coverage:** Is each adapter's streaming path tested independently? Is the HASH_ONLY suppression tested (negative-path: HASH_ONLY → no token.delta emitted)?

Write 3-5 bullets.

- [ ] **Step 3: Surface to Adam**

Present review bullets. Wait for `approve` / `request changes` / `skip`.

- [ ] **Step 4: Execute Adam's decision**

If approve: `gh pr merge 19 --squash --delete-branch`
If request changes: `gh pr comment 19 --body "<changes>"`; mark deferred.
If skip: mark skipped.

- [ ] **Step 5: Post-merge sanity check**

Run: `git fetch origin && git log origin/main -1 --oneline`
Then: `gh run list --branch main --limit 1` and note the run ID.

- [ ] **Step 6: Rebase check**

Run: `for pr in 20 21; do echo -n "PR #$pr mergeable: "; gh pr view $pr --json mergeable --jq '.mergeable'; done`
Expected: all `MERGEABLE`.

- [ ] **Step 7: No local commit (merge is the commit)**

---

## Task 5: Merge PR #20 (.x.3 replay-cognition)

**Files:**
- PR diff scope: cognition replay engine (binary comparator + classifier hook). Likely touches `phoenix/audit/replay.py` or a new `phoenix/cognition/replay.py`, plus tests. Medium-risk because it's a new engine, not a refinement.

- [ ] **Step 1: Fetch diff**

Run: `gh pr diff 20`
Expected: new file(s) for cognition replay; modifications to existing replay engine ONLY where the binary comparator hook is wired in. No changes to verification-gate or router internals.

- [ ] **Step 2: Three-axis review**

- **Correctness:** The Phase 7 replay engine is bit-exact for physics solves. Cognition is not bit-exact (LLM responses vary across runs). What's the binary comparator's semantics — is it a hash comparison on a normalized canonical form, or a tolerance-based equality? Is the classifier hook called when the comparator fails (i.e., the disagreement-classifier reuses the cognition_wobble classifier from Step 5)?
- **Boundary discipline:** Does the replay code live alongside Phase 7 replay or in a new cognition-replay module? Either is defensible; check that the choice doesn't create circular imports.
- **Test coverage:** Is there at least one test that exercises a known-disagreement case (replay produces different output than original, classifier categorizes it)? Is there at least one test that exercises a known-agreement case (replay matches, classifier never fires)?

Write 3-5 bullets.

- [ ] **Step 3: Surface to Adam**

Present review bullets. Wait for `approve` / `request changes` / `skip`.

- [ ] **Step 4: Execute Adam's decision**

If approve: `gh pr merge 20 --squash --delete-branch`
If request changes: `gh pr comment 20 --body "<changes>"`; mark deferred.
If skip: mark skipped.

- [ ] **Step 5: Post-merge sanity check**

Run: `git fetch origin && git log origin/main -1 --oneline`
Then: `gh run list --branch main --limit 1` and note the run ID.

- [ ] **Step 6: Rebase check**

Run: `gh pr view 21 --json mergeable --jq '"PR #21 mergeable: " + .mergeable'`
Expected: `MERGEABLE`.

- [ ] **Step 7: No local commit (merge is the commit)**

---

## Task 6: Merge PR #21 (.x.6 encryption-age) — Most Novel Surface

**Files:**
- PR diff scope: encryption ceremony using `age` (Adam's standing crypto choice for reference impl). Touches the Omega Ledger `prompt_encrypted` column + new key-handling code. Highest risk in this sweep because it's a security ceremony.

- [ ] **Step 1: Fetch diff**

Run: `gh pr diff 21`
Expected: new encryption module (likely `phoenix/ledger/encryption.py` or `phoenix/security/age_encryption.py`), wiring into the Omega Ledger writer when `prompt_disposition=ENCRYPTED_OPT_IN`, plus tests. Does NOT touch the production key-management ceremony (which 13-D2 explicitly deferred to first commercial customer).

- [ ] **Step 2: Three-axis review (deeper — security surface)**

- **Correctness:**
  - Does the encryption *only* fire when `prompt_disposition == ENCRYPTED_OPT_IN`? (HASH_ONLY and VERBATIM paths must not call into the encryption module.)
  - Is the `age` recipient key sourced from a documented config location, not hard-coded?
  - Are encrypted blobs round-trippable in tests (encrypt → decrypt → original)? Is the decrypt-side present, even if only in tests, so we can prove the encrypted blob is recoverable?
  - Is key absence a hard error (refuse-to-encrypt) or silent skip (defaults to HASH_ONLY)? Buildguide 13-D2 says ENCRYPTED_OPT_IN is opt-in — a silent fallback would be a privacy regression.
- **Boundary discipline:**
  - Does the encryption module stay inside its file, or does it leak `age` library imports into the ledger writer?
  - Does the change touch the Cloud-seams registry (Phase 10) or stay local-only? Reference impl should stay local-only per 13-D2.
- **Test coverage:**
  - Round-trip test (encrypt → decrypt → original).
  - HASH_ONLY/VERBATIM no-encryption path tested.
  - ENCRYPTED_OPT_IN happy-path tested.
  - Key-absence failure mode tested (and the failure mode is the *expected* one per the correctness review above).
- **Defense-in-depth check:** Does the column-vs-key separation hold — the `prompt_encrypted` column is populated *only* when both `disposition=ENCRYPTED_OPT_IN` AND a valid `age` key is configured?

Write 5-8 bullets for this PR (more than the standard 3-5 because of the security surface).

- [ ] **Step 3: Surface to Adam**

Present review bullets. If any concern is raised in §2, recommend deferring to a dedicated review session (per spec §7 risk mitigation) — DO NOT merge a security PR under time pressure. Wait for `approve` / `request changes` / `skip` / `defer-to-dedicated-review`.

- [ ] **Step 4: Execute Adam's decision**

If approve: `gh pr merge 21 --squash --delete-branch`
If request changes: `gh pr comment 21 --body "<changes>"`; mark deferred.
If skip OR defer-to-dedicated-review: mark explicitly in execution log with the reason.

- [ ] **Step 5: Post-merge sanity check**

Run: `git fetch origin && git log origin/main -1 --oneline`
Then: `gh run list --branch main --limit 1` and note the run ID. For the security PR, also confirm the encryption tests appeared in the CI run output:
Run: `gh run view <run-id> --log | grep -E "(encrypt|age)" | head -10`
(Skip if run hasn't finished.)

- [ ] **Step 6: Rebase check**

No remaining PRs in this sweep, so this step is N/A. Confirm with: `gh pr list --state open --label phase-13 2>&1 | head` (or just `gh pr list --state open` if no label).

- [ ] **Step 7: No local commit (merge is the commit)**

---

## Task 7: Post-sweep — Execution log + version-bump decision + final CI check

**Files:**
- Modify: `docs/superpowers/specs/2026-05-27-phase-13-pr-sweep-design.md` (append §8 execution log).

- [ ] **Step 1: Confirm `main` is green after all merges**

Run: `gh run list --branch main --limit 3 --json status,conclusion,name,databaseId --jq '.[] | "\(.databaseId) \(.name): status=\(.status) conclusion=\(.conclusion)"'`
Expected: most recent runs show `status=completed conclusion=success`. If any are still in progress, note the run IDs and tell Adam they'll continue in background.

- [ ] **Step 2: Surface the `[OPEN: version-bump-policy]` decision**

Present to Adam: "All requested PRs merged. Current version is still `1.1.0.dev0`. The spec's default if you don't raise it is 'stay at dev0.' Would you like to bump to `1.1.0.dev1` to pin the post-sweep state, or stay at dev0?"

Wait for decision. If bump requested, create a follow-up tiny commit:

```bash
# Only if Adam chose to bump:
git checkout main
# Edit pyproject.toml version 1.1.0.dev0 -> 1.1.0.dev1
git add pyproject.toml CHANGELOG.md
git commit -m "chore: bump 1.1.0.dev0 -> 1.1.0.dev1 (post-sweep state pin)

Captures the cumulative state of phase-13 sub-improvements
.x.1 through .x.6.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

If skip, proceed to Step 3.

- [ ] **Step 3: Append the execution log to the spec**

Modify `docs/superpowers/specs/2026-05-27-phase-13-pr-sweep-design.md` §8: replace the placeholder block with actual outcomes.

Use Edit tool to replace the `[outcome]` placeholders with one of: `MERGED <sha>`, `DEFERRED (reason)`, `SKIPPED (reason)`, `CHANGES REQUESTED (link to comment)`.

Example post-fill block:

```
PR #17: MERGED abc1234
PR #16: MERGED def5678
PR #18: MERGED ghi9012
PR #19: MERGED jkl3456
PR #20: MERGED mno7890
PR #21: DEFERRED (security concern: key-absence fallback semantics — needs dedicated session)
main green after sweep: Y (CI run https://github.com/nah414/Phoenix/actions/runs/<id>)
Notable findings: <bullets if any>
Version bump decision: stayed at 1.1.0.dev0 / bumped to 1.1.0.dev1
```

- [ ] **Step 4: Commit the execution log**

```bash
git add docs/superpowers/specs/2026-05-27-phase-13-pr-sweep-design.md
git commit -m "docs: Phase 13 PR sweep execution log (2026-05-27)

Records terminal outcome for each of the six follow-up PRs
driven through review in the sweep. Closes the planning loop
opened by cc15d80.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git log -1 --oneline
```

- [ ] **Step 5: Summarize for Adam**

Present a final 5-line summary:
1. Sweep outcome counts (X merged / Y deferred / Z skipped).
2. `main` CI state.
3. Version bump decision.
4. 13-D1 LICENSE status (still pending Adam, if unchanged).
5. Suggested next session focus (top 1-2 candidates: drive a deferred PR to closure, resolve 13-D1, cut `1.0.0` final, refresh memory).

---

## Self-review

**Spec coverage check:**
- Spec §1 context — covered by plan header.
- Spec §2 goal — covered by plan header `Goal`.
- Spec §3 out-of-scope — Task 7 Step 5 surfaces 13-D1 as a next-session candidate, not as in-scope work. ✓
- Spec §4 sequence — Tasks 1-6 in exactly the spec's order (17, 16, 18, 19, 20, 21). ✓
- Spec §4 per-PR review pattern — Tasks 1-6 each have the 7-step loop (fetch → review → surface → decide → execute → post-check → rebase). ✓
- Spec §5 acceptance — Task 7 Step 4 commits the execution log, satisfying "short execution log committed." Task 7 Step 1 satisfies "main is green in CI after the last merge." ✓
- Spec §6 [OPEN: version-bump-policy] — Task 7 Step 2 surfaces this explicitly. ✓
- Spec §6 [OPEN: rebase-strategy] — Each task's Step 6 checks `mergeable` status; spec default is rebase-on-merge which `gh pr merge --squash` implements. ✓
- Spec §7 risk: encryption-age needs deeper review — Task 6 Step 2 has 5-8 bullets instead of 3-5, and Step 3 explicitly recommends `defer-to-dedicated-review` if any concern surfaces. ✓
- Spec §7 risk: token-exhaustion mid-sweep — covered implicitly (each task is self-contained; merged PRs stay merged).

**Placeholder scan:** No TBDs, TODOs, or "implement later." The `<sha>` and `<id>` placeholders in Task 7 are template fields filled in at runtime — acceptable per skill since they describe the shape of the log entry, not work to defer.

**Type/command consistency:**
- `gh pr merge` uses `--squash --delete-branch` consistently across all tasks. ✓
- Rebase-check command uses the same `gh pr view <N> --json mergeable --jq '.mergeable'` shape consistently. ✓
- Post-merge sanity uses the same `git fetch origin && git log origin/main -1 --oneline` consistently. ✓
- Task 1 Step 6 had a "Hmm correction" note — that's a draft-thinking artifact I should clean up.

Cleaning up the Task 1 Step 6 wording now.
