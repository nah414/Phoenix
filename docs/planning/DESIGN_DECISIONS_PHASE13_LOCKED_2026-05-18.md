# Phase 13 Design Decisions Locked 2026-05-18

**Status:** CONDITIONALLY LOCKED — 13-D1 (license) pending frank-data root LICENSE declaration. All other decisions LOCKED per the original triggers.
**Source:** Adam ↔ Claude (Opus 4.7, chat) design session 2026-05-18.
**Authoritative location:** `C:\Phoenix\DESIGN_DECISIONS_PHASE13_LOCKED_2026-05-18.md`
**Companion docs:**
- `PHOENIX_ARCHITECTURE_v1.md` (v1.1 spec, locked 2026-05-07)
- `BUILDGUIDE_phoenix_v1_phase13_cognition_mcp_client.md` (Phase 13 build guide; Section 0 imports this doc verbatim)

---

## What Phase 13 is

Phase 13 extends Phoenix v1.1 with **two substrates that close the
"universal model integration" gap** identified during the 2026-05-18
design review: a **cognition substrate** (native adapters for cloud
LLM providers) and an **MCP-client mode** (Phoenix can call any
MCP server, covering local LLMs, LiteLLM pass-through, customer-
sandboxed models, and the rest of the MCP ecosystem). Phase 13 also
adds three new `WobbleAxis` implementations for cross-model
verification of cognition outputs, an independent disagreement
classifier for those axes, streaming-token support on the WebSocket
event surface, and the privacy controls commercial deployments
require.

Phase 13 lands as v1.1 work. It begins after `1.0.0` final release.

## Locked decisions

### Decision 13-D1 — License: stay Apache 2.0 for now

Phoenix continues under Apache License, Version 2.0 (Decision 34 in
`PHOENIX_ARCHITECTURE_v1.md`, carried forward unchanged).

**Why this is the right license for the universal-model-integration
goal.** Apache 2.0 already grants *any* user — agent, integrator,
commercial customer, sandboxed deployment — a royalty-free,
perpetual, irrevocable license to use, modify, and redistribute
Phoenix for any purpose. The patent grant clause additionally
protects commercial customers from patent-troll claims by the
licensor. Removing the license entirely would invert this: copyright
defaults to *all rights reserved*, and commercial customers' legal
review would block adoption.

**Revisit trigger.** Adam may file a patent on Phoenix methodology
(three-axis wobble verification, hashchained Omega-Ledger
provenance, cognition disagreement classifier, or related) before
v1.0 launches. If a patent is filed, the license decision MUST be
re-opened before publishing the patent — because Apache 2.0's patent
grant clause would make the patent royalty-free to all Phoenix
users, which may or may not be Adam's commercial intent. Options at
that point:

1. Keep Apache 2.0 (patent stays royalty-free to Phoenix users).
2. Move to a dual-license model (Apache 2.0 for non-commercial /
   research; commercial license for patent-using deployments).
3. Move to source-available license (BSL, SSPL) with patent
   retention.

Decision deferred until and unless the trigger fires.

**Dependency check (resolved 2026-05-18, partial):**

- **SynQc TDS Core:** MIT (Adam Lisowski 2025), verified at
  `Downloads\synqc-temporal-dynamics-series-hybrid-controller-main\LICENSE`.
  ✅ Apache-2.0 compatible — MIT can be vendored under Apache 2.0 with the
  MIT copyright notice preserved in Phoenix's `NOTICE.md`.
- **frank-data:** No root LICENSE at `C:\frank-data\`. Adam-authored;
  defaults to all-rights-reserved without an explicit grant. **Blocker for
  Phase 13 implementation start.** Resolution: Adam declares Apache 2.0 (or
  compatible) by adding `C:\frank-data\LICENSE`. Third-party models inside
  frank-data (Qwen 3 4B NPU, Phi-4 mini) retain their original licenses and
  are separately verified when each is vendored into Phoenix proper.

### Decision 13-D2 — Privacy posture: hash-only default + opt-in verbatim

The Omega Ledger's per-solve entry schema gains a
`prompt_disposition` field with three values:

- `HASH_ONLY` (default) — SHA-256 of the canonicalized prompt is
  stored; the prompt text itself is not. Sufficient for bit-exact
  replay verification (the hash anchors the ledger entry) but
  protects sensitive content from exposure via the ledger.
- `VERBATIM` (explicit opt-in) — full prompt text stored. Required
  for the long-window bit-exact replay battery to round-trip
  through actual re-execution; otherwise replay can only verify
  hash equality, not regenerate.
- `ENCRYPTED_OPT_IN` (explicit opt-in, future-extensible) — prompt
  encrypted at rest with a per-org key. Decryption requires the org
  key plus Actor permission; admins without the key cannot read
  prompts. Phase 13 ships the column + storage path; the key-
  management ceremony (Section 7.6-style enrollment) lands when
  the first commercial customer requires it.

Per-actor and per-org defaults are configured via the
`ActorPermissions` registry, with a new capability
`can_store_prompt_verbatim` (default: false) gating any opt-in.

**Why default to hash-only.** Two reasons. First, commercial
customers' compliance teams reject systems that log prompts
verbatim by default — especially in regulated industries (finance,
healthcare, legal). Second, prompts are fundamentally different in
privacy character from physics tasks: a physics task's Hamiltonian
parameters are not personally identifying; a prompt may contain
names, financial details, medical history, or trade secrets.

**Replay implications.** Strict-mode replay against a `HASH_ONLY`
entry verifies the prompt hash matches but cannot regenerate the
output (the model can't be re-invoked without the prompt text).
This is documented as expected behavior; consumers who require
re-executable replay must opt in to `VERBATIM` for those solves.

### Decision 13-D3 — Cognition disagreement classifier: independent from physics

The cognition wobble axes (cross-model agreement, self-consistency,
prompt-perturbation) get their own disagreement classifier, distinct
from the vendored `wobble/disagreement_types.py` classifier trained
on physics-disagreement patterns.

**Why independence is required.** Physics disagreements are
predominantly numerical: cross-precision wobble means "the two
solvers got different floating-point answers and the question is
how different." Cognition disagreements are predominantly semantic
and pragmatic: cross-model wobble means "Claude said X, GPT said
Y, and the question is whether X and Y are *meaningfully*
different — same fact stated differently, factual disagreement,
interpretive divergence, refusal-vs-answer, tool-choice divergence,
or stylistic-only difference." A classifier trained on one axis
performs poorly on the other.

**Scope of the classifier work.** A new
`vendor/cognition_wobble/disagreement_types.py` module ships a
disagreement taxonomy distinct from physics. Initial taxonomy
(open for refinement during build):

- `FACTUAL_AGREEMENT` — models agree on the load-bearing claims.
- `STYLISTIC_DIVERGENCE` — models agree on facts, differ in
  presentation or detail level.
- `FACTUAL_DISAGREEMENT` — models disagree on a verifiable claim.
- `INTERPRETIVE_DIVERGENCE` — models read the prompt differently;
  one answers question A, another answers question B.
- `REFUSAL_DIVERGENCE` — one model answered, another refused on
  safety / policy / capability grounds.
- `TOOL_CHOICE_DIVERGENCE` — models chose different tools (or one
  chose to call no tool when another did).
- `UNCLASSIFIED` — classifier confidence below threshold; raw
  distance score surfaced, no class assigned.

**Where the care lives.** Step 5 of the Phase 13 build guide is
the classifier work. It must include: (a) a calibration eval set
of at least 200 paired examples spanning all taxonomy classes;
(b) per-class precision/recall reporting at acceptance time; (c)
the `UNCLASSIFIED` escape hatch so the classifier never
hallucinates a class it isn't confident about; (d) `[OPEN: ...]`
markers for any decision the build-time data doesn't resolve.

This is significant work. Step 5 may need to split into 5a + 5b if
the eval-set construction itself becomes a phase-sized task.
Surface to Adam if so.

### Decision 13-D4 — MCP-client allowlist: per-server explicit registration

Phoenix-as-MCP-client may **only** dispatch to MCP servers that
have been explicitly registered by an admin Actor. There is **no**
trust-on-first-use, **no** empty-default-allows-all, **no**
discovery-based auto-add.

**The admin surface:**

```
POST /v1/admin/mcp-servers/{name}
Authorization: Phoenix-Actor <admin-signed>
Body: {
  "transport": "stdio" | "http+sse",
  "endpoint": "<command or URL>",
  "auth": { ... },                       // provider-specific
  "allowed_tools": ["tool1", "tool2"],   // explicit allowlist; "*" forbidden
  "max_budget_usd_per_day": 50.0,        // hard ceiling, defense-in-depth
  "audit_export_policy": "full" | "hashes_only",
  "prompt_disposition_override": null | "HASH_ONLY" | "VERBATIM"
}

GET /v1/admin/mcp-servers              // list registered servers
DELETE /v1/admin/mcp-servers/{name}    // de-register (revokes immediately)
```

**Dispatch behavior.** When a task references an MCP server name
that is not in the registry, the router raises
`MCPServerNotRegistered` (403) before any network call. When a
task references an allowed server but requests a tool not in
`allowed_tools`, the router raises `MCPToolNotAllowed` (403). Both
errors are audit-logged.

**Why per-server, not provider-class:** Different MCP servers
running the same model can have wildly different trust profiles
(a customer's own VPC-hosted Llama vs a random public MCP server
exposing the same model). Allowlisting at the server level matches
the actual trust surface.

### Decision 13-D5 — Sequencing: 1.0.0 first, Phase 13 parallel with perception Phase 12

The build order is:

1. **1.0.0 final release prep** (workstream alongside Phase 13
   build-guide drafting). Includes: code signing for distribution
   artifacts, NATS bundling in standalone binary, macOS standalone
   build, README status-table backfill, doc-debt cleanup. Closes
   the rc1 → 1.0.0 gap.
2. **Phase 13 build-guide drafting** (this document and the build
   guide it generates) begins immediately. The actual
   implementation work begins after 1.0.0 ships, in parallel with
   perception harness's Phase 12 build-guide drafting (different
   subsystems; no overlap).
3. **Phase 13 implementation + perception Phase 12 implementation**
   run as parallel tracks. Each has its own phase-gates and review
   cadence with Adam.

**Why this order.** 1.0.0 has the smallest, clearest scope and the
fewest open architectural questions. Phase 13 has the largest
substrate-extension surface and the most coupling to architectural
decisions (cognition disagreement classifier in particular). Doing
1.0.0 first ships the release-candidate to release, then frees
attention for the deeper Phase 13 work without conflicting with the
perception harness's parallel build.

## Re-lock triggers

| Trigger | Decision to revisit |
|---|---|
| Adam files a patent on Phoenix methodology | 13-D1 (license) |
| First commercial customer requires encrypted-at-rest prompts | 13-D2 (`ENCRYPTED_OPT_IN` storage path) |
| Classifier accuracy < 0.7 F1 on the calibration set | 13-D3 (may need to split 5a/5b) |
| Customer requires federated MCP discovery (e.g., via Anthropic registry) | 13-D4 (admin-approval-gated discovery as an additive path) |
| 1.0.0 release prep blocks on signing certificate procurement > 4 weeks | 13-D5 (Phase 13 build-guide drafting may proceed; implementation timing recomputed) |

## Companion notes

- `E:\CLAUDE_NOTES.md` — cross-session Claude notes; will be
  updated with a 2026-05-18 entry capturing today's design context
  for whichever Claude (Code or chat) picks up Phase 13
  implementation.
- The Phase 13 build guide's header carries a "NOTE FOR CLAUDE
  CODE" block per Adam's 2026-05-18 request; that block points at
  Section 0, which imports this document verbatim.
