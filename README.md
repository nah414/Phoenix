# Phoenix

**An AI-agent-consumable middleware harness for accuracy, performance, and safety.**

Phoenix is downloadable software that AI agents and integrators call to get validated quantum computation, hardware-aware routing, and physics-grounded verification of results — with provenance, error bars, and honest reporting of uncertainty. It is not a SaaS, not an end-user app, not a chat interface, and does not host an LLM. It is a power tool that sits between a software stack (or an agent framework) and the underlying compute (local accelerators, cloud quantum providers), making any system that integrates with it more accurate and more honest about its uncertainty.

Phoenix is in pre-release development. The architecture is locked at v1; build guides direct implementation phase by phase.

## Documents

- **[`PHOENIX_ARCHITECTURE_v1.md`](PHOENIX_ARCHITECTURE_v1.md)** — locked v1 architecture spec. Trinity Core (the three-engine physics heart vendored from dr-frank-and-eddy v6.6 + SynQc TDS), the seven wrapping layers (front door, task grammar, router, verification gate, safety gate, dev-ops backdoor, plus separate-codebase reference admin client), mandatory three-axis wobble verification, hashchained Omega Ledger provenance, end-to-end cost-ceiling enforcement, the Phoenix Cloud commercial path, and 14 catalogued open design tensions.
- **[`BUILDGUIDE_phoenix_v1_phase0_skeleton.md`](BUILDGUIDE_phoenix_v1_phase0_skeleton.md)** — Phase 0 build guide. The absolute-minimum repository skeleton: pinned dependencies via `uv`, `phoenix/` package layout (one directory per architecture Section), `vendor/` skeleton, launcher chain, per-section READMEs, an `evals/` scaffold for audit/debug correctness, smoke + integration tests, and an empty FastAPI daemon on port 8003.

Subsequent phase build guides land as their phases ship: Phase 1 (vendor sync from `C:\frank-data\` + Tier-1 calibration battery), Phase 2 (Solver), Phase 3 (Control + Orchestrate), Phase 4 (Router), Phase 5 (verification gate), Phase 6 (safety gate + identity + state + queue), Phase 7 (audit + ledger + drift), Phase 8 (admin), Phase 9 (adapters + MCP + CLI), Phase 10 (OTel + cloud seams + standalone binary), Phase 11 (release).

## Status

| Component | State |
|---|---|
| Architecture v1 | **Locked 2026-05-06** |
| Phase 0 (skeleton) | Build guide drafted; implementation pending |
| Phase 1 (vendor sync) | Pending Phase 0 acceptance |
| Trinity Core (physics heart) | Pending Phase 1 |
| Front door (REST + WebSocket + CLI + MCP) | Pending Phase 5+ |
| Reference admin client | Deferred to v1.1 — separate repo |

See [`PHOENIX_ARCHITECTURE_v1.md`](PHOENIX_ARCHITECTURE_v1.md) Sections 10.7 and 10.8 for the v1 and v1.1 acceptance criteria.

## License

Apache License, Version 2.0. See [`LICENSE`](LICENSE).

The Apache 2.0 license is the explicit choice for Phoenix per architecture Decision 34 — open source, ecosystem-compatible, and crucially includes a patent grant as belt-and-suspenders against future patent claims on calibration methodology.

## Author

**Adam** ([@nah414](https://github.com/nah414)) — solo researcher and builder. dr-frank-and-eddy is Adam's lab bench where physics evolves; Phoenix is the production middleware that vendors a frozen v6.6 snapshot of that work plus SynQc TDS Core into a single Trinity Core substrate.

Phoenix is built with [Claude](https://claude.com/claude-code) as design partner.
