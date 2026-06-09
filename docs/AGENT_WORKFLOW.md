# Agent workflow — detailed operating rules

This is the long-form companion to [`../CLAUDE.md`](../CLAUDE.md). `CLAUDE.md`
is the short version that auto-loads every session; this file holds the full
rationale, the exact halt list, branch/commit conventions, and the reporting
format.

**Audience:** any AI agent (Claude Code or otherwise) working in this repo.
**Context:** the maintainer (Adam, [@nah414](https://github.com/nah414)) often
drives sessions remotely from a phone via Dispatch. Every rule below is shaped
by that: minimize round-trips, never block on something that can't be answered
with a thumb, and make autonomous progress safe by keeping it reversible.

**Precedence:** these rules override default agent behavior. A direct
instruction from the maintainer in a live session overrides this file.

---

## 1. No interactive pickers — plain text only

**Never** use the interactive multiple-choice / option-selection question tool
(in Claude Code this is the `AskUserQuestion` picker). It renders poorly and is
hard to answer from a phone over Dispatch.

If you genuinely need input:

- Ask in **plain prose** in your normal reply.
- Make the question answerable with a short text response — ideally a single
  word or line.
- Provide your **recommended default** in the same message and say you'll
  proceed with it if there's no objection, when the choice is reversible.

But first ask whether you need to ask at all — see the next rule. Most
questions are really reversible decisions you can just make.

## 2. Default to autonomous execution

At any decision point or fork:

1. Choose the **conservative, reversible** option — the one that's easiest to
   undo, has the smallest blast radius, and doesn't foreclose other choices.
2. Record it as a **one-line note** (in your running narration and again in the
   final report), e.g. `Note: branched off main rather than the in-flight
   feature branch to keep this change isolated — reversible.`
3. **Keep going.** Do not stop and wait for trivial, reversible, or easily
   corrected choices.

Examples of "just decide and note it":

- Naming a branch, file, function, or test.
- Choosing a library already vendored/available vs. matching existing patterns
  (prefer matching the surrounding code).
- Structuring a refactor, picking a test layout, choosing log wording.
- Whether to write a helper vs. inline — pick the one that matches the codebase.

When you are genuinely uncertain and the choice is **hard to reverse**, see
rule 3 instead of guessing.

## 3. Halt only for destructive or irreversible actions

Stop and get **explicit approval** before any of these:

- **Pushing** to any remote (`git push`, including to a feature branch).
- **Opening, updating, or merging a pull request.**
- **Committing to `main`** (or any protected/shared branch).
- **Force-push** (`git push --force` / `--force-with-lease`).
- **History rewrite** (`git rebase`, `git reset --hard` on shared history,
  `git commit --amend` on already-pushed commits, `filter-branch`, etc.).
- **Deleting or overwriting data or files** you did not create in this session,
  or that contradict how they were described to you.
- **Changing access or permissions** — credentials, tokens, CI secrets,
  branch protection, file ACLs, GitHub settings, published artifacts.
- **Publishing to an external service** — PyPI, container registries, releases,
  anything that leaves the local machine and may be cached/indexed.

Everything else is fair game autonomously: creating branches, writing/editing
files, committing to a **feature branch**, running tests, running read-only or
local commands, scaffolding, refactoring.

When you must halt, state plainly: what you want to do, why, and the exact
command(s) you propose — so it can be approved with one short reply.

## 4. Branch and commit conventions

- **Never commit directly to `main`.** Always work on a feature branch.
- Branch naming: match what's already in the repo. Observed patterns include
  `phase-13.x.9-auto-capture-wiring` (phase work) and `docs/...` (docs). Use a
  short, descriptive, kebab-case name with a sensible prefix
  (`feat/`, `fix/`, `docs/`, `chore/`, or `phase-...` for build-guide phases).
- **Where to branch from:** prefer branching off `main` for work that is
  independent of any in-flight branch, so it can merge on its own. Only branch
  off another feature branch when your work genuinely depends on it. Note your
  choice.
- **Committing is allowed** on a feature branch without approval — it's
  reversible and keeps work tracked. Follow the repo's existing commit style:
  Conventional Commits scoped by subsystem, e.g.
  `feat(drift): wire auto-capture into DriftDetector.run_cycle`,
  `docs(plan): add Phase 13.x.9 ...`.
- **Pushing and PRs always require approval** (rule 3).
- Do not skip hooks (`--no-verify`) or bypass signing unless explicitly asked.

**PR cadence — consolidate, don't PR every change.** Batch related work onto a
branch and open at most **one PR per work-session/batch**, not one per task or
per individual change. It is expected and fine for a branch to accumulate many
commits and to sit for days before any PR is opened — that is the normal state,
not a backlog to clear. **Never open a PR proactively**; open one only when the
maintainer explicitly asks. The default posture between tasks is *keep the work
on the branch*, not *ship it*. (Opening, updating, or merging a PR is a
halt-list action regardless — see §3 — so this rule governs cadence even once
approval is on the table: prefer one consolidated PR over several small ones.)

## 5. Plan substantial work — but don't block on it

For anything beyond a small, obvious change:

1. Produce a **brief plan** before diving in (the steps, the files you expect to
   touch, the risks). Keep it proportionate — a few lines, not a document.
2. **Execute it autonomously.** Work through the decisions inside the plan using
   rule 2; do not pause mid-flight for sign-off on reversible choices.
3. Surface the **design choices and trade-offs you made** in your final report,
   so they can be reviewed asynchronously after the fact.

The goal: the maintainer reviews a coherent finished unit of work plus your
notes, rather than being interrupted for each fork along the way.

## 6. Test before handing back

- Run the relevant tests (`pytest`, the phase's acceptance battery, or whatever
  the touched subsystem uses) before returning control.
- Report results honestly: if tests fail, say so and include the output; if you
  skipped a step, say that; if something is done and verified, say so plainly
  without hedging.
- Prefer the repo's existing test/verification entry points over ad-hoc
  commands. On Windows/PowerShell, mind the shell differences (`$null`,
  `$env:VAR`, no `&&` chaining in Windows PowerShell — use `;` or `if ($?)`).

## 7. Final report format

When you hand back, include:

- **What changed** — files touched, one line each.
- **Decisions & trade-offs** — the one-line notes from rule 2, plus any design
  choices from rule 5, gathered for async review.
- **Test results** — what you ran and the outcome.
- **What needs approval (if anything)** — the halt-list actions you stopped
  short of (push / PR / etc.), with the exact commands proposed.
- **Open questions (if any)** — in plain text, with your recommended default.

---

## Platform notes (Windows / PowerShell)

- Paths are `C:\...`; use absolute paths.
- PowerShell, not bash: `$null` not `/dev/null`, `$env:VAR` not `$VAR`,
  backtick for line-continuation. Windows PowerShell 5.1 has **no** `&&`/`||`
  chaining — use `;` or `if ($?) { ... }`.
- Default file encoding for `Out-File`/`Set-Content` is UTF-16; pass
  `-Encoding utf8` when other tools will read the file.

## Why these rules exist (rationale)

The maintainer frequently supervises from a phone, where reading long output
and tapping through option pickers is painful and dictating is the easy path.
So: text-only questions, autonomous reversible progress, and hard stops only at
the few actions that can actually cause harm or can't be undone. The result is
that a remote session can run a substantial task end-to-end and come back with
one reviewable report, while anything that touches the outside world or shared
history still waits for an explicit "go."
