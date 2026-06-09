# CLAUDE.md — Phoenix agent operating rules

This file is auto-loaded into every Claude Code session for this repo. It
encodes how the maintainer (Adam, [@nah414](https://github.com/nah414)) wants
agents to behave here. The full rationale and detail live in
[`docs/AGENT_WORKFLOW.md`](docs/AGENT_WORKFLOW.md) — read it for anything not
covered below.

These rules are optimized for the maintainer driving remotely from a phone
via Dispatch. **They override default agent behavior; user instructions in a
given session still take precedence over this file.**

## The rules (short form)

1. **No interactive pickers, ever.** Never use the multiple-choice / option
   question tool. If you must ask something, ask in **plain text** in your
   reply so it can be answered remotely on a phone.

2. **Default to autonomous execution.** At a decision or fork, choose the
   **conservative, reversible** option, record it as a one-line note, and keep
   going. Do not stop and wait for trivial or reversible choices.

3. **Halt only for destructive or irreversible actions.** Explicit approval is
   required before: pushing, opening or merging a PR, committing to `main`,
   force-push, history rewrite, deleting data/files, or anything that changes
   access or permissions. Everything else: proceed.

4. **Always work on a feature branch; never commit to `main`.** Never push or
   open a PR without explicit approval.

5. **Consolidate work; don't PR every change.** Batch related work onto the
   branch and open at most **one PR per work-session/batch** — not one per task
   or per change. A branch is expected to accumulate many commits and may sit
   for days before any PR. **Never open a PR proactively**; only when the
   maintainer explicitly asks. Default posture between tasks is *keep the work
   on the branch*, not *ship it*.

6. **Never skip git hooks or bypass signing.** Do not use `--no-verify`, and
   never disable, skip, or work around pre-commit / commit-msg hooks or commit
   signing — unless the maintainer explicitly asks. If a hook fails, fix the
   underlying issue rather than bypassing it.

7. **Plan substantial work, but don't block on it.** For substantial changes,
   produce a brief plan and surface design choices in your **final report** for
   async review — proceed through them autonomously rather than blocking
   mid-flight.

8. **Test before handing back.** Prefer running the relevant tests and
   reporting results before you return control.

See [`docs/AGENT_WORKFLOW.md`](docs/AGENT_WORKFLOW.md) for the detailed
workflow, the full halt list, branch/commit conventions, and reporting format.

## Repo-specific context (read before working)

- Phoenix is **instrument-grade quantum-accuracy middleware**, not a generic
  agent harness. The canonical spec is
  [`PHOENIX_ARCHITECTURE_v1.md`](PHOENIX_ARCHITECTURE_v1.md); the docs hub is
  [`docs/`](docs/README.md).
- Development is **phase-gated**, not calendar-gated. Releases follow PEP 440;
  changes are recorded in [`CHANGELOG.md`](CHANGELOG.md) as phase landings.
- Per-phase build guides under
  [`docs/build-guides/`](docs/build-guides/README.md) are the execution
  discipline against the architecture spec. Honor them.
- Platform is **Windows / PowerShell**. Use PowerShell syntax (`$null`,
  `$env:VAR`, backtick line-continuation), and absolute `C:\` paths.
