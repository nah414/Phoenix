# Phoenix

**An AI-agent-consumable middleware harness for accuracy, performance, and safety.**

Phoenix is downloadable software that AI agents and integrators call to get validated quantum computation, hardware-aware routing, and physics-grounded verification of results — with provenance, error bars, and honest reporting of uncertainty. It is not a SaaS, not an end-user app, not a chat interface, and does not host an LLM. It is a power tool that sits between a software stack (or an agent framework) and the underlying compute (local accelerators, cloud quantum providers), making any system that integrates with it more accurate and more honest about its uncertainty.

Phoenix is in pre-release development. The architecture is locked at v1; build guides direct implementation phase by phase.

## Documents

- **[`PHOENIX_ARCHITECTURE_v1.md`](PHOENIX_ARCHITECTURE_v1.md)** — locked v1 architecture spec, revised to v1.1 on 2026-05-07. Trinity Core (Solver and Control vendored from dr-frank-and-eddy; Orchestrate built greenfield in Phoenix per the 2026-05-06 SynQc-greenfield revision), the seven wrapping layers (front door, task grammar, router, verification gate, safety gate, dev-ops backdoor, plus separate-codebase reference admin client), mandatory three-axis wobble verification, hashchained Omega Ledger provenance, end-to-end cost-ceiling enforcement, the Phoenix Cloud commercial path, and 16 catalogued open design tensions (14 from v1.0 + 2 unresolved from v1.1's perception extension; 11.14.7 resolved 2026-05-08 by the locked `LatencyTier` enum).
- **[`BUILDGUIDE_phoenix_v1_phase0_skeleton.md`](BUILDGUIDE_phoenix_v1_phase0_skeleton.md)** — Phase 0 build guide. The absolute-minimum repository skeleton: pinned dependencies via `uv`, `phoenix/` package layout (one directory per architecture Section), `vendor/` skeleton, launcher chain, per-section READMEs, an `evals/` scaffold for audit/debug correctness, smoke + integration tests, and an empty FastAPI daemon on port 8003.

**All v1 phases shipped 2026-05-06 → 2026-05-14** (Phase 0 → Phase 12; package version `1.0.0.dev12` → `1.0.0rc1` at Phase 12 acceptance). Phase 11 closed v1's logical surface with the Section 10.7 acceptance battery (four panic-mode isolation + composition tests plus a long-window bit-exact replay test, all marked `@pytest.mark.acceptance`). Phase 12 closes v1's release-artifact surface: three distribution artifacts ship — pip wheel (`phoenix-middleware`), Docker image (`ghcr.io/nah414/phoenix:1.0.0rc1`), and Nuitka standalone binary (Linux + Windows) — all CI-built via the GitHub Actions matrix per Section 1 Decision 30. See [`docs/distribution/`](docs/distribution/) for install + run guides.

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
| Phase 3 (Control + Orchestrate wiring through pipeline) | **Shipped 2026-05-08** (package version `1.0.0.dev3`) -- typed `KPIBundle`, `ControlProvenance`/`OrchestrateProvenance`, vendored `DPDScheduler` adapter, `CrossControlAxis` (Axis 2, trace-distance metric), 6 of 7 Orchestrate modules, `LocalClassicalSimulator` adapter, three-layer `solve()` with default `R3_TWO_AXES` depth, full `Result` envelope on `POST /v1/tasks`. |
| Phase 4 (Router subsystem + Provider Registry) | **Shipped 2026-05-08** (package version `1.0.0.dev4`) -- seven-stage `Router.decide` algorithm, `ProviderRegistry`, 3 quantum provider stubs, pricing v1, intelligence layer Source A, equivalence registry, failover protocol with exponential-backoff quarantine. |
| Phase 5 (Verification gate + rung table + Axis 3 + classifier) | **Shipped 2026-05-08** (package version `1.0.0.dev5`) -- `VerificationGate.verify(task)` with adaptive rung selection, reactive promotion, `CrossProviderAxis` (Axis 3), `PhoenixDisagreementType` extension, drift-state stub, QHO Tier-1 eigenstate + observable plumbing. |
| Phase 6a (Safety gate + identity + WebSocket events) | **Shipped 2026-05-08** (package version `1.0.0.dev6`) -- Ed25519 keystore at `~/.phoenix/runtime/master_key.bin`; bootstrap-actor mint for dev-mode UX; `Phoenix-Actor` header parsing; `ActorPermissions` registry (JSON-file backed; SQLite at Phase 6b); token-bucket rate limiter (default/elevated/admin tiers); kill switch with refuse-to-start posture; safety gate 9-stage pipeline (Sections 7.4 stages 0–6 functional; 7+8 placeholders); `POST /v1/identity/ws-token` (60s single-use bearer); WebSocket `/v1/ws/tasks/{task_id}/stream` streaming verification-gate events; `StateBackend` Protocol skeleton. 113 tests passing. |
| Phase 13 (Cognition substrate + MCP-client mode) | **Shipped 2026-05-20** (package version `1.1.0.dev0`) -- :class:`CognitionProvider` Protocol + Anthropic/OpenAI/Google/LiteLLM adapters; three cognition wobble axes (cross-model / self-consistency / prompt-perturbation); `vendor/cognition_wobble/` substrate with GBM + LLM-judge + hybrid classifiers; `phoenix.mcp` per-server registered MCP-client mode (13-D4: no TOFU, no `'*'`, no auto-add); WebSocket streaming `token.delta` (with `HASH_ONLY` suppression per 13-D2); ledger schema v4 (`prompt_disposition` columns); `PromptEncryptor` Protocol + null impl; 7 new permission flags; safety-gate stage 6b for cognition routing; three admin endpoints (grant-prompt-verbatim / cognition-budget-override / cognition-spend audit); three new `@pytest.mark.acceptance` tests. mypy --strict clean on 168 source files. |
| Trinity Core (physics heart) | All three subsystems wired end-to-end (Phase 3); cross-provider verification (Axis 3) shipped at Phase 5 |
| Provider routing | **Live (Phase 4)** -- routes any task through the Router with seven-stage filtering + ranking + failover; cloud SDK adapters (qiskit-ibm-runtime, amazon-braket-sdk, ionq) ship as stubs that raise on submit, real wiring lands in a focused later phase |
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
