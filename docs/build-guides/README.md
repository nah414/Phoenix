# Phoenix v1 build guides

This directory holds the per-phase build guides that were used to execute the
Phoenix v1 / v1.1 build against the locked architecture spec
([`PHOENIX_ARCHITECTURE_v1.md`](../../PHOENIX_ARCHITECTURE_v1.md)).

These are **internal development records** — each guide directs the build of one
phase (prerequisites, file-by-file targets, acceptance checks, and a dated
changelog). They are not user-facing documentation; for installing and running
Phoenix, see [`../distribution/`](../distribution/README.md).

## Phases

| Phase | Guide | Scope |
|-------|-------|-------|
| 0  | [skeleton](BUILDGUIDE_phoenix_v1_phase0_skeleton.md) | Minimum repository skeleton (deps, package layout, launcher chain, test/eval scaffolding) |
| 1  | [vendor-sync](BUILDGUIDE_phoenix_v1_phase1_vendor_sync.md) | Vendor sync + Tier-1 battery |
| 2  | [solver](BUILDGUIDE_phoenix_v1_phase2_solver.md) | Solver wiring through the Trinity Core pipeline |
| 6b | [infrastructure](BUILDGUIDE_phoenix_v1_phase6b_infrastructure.md) | Infrastructure layer (state backend, NATS, drift detector, drift WS) |
| 7  | [audit-ledger](BUILDGUIDE_phoenix_v1_phase7_audit_ledger.md) | Audit log + Omega Ledger + drift→router feedback |
| 8  | [admin-devops](BUILDGUIDE_phoenix_v1_phase8_admin_devops.md) | Admin dev-ops backdoor |
| 9  | [adapters-mcp-cli](BUILDGUIDE_phoenix_v1_phase9_adapters_mcp_cli.md) | LoRA adapters + CLI + MCP |
| 10 | [cost-ceiling-cloud-seams](BUILDGUIDE_phoenix_v1_phase10_cost_ceiling_cloud_seams.md) | Cost-ceiling enforcement + Phoenix Cloud abstraction seams |
| 11 | [acceptance-composition](BUILDGUIDE_phoenix_v1_phase11_acceptance_composition.md) | Compositional acceptance tests + per-directory READMEs |
| 12 | [distribution](BUILDGUIDE_phoenix_v1_phase12_distribution.md) | Distribution + release artifacts |
| 13 | [cognition-mcp-client](BUILDGUIDE_phoenix_v1_phase13_cognition_mcp_client.md) | v1.1 cognition substrate + MCP-client mode |

> **Phase numbering note.** Phases 3, 4, 5, and 6a have no standalone build
> guide in this directory — that work was sequenced as "steps" rather than
> phase guides (see `../planning/ROADMAP_post_step5b_2026-05-20.md`). The
> numbering above is preserved verbatim from the original guides; the gap is
> expected, not a missing file.
