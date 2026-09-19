# Phoenix

**An AI-agent-consumable middleware harness for accuracy, performance, and safety.**

Phoenix is downloadable software that AI agents and integrators call to get validated quantum computation, hardware-aware routing, and physics-grounded verification of results — with provenance, error bars, and honest reporting of uncertainty.

It is not a SaaS, not an end-user app, not a chat interface, and does not host an LLM. It is a power tool that sits between a software stack (or an agent framework) and the underlying compute (local accelerators, cloud quantum providers), making any system that integrates with it more accurate and more honest about its uncertainty.

Phoenix v1.0 is released ([GitHub Release](https://github.com/nah414/Phoenix/releases/tag/1.0.0), Apache 2.0). Active development continues on the v1.1 line.

## Documents

- **[`PHOENIX_ARCHITECTURE_v1.md`](PHOENIX_ARCHITECTURE_v1.md)** — the locked v1 architecture spec (revised to v1.1 on 2026-05-07). Covers the Trinity Core physics heart, the seven wrapping layers, mandatory three-axis wobble verification, hashchained Omega Ledger provenance, end-to-end cost-ceiling enforcement, and the Phoenix Cloud commercial path.
- **[`docs/`](docs/README.md)** — the documentation hub: distribution (install/run) and reproducibility guides, plus extension planning.

### Future extension planning (not part of locked v1)

- **[`PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md`](docs/planning/PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md)** — **LOCKED v1 (2026-05-07).** Extension plan for a future Phoenix v1.x perception-harness extension (autonomous-system middleware for adverse-weather AV perception, industrial autonomy, defense), reusing 70-80% of v1's substrate.

  *Historical record:* [`PHOENIX_PERCEPTION_HARNESS_PLAN_v0.md`](docs/planning/archive/PHOENIX_PERCEPTION_HARNESS_PLAN_v0.md) — the original v0, superseded by v1 above.

## Status

**Current release:** v1.0.0 (Apache 2.0), tagged 2026-05-28. Active development continues on the v1.1 line.

The full v1.0 surface is built and tested end-to-end. What's live:

- **Trinity Core** — the physics heart (Solver, Control, Orchestrate) wired end-to-end, with mandatory three-axis wobble verification (cross-precision, cross-control, cross-provider).
- **Provider routing** — a multi-stage router with provider registry, ranking, and failover. Cloud SDK adapters (IBM, AWS Braket, IonQ) ship as stubs that raise on submit; real wiring lands in a focused later phase.
- **Verification & safety gates** — adaptive verification with reactive promotion, plus identity (Ed25519), permissions, rate limiting, and a fail-closed kill switch.
- **Audit & provenance** — structured audit events, a hashchained Omega Ledger, and bit-exact replay that re-executes a recorded task and verifies its result hash.
- **Cost & cloud seams** — 24h cost-ceiling enforcement, plus Phoenix Cloud abstraction seams that let tenant-aware implementations drop in without changing Phoenix core.
- **Front door** — a full surface across REST, WebSocket, CLI, and an MCP server (`phoenix mcp serve`) for IDE clients, plus an admin dev-ops API and LoRA adapter subsystem.
- **Distribution** — three release artifacts: pip wheel (`phoenix-middleware`), Docker image (`ghcr.io/nah414/phoenix`), and Nuitka standalone binary (Linux + Windows), all CI-built across Python 3.11/3.12/3.13.

**In progress (v1.1):** a cognition substrate — `CognitionProvider` adapters (Anthropic / OpenAI / Google / LiteLLM), three cognition wobble axes, and a per-server registered MCP-client mode — shipped 2026-05-20 (Phase 13). Since then the v1.1 line has added encryption administration (the `age` ceremony, admin CLI and rotate-key endpoint), per-actor key isolation plumbing, the cognition drift extension, and the Phase 13 Step 5c cognition-classifier harness: training and evaluation, corpus generation and labeling tooling, FELM/SAC3 dataset adapters, the `phoenix cognition` operator console, and a mobile control panel (PWA) served at `/cognition`. Still open for Step 5c: the real labeled corpus and a trained classifier that clears the macro-F1 ≥ 0.70 gate on it; until then the shipped default classifier is unchanged. Detail per landing is in [`CHANGELOG.md`](CHANGELOG.md).

**Security changes on the v1.1 line (2026-09, breaking):**

- Every authenticated route requires a signed `Authorization` header; a request without one gets 401. The CLI and `phoenix mcp serve` sign only as `--actor` or a configured `default_actor`, so a one-time setup is needed: set `default_actor` (and `rest_url: http://127.0.0.1:8003`) in `~/.phoenix/config.yaml`. The new `phoenix identity header` prints a header for curl or `/docs`. The cognition UI's API (`/v1/cognition/*`) needs the `PHOENIX_UI_TOKEN` token (the desktop launcher mints one per launch) or a signed actor.
- The launcher binds NATS (4222, monitor 8222) to `127.0.0.1` only, in every mode, Docker included.
- `POST /v1/adapters` loads adapters only from `phoenix.adapters` or a namespace listed in the daemon's `PHOENIX_ADAPTER_ALLOWLIST`; any other module is refused with 403.

Setup and migration: [`docs/distribution/run.md`](docs/distribution/run.md) (Authentication, Loading adapters) and the CHANGELOG.

**Planned / deferred:** the reference admin client (a separate repo, deferred to v1.1) and the perception-harness extension (plan locked; build-guide drafting unblocked).

See [`PHOENIX_ARCHITECTURE_v1.md`](PHOENIX_ARCHITECTURE_v1.md) Sections 10.7 and 10.8 for the v1 and v1.1 acceptance criteria.

## License

Apache License, Version 2.0. See [`LICENSE`](LICENSE).

The Apache 2.0 license is the explicit choice for Phoenix per architecture Decision 34 — open source, ecosystem-compatible, and crucially includes a patent grant as belt-and-suspenders against future patent claims on calibration methodology.

## Author

**Adam** ([@nah414](https://github.com/nah414)) — co-researcher and builder. dr-frank-and-eddy is our lab bench where physics evolves; Phoenix is the production middleware that vendors a frozen v6.6 snapshot of that work plus SynQc TDS Core into a single Trinity Core substrate.

Phoenix is built with [Claude](https://claude.com/claude-code) as design partner.
