# Phoenix

**An AI-agent-consumable middleware harness for accuracy, performance, and safety.**

Phoenix is downloadable software that AI agents and integrators call to get validated quantum computation, hardware-aware routing, and physics-grounded verification of results — with provenance, error bars, and honest reporting of uncertainty. It is not a SaaS, not an end-user app, not a chat interface, and does not host an LLM. It is a power tool that sits between a software stack (or an agent framework) and the underlying compute (local accelerators, cloud quantum providers), making any system that integrates with it more accurate and more honest about its uncertainty.

Phoenix is in pre-release development. The architecture is locked at v1; build guides direct implementation phase by phase.

## Documents

- **[`PHOENIX_ARCHITECTURE_v1.md`](PHOENIX_ARCHITECTURE_v1.md)** — locked v1 architecture spec, revised to v1.1 on 2026-05-07. Trinity Core (Solver and Control vendored from dr-frank-and-eddy; Orchestrate built greenfield in Phoenix per the 2026-05-06 SynQc-greenfield revision), the seven wrapping layers (front door, task grammar, router, verification gate, safety gate, dev-ops backdoor, plus separate-codebase reference admin client), mandatory three-axis wobble verification, hashchained Omega Ledger provenance, end-to-end cost-ceiling enforcement, the Phoenix Cloud commercial path, and 16 catalogued open design tensions (14 from v1.0 + 2 unresolved from v1.1's perception extension; 11.14.7 resolved 2026-05-08 by the locked `LatencyTier` enum).
- **[`BUILDGUIDE_phoenix_v1_phase0_skeleton.md`](BUILDGUIDE_phoenix_v1_phase0_skeleton.md)** — Phase 0 build guide. The absolute-minimum repository skeleton: pinned dependencies via `uv`, `phoenix/` package layout (one directory per architecture Section), `vendor/` skeleton, launcher chain, per-section READMEs, an `evals/` scaffold for audit/debug correctness, smoke + integration tests, and an empty FastAPI daemon on port 8003.

**Phase 0 + Phase 1 + Phase 2 + Phase 3 shipped 2026-05-06 / 2026-05-08** (commits `f3b39b1`, `b86ee94`, Phase 2 chain, Phase 3 chain; package version `1.0.0.dev3`). Subsequent phase build guides land as their phases ship: Phase 4 (Router), Phase 5 (verification gate), Phase 6 (safety gate + identity + state + queue), Phase 7 (audit + ledger + drift), Phase 8 (admin), Phase 9 (adapters + MCP + CLI), Phase 10 (OTel + cloud seams + standalone binary), Phase 11 (release).

### Future extension planning (not part of locked v1)

- **[`PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md`](PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md)** — **LOCKED v1 (2026-05-07).** Extension plan for a future Phoenix v1.x perception-harness extension (autonomous-system middleware for adverse-weather AV perception, industrial autonomy, defense). All 21 design decisions recorded; plan positions perception as Phase 12 onwards (after v1 release at Phase 11), reusing 70-80% of v1's substrate. Phase 12 build guide drafts when v1 reaches its Phase 5 verification-gate milestone, so v1 implementation attention is not diluted. The locked v1.0 architecture's load-bearing structure and existing build-guide pipeline (Phases 0-11) remain unchanged; v1.1 architecture revision (also 2026-05-07) is documentation-only — Section 11.14 added with 7 new tensions, Section 10.8 v1.1 acceptance criteria extended.

  *Historical record:* [`PHOENIX_PERCEPTION_HARNESS_PLAN_v0.md`](PHOENIX_PERCEPTION_HARNESS_PLAN_v0.md) — original v0 with 21 open questions, superseded by v1 above.

## Status

| Component | State |
|---|---|
| Architecture v1 | **Locked 2026-05-06; revised to v1.1 on 2026-05-07** (documentation-only revision: Section 11.14 + 10.8 perception extension) |
| Phase 0 (skeleton) | **Shipped 2026-05-06** (commit `f3b39b1`) |
| Phase 1 (vendor sync) | **Shipped 2026-05-06** (commit `b86ee94`, package version `1.0.0.dev1`) |
| Phase 2 (Solver wiring through Trinity Core pipeline) | **Shipped 2026-05-08** (package version `1.0.0.dev2`) -- `WobbleAxis` Protocol + `CrossPrecisionAxis` (Axis 1), engine adapter, Solver-only `solve()` orchestrator, `POST /v1/tasks`. |
| Phase 3 (Control + Orchestrate wiring through pipeline) | **Shipped 2026-05-08** (package version `1.0.0.dev3`) -- typed `KPIBundle`, `ControlProvenance`/`OrchestrateProvenance`, vendored `DPDScheduler` adapter, `CrossControlAxis` (Axis 2, trace-distance metric), 6 of 7 Orchestrate modules (engine + bundle_builder + provider_client + result_extractor + drift_feedback + kpi_bundle), `LocalClassicalSimulator` adapter, `RoutingDecision`/`ProviderSelection` typed dataclasses, three-layer `solve()` with default `R3_TWO_AXES` depth, full `Result` envelope on `POST /v1/tasks`. 50 tests passing. |
| Trinity Core (physics heart) | All three subsystems wired end-to-end (Phase 3); cross-provider verification (Axis 3) deferred to Phase 5 alongside the rung table; cloud quantum providers deferred to Phase 4 |
| Front door (REST + WebSocket + CLI + MCP) | `/v1/health` (Phase 0) + `POST /v1/tasks` returning full `Result` envelope (Phase 3) live; full surface pending Phase 5+ |
| Reference admin client | Deferred to v1.1 — separate repo |
| Perception harness extension | **LOCKED v1 plan 2026-05-07** — Phase 12 build guide drafts after v1 reaches Phase 5 milestone |

See [`PHOENIX_ARCHITECTURE_v1.md`](PHOENIX_ARCHITECTURE_v1.md) Sections 10.7 and 10.8 for the v1 and v1.1 acceptance criteria.

## License

Apache License, Version 2.0. See [`LICENSE`](LICENSE).

The Apache 2.0 license is the explicit choice for Phoenix per architecture Decision 34 — open source, ecosystem-compatible, and crucially includes a patent grant as belt-and-suspenders against future patent claims on calibration methodology.

## Author

**Adam** ([@nah414](https://github.com/nah414)) — solo researcher and builder. dr-frank-and-eddy is Adam's lab bench where physics evolves; Phoenix is the production middleware that vendors a frozen v6.6 snapshot of that work plus SynQc TDS Core into a single Trinity Core substrate.

Phoenix is built with [Claude](https://claude.com/claude-code) as design partner.
