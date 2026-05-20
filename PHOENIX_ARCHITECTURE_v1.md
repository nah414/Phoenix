# PHOENIX — Architecture Specification v1

**Status:** v1.1 — locked, with the 2026-05-07 perception harness extension revision applied on top of the 2026-05-06 SynQc-greenfield revision. Build guides cite from this document.
**Authoritative location:** `C:\Phoenix\PHOENIX_ARCHITECTURE_v1.md`
**GitHub remote:** `nah414/Phoenix`.
**Date opened:** 2026-05-05. **v0 closed:** 2026-05-06. **v1 locked:** 2026-05-06. **v1 revised:** 2026-05-06 (Orchestrate becomes greenfield; SynQc TDS Core becomes design-reference only — see Section 1 Decision 37 and Section 2.5). **v1.1 revised:** 2026-05-07 (perception harness extension plan locked; Section 11.14 added with 7 new tensions; Section 10.8 v1.1 acceptance criteria extended — see `PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md`).
**Author of record:** Adam (with Claude as design partner)

---

## What this document is

This is the **architecture specification** for Phoenix — not a build guide. It is the blueprint that future build guides will cite when they direct Claude Code into the actual implementation work. Sending Claude Code into greenfield work without an architecture spec is exactly the trap the v6.4.4 build guide on dr-frank-and-eddy avoided by reading from disk; we extend that discipline by writing the spec first, gating it through Adam's review, and only then producing build guides that reference it.

The document covers what Phoenix is, why it exists, what it is and is not responsible for, the seven internal layers and their contracts, the public API surface, the file layout, the v1 acceptance criteria, and the open design tensions that remain after the v0→v1 disposition pass. It does not cover implementation order, sprints, or pull-request structure — that is build-guide territory.

**v0 → v1 transition (2026-05-06):** v0 captured the locked decisions from the May 5 design conversation plus 19 open tensions. v1 lands with: (1) five Section 11 dispositions resolved and folded into Sections 4.7, 5.2, 5.4, 8.3, 8.4; (2) Phoenix Cloud's commercial-bundle scope clarified under Decision 35; (3) cost-ceiling enforcement specified end-to-end across the Router, verification gate, and post-solve accounting; (4) cloud-quantum reproducibility honesty surfaced explicitly on the Result envelope; (5) Phoenix Cloud's three abstraction seams specified concretely as Protocol definitions in `phoenix/_internal/cloud_seams.py`; (6) reference admin client moved to v1.1 acceptance with a new Section 10.8; (7) two new v1 acceptance tests added (compositional fail-closed "panic mode" and long-window six-month replay). Open tension count: 14 (down from 19).

**v1 revision (2026-05-06): Orchestrate becomes greenfield.** During Phase 1 build-guide drafting, Phoenix discovered that SynQc TDS Core's actual source structure (a FastAPI service with auth/Redis/agents/jobs scaffolding) is the wrong shape to vendor verbatim into Phoenix's middleware-grade Orchestrate subsystem. Decision 37's framing of "code skeleton, not literal git fork" wins over the conflicting "vendored verbatim" language elsewhere in the v0 spec. v1 (revised) ships:
- Trinity Core's Solver and Control vendored from frank-data; Orchestrate built greenfield in Phoenix.
- Section 2.5 rewritten to describe greenfield Orchestrate organized by Phoenix-native concerns (bundle building, provider client dispatch, result extraction, drift feedback, cross-provider verification, KPI aggregation, top-level engine).
- Section 10 updated: directory tree drops `vendor/synqc_tds/`; vendoring map drops the SynQc TDS table; `phoenix/trinity/orchestrate/` description specifies the seven-module Phoenix-native breakdown; vendor sync script takes only frank-data as input.
- Section 1 Decisions 4, 5, 7, 9, 37 reworded to reflect greenfield Orchestrate.
- `vendor/VENDOR_VERSION.txt` format drops the `synqc_tds_commit` field.

The architecture's load-bearing structure (seven layers, three peer engines, mandatory three-axis wobble, hashchained provenance, Phoenix Cloud commercial path, fourteen open tensions, all v1 acceptance criteria) is unchanged. The revision narrows the substrate that Phoenix vendors and clarifies that Orchestrate is Phoenix-native code informed by SynQc patterns, not vendored from SynQc.

The document is expected to continue to evolve via the Section 11.10/11.12 update protocol; future evolutions land at v1.1 and beyond.

**v1.1 revision (2026-05-07): Perception harness extension plan locked.** Adam's review of `PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md` (companion document to this architecture spec) approved the perception harness as a v1.x extension landing at Phase 12 onwards, after v1 ships at Phase 11. v1.1 ships with: (1) Section 11.14 added with 7 new tensions catalogued from the perception extension plan (4 RESOLVED in v1.1, 1 build-guide territory, 1 deferred to v1.x perception milestone, 1 architecture revision recommended); (2) Section 10.8 v1.1 acceptance criteria extended with the perception harness criterion. The locked v1 architecture's load-bearing structure (seven layers, three peer engines in Trinity Core, mandatory three-axis quantum wobble, hashchained provenance, Phoenix Cloud commercial path, all v1 acceptance criteria from Section 10.7) is unchanged. v1.1 is a documentation-only revision; no v1 implementation impact. Phoenix v1's Phase 0 → Phase 11 build pipeline proceeds unchanged; perception extension work begins at Phase 12 only after v1 reaches its Phase 5 verification-gate milestone, so v1 implementation attention is not diluted. Open tension count after v1.1: 17 (14 from v1.0 + 3 unresolved from v1.1: 11.14.2, 11.14.6, 11.14.7).

**v1.1 follow-up (2026-05-08): perception-friendly v1 design choices locked.** A second documentation-only revision lands the same day, capturing five architectural decisions Adam approved that future-proof v1 for the perception extension without writing perception code: (a) `WobbleAxis` Protocol parameterizes Phase 5's verification gate (Section 6.3) so perception's three axes plug in as `WobbleAxis` impls without forking the gate; (b) `CloudSeams` registry refactored from named slots to generic name-keyed registration (Section 10.3.1) so v1.x extensions register additional seams without core changes; (c) front-door endpoint namespacing kept as `/v1/...` flat with implicit physics semantics, perception slots in as `/v1/perception/*` sibling (no spec change required, decision recorded for clarity); (d) `LatencyTier` enum defined in v1 with all three values (BATCH_REALTIME routable, STREAMING_REALTIME and PERCEPTION_REALTIME defined-but-not-routable) per the post-Decision-28 paragraph in Section 1; (e) strict no-perception-code-in-v1 discipline confirmed (only the spec acknowledges perception). The follow-up also resolves three architectural drifts between spec and vendored substrate (`AgreementType` → `DisagreementType`, `DPDEngine` → `DPDScheduler`, and the §0 Orchestrate paragraph that had been missed in the 2026-05-06 SynQc-greenfield revision). With 11.14.7 now RESOLVED by the locked `LatencyTier` enum, the open-tension count drops to 16 (14 from v1.0 + 2 unresolved from v1.1: 11.14.2, 11.14.6).

**v1.1 second-round resolution (2026-05-20): post-Phase-13 architecture tension closeout.** Following Phase 13's completion (cognition substrate + MCP-client mode + privacy controls + permission registry extensions, all shipped to main in PR #15), Adam reviewed and locked the remaining 14 v1.0 open tensions catalogued in Sections 11.1 through 11.8. Each entry gains a `(RESOLVED in Phoenix v1.X)` title marker and a `**Resolution (Phoenix v1.X, approved by Adam 2026-05-20):**` block per the 11.12 update protocol. Two tensions were RESOLVED-and-shipped-during-v1 (11.1.4 LoRA validator at Phase 9; 11.2.1 provider equivalence registry at Phase 4) — these are now reconciled against the actual shipped code. The other 12 are locked dispositions: 9 deferred-with-context to v1.x (with Phase 13 hindsight where applicable), 1 deprecated-in-practice (11.8 translator handler set; structured-JSON + LoRA-NL won), 2 reclassified as out-of-scope for Section 11 (11.6.1 reference-client decision lives in reference-client repo; 11.7.2 launcher icon is design-asset work). **Open-tension count after the 2026-05-20 second-round resolution: 2** (both from v1.1 perception: 11.14.2 substrate vendoring scope and 11.14.6 perception verification axes count; both correctly stay open until perception build-guide drafting begins per the v1.1 plan). v1.1 is still a documentation-only revision; no v1 implementation impact.

## What Phoenix is

Phoenix is a **production-grade quantum-accuracy middleware**. It is a downloadable software component that other systems integrate with to gain access to validated quantum computation, hardware-aware routing, and physics-grounded verification of results.

Phoenix is *not* an end-user application. It is *not* a SaaS service. It is *not* a chat interface. It is a power tool that sits between a user's software stack and their hardware (or cloud providers), and its job is to make any system that integrates with it **more accurate and more honest about its uncertainty**.

The closest existing analogies are middleware-shaped: a database driver, a search SDK, a streaming engine, a payments processor library. Phoenix's product category is "the layer your software calls when it needs quantum computation done correctly, with provenance, with error bars, and across whatever providers are available."

**At Phoenix's center is Trinity Core**, a unified three-engine physics core that combines equation-level solving, control-level verification, and hardware-orchestrated execution into a single composable pipeline. Trinity Core is what gives Phoenix its accuracy floor; everything else in the architecture (front door, task grammar, router, verification, safety gate, dev ops backdoor) exists to expose Trinity Core to users with the right discipline. Section 2 covers Trinity Core in depth.

## Trinity Core (the physics heart of Phoenix)

Trinity Core is a unified physics core with three composable subsystems, each representing a peer-grade quantum framework that already exists in production form across Adam's projects. The three subsystems are *not* a layered protocol stack where one wraps another; they are *peer engines* that share data and compose along a shared pipeline.

**Solver** vendors the dr-frank-and-eddy synthesis engine — twelve calibrated equation solvers covering non-relativistic TISE/TDSE, Pauli, Dirac, Klein-Gordon, Breit-Pauli, Ehrenfest, WKB, Stochastic SE, gravitational decoherence, Wheeler-DeWitt, and semiclassical gravity. This is the *equation-level* layer: take a Hamiltonian specification, return a numerical solution with calibrated error bars against analytical references. Calibration profile vendored at v6.6.

**Control** vendors the Drive-Probe-Drive engine from `synthesis/core/` — DPDScheduler, LindbladPropagator (RK4 4th-order, verified against QuTiP mesolve and Qiskit Dynamics), ProbeModel (information-backaction tradeoff), and HardwareBackend abstractions for four quantum modalities (superconducting, trapped-ion, NMR, telecom-photonic). This is the *control-level* layer: take a candidate solution, verify it via a formal three-phase Drive-Probe-Drive protocol with POVM weak measurement, XY4 dynamical decoupling, and dual-clock synchronization. Provable error suppression via `p_eff = p_phys × (1 − η_DD) × (1 − η_probe) × (1 − η_clock)`, with each suppression mechanism independently tunable.

**Orchestrate** is **greenfield Phoenix code** organized into seven Phoenix-native modules (engine, bundle_builder, provider_client, result_extractor, drift_feedback, cross_provider, kpi_bundle) per Section 2.5 and the 2026-05-06 SynQc-greenfield revision. SynQc TDS Core serves as a *design reference* for Orchestrate's contracts — informing how the Phoenix-native modules organize scheduling, probe handling, demodulation, and drift adaptation — but no SynQc code is vendored. The hardware backend interface talks to IBM Quantum, AWS Braket, IonQ direct, Azure Quantum, Rigetti, FPGA/DAQ, or local simulators via Phoenix's `BaseProviderClient` Protocol (defined in `phoenix/trinity/orchestrate/provider_client.py`). This is the *hardware-orchestration* layer: take a verified DPD bundle, run it across the chosen provider, return a typed `KPIBundle` with fidelity, latency, backaction, shot-usage, and status fields.

The three subsystems compose along a shared pipeline. A complete Phoenix solve traverses all three: Solver predicts the answer, Control verifies the prediction's robustness against decoherence and probe back-action, Orchestrate runs it on the chosen backend and returns measured KPIs alongside the theoretical prediction. The **mandatory physics-wobble verification** locked earlier — cross-precision and cross-solver checks — now runs across all three layers, giving the wobble formula three independent disagreement axes (cross-precision inside Solver, cross-control inside Control via probe-strength sweep, cross-provider inside Orchestrate) instead of one. That makes the agreement metric far more rigorous than what was sketched before this audit.

The three engines share a unified data model: a density-matrix representation that travels through all three subsystems unchanged (all three already speak ρ), a `KPIBundle` that accumulates fidelity / latency / backaction / agreement as the pipeline progresses, and a result envelope carrying provenance from each layer. The full data model and the per-layer API contracts are detailed in Section 2.

## Why Phoenix exists

Three problems Phoenix is designed to solve.

**Quantum computation is hard to use correctly.** A working quantum result requires choosing the right Hamiltonian representation (Solver layer concern), choosing the right control sequence to verify it on hardware (Control layer concern), calibrating against analytical references (Solver), propagating numerical *and* decoherence error bars (Solver + Control), and handling provider-specific quirks (Orchestrate). Today's tooling — Qiskit, Braket, CuPy, custom solver suites — gives developers raw access to a *single* layer without a discipline for using all three together. Phoenix encodes the discipline by making Trinity Core the only path through.

**Results without provenance are not science.** Every Phoenix solve produces a result that carries its own audit trail across all three Trinity Core layers: what solver ran with what calibration profile (Solver), what verification protocol with what probe strength (Control), on what provider with what measured KPIs (Orchestrate), under what error bar at every stage. The Omega Ledger pattern from dr-frank-and-eddy — SHA-256 hashchain of every solve — is core, not optional. If a Phoenix result is challenged six months later, the chain reproduces the answer with the full three-layer trace.

**Most quantum work today is single-provider, single-solver, single-precision.** That's fine for prototyping, terrible for science, and worse for any system that needs to defend its outputs. Phoenix runs cross-precision, cross-control, and cross-provider checks on every solve by default, returning typed agreement metrics alongside the value. A user explicitly accepts wider error bars to skip checks; the default is rigor.

## What Phoenix is responsible for, and what it is not

Phoenix's job:
- Accept structured task descriptions in a fixed grammar (the vendored Pāṇinian grammar from dr-frank-and-eddy v6.6).
- Route tasks through Trinity Core's three subsystems (Solver → Control → Orchestrate) with the right parameters per layer.
- Run mandatory physics-wobble verification across all three layers, returning typed `Result(value, error_bar, sigma, agreement_type)`.
- Maintain hashchained provenance for every solve, with each Trinity Core layer contributing its own provenance fields.
- Expose its task surface via REST + WebSocket + CLI + MCP, all backed by the same job grammar.
- Authenticate users via per-install Ed25519 identity, with optional org enrollment.
- Surface a dev-ops backdoor for inspection, logs, queue depth, calibration drift, emergency kill, per-layer Trinity Core health, and per-provider status.
- Support real-time loops at the latency tier appropriate to release version (batch real-time in v1, streaming in v2 — see Locked Decisions).

Phoenix's job is *not*:
- Producing curated cognitive answers (that's what dr-frank-and-eddy's Third Space did, and it's now a *client* of Phoenix, not part of it).
- Hosting an LLM. Phoenix is BYO-model — users plug in their own LLM via a fixed interface.
- Training models or LoRA adapters. Phoenix loads pre-trained adapters; training stays elsewhere.
- Calibrating solvers from scratch. Phoenix vendors a frozen Trinity Core snapshot from dr-frank-and-eddy v6.6 with calibration profiles attached.
- Inventing physics. Phoenix runs vendored Trinity Core; new physics work happens in dr-frank-and-eddy first, then gets re-vendored into a future Phoenix release.

This separation is the entire point. dr-frank-and-eddy is Adam's lab bench, where physics evolves. Phoenix is the production middleware that ships *frozen, dated, verifiable* Trinity Core snapshots to users who need correctness guarantees.

```
=== SECTION 0 COMPLETE — AWAITING ADAM REVIEW ===
```

---

# Section 1 — Locked Decisions and Document Conventions

## Locked decisions (do not re-litigate without Adam's explicit go)

These are settled from the May 5 design conversation, including the Trinity Core audit. Every later section of this document treats them as fixed. If a downstream design tension forces a change to one of these, it is a Section-1 change, escalated to Adam, not a quiet drift.

**Product identity**

1. Phoenix is **middleware**, not an application. It is downloadable software that other systems integrate with. There is no "Phoenix UI" in the dr-frank-and-eddy sense; if a UI exists in v1, it is a thin reference admin client, not the product.
2. Phoenix's purpose is to make systems that integrate it **more accurate and more honest about uncertainty**.
3. Phoenix is BYO-LLM. Users plug in their own model via a fixed interface (the vendored Pāṇinian task grammar). Phoenix does not ship its own LLM, though it documents reference adapters.

**Trinity Core (the physics heart)**

4. **Trinity Core is Phoenix's physics heart.** It is a unified three-subsystem core: **Solver** (12 equation solvers vendored from dr-frank-and-eddy `synthesis/equations/`), **Control** (DPD engine vendored from dr-frank-and-eddy `synthesis/core/`), and **Orchestrate** (greenfield Phoenix code, designed natively for Phoenix's task lifecycle). Solver and Control are versioned together at the same frank-data snapshot. Orchestrate uses SynQc TDS Core as a *design reference* — Phoenix does not vendor from SynQc; see Decision 37.
5. **Three peer engines, one cohesive core.** The three subsystems share data structures (density matrix `ρ`, `KPIBundle`, result envelope) and pass through a unified pipeline. Solver and Control versions move together with each frank-data re-vendor; Orchestrate evolves on Phoenix's own release cadence as greenfield code. Phoenix never ships one of the three without the others.
6. **A complete Phoenix solve traverses all three Trinity Core layers** by default. Users explicitly opt out per layer (e.g., "skip Orchestrate, simulator only") at the cost of widened error bars, and the opt-out is recorded in the result's provenance trace.

**Substrate vendoring**

7. Phoenix v1 vendors **dr-frank-and-eddy at a pinned commit** as its substrate. This includes Trinity Core's Solver and Control subsystems (per Decisions 4-6), the Sanskrit codec (`evolution/knowledge/sanskrit_codec.py`), the generative grammar (`evolution/knowledge/grammar/`), the wobble disagreement types and classifier (`wobble/`), and the Actor authentication module (`evolution/knowledge/actor.py`). Trinity Core's Orchestrate subsystem is NOT vendored — it is greenfield Phoenix code (Decisions 4 and 37). The vendored substrate is **frozen and version-stamped** at the pinned commit recorded in `vendor/VENDOR_VERSION.txt`; it does not auto-update. Future Phoenix releases re-vendor a newer frank-data commit. The actual pinned commit is specified per release in `vendor/VENDOR_VERSION.txt`, not in this architecture document — the spec describes the discipline, not the commit.
8. **LoRA hot-swap is a v1 capability, not v1 content.** Phoenix v1 ships the *interface* for loading trained adapters (v6.7-style or otherwise) but does not vendor a specific adapter. Users bring their own trained adapter or run Phoenix without one.
9. **dr-frank-and-eddy stays untouched** as Adam's lab bench. Phoenix never live-imports from `C:\frank-data\` at runtime; it vendors stamped copies under `C:\Phoenix\vendor\`. SynQc TDS Core is a *design reference* used during Orchestrate's design phase to inform Phoenix's greenfield implementation; Phoenix never imports from SynQc at runtime, never vendors SynQc files, and treats SynQc's source as informational rather than authoritative for the Orchestrate subsystem's contracts.

**Authentication and identity**

10. **Per-install Ed25519 identity** is the default. Each Phoenix deployment generates its own keypair on first run, stored via OS-native protection (DPAPI on Windows, Keychain on macOS, libsecret on Linux). Mirrors dr-frank-and-eddy's pattern.
11. **Org enrollment is opt-in.** A deployment can enroll into an organization, deriving a per-install subkey from the org root via HKDF. This makes Phoenix usable for multi-machine teams without breaking blast-radius containment — revoking one compromised install revokes its subkey, not the whole org.
12. **The Actor pattern is vendored from dr-frank-and-eddy v6.6 unchanged.** Typed `Actor` value at the engine boundary, HMAC-signed payload with a 5-minute validity window, constant-time signature compare. Raw strings raise `TypeError` before any whitelist check.

**Verification**

13. **Mandatory physics-wobble verification** on every solve, running across all three Trinity Core layers. Every Phoenix result is a typed `Result(value, error_bar, sigma, agreement_type)` tuple, never a bare number. The wobble protocol runs cross-precision inside Solver (same solve at two grid resolutions), cross-control inside Control (probe-strength sweep ε ∈ {ε₁, ε₂}), and cross-provider inside Orchestrate (same bundle on simulator vs hardware where available).
14. **Adaptive depth within the mandatory floor.** Mandatory means non-zero rigor on every solve; it does *not* mean a fixed protocol. The depth (how many cross-checks run, at what cost) is determined by the user's stated error-bar tolerance — tighter bars demand more checks. This is an open design tension addressed in Section 11.

**Commercial-grade capabilities (v1)**

Phoenix v1 ships with four commercial-grade capabilities that distinguish it from a research prototype and make it defensible as instrument-grade middleware. These are not extras — they are the operational rigor that lets a regulated user (national lab, hardware vendor, regulated R&D group) bet a finding on Phoenix's output.

15. **Hashchained provenance with bit-exact replay.** The Omega Ledger pattern from dr-frank-and-eddy is vendored and extended for replay support. Every Phoenix solve produces a ledger entry containing: input hash, calibration profile hash, library-version manifest hash, full Trinity Core trace, output hash, prior-entry hash. The chain is append-only and tamper-evident. The replay subsystem can reconstruct any historical solve from its ledger entry, in the appropriate reproducibility mode (per Decisions 19-21).
16. **Audit-grade structured logging.** Every Trinity Core layer transition (Solver-to-Control, Control-to-Orchestrate), every router decision, every authentication check, every drift alert, every config change emits a structured event with timestamp, actor identity, layer, parameters, and result hash. Native Phoenix event format internally; OpenTelemetry export adapter on top so users get standards-compliance without a hard dependency. Default destination is local JSONL file; OTel adapter is opt-in via config and exports to any OTLP-compatible backend (Datadog, Splunk, Honeycomb, etc.).
17. **Calibration drift monitoring.** Phoenix continuously self-checks for drift via three independent detectors: the Tier-1 analytical battery (HO-1, ISW-1, H1S-1, RABI-1, SCG-1), an ML-based statistical drift detector built on the `ml/drift_ensemble.py` pattern from dr-frank-and-eddy that watches solver-output distributions against learned baselines, and a cross-version comparison that runs current Phoenix's Tier-1 against the previous release's recorded Tier-1 results. Cadence: every 6 hours by default (configurable via `PHOENIX_DRIFT_CADENCE_HOURS`), plus on every Phoenix process startup, plus on every `requirements.lock` change. **PERF:** combined runtime ~5-7 minutes per cycle, runs on a low-priority background thread, never blocks user-facing solves. **SAFETY:** if any two detectors fire simultaneously, Phoenix escalates to "high confidence drift" — single-detector firings can be transient noise, multi-detector agreement is real. Phoenix never silently returns a result while it knows it's miscalibrated; if a single detector has fired, the result still ships but carries a `drift_warning` field in its provenance and the dev-ops backdoor surfaces a loud alert.
18. **Multi-region cloud-quantum failover.** When a cloud quantum provider is degraded (deep queue, hardware offline, network failure), Phoenix routes around it to an equivalent provider automatically. Every routing decision is recorded in the ledger entry's provenance trace. The hard architectural question — *when is provider X equivalent to provider Y for circuit Z* — is deferred to Section 11 (Open Design Tensions); v1 ships with conservative equivalence defaults and a manual override interface.

**Reproducibility**

19. **Three reproducibility modes for v1.** `default` (full provenance recorded, no replay guarantee — fast and pleasant for solo users), `strict` (full provenance plus bit-exact local replay guarantee — for users who need to be able to reproduce a result later), `replay` (strict mode plus the result is *required* to be replayed and verified before the API returns — strongest reproducibility guarantee any quantum middleware on the market would make, at roughly 2x wall-clock cost). Mode is set per-solve via the `reproducibility` parameter; the chosen mode is recorded in the ledger entry as part of provenance.
20. **Reproducibility scope rules.** Bit-exact replay is guaranteed for the *deterministic* portion of the pipeline: solver execution, control verification, post-shot processing. Cloud-quantum hardware shots are intrinsically nondeterministic and are *recorded once* in the ledger; replay reads from the recorded shots rather than re-running on hardware. This means a Phoenix result is fully reproducible without re-running the cloud quantum provider — but the original cloud run cannot be bit-exactly reproduced, only the post-shot pipeline can. We document this limit clearly. **PERF:** strict and replay modes force single-threaded BLAS and disable some vectorization to guarantee bit-exactness, costing roughly 15-30% wall-clock vs default mode on local solves.
21. **Operational discipline backing reproducibility.** Every Phoenix release pins its full dependency tree into a `requirements.lock` that ships as part of the release artifact. The replay path refuses to run if the current dep tree doesn't match the ledger entry's recorded versions; users see a clear error rather than a silently-different replay. Random seeds for every RNG (including those inside vendored libraries) are recorded per-solve. Floating-point environment (rounding mode, denormal handling, FMA enable/disable, BLAS thread count) is captured and replayed.

**Operational discipline**

22. **OpenTelemetry as the audit-log export standard, not the internal format.** Phoenix's internal event log uses a native Phoenix format (typed dataclasses, JSON-serializable, schema-versioned). The OpenTelemetry adapter sits on top of that format and exports events to any OTLP-compatible backend. This gives us: (a) freedom to evolve the internal schema without breaking on OTel spec changes, (b) standards-compliance for users who need it, (c) zero hard dependency on OTel for users who don't.

**Provider scope**

23. **Phoenix v1** ships local hardware adapters (NPU, GPU, CPU) plus **all three cloud quantum providers**: IBM Quantum (Qiskit Runtime), AWS Braket, IonQ direct. Quantum SDKs are the most volatile space and stress-testing the adapter interface against them first means v1.1 work is "known pattern plus more" rather than "everything new at once."
24. **Phoenix v1.1** adds cloud GPU (Lambda Cloud, RunPod) and cloud cognition APIs (Anthropic, OpenAI, Google). Same adapter interface; no architectural changes expected.
25. **The 5 co-authors** (Claude, Gemini, ChatGPT, Grok, Perplexity) are *not* part of Phoenix v1 core. They become a reference admin/test-pilot client that drives Phoenix via MCP — example consumer of the platform, not platform component.

**Real-time scaling roadmap**

26. **Phoenix v1 ships batch real-time** — 10-100 ms loops on local hardware (NPU/GPU/CPU). This is sufficient for drift tracking, adaptive calibration, and slow-control feedback loops in lab settings. Cloud-orchestrated loops run in the 100 ms-1 s range depending on provider latency, and that is acceptable for v1.
27. **Phoenix v1.x extends Trinity Core's Solver layer to medium-size systems** via tensor-network execution (MPS/TJM, already present at `synthesis/quantum/tensor_lindblad.py` in dr-frank-and-eddy but not yet wired into the production path). Enables 16-24 qubit problems in the real-time loop.
28. **Phoenix v2 ships streaming real-time as a first-class application mode** — sub-millisecond loops, standing-computation API, continuous probe-drive feedback where the user describes a continuous experiment and Phoenix sets up a pipeline that runs continuously. The architecture supports it natively (DPD primitive + SynQc-inspired greenfield adapt module + Solver per-update calls), but the streaming-result mode and standing-computation API are real engineering past v1.

**`LatencyTier` enum (canonical encoding of Decisions 26–28, added 2026-05-08).** The three tiers above are encoded as a single enum so the router, scheduler, and front-door scheduling logic can route by tier rather than by string-comparison or hardcoded Decision references. Defined in `phoenix/_internal/latency.py`:

```python
from enum import Enum

class LatencyTier(Enum):
    """Latency tier each Phoenix solve commits to. Drives Router selection,
    timeout policies, and front-door scheduling decisions."""

    BATCH_REALTIME = "batch_realtime"
    """v1 (Decision 26): 10-100 ms loops on local hardware; 100 ms-1 s for
    cloud-orchestrated. The default tier for v1 solves."""

    STREAMING_REALTIME = "streaming_realtime"
    """v2 (Decision 28): sub-millisecond loops, standing-computation API.
    Defined here so the routing layer accepts the tier as a parameter from
    day one; v1 raises `LatencyTierNotImplemented` if a request specifies
    this tier (deferred to v2 implementation)."""

    PERCEPTION_REALTIME = "perception_realtime"
    """v1.1 (Section 11.14.7, locked 2026-05-08): sub-100 ms hard real-time
    per sensor frame end-to-end. Routed only by the perception harness
    extension at Phase 12+; v1 raises `LatencyTierNotImplemented` for
    non-perception tasks specifying this tier."""
```

**Why now (v1, not v1.1 or perception phase):** v1 commits to the enum's three values from day one even though only `BATCH_REALTIME` is routable in v1. Defining the enum in v1 prevents the perception extension from having to retroactively add an enum value (which would churn every caller). Routing logic in the Router (Phase 4) accepts a `LatencyTier` parameter; routes only `BATCH_REALTIME`; raises a typed `LatencyTierNotImplemented` for the other two with a message naming which release will support them. The Router's `RoutingDecision` provenance records the requested tier so audit-log readers can see which tier a solve operated under.

**Distribution**

29. **Three release artifacts** for v1: Python pip package, Docker image, standalone binary (Nuitka-compiled). Every release runs its full integration suite against all three before shipping. Pip is for Python developers, Docker is for ops teams, standalone is for non-developer users.
30. **CI tests all three artifacts** before each release. A release is not green until all three pass.

**Backend**

31. **State backend:** SQLite by default (zero-config, in-process), Postgres opt-in via config flag for org deployments needing concurrency, replication, or audit-grade durability. State backend chosen at startup, not switchable at runtime.
32. **Queue backend:** NATS JetStream regardless of deployment size. Single Go binary, embeddable, file-backed durable queues. No Redis dependency.
33. **A solo Phoenix install boots two processes:** Phoenix itself + NATS. Both managed by the same launcher script. No Docker required for solo use.

**License**

34. **Apache 2.0.** Open source, ecosystem-compatible, includes patent grant. Anyone can vendor Phoenix into anything including closed-source commercial products. The patent grant is belt-and-suspenders against future patent claims on calibration methodology.

**Business model**

35. **Free + paid hosted SaaS** (Phoenix Cloud) is the planned commercialization path. Phoenix the middleware stays fully free and open. Phoenix Cloud is a *separate product* that runs Phoenix for users who don't want to self-host, with multi-tenancy and tenant isolation enforced at the *deployment* layer outside Phoenix's process boundary. This means Phoenix v1 itself does not need to be multi-tenant; the future Phoenix Cloud hosting layer is. Phoenix v1 ships three thin abstraction seams to support the future hosting layer without architectural invasion: an HTTP-layer authentication abstraction, an audit-log export hook, and a per-job resource budget interface. Each abstraction is useful even outside the hosted scenario.

**Clarification of the free / paid model (added 2026-05-06):** Phoenix-the-middleware is fully free under Apache 2.0 and there is no feature fork between free and paid Phoenix. The "paid tier" is **Phoenix Cloud's commercial bundle** — hosting *plus* additional commercial features intended for regulated and enterprise users:
- **Enterprise SSO and directory integration** (SAML/OIDC, SCIM provisioning) layered above Phoenix's per-install Ed25519 + HKDF org subkeys (Decisions 10-11), enforced in the hosting layer not Phoenix's process.
- **Audit-log retention guarantees with SLA** (multi-year, geo-redundant, tamper-evident) sitting above Phoenix's local JSONL audit log + OTel export (Decisions 16, 22). Phoenix-the-middleware writes events; Phoenix Cloud assumes the long-term retention contract.
- **White-glove drift recalibration** — when a regulated tenant's Tier-1 battery shows drift (Decision 17), Phoenix Cloud's ops team runs the diagnosis, ships a recalibrated `vendor/calibration_profile.json` to that tenant out-of-cycle, and assumes operational responsibility for the recovery. The tenant's Phoenix-the-middleware install handles the new profile via the normal vendor-sync path (Section 10.4); the commercial value is the on-call response and the audit packaging.
- **Dedicated provider rate contracts** — pre-negotiated capacity on IBM Quantum / AWS Braket / IonQ on the tenant's behalf, with cost-ceiling guarantees and priority queue access. Phoenix-the-middleware's pricing-data and routing layers (Section 4) consume the contract terms transparently; the commercial value is the contract negotiation and the rate.
- **Compliance attestations** (SOC 2 Type II, HIPAA BAA, ISO 27001 where applicable) on the hosting layer, not on Phoenix-the-middleware itself.

The full architecture of Phoenix Cloud — multi-tenancy fabric, billing, on-call ops, the SLA-bearing audit retention store, the SSO directory — lives in a separate architecture document for that product. v0 of Phoenix's architecture only commits to the three abstraction seams that make Phoenix Cloud's commercial bundle implementable above an unmodified Phoenix-the-middleware. Those seams are specified concretely in Section 10.3 (`phoenix/_internal/cloud_seams.py`).

**Repository**

36. **`nah414/Phoenix`** on GitHub, fresh clean repository history.
37. **SynQc TDS Core is a design reference for Phoenix's Orchestrate subsystem**, not a vendoring source. No SynQc code lives in Phoenix's repository or runtime; Phoenix's Orchestrate is greenfield Phoenix code organized by Phoenix-native concerns (bundle building, provider client dispatch, result extraction, drift feedback, KPI aggregation, cross-provider verification — see Section 10.3). The architectural concepts SynQc named (scheduling, probe types, signal demodulation, drift adaptation) inform the design of Phoenix's modules, but the specific module breakdown reflects Phoenix's task lifecycle, not SynQc's. Discovered during Phase 1 build-guide drafting (2026-05-06): SynQc TDS Core's actual structure (FastAPI service with auth/Redis/agents/jobs) is the wrong shape to vendor wholesale into Phoenix's middleware-grade Orchestrate; the greenfield approach is the correct one.
38. **Local C drive is authoritative.** All files written under `C:\Phoenix\`. GitHub push happens after a section clears Adam's review.

## Conventions for this document

These apply to every section authored from this point forward.

**Live reads beat memory.** Before a section references behavior or interface from dr-frank-and-eddy v6.6 or SynQc TDS, the section author reads the relevant file from disk. This is the rule that surfaced Trinity Core's actual three-engine shape and prevented us from sketching greenfield design for code that already existed.

**Stop gates between sections.** Each major section ends with a `=== SECTION N COMPLETE — AWAITING ADAM REVIEW ===` line. The next section is not authored until Adam approves the previous one. This mirrors the build-guide phase-gate pattern.

**Ask, don't assume.** Genuine ambiguities are marked `[OPEN: <description>]` and tracked in Section 11 (Open Design Tensions). They are not silently resolved by the author. Adam decides.

**No bullet-blasts.** Where prose carries the meaning cleanly, prose wins. Bullets are used only when the content is structurally enumerated (locked decisions, layer responsibilities, acceptance criteria) and the bullet form aids scanning more than prose would.

**Cross-references use file paths.** When a section refers to another section or another file, the reference is a path Claude Code can `view`, not a vague "see above." This makes the doc machine-actionable as well as human-readable.

**Performance and safety callouts** are flagged inline with `**PERF:**` and `**SAFETY:**` prefixes where they appear, and consolidated in Section 10.

**Trinity Core terminology is consistent.** "Trinity Core" refers to the unified three-engine core. "Solver" / "Control" / "Orchestrate" are its three subsystems. The lowercase forms refer to *general* solving / control / orchestration concepts; capitalized forms refer to the specific Trinity Core subsystems. Mixing the two is the kind of language drift that compounds over a long doc.

```
=== SECTION 1 COMPLETE — AWAITING ADAM REVIEW ===
```

---

# Section 2 — Trinity Core Deep Dive

## What Section 2 covers

Section 2 is the architectural core of the entire document. It specifies how Trinity Core's three subsystems (Solver, Control, Orchestrate) compose into a single physics pipeline; what data flows between them; the API contract each subsystem exposes to the rest of Phoenix and to the others; how the LoRA hot-swap interface plugs in; how the mandatory wobble protocol runs across all three subsystems; and the path from v1 batch real-time toward v2 streaming real-time.

The decisions referenced in this section trace back to Section 1's locked decisions (notably 4-6 on Trinity Core, 13-14 on wobble verification, 15-21 on commercial-grade capabilities and reproducibility, 26-28 on real-time scaling). Section 2 does not introduce new locked decisions; it specifies the interfaces those decisions imply.

Open design tensions encountered while writing Section 2 are flagged with `[OPEN: <description>]` markers and tracked in Section 11. They are not silently resolved.

## 2.1 — The Trinity Core pipeline at a glance

A Phoenix solve is a single typed pipeline that traverses three subsystems in order. Each subsystem reads a typed input, produces a typed output, and contributes provenance fields to the result envelope. The shared data type that flows through all three is the density matrix `ρ`; every Trinity Core subsystem already speaks ρ natively.

Conceptual flow:

```
PhysicsTask  -->  Solver  -->  CandidateAnswer (ρ_solver, error_bar_solver)
                              |
                              v
                          Control  -->  VerifiedAnswer (ρ_verified, KPIBundle_control)
                                       |
                                       v
                                   Orchestrate  -->  Result (value, error_bar, sigma, agreement_type)
```

The arrows are typed boundaries — each subsystem's output is the contract its consumer relies on. A subsystem may be skipped only via explicit user opt-out (Decision 6); the opt-out is recorded in provenance and the result's `error_bar` widens accordingly.

## 2.2 — The shared data model

Three dataclasses are passed along the pipeline. They are typed, hashable, JSON-serializable, and version-stamped.

**`PhysicsTask`** is the input to Trinity Core. Constructed from a Pāṇinian-grammar-validated user request (the task grammar layer is upstream of Trinity Core; see Section 3). It contains the `PhysicsContext` already used by dr-frank-and-eddy's solver registry — mass, spin, velocity, fields, gravity regime, etc. Reference: `synthesis/equations/base.py::PhysicsContext`. Phoenix vendors this dataclass unchanged.

**`CandidateAnswer`** is what Solver produces. Fields: `rho_solver: np.ndarray` (the density matrix from solver execution), `solver_id: str` (which of the 12 solvers ran), `solver_confidence: float` (the registry's `can_handle()` confidence score), `error_bar_solver: float` (the cross-precision wobble result from running at two grid resolutions), `calibration_profile_hash: str` (which calibration this solver was running under), `frontier_physics: bool` (set by Wheeler-DeWitt and gravitational solvers per Section 1 Decision 7).

**`VerifiedAnswer`** is what Control produces. Fields: `rho_verified: np.ndarray` (the post-DPD density matrix), `dpd_result: DPDResult` (the full DPDResult dataclass from `synthesis/core/dpd_engine.py`, vendored unchanged), `kpi_bundle_control: KPIBundle` (fidelity/latency/backaction from the control phase), `error_bar_control: float` (the cross-control wobble result from running probe-strength sweep ε ∈ {ε₁, ε₂}), `probe_strengths_used: List[float]` (which ε values were exercised).

**`Result`** is what Orchestrate produces — the final return value of any Phoenix solve. Fields: `value: Any` (the requested observable: an energy, a state, a probability distribution, depending on the task), `error_bar: float` (the combined error bar across all three Trinity Core layers), `sigma: float` (the standard wobble disagreement metric, computed across the three independent axes), `agreement_type: DisagreementType` (enum: `CONVERGED`, `WOBBLE`, `SPLIT`, `DEGRADED`, `DEGRADED_BUDGET_BOUND` — see Section 6.2 for the full enum and Section 4.7 for `DEGRADED_BUDGET_BOUND` semantics), `kpi_bundle_orchestrate: KPIBundle` (final KPIs from the chosen provider), `provenance: ProvenanceTrace` (the full audit trail per Section 1 Decision 15).

**Reproducibility asterisk surfaced on the Result envelope.** `Result.provenance` carries a `cloud_shots_recorded: bool` field that is `True` whenever any Trinity Core run inside the solve invoked a cloud-quantum provider whose shot results were intrinsically nondeterministic and were *recorded once* in the ledger per Section 1 Decision 20. When `cloud_shots_recorded=True`, the strongest reproducibility guarantee Phoenix can make is: "the post-shot pipeline reproduces bit-exactly via the recorded shots; the original cloud run cannot be re-run on hardware to match bit-exactly." This is meaningful and Phoenix must not let the user infer otherwise. CLI output, MCP tool responses, and the WebSocket `task.complete` event all surface this field prominently when it is `True`. The user-facing `docs/reproducibility/` (Section 10.6) leads with the asterisk, not buries it.

The combined error bar is the quadrature sum of the three layer error bars: `error_bar = sqrt(error_bar_solver**2 + error_bar_control**2 + error_bar_orchestrate**2)`. **[OPEN: confirm quadrature is the right combiner — it assumes layer errors are independent, which is approximately but not exactly true. May need refinement after empirical data.]**

## 2.3 — Solver subsystem (vendored synthesis engine)

Solver is the equation-level layer. It vendors dr-frank-and-eddy's `synthesis/equations/` module unchanged at v6.6.

**Vendored interface (verbatim from `base.py`):**

```python
class EquationSolver(ABC):
    @abstractmethod
    def can_handle(self, ctx: PhysicsContext) -> Tuple[bool, float]:
        """Return (handles, confidence_0_to_1)."""

    @abstractmethod
    def build_hamiltonian(self, ctx: PhysicsContext) -> np.ndarray: ...

    @abstractmethod
    def solve(self, ctx: PhysicsContext, H: np.ndarray) -> dict: ...

    @abstractmethod
    def validate_parameters(self, ctx: PhysicsContext) -> None: ...

    @abstractmethod
    def calibration_check(self) -> CalibrationResult: ...
```

The 12 vendored solvers are: TISESolver, TDSESolver (`non_relativistic.py`); PauliSolver (`pauli.py`); DiracSolver (`dirac.py`); KleinGordonSolver (`klein_gordon.py`); BreitPauliSolver (`breit_pauli.py`); EhrenfestSolver (`ehrenfest.py`) which also exposes WKB; StochasticSESolver (`stochastic.py`) which delegates to TJM/MPS for 16+ qubits; GravitationalDecoherenceSolver, SemiclassicalGravitySolver (`gravitational.py`); WheelerDeWittSolver (`wheeler_dewitt.py`); plus the LindbladSolver in `lindblad.py` and the RedfieldSolver in `redfield.py` which together complete the open-system row.

Solver auto-selection uses the vendored `HamiltonianClassifier` from `registry.py`. The classifier walks all registered solvers, calls `can_handle(ctx)` on each, and picks the highest-confidence handler. Phoenix exposes a `SolverPolicy` overlay above the classifier that lets the router (Section 4) influence selection — for instance, "prefer TJM-based solvers for 16+ qubit problems" or "exclude Wheeler-DeWitt unless explicit user opt-in."

**Cross-precision wobble inside Solver:** Solver runs the chosen solver at two grid resolutions (default: `N` and `2N`), compares the two results, and produces `error_bar_solver` from the disagreement. **PERF:** this doubles solver wall-clock cost relative to single-precision. **SAFETY:** if the two resolutions disagree by more than a configurable threshold (default 10%), Solver flags the result as `WOBBLE` and the wobble propagates to the final `agreement_type`.

**Calibration profile:** Solver vendors the v6.6 calibration profile as a frozen JSON manifest. Every solver result carries the manifest's hash in its provenance. The drift monitor (Section 1 Decision 17) compares live solver output against this manifest.

## 2.4 — Control subsystem (vendored DPD engine + Lindblad)

Control is the control-level layer. It vendors `synthesis/core/dpd_engine.py`, `lindblad_rk4.py`, `probe_model.py`, and `hardware_backends.py` unchanged at v6.6.

**Vendored interface (verbatim from `dpd_engine.py`):**

```python
@dataclass
class DPDBlock:
    drive1_hamiltonian: np.ndarray
    drive1_duration: float
    probe_type: ProbeType
    probe_strength: float
    probe_duration: float
    probe_observable: Optional[np.ndarray]
    drive2_hamiltonian: Optional[np.ndarray]
    drive2_duration: Optional[float]
    adaptive: bool
    label: str

@dataclass
class DPDResult:
    final_state: np.ndarray
    probe_outcomes: List[Dict[str, Any]]
    mutual_information: List[float]
    total_backaction: float
    timing: Dict[str, float]
    trace_preservation: float
    positivity_check: bool
    n_blocks: int
    drift_corrections: List[Dict[str, Any]]
    metadata: Dict[str, Any]
```

Control accepts a `CandidateAnswer` from Solver and produces a `VerifiedAnswer`. It does this by constructing a DPDBlock sequence appropriate to the candidate's Hamiltonian regime (open-system: Lindblad-mediated DPD; closed-system: unitary DPD), executing the sequence on the simulated state, and verifying that `final_state` agrees with `rho_solver` within tolerance.

**Cross-control wobble inside Control:** Control runs the DPD sequence at two probe strengths — typically ε₁ = 0.1 (weak) and ε₂ = 0.5 (information-optimal), with a third optional ε₃ = 1.0 (projective) for users who explicitly request it. The two probe strengths produce two backaction-corrected estimates of the observable; their disagreement produces `error_bar_control`. **PERF:** this doubles Control wall-clock cost. **SAFETY:** if the probe-strength sweep produces disagreement larger than the solver's predicted uncertainty, Control flags the result and the user sees a `WOBBLE` agreement_type.

**Hardware backend selection:** Control's `hardware_backends.py` provides parameters for four quantum modalities (superconducting, trapped-ion, NMR, telecom-photonic). The router (Section 4) tells Control which modality to target for a given task; Control loads the corresponding `HardwareParams` (T1, T2, gate errors, qubit frequency, anharmonicity, probe latency, max qubits, native gate set) and uses them to parameterize the DPD sequence.

**Frontier physics gate:** if the upstream `PhysicsTask` carries `frontier_physics=True` (Wheeler-DeWitt or gravitational solver was used), Control inspects the user's `Actor` for the explicit `frontier_physics` permission. Without permission, Control raises `FrontierPhysicsRefused` and the pipeline halts. This implements the safety gate from Section 1 Decision 7.

## 2.5 — Orchestrate subsystem (greenfield Phoenix code)

Orchestrate is the hardware-orchestration layer. Per Decisions 4 and 37, Orchestrate is **greenfield Phoenix code** — not vendored. SynQc TDS Core was originally named as a vendoring source in the v0 spec, but the 2026-05-06 architecture revision (driven by Phase 1 build-guide drafting against actual SynQc source) found SynQc's structure unsuitable for verbatim vendoring; SynQc TDS Core now serves as a *design reference* for Orchestrate's contracts and concerns, with all Orchestrate code authored fresh in Phoenix.

**Phoenix-native module breakdown** (organized by Phoenix concern, not SynQc terminology — full file list in Section 10.3):
- **`bundle_builder`** — translates a `VerifiedAnswer` from Control plus a `ProviderSelection` from the router into a provider-specific submission shape (Qiskit circuit, Braket task, IonQ shot batch, classical-simulator Hamiltonian). Pure translation, no I/O.
- **`provider_client`** — `BaseProviderClient` Protocol plus dispatch into the concrete adapters under `phoenix/providers/`. Handles connection management, submission, polling, raw-result return; per-provider adapter classes live in `phoenix/providers/{quantum,classical,cognition,cloud_gpu}/`.
- **`result_extractor`** — translates provider-specific raw results (shot counts, expectation values, density-matrix estimates) into Phoenix-uniform observables and `KPIBundle` fields. Pure post-processing, no I/O.
- **`drift_feedback`** — emits drift signals to the Router's intelligence layer (Section 4.6) and the drift detector (Section 6.5) from the just-completed solve's measured KPIs, so future routing benefits from the latest empirical fidelity/latency.
- **`cross_provider`** — Axis 3 wobble: when triggered by the verification gate (Section 6.4 rung selection), runs the same bundle on a second provider and produces `error_bar_orchestrate`.
- **`kpi_bundle`** — typed `KPIBundle` aggregator: `fidelity`, `latency_us`, `backaction`, `shots_used`, `shot_budget`, `status` (`ok` / `warn` / `fail`).
- **`engine`** — top-level orchestrator that runs the above through the orchestration pipeline and returns a `Result` to Trinity Core's pipeline.

Orchestrate accepts a `VerifiedAnswer` from Control plus a `ProviderSelection` from the router and produces a `Result`. The engine sequences: bundle_builder translates the bundle; provider_client submits and polls; result_extractor processes the raw results into observables and KPI fields; drift_feedback emits the signals; the typed `KPIBundle` becomes part of the Result.

**Hardware backend selection** is informed by the vendored `synthesis/core/hardware_backends.py` (frank-data) for the four base modalities (superconducting, trapped-ion, NMR, telecom-photonic) plus per-provider concrete adapter classes under `phoenix/providers/`. Phoenix never vendored SynQc's separate hardware-backend code; the vendored frank-data hardware params are the authoritative source for modality-level constants, and provider-specific overrides come from each `BaseProviderClient` implementation.

**Cross-provider wobble inside Orchestrate:** when the user has stated tight error-bar tolerance, Orchestrate runs the same bundle on a second provider (typically the local simulator alongside the chosen cloud provider) and compares results. The disagreement produces `error_bar_orchestrate`. **PERF:** doubles cloud-provider cost when triggered; the router decides whether to trigger based on the user's stated tolerance. **SAFETY:** if cloud and simulator disagree beyond noise expectations, Orchestrate flags `WOBBLE` and the dev-ops backdoor surfaces an alert.

**Multi-provider failover (Section 1 Decision 18):** Orchestrate watches provider queue depth and health. If the chosen provider is degraded (queue > threshold, hardware offline, network failure), Orchestrate routes around it to an equivalent provider per the equivalence registry. The routing decision is recorded in provenance. **[OPEN: provider equivalence — when does X-on-IBM equal X-on-Braket — deferred to Section 11.]**

**KPIBundle:** every Orchestrate execution produces a typed `KPIBundle` (Phoenix-native dataclass — designed for Phoenix's task lifecycle, not vendored) with the fields named above. The bundle becomes part of the final Result.

## 2.6 — How the wobble protocol composes across all three layers

The mandatory physics-wobble verification from Section 1 Decision 13 runs across three independent disagreement axes, one per Trinity Core subsystem. The agreement metric is computed at the Result envelope level after all three subsystems have produced their disagreement contributions.

The three axes:
- **Cross-precision (inside Solver):** same solver, two grid resolutions. Captures numerical convergence error.
- **Cross-control (inside Control):** same Hamiltonian, two probe strengths. Captures backaction-induced disagreement.
- **Cross-provider (inside Orchestrate):** same bundle, two providers (cloud + simulator). Captures provider-dependent shot noise plus systematic provider error.

The combined `sigma` is computed as the wobble formula vendored from dr-frank-and-eddy's Frank module: `sigma = sqrt(Var(Result_axes))`. With three axes the sigma is more rigorous than the single-axis case dr-frank-and-eddy currently uses, because three independent disagreements have less probability of all happening to coincide than two.

**Adaptive depth (Section 1 Decision 14):** the user supplies a `max_error_bar` parameter with the task. Trinity Core walks the three axes and decides at each layer whether to run the cross-check based on whether the running combined error bar already meets the tolerance. If Solver alone produces a tight result and the user accepts wider bars, Control may skip the probe-strength sweep. If the user demands tight bars, all three axes always run. **[OPEN: the formula that translates `max_error_bar` into per-axis enable/disable decisions, deferred to Section 11.]**

## 2.7 — LoRA hot-swap interface

Per Section 1 Decision 8, Phoenix v1 ships the *interface* for loading LoRA adapters (v6.7-style or otherwise) without vendoring a specific adapter. The interface lives at the boundary between the upstream task-grammar layer (Section 3) and Trinity Core, not inside Trinity Core itself — it affects how a user's natural-language input becomes a `PhysicsTask`, not how Trinity Core executes the task.

**The interface:**

```python
class LoRAAdapter(Protocol):
    name: str
    version: str
    base_model_fingerprint: str  # which LLM this adapter was trained on top of
    capabilities: List[str]      # e.g. ["sanskrit-glyph-bidirectional", "physics-domain-extension"]

    def encode_to_grammar(self, natural_language: str) -> GrammarTokens: ...
    def decode_from_grammar(self, tokens: GrammarTokens) -> str: ...
    def fingerprint(self) -> str: ...   # hash for ledger provenance
```

A loaded adapter declares its capabilities; Phoenix's task-grammar layer queries those capabilities and routes input through the adapter when relevant. The fingerprint goes into every ledger entry that used the adapter, so reproducibility-strict mode can verify the adapter version on replay.

**v6.7-style adapter compatibility:** the v6.7 LoRA from dr-frank-and-eddy that teaches Qwen3 to read and emit Sanskrit glyphs natively is one possible loaded adapter. Phoenix doesn't vendor it but documents the compatibility contract: any v6.7-derived adapter declaring `sanskrit-glyph-bidirectional` capability is loadable.

**Inference-time validation:** when an adapter is loaded, Phoenix runs a small validation suite (a few canonical grammar round-trips) before accepting it. If validation fails, the adapter is rejected and the load operation returns a typed error rather than silently using a broken adapter. **[OPEN: what's the right validation suite — deferred to Section 11.]**

## 2.8 — From v1 batch real-time toward v2 streaming real-time

Section 1 Decisions 26-28 lock the real-time scaling roadmap. Section 2 specifies how Trinity Core's architecture supports each tier without breaking earlier contracts.

**v1 — batch real-time (10-100 ms loops on local hardware):** the pipeline above is the v1 contract. A solve enters as a `PhysicsTask`, traverses Solver → Control → Orchestrate, and returns a `Result`. The latency budget is dominated by Solver (1-50 ms for small Hamiltonians), Control (5-20 ms for a typical DPD sequence), and Orchestrate (1-30 ms for local provider, 10-200 ms for cloud). For small-system local-only solves the round-trip is solidly inside 10-100 ms. **PERF:** the `default` reproducibility mode (per Section 1 Decision 19) is faster than `strict`/`replay`; users wanting tight loops should use `default` and accept that bit-exact replay is not promised.

**v1.x — medium systems via tensor-network execution (16-24 qubits):** the existing `synthesis/quantum/tensor_lindblad.py` MPS/TJM module from dr-frank-and-eddy is wired into Solver as an alternate execution path. The Solver registry's `can_handle()` confidence scores adjust to prefer TJM-based execution when problem size > 15 qubits. Control and Orchestrate are unchanged because they already operate on density matrices and don't care about the underlying solver representation. **[OPEN: whether the MPS-truncation error becomes a fourth wobble axis or rolls up into `error_bar_solver`, deferred to Section 11.]**

**v2 — streaming real-time as a first-class application mode:** the v1 pipeline is *batch* — a complete solve goes in, a complete Result comes out. v2 adds *streaming* — the user describes a continuous experiment (a Hamiltonian that evolves over time, an adaptive control loop that updates from probe results), and Phoenix sets up a standing pipeline. Probe results stream in continuously; drive corrections stream out continuously; the user's program subscribes to a result stream rather than awaiting a batch return. The architecture supports it natively because DPD's three-phase protocol *is* the right primitive for streaming control, SynQc's `adapt` module is already designed for the drift-tracking loop, and Solver's per-update calls are fast enough for small systems. The engineering work for v2 is exposing this as a first-class API mode (a `StandingComputation` object, a result-stream protocol, lifecycle methods for start/pause/resume/stop) rather than inventing new physics. The v1 batch contract is preserved — every v1 user keeps working unchanged in v2.

## 2.9 — Phoenix's relationship to Trinity Core

Trinity Core is the physics heart, but Phoenix is more than Trinity Core. The other six layers (front door, task grammar, router, verification gate, safety gate, dev ops backdoor) wrap Trinity Core and give it the production-middleware properties — multi-protocol exposure, structured input parsing, provider routing, audit-grade observability, frontier physics safety, operational visibility.

Trinity Core is what Phoenix *does*. The other layers are how Phoenix *delivers* it.

Sections 3 through 9 cover the wrapping layers in order: Section 3 (task grammar layer), Section 4 (router), Section 5 (front door — REST/WebSocket/CLI/MCP), Section 6 (verification gate — wobble protocol orchestration above what's inside Trinity Core), Section 7 (safety gate — frontier physics, actor authentication, rate limiting), Section 8 (dev ops backdoor), Section 9 (the v6.4.4 reference admin client built from the 5 co-authors).

```
=== SECTION 2 COMPLETE — AWAITING ADAM REVIEW ===
```

---

# Section 3 — Task Grammar Layer

## What Section 3 covers

Section 3 specifies the layer between Phoenix's external surface (REST/WebSocket/CLI/MCP, Section 5) and Trinity Core (Section 2). This is the layer that takes user input — natural language, structured JSON, or grammar tokens — and produces a typed, validated `PhysicsTask` that Trinity Core can consume.

The grammar substrate vendored from dr-frank-and-eddy v6.6 is the *what's well-formed* layer. Section 3 specifies the *what's executable* layer that sits on top of it: how a well-formed grammar statement becomes an executable Trinity Core task, how the LoRA hot-swap interface from Section 2 actually plugs in, how schema versioning works, and how the layer fails safe when input is malformed or maliciously crafted.

The decisions referenced trace back to Section 1 (notably 7-9 on substrate vendoring, 10-12 on authentication, 19-21 on reproducibility) and Section 2 (particularly the `PhysicsTask` data model and the LoRA Protocol).

## 3.1 — Why this layer exists

Trinity Core's input contract is `PhysicsTask`, a typed dataclass containing a `PhysicsContext` with mass, spin, velocity, fields, gravity regime, etc. — well-defined values that solvers can consume.

But Phoenix users do not type `PhysicsContext(mass=9.109e-31, spin=0.5, ...)` directly. They submit input in one of several forms:

- **Structured JSON** (the common API case) — a dict matching Phoenix's task schema.
- **Grammar tokens** — well-formed strings in the vendored Pāṇinian grammar (e.g. `"Ĥ ⦗Ψ⦘ = 𝐸 ⦗Ψ⦘"`).
- **Natural language** — only when an LLM adapter is loaded via the LoRA hot-swap interface; the adapter encodes natural language into grammar tokens, which then take the same path as direct grammar input.

Each input form has its own validation, parsing, and security concerns. The task grammar layer is where all of those concerns are addressed. By the time a request leaves this layer headed into Trinity Core, it is a well-typed, schema-validated, actor-authenticated, frontier-physics-gated `PhysicsTask`. Trinity Core never sees a raw user string.

This separation is load-bearing for security. Trinity Core operates on dense numerical data and trusts its inputs; the task grammar layer is where Phoenix's untrusted-input attack surface lives. Putting validation here keeps Trinity Core's hot path simple and fast.

## 3.2 — Vendored grammar substrate

Phoenix v1 vendors the entirety of `evolution/knowledge/grammar/` from dr-frank-and-eddy v6.6 unchanged. The vendored module exposes (per `evolution/knowledge/grammar/__init__.py`):

```python
from phoenix.vendor.grammar import (
    Grammar, Production, Symbol, ParseTree,
    GrammarLoadError, ParseError, GenerationError,
    load_default_grammar, load_grammar, load_grammar_from_dict,
    generate, parse,
)
```

The shipping grammar is `physics_v1.yaml` — 13 non-terminals, 51 productions, covering equation forms (=, ≈, ∝, ⇒, ⇔), scalar arithmetic, operator algebra (⊗, ⊕, commutators, Ĥ, 𝒪, 𝒰), vector expressions, differential operators (∂ₜ, ∇, ∇²), constants (ℏ, 𝑐, 𝑘, α, 0), quantifiers (∀, ∃), and base variables.

**The eight invariants** that the grammar substrate enforces (per the dr-frank-and-eddy grammar README) carry over to Phoenix unchanged: productivity, determinism, bounded generation, parser round-trip, codec round-trip, E4 backend compatibility, security (`yaml.safe_load` only, no `!!python/object:` injection), and performance (100 samples at depth 6 in <1 second). The 31-test invariant suite is vendored alongside the code; Phoenix CI runs it as a smoke test on every release.

**What Phoenix does NOT do to the grammar substrate:**
- Does not modify `physics_v1.yaml`. Future grammar evolution happens in dr-frank-and-eddy first, then gets re-vendored into a future Phoenix release. (Section 1 Decision 7.)
- Does not modify the loader, generator, or parser code.
- Does not bypass the security checks (`safe_load`, the productivity validation, the load-time error surface).

**What Phoenix DOES build on top:**
- A translation layer (`grammar → PhysicsTask`) that maps well-formed grammar statements into Trinity Core's input contract. This is the new Phoenix code that didn't exist in v6.6 because v6.6's grammar served the Evolution Lab's E4 candidate generator, not Trinity Core.
- A schema-validated structured-JSON entry point that bypasses grammar parsing for users who'd rather construct tasks programmatically.
- The LoRA hot-swap interface that lets natural-language input become grammar tokens (Section 2.7, expanded in Section 3.5).

## 3.3 — The structured-JSON entry point

The most common Phoenix input shape is structured JSON. A user (or an upstream agent framework, or a software stack integrating Phoenix) submits a task as a typed dict matching Phoenix's task schema. This bypasses grammar parsing entirely.

**Schema (versioned, JSON-Schema validated):**

```json
{
  "schema_version": "1.0",
  "physics_context": {
    "mass_kg": 9.109e-31,
    "spin": 0.5,
    "particle_type": "electron",
    "regime_hint": "non_relativistic_ti",
    "fields": {
      "magnetic_field_T": [0.0, 0.0, 1.0]
    }
  },
  "observables": ["energy_eigenvalues"],
  "n_eigenvalues": 5,
  "tolerance": {
    "max_error_bar": 1e-6
  },
  "reproducibility_mode": "default",
  "actor": {
    "fingerprint": "ed25519:...",
    "signature_hmac": "..."
  }
}
```

**Validation pipeline:**

1. `schema_version` is checked against Phoenix's supported list. Unknown versions fail fast with a typed `UnsupportedSchemaError`.
2. The full JSON is validated against the JSON-Schema for that version. Invalid shape returns a typed `SchemaValidationError` with the offending path.
3. The `actor` block is verified: HMAC signature checked against the per-install or org-derived key, 5-minute window enforced, constant-time compare. Per Section 1 Decision 12. Failure raises `AuthError`.
4. The `physics_context` is loaded into a `PhysicsContext` dataclass via the vendored factory. Type errors raise `TypeError` at the boundary.
5. The `tolerance.max_error_bar` is parsed and bound-checked. Negative or unreasonable values raise `ToleranceError`.
6. Frontier-physics check: if the resolved regime triggers Wheeler-DeWitt or gravitational solvers, the actor is checked for explicit `frontier_physics` permission. Without permission, raises `FrontierPhysicsRefused` (per Section 1 Decision 7 and Section 2.4).
7. If all checks pass, a `PhysicsTask` is constructed and handed off to Trinity Core's Solver subsystem.

**Schema versioning policy:** Phoenix supports the latest two schema versions concurrently. When a new schema version ships, the previous one stays valid for at least one release cycle. Schema deprecation is announced in release notes; deprecated-but-supported schemas log a warning into the audit log on every use. Users have advance notice to migrate.

**SAFETY:** the JSON-Schema validator runs *before* any signature verification. This is intentional — schema-malformed payloads should not consume crypto cycles. But it means schema parse errors can leak shape info to unauthenticated callers. Phoenix's error responses are deliberately minimal (just the offending path, no actual values) to limit information leak.

## 3.4 — The grammar-tokens entry point

For users who want to compose physics statements at the grammar level — typically other AI agents using Phoenix as a tool, or research workflows that want to express tasks compactly — Phoenix accepts grammar-token input directly.

**Input form:** a string conforming to `physics_v1.yaml`. Example: `"Ĥ ⦗Ψ⦘ = 𝐸 ⦗Ψ⦘"`. The string can be passed via REST as a JSON field, via WebSocket as a message, via CLI as a positional argument, or via MCP as a tool-call parameter.

**Validation pipeline:**

1. The string is parsed via the vendored `parse(grammar, input_string)`. Parse failures raise `ParseError` (vendored typed error) with position information; Phoenix wraps this in a typed API response.
2. The parse tree is walked by Phoenix's translation layer (`grammar → PhysicsTask`, Section 3.6) to extract physics-context fields. Translation failures (well-formed grammar but no Trinity-Core-executable interpretation) raise `UnexecutableStatementError`.
3. The resulting `PhysicsTask` goes through the same actor authentication, tolerance bounds, and frontier-physics gates as the structured-JSON path (steps 3-6 from Section 3.3).
4. On success, hands off to Trinity Core.

**Why have grammar-tokens as a separate entry point at all?** Two reasons. First, it lets agents compose tasks programmatically using a fixed vocabulary, which is far more robust than natural language and more expressive than a JSON schema in some edge cases (e.g. expressing a custom Hamiltonian as a sum of operators). Second, it's the path that LoRA-loaded LLMs feed into — the LLM produces grammar tokens, those tokens take this entry point.

**SAFETY:** the parser uses `yaml.safe_load` and rejects all `!!python/object:` and `!!python/name:` injection attempts (per the vendored grammar's invariant 7). This carries over to Phoenix unchanged. But Phoenix adds one extra check: the input string length is bounded at 16 KB before parsing begins. Beyond that the parser refuses to start. **PERF:** the vendored parser is Packrat-style with memoization; well-formed inputs parse in microseconds. Pathological inputs are guarded by the `hard_cap` (10,000 recursive calls).

## 3.5 — The LoRA hot-swap interface

Section 2.7 specified the `LoRAAdapter` Protocol that a loaded adapter implements. Section 3.5 specifies how that Protocol actually plugs into the task grammar layer at runtime.

**Adapter loading lifecycle:**

1. User issues `phoenix lora load <adapter-path>` (CLI) or equivalent REST call. Authentication required; loading an adapter is a privileged operation.
2. Phoenix loads the adapter via the Protocol's class-load mechanism (typically a Python entry point, a directory of weights, or a HuggingFace-style snapshot).
3. Phoenix validates the adapter via inference-time validation (Section 2.7). Phoenix runs a small canonical round-trip suite: a fixed set of 8-16 grammar statements is decoded by the adapter and re-encoded; the result must match the original. Fails-fast on regression. **[OPEN: the validation suite content — deferred to Section 11.]**
4. On successful validation, the adapter is registered in Phoenix's adapter registry with its declared capabilities (e.g. `["sanskrit-glyph-bidirectional", "physics-domain-extension"]`).
5. The adapter's fingerprint hash is recorded in Phoenix's persistent state (SQLite by default, Postgres in org mode).

**Runtime usage:**

- **Natural-language input arrives at Phoenix** (REST/WebSocket/CLI/MCP).
- Phoenix's task grammar layer queries the adapter registry for adapters declaring `natural-language-encoding` or compatible capability.
- If exactly one adapter matches, it's used. If multiple match, the user's task includes an `adapter_hint` field that selects; without a hint Phoenix uses the most recently loaded matching adapter and logs a warning.
- The adapter's `encode_to_grammar(natural_language)` produces a `GrammarTokens` value.
- The grammar tokens take the same path as Section 3.4: parse, translate, validate, hand off.
- The adapter fingerprint is recorded in the task's provenance trace, so reproducibility-strict mode can verify the same adapter on replay.

**Reproducibility implications (Section 1 Decisions 19-21):**

- In `default` mode: adapter fingerprint is recorded but no replay constraints.
- In `strict` mode: replay requires the same adapter version present and validating. Replay fails with `AdapterVersionMismatch` if the loaded adapter's fingerprint doesn't match the ledger entry's recorded fingerprint.
- In `replay` mode: same as strict, plus the round-trip is exercised on every solve.

**v6.7 LoRA compatibility:** the v6.7 LoRA from dr-frank-and-eddy that teaches Qwen3 to read and emit Sanskrit glyphs natively is one possible loaded adapter. Phoenix doesn't vendor it, but its Protocol contract is satisfied by any v6.7-derived adapter declaring `sanskrit-glyph-bidirectional` capability.

**SAFETY:** the adapter is run inside a subprocess with a per-call timeout (default 5 seconds; configurable). A misbehaving adapter cannot wedge the Phoenix process. The subprocess has restricted filesystem access (cannot write outside its own scratch directory) and no network access. If the adapter requires network access (e.g., a hosted-model adapter that calls Anthropic), that's a *different* kind of adapter — a `RemoteLLMAdapter` Protocol — covered in Section 5.

## 3.6 — The grammar-to-PhysicsTask translation layer

This is the new Phoenix code that doesn't exist in dr-frank-and-eddy v6.6. The vendored grammar parser produces a `ParseTree`; Trinity Core's input is a `PhysicsTask` containing a `PhysicsContext`. Translation between them is a Phoenix-side responsibility.

**Translation contract:**

```python
def translate_parse_tree_to_task(
    tree: ParseTree,
    actor: Actor,
    tolerance: ToleranceSpec,
) -> PhysicsTask:
    """Translate a well-formed grammar parse tree into a Trinity Core task.

    Raises:
        UnexecutableStatementError: well-formed grammar but no executable mapping.
        AmbiguousStatementError: multiple valid interpretations, user must disambiguate.
        FrontierPhysicsRefused: actor lacks permission for the implied regime.
    """
```

**Translation strategy:** the parse tree is walked top-down. Each non-terminal has a registered handler that knows how to map its production into PhysicsContext fields. For example:
- A `Statement` of form `[<Equation>]` where the equation is `Ĥ ⦗Ψ⦘ = 𝐸 ⦗Ψ⦘` translates to "stationary Schrödinger problem; populate `regime_hint=NON_RELATIVISTIC_TI`."
- A `Statement` with quantifier `∀` over a parameterized variable translates to "parameter sweep over the bound variable."
- A `Statement` involving `∇²` plus relativistic operators triggers `regime_hint=KLEIN_GORDON` or `regime_hint=DIRAC` depending on spin context.

**Coverage policy:** v1 ships translation handlers for the 13 non-terminals in `physics_v1.yaml`. Not every grammatically valid statement maps to an executable task — that's expected. Statements that don't map raise `UnexecutableStatementError` with a clear message about what was missing (e.g., "statement omits mass parameter"). The error response includes a suggested completion (the Trinity Core registry's nearest-handler hint).

**Ambiguity handling:** some grammar statements have multiple valid Trinity Core interpretations (e.g., a Hamiltonian could be solved time-independently for eigenvalues or time-dependently for evolution). When ambiguity is detected, translation raises `AmbiguousStatementError` with the candidate interpretations enumerated. The user's task can include a `regime_hint` field that resolves ambiguity in advance.

**Versioning:** the translation table is itself versioned (`translator_v1`, `translator_v1.1`, etc.). Translator version is recorded in every task's provenance. Translator versions are tied to grammar versions — a `physics_v1` grammar requires a `translator_v1.x`. If grammar evolves to `physics_v2`, a new translator is added; old grammars/translators stay supported per the schema versioning policy (Section 3.3).

**[OPEN: the v1 translator handler set — full specification of what each non-terminal's handler does — is too detailed for v0 and is deferred to Section 11. Section 3 specifies the contract; the handler implementations are a build-guide concern.]**

## 3.7 — Failure modes and error surface

The task grammar layer has five distinct failure modes, each with a typed exception and a deterministic API response:

| Failure | Typed exception | HTTP status | Cause |
|---|---|---|---|
| Unknown schema version | `UnsupportedSchemaError` | 400 | Schema version not in supported list |
| Schema-invalid JSON | `SchemaValidationError` | 400 | JSON shape doesn't match schema |
| Auth signature failure | `AuthError` | 401 | Bad HMAC, expired window, or wrong key |
| Frontier-physics refusal | `FrontierPhysicsRefused` | 403 | Actor lacks frontier_physics permission |
| Unparseable grammar | `ParseError` (vendored) | 400 | Input not in the grammar |
| Unexecutable statement | `UnexecutableStatementError` | 400 | Well-formed but no Trinity Core mapping |
| Ambiguous statement | `AmbiguousStatementError` | 400 | Multiple valid mappings; needs `regime_hint` |
| Adapter timeout | `AdapterTimeoutError` | 504 | LoRA subprocess exceeded budget |
| Adapter validation failed | `AdapterValidationError` | 503 | Adapter failed round-trip check |
| Tolerance out of bounds | `ToleranceError` | 400 | `max_error_bar` invalid |

Every failure logs a structured event to the audit log (per Section 1 Decision 16) with the full context — what was submitted, why it was rejected, who submitted it. Failed requests still consume their actor's rate budget but never make it to Trinity Core.

**SAFETY:** error responses to unauthenticated callers (i.e., before step 3 of Section 3.3 has succeeded) reveal only the failure category, never internal state. Errors to authenticated callers can include more detail because the actor identity is logged.

## 3.8 — Performance budget

The task grammar layer's latency budget is part of Phoenix's overall real-time posture (Section 1 Decisions 26-28).

| Stage | Target latency (P50) | Notes |
|---|---|---|
| Schema validation | <1 ms | JSON-Schema in compiled form |
| Actor signature verification | <1 ms | Ed25519 verify is microseconds |
| Grammar parse (when used) | <5 ms | Vendored Packrat parser, memoized |
| Translation to PhysicsTask | <2 ms | Tree walk over small parse trees |
| Frontier-physics check | <1 ms | Single dict lookup against actor |
| **Total layer overhead** | **<10 ms P50** | Inside the v1 batch real-time budget |

**PERF:** for the structured-JSON path (no grammar parse, no translation, no LoRA adapter), total layer overhead is <3 ms P50.

**PERF:** the LoRA adapter path adds whatever the adapter takes — for a quantized 4B parameter model on local NPU, encode latency is typically 100-500 ms. This is the v1 path *not* used for tight real-time loops; users who need <100 ms loops should use direct grammar tokens or structured JSON.

The 10ms budget is a soft commitment for v1 batch real-time. v2 streaming real-time tightens this further by amortizing schema validation across the standing pipeline (validate once on subscription, not on every event).

## 3.9 — How this layer interacts with the others

**Upstream from this layer:** Section 5's front-door layer (REST/WebSocket/CLI/MCP) handles transport-level concerns (HTTP routing, auth-token extraction, rate limiting), then hands off to the task grammar layer. Section 3 doesn't care which front door delivered the input.

**Downstream from this layer:** Trinity Core's Solver subsystem receives a validated `PhysicsTask` and executes per Section 2. The task grammar layer doesn't make routing decisions — it doesn't decide which provider runs the task. That's Section 4's router.

**Sibling concern — Section 7's safety gate:** frontier-physics checks happen here in step 6 of the structured-JSON pipeline and equivalently for grammar-tokens. The actual safety policy (which permissions are granted, how org-level overrides work) is Section 7's responsibility.

**Sibling concern — Section 6's verification gate:** wobble protocol orchestration above what Trinity Core does internally is Section 6. The task grammar layer does not do wobble; it just sets `tolerance.max_error_bar` on the task, and Trinity Core's adaptive depth (Section 2.6) decides how deep to verify.

```
=== SECTION 3 COMPLETE — AWAITING ADAM REVIEW ===
```

---

# Section 4 — The Router (Provider Selection and Hardware-Intelligence Routing)

## What Section 4 covers

Section 4 specifies how Phoenix decides *where* a task runs. After the task grammar layer (Section 3) has produced a validated `PhysicsTask` and Trinity Core's Solver subsystem (Section 2.3) has produced a `CandidateAnswer`, the router decides which concrete provider runs the Control and Orchestrate phases. This includes hardware modality selection (superconducting, trapped-ion, NMR, photonic, classical sim), specific provider selection within a modality (IBM vs Braket vs IonQ for superconducting/trapped-ion access), failover when the chosen provider degrades, and the cost/latency/fidelity trade-off surface that drives all of these decisions.

Decisions referenced trace back to Section 1 (provider scope 23-25, real-time scaling 26-28, multi-region failover 18, multi-provider business model 35) and Section 2 (Orchestrate subsystem 2.5, KPIBundle, the cross-provider wobble axis).

Open design tensions encountered while writing Section 4 are flagged with `[OPEN: ...]` and tracked in Section 11.

## 4.1 — Why the router is its own layer

The router could in principle live inside Trinity Core's Orchestrate subsystem, but Phoenix splits it out as its own layer for three reasons.

First, routing decisions are *policy*, not *physics*. The 12 vendored solvers, the DPD engine, the SynQc TDS modules — those encode physics. Whether a given task should prefer IBM Quantum's Eagle processor over IonQ Forte for fidelity-vs-cost reasons is operational policy that changes weekly as provider availability and pricing shift. Keeping policy out of Trinity Core lets the physics core stay frozen while the router evolves.

Second, the router is the layer that needs *cross-provider awareness*. Trinity Core's Orchestrate subsystem talks to one provider at a time (per VerifiedAnswer it produces a Result via the chosen provider). The router watches *all* providers, knows their queue depths, knows their current health, and decides which one to pick. Putting this at the same layer as Trinity Core would require Trinity Core to know about provider state, which violates the layering.

Third, the router is where Phoenix's *commercial-grade capabilities* (Section 1 Decisions 17-18) actually fire. Calibration drift monitoring decisions ("avoid IBM Cairo this week, last calibration cycle showed drift") and multi-provider failover both live here. Audit-grade structured logging emits routing-decision events (Decision 16). Reproducibility-strict mode pins the provider per ledger entry (Decisions 19-21). All of these are router-layer concerns.

## 4.2 — The vendored substrate

Phoenix vendors two existing provider abstractions, layered:

**Lower-level: Frankenstein 1.0's `ProviderAdapter` ABC.** This is the universal provider interface used by dr-frank-and-eddy across all 19 quantum providers (16 hardware + 3 simulators) plus 12 classical hardware types. Vendored verbatim from `integration/providers/base.py`. The Phoenix router talks to this layer when it needs raw provider operations — connect/disconnect/list_backends/submit_job/get_job_status/get_job_result. Categories are typed via `ProviderCategory` enum (`QUANTUM_CLOUD`, `QUANTUM_HARDWARE`, `QUANTUM_SIMULATOR`, `CLASSICAL_CPU`, `CLASSICAL_GPU`, `CLASSICAL_ACCELERATOR`); quantum technology via `QuantumTechnology` enum (`SUPERCONDUCTING`, `TRAPPED_ION`, `NEUTRAL_ATOM`, `PHOTONIC`, `ANNEALING`, `SIMULATION`).

**Higher-level: SynQc TDS's `BaseProviderClient` Protocol.** This is the experiment-preset interface used by SynQc TDS Core's Orchestrate subsystem. Vendored verbatim alongside its `ProviderLiveResult` dataclass (`raw_counts`, `expected_distribution`, `fidelity`, `latency_us`, `backaction`, `shots_used`). Trinity Core's Orchestrate subsystem talks to this layer when running an experiment preset; the router selects which concrete `BaseProviderClient` implementation gets invoked.

The two layers compose: a `BaseProviderClient` implementation typically wraps one or more `ProviderAdapter` instances. The router picks the `BaseProviderClient`; that client internally uses `ProviderAdapter` for raw provider operations. Phoenix vendors both, unchanged.

**What Phoenix builds new on top:**

The router itself — a `Router` class that takes a `RoutingRequest` (the task plus user policy preferences) and returns a `RoutingDecision` (the chosen `BaseProviderClient` instance plus rationale plus alternates for failover). The `Router` does not run jobs; it makes selection decisions. Trinity Core's Orchestrate subsystem then takes the chosen client and executes.

A `ProviderRegistry` that tracks currently-available providers, their health state, their pricing, their queue depth, their last-calibration timestamp, and their historical reliability. This is new Phoenix code that doesn't exist in either vendored substrate at the level of integration the router needs.

## 4.3 — The routing inputs

The Router accepts a typed `RoutingRequest`:

```python
@dataclass
class RoutingRequest:
    # The task itself
    task: PhysicsTask
    candidate: CandidateAnswer  # Solver's output, drives modality selection

    # User policy
    cost_ceiling_usd: Optional[float]    # None = no ceiling
    latency_budget_ms: Optional[float]   # None = no budget
    fidelity_floor: Optional[float]      # None = use task's tolerance
    reproducibility_mode: ReproducibilityMode  # default | strict | replay

    # Provider preferences
    preferred_providers: List[str]       # ordered preferences
    excluded_providers: List[str]        # never route to these
    allow_failover: bool                 # if True, router can pick alternates
    allow_simulator_fallback: bool       # if True, router can fall back to classical sim

    # Actor (carries authentication and frontier_physics permission)
    actor: Actor
```

The Router produces a typed `RoutingDecision`:

```python
@dataclass
class RoutingDecision:
    primary: ProviderSelection           # the chosen provider + backend
    alternates: List[ProviderSelection]  # for failover, in priority order
    rationale: str                       # human-readable explanation
    estimated_cost_usd: float
    estimated_latency_ms: float
    estimated_fidelity: float
    decision_provenance: Dict[str, Any]  # full input snapshot for ledger
```

`ProviderSelection` carries the resolved `BaseProviderClient` instance plus the specific backend name (e.g., `provider_id="ibm_quantum"`, `backend_name="ibm_brisbane"`, `quantum_technology=SUPERCONDUCTING`).

## 4.4 — The routing decision algorithm

The Router's decision is a multi-stage filter-and-rank process. Each stage narrows the candidate set; if the candidate set ever empties, the Router raises `NoEligibleProvidersError` with the rationale of which stage rejected which candidates.

**Stage 1 — Modality eligibility.** The candidate's Hamiltonian determines which quantum technologies can execute it natively. A 2-qubit Bell state runs anywhere; a 24-qubit highly-connected circuit narrows sharply. The Router consults the registered `HardwareBackend` parameters (vendored `synthesis/core/hardware_backends.py`) to filter.

**Stage 2 — User policy filter.** Apply `excluded_providers`, `cost_ceiling_usd`, `latency_budget_ms`, `fidelity_floor`. Drop any candidate that violates user policy.

**Stage 3 — Provider health filter.** Drop any candidate whose `ProviderRegistry` health state is `DEGRADED` (current calibration drift exceeds threshold) or `OFFLINE` (provider returned health-check failure within the last N minutes). Healthy + queued is acceptable; unhealthy is not.

**Stage 4 — Frontier-physics gate.** If the task carries `frontier_physics=True` and the actor lacks `frontier_physics` permission, drop *all* candidates and raise `FrontierPhysicsRefused`. (Section 7's safety gate has already validated the permission upstream; this is a defense-in-depth re-check at the routing boundary.)

**Stage 5 — Reproducibility constraint.** If `reproducibility_mode == replay`, the Router consults the ledger entry being replayed and forces the same `ProviderSelection` that the original run used. No ranking happens; the Router either confirms the recorded provider is still available or raises `ReplayProviderUnavailable`.

**Stage 6 — Ranking.** Surviving candidates are scored by a weighted function:

```
score(candidate) =
    w_fidelity * (estimated_fidelity_normalized)
  - w_cost     * (estimated_cost_usd / cost_ceiling)
  - w_latency  * (estimated_latency_ms / latency_budget)
  - w_queue    * (current_queue_depth_normalized)
  + w_pref     * (1 if candidate.provider_id in preferred_providers else 0)
```

The default weights ship as `w_fidelity=0.4, w_cost=0.2, w_latency=0.2, w_queue=0.1, w_pref=0.1`. Users can override per-task or globally via config. The chosen primary is the highest-scoring candidate; alternates are the next two highest.

**Stage 7 — Decision provenance.** Every `RoutingDecision` records its full input snapshot (which candidates were considered, which were filtered out at each stage, the scoring weights used) into `decision_provenance`. This blob lands in the ledger entry per Section 1 Decision 15. Reproducibility-strict mode requires this for replay.

## 4.5 — Multi-provider failover

When Trinity Core's Orchestrate subsystem invokes the chosen `BaseProviderClient` and that invocation fails (network error, provider outage, queue timeout exceeded, hardware error), Orchestrate signals failure back to the Router rather than failing the whole pipeline.

The Router's failover protocol:

1. Mark the failed provider's `ProviderRegistry` health state as `DEGRADED` for an exponential-backoff duration (default starts at 5 minutes, doubles per consecutive failure, caps at 1 hour).
2. Take the next alternate from the original `RoutingDecision.alternates` list.
3. Verify the alternate is still healthy (it may have degraded since the original decision). If yes, return a new `RoutingDecision` pointing at the alternate. If no, repeat with the next alternate.
4. If all alternates are exhausted and `allow_simulator_fallback=True`, fall back to the local simulator with a `degraded` agreement_type flag on the eventual Result. If `False`, raise `AllAlternatesExhausted`.

Every failover decision is logged as a structured audit-log event with the original provider, the failure mode, and the chosen alternate. The Result's `provenance.routing_decisions` field is a list, not a single value — failover means multiple routing decisions per task, and all of them are recorded.

**[OPEN: provider equivalence — when is "circuit X on IBM Eagle" equivalent to "circuit X on IBM Brisbane" or "circuit X on IonQ Forte" for failover purposes? Conservative defaults: same `quantum_technology` enum value AND same number of qubits AND fidelity within 10% of original. v1 ships these conservative defaults plus a manual override config. The general theory of provider equivalence is deferred to Section 11 because it's genuinely a research problem.]**

## 4.6 — The hardware-intelligence layer

The Router doesn't make blind choices — it consults hardware-intelligence data to estimate fidelity, latency, and cost for each candidate. Three sources feed the intelligence layer:

**Source A — vendored hardware parameters.** Per Section 4.2, `synthesis/core/hardware_backends.py` ships static `HardwareParams` for the four base modalities (superconducting, trapped-ion, NMR, telecom-photonic). These are baseline estimates; concrete provider backends (e.g., `ibm_brisbane`) override the static params with provider-reported values.

**Source B — live provider telemetry.** When connected, providers expose calibration data: per-qubit T1/T2, per-gate error rates, current queue depth, recent successful job count. The `ProviderRegistry` polls this on a configurable cadence (default 5 minutes for queue depth, 1 hour for calibration data). Stale data is flagged; if a provider's telemetry is older than 24 hours the Router treats that provider as `DEGRADED` until refresh.

**Source C — Phoenix's own historical ledger.** Past `Result` records contain measured fidelity/latency/backaction. The Router can query its own ledger to estimate "what fidelity did we actually get from `ibm_brisbane` last week on similar tasks?" and use that empirical value to override or supplement provider-reported numbers. This is where calibration drift monitoring (Section 1 Decision 17) feeds back into routing — a provider that drifted three releases ago has lower estimated_fidelity than its self-reported number.

The intelligence layer combines all three sources into the `estimated_fidelity`, `estimated_latency_ms`, and `estimated_cost_usd` fields on `RoutingDecision`. The combination weighting is configurable; default weights favor live telemetry (50%), historical ledger (30%), static params (20%).

**PERF:** the intelligence query runs on every routing decision. Caching is essential — the live-telemetry layer maintains an in-memory cache with TTL matching the polling cadence; the historical-ledger layer caches per-provider rolling-window aggregates. Total intelligence overhead per routing decision: <2 ms P50.

**SAFETY:** if all three sources fail (provider telemetry unreachable, ledger empty, static params missing for a new modality), the Router raises `IntelligenceUnavailable` rather than guessing. A user explicitly opts into "best-effort routing" via a config flag if they want to allow guessing in this case.

## 4.7 — Cost estimation

Every cloud quantum and cloud GPU provider has a different pricing model. The Router's cost estimator is a pluggable strategy per provider:

- **IBM Quantum:** charges per QPU-second, with a free tier for selected backends. Cost = `qpu_seconds * provider_rate`.
- **AWS Braket:** charges per shot for QPUs, per task for simulators. Cost = `shots * per_shot_rate + per_task_fee`.
- **IonQ direct:** charges per gate plus per shot. Cost = `n_gates * per_gate_rate + shots * per_shot_rate`.
- **Lambda Cloud (v1.1):** charges per GPU-hour. Cost = `gpu_hours * instance_rate`.
- **RunPod (v1.1):** similar to Lambda but with spot-pricing tier.
- **Local providers:** zero cost; Phoenix returns `cost_estimate=0.0`.

The pricing data ships as a versioned JSON file (`phoenix/router/pricing/pricing_v1.json`) updated per Phoenix release. Users can override per-provider rates via config to match their actual contract terms (e.g., enterprise customers with negotiated rates). **PERF:** cost estimation is pure arithmetic, sub-millisecond.

**Pricing-data staleness policy.** Pricing data going stale doesn't make Phoenix produce wrong physics; it makes cost estimates inaccurate. Hard error on stale pricing would deny user work because of a number that's a hint, not a constraint; silent best-effort would let users spend money assuming an inaccurate estimate. Phoenix's policy is **soft warn, never hard error**. Every routing decision's `decision_provenance` carries a `pricing_data_staleness_days` field computed against the pricing JSON's release date. If staleness exceeds 90 days, the warning fires in the Result envelope and is surfaced in CLI output and on the dev-ops backdoor's `/v1/admin/budget` endpoint. Operators can refresh out-of-band via the `phoenix admin pricing-update` command (Section 8) without waiting for a Phoenix release. Resolved from Section 11.2.2.

**Default budgets and ceiling-enforcement path.** Cost estimation only matters if Phoenix actually enforces ceilings on it. v1 ships sensible defaults so the first user who fat-fingers `max_error_bar=1e-7` does not burn through their AWS Braket budget overnight, and so an automated agent driving Phoenix can't run away.

**Default ceilings (overridable per-install via `~/.phoenix/config.yaml` and per-task via `RoutingRequest`):**
- Per-solve cost ceiling: `$5.00` USD for any single solve at `default` reproducibility, `$25.00` at `strict`, `$50.00` at `replay`.
- Per-actor-per-24-hours cumulative ceiling: `$50.00` USD for `default`-tier actors, `$500.00` for `elevated`-tier actors, no ceiling for `admin`-tier actors. Mirrors the rate-limit-tier discipline from Section 7.5.
- Per-org-per-24-hours cumulative ceiling: `$2000.00` USD by default; org admins can raise via the dev-ops backdoor.

**Enforcement points:**
1. **Pre-solve check (router, Stage 2).** When the routing algorithm filters by `cost_ceiling_usd`, it also filters by the actor's remaining 24-hour budget (`actor_remaining_budget_usd = ceiling - sum(spend_in_last_24h)`). If `estimated_cost_usd > min(per_solve_ceiling, actor_remaining_budget_usd)`, the candidate is dropped. If all candidates fail the check, the Router raises `CostCeilingExceeded` rather than `NoEligibleProvidersError` so the user sees the actual reason.
2. **Mid-pipeline check (verification gate).** When the verification gate considers promoting from rung R_n to R_n+1, it estimates the additional cost of the next axis run (typically a second `RoutingRequest` for cross-provider verification). If promotion would push cumulative solve cost past `per_solve_ceiling`, the gate **does not promote**; instead it records `agreement_type=DEGRADED_BUDGET_BOUND` and includes a structured `budget_bound_skipped_axis` field in `Result.provenance`. The result still ships, but the user knows the verification was budget-truncated. **SAFETY:** this is the right tradeoff because the alternative (refuse the solve and lose the work paid-for so far) is operationally worse than ship-with-degraded-tag. The user can re-submit at higher ceiling if they want the deeper verification.
3. **Post-solve accounting (state backend).** Every completed solve writes its actual measured cost (from `KPIBundle_orchestrate.shots_used * provider_rate` or equivalent) to the `solve_cost_ledger` table. The 24-hour cumulative is read from this table; it survives Phoenix process restarts.

**Admin override path:** an admin actor with `is_admin=True` can issue `POST /v1/admin/budget/override` with `{actor: "<name>", new_ceiling_usd: <amount>, expires_at: <ts>}` to grant a temporary budget bump. Override events are top-priority audit-log entries and Omega Ledger links per Section 1 Decision 16. **SAFETY:** override never *removes* a ceiling — `new_ceiling_usd=null` is rejected; the user must specify a finite value. The lowest possible override is the ceiling already in effect; override only grants more budget, never less.

**v1 acceptance:** see Section 10.7's added "Cost-ceiling enforcement" criteria.

## 4.8 — Routing under reproducibility-strict and replay modes

Section 1 Decisions 19-21 specify three reproducibility modes. The Router's behavior differs significantly across them.

**`default` mode:** the Router's full algorithm runs (Stages 1-7). The chosen provider is recorded in the ledger entry for audit purposes only; replay does not require it.

**`strict` mode:** the Router runs the full algorithm but additionally records the routing weights, the candidate scores, and the alternates list in `decision_provenance`. On replay, the Router verifies that the same provider is still available; if not, replay fails with `ReplayProviderUnavailable`. The replay does *not* re-run the routing algorithm — it uses the recorded decision verbatim.

**`replay` mode:** strict's verification plus the result is required to be replayed and verified before the API returns. The Router participates in this by ensuring the routing decision itself is reproducible from the ledger — same input, same weights, same candidates, same scoring, same selection. **PERF:** replay mode roughly doubles wall-clock cost on the routing path because the decision is computed twice (live + replay verification).

The replay path is only well-defined for the *router's choice*; the underlying provider's response (e.g., shot results from real quantum hardware) is intrinsically nondeterministic. Replay reads recorded shots from the ledger rather than re-running the provider, per Section 1 Decision 20.

## 4.9 — Performance budget

The Router's latency budget is part of Phoenix's overall real-time posture (Section 1 Decisions 26-28).

| Stage | Target latency (P50) | Notes |
|---|---|---|
| Stage 1 — modality eligibility | <0.5 ms | Hash lookup against `HardwareParams` |
| Stage 2 — user policy filter | <0.5 ms | Set membership / range check |
| Stage 3 — provider health filter | <0.5 ms | In-memory registry lookup |
| Stage 4 — frontier-physics gate | <0.1 ms | Single dict lookup |
| Stage 5 — reproducibility constraint | <0.5 ms | Ledger entry lookup if replay |
| Stage 6 — ranking | <2 ms | Score N candidates, typically N≤6 |
| Stage 7 — provenance recording | <1 ms | Dict construction |
| **Total Router overhead** | **<5 ms P50** | Inside the v1 batch real-time budget |

**PERF:** for the common case (well-cached registry, no replay, default policy), Router overhead is <2 ms P50.

**PERF:** the heavy operation is Stage 6 ranking when many candidates are eligible. With v1's provider scope (local + IBM + Braket + IonQ), candidate count is typically 4-8. v1.1 adds cloud GPU and cloud cognition; candidate count grows but stays under 20.

## 4.10 — How the Router interacts with the other layers

**Upstream:** Section 3's task grammar layer hands the Router a validated `PhysicsTask`. Section 2's Trinity Core Solver subsystem hands the Router a `CandidateAnswer`. The Router takes both to make its decision.

**Downstream:** Section 2's Trinity Core Orchestrate subsystem receives the `RoutingDecision` and executes via the chosen `BaseProviderClient`. Failures propagate back to the Router for failover handling per Section 4.5.

**Sibling concern — Section 6's verification gate:** when cross-provider wobble is required (the Result's third disagreement axis), Section 6 may instruct the Router to choose a *second* provider for the verification run. This is a separate `RoutingRequest` with `excluded_providers` set to the primary's choice; Section 4 doesn't know it's the verification run.

**Sibling concern — Section 8's dev ops backdoor:** ops users can query the Router's `ProviderRegistry` directly via the dev ops API to see current provider health, queue depth, recent failover history, and routing decisions. The Router exposes a read-only inspection interface for this purpose.

**Sibling concern — Section 7's safety gate:** frontier-physics permission checks live in Section 7. The Router re-checks defensively in Stage 4 but trusts Section 7's primary check.

```
=== SECTION 4 COMPLETE — AWAITING ADAM REVIEW ===
```

---

# Section 5 — The Front Door (REST, WebSocket, CLI, MCP)

## What Section 5 covers

Section 5 specifies how external clients reach Phoenix. Four protocols are supported in v1 (Section 1 Decision: REST + WebSocket + CLI + MCP), and they all converge on the same internal contract — the task grammar layer (Section 3). This section specifies each protocol's surface, how they share authentication and rate limiting, how streaming differs from request/response, and the single architectural rule that makes four protocols sustainable rather than four products to maintain: *the REST API is canonical, everything else is a thin adapter*.

Decisions referenced trace back to Section 1 (notably 16 on audit logging, 22 on OpenTelemetry, 25 on co-authors as MCP clients, 26-28 on real-time scaling) and Section 3 (the task grammar layer the front door delegates to).

Open design tensions encountered while writing Section 5 are flagged with `[OPEN: ...]` and tracked in Section 11.

## 5.1 — Why four protocols, and why one canonical surface

Each protocol exists because there's a real audience that prefers it. REST is what every Python integrator, every agent framework, and every general-purpose HTTP client speaks natively. WebSocket is what real-time dashboards, live experiment visualizations, and the v2 streaming-real-time API mode require. CLI is what scripted automation, CI pipelines, and operators reach for. MCP is what Claude Code, Cursor, Cline, and other agentic IDEs use natively, with no SDK required.

The trap is treating each as its own product. Four protocols × full feature surface = four codebases with subtly different behaviors, four documentation sets, four bug surfaces, four compliance audits. The way out is a *canonical contract* that all four share.

**Phoenix's canonical contract is the REST API.** Every other protocol is a thin adapter over REST. The CLI calls REST. The MCP server calls REST (per the pattern dr-frank-and-eddy already established with `frankenstein/mcp_server/server.py`: "Tools call the FastAPI backend over HTTP — same pattern as the CLI"). WebSocket exposes a streaming view of REST resources, not a parallel surface. This means new functionality lands once — in REST — and the other three protocols inherit it via their adapters with at most a small wiring change.

The architectural cost is one extra hop for non-REST clients (CLI → REST, MCP → REST, WebSocket → REST). The benefit is a single source of truth for behavior, auth, rate limiting, audit logging, and the task grammar contract. **PERF:** the extra hop is sub-millisecond on local Phoenix installs because it's an in-process HTTP loopback (or, in production, a Unix domain socket).

## 5.2 — The canonical REST surface

Phoenix's REST API is versioned at `/v1/...` and follows OpenAPI 3.1. The full schema ships as part of the release artifact at `phoenix/openapi.yaml` and is also served live at `GET /v1/openapi.json` for tooling.

**Core endpoints (synchronous request/response):**

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/tasks` | Submit a task. Body: structured JSON or grammar tokens (Section 3.3, 3.4). Returns `Result` for batch tasks or `task_id` for async/streaming. |
| `GET` | `/v1/tasks/{task_id}` | Fetch a task's status and (if complete) its Result. |
| `GET` | `/v1/tasks/{task_id}/provenance` | Fetch the full provenance trace for a completed task. |
| `POST` | `/v1/tasks/{task_id}/replay` | Re-execute a historical task in `strict` or `replay` reproducibility mode. |
| `GET` | `/v1/health` | Liveness/readiness probe. Returns version, vendored substrate version, calibration profile hash, last drift-check status. |
| `GET` | `/v1/calibration/status` | Latest drift-monitoring status across all three detectors (Section 1 Decision 17). |
| `GET` | `/v1/providers` | List currently-known providers, their health state, queue depth, last calibration timestamp. |
| `GET` | `/v1/providers/{provider_id}/backends` | List backends available on a specific provider. |

**Adapter and identity endpoints:**

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/adapters` | Load a LoRA adapter (privileged). Triggers Section 3.5 validation. |
| `GET` | `/v1/adapters` | List currently-loaded adapters with fingerprints and capabilities. |
| `DELETE` | `/v1/adapters/{adapter_id}` | Unload an adapter (privileged). |
| `POST` | `/v1/identity/enroll` | Enroll an install into an org via HKDF-derived subkey (Section 1 Decision 11). |
| `GET` | `/v1/identity` | Return the current install's fingerprint and org membership status. |

**Audit and observability endpoints:**

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/audit/events` | Stream the structured audit log (filterable by actor, time window, event type). |
| `GET` | `/v1/audit/ledger/{ledger_index}` | Fetch a specific ledger entry by index. |
| `GET` | `/v1/audit/ledger/verify` | Walk the hashchain and report integrity (Omega Ledger pattern). |

**Dev-ops endpoints:**

These live under `/v1/admin/...` and require an actor with `admin` permission. Detailed in Section 8 (dev ops backdoor).

**Standard error envelope:** all REST errors use the same JSON envelope:

```json
{
  "error": {
    "code": "schema_validation_error",
    "message": "physics_context.mass_kg must be positive",
    "path": "physics_context.mass_kg",
    "request_id": "req_abc123",
    "documentation_url": "https://phoenix.docs/errors/schema_validation_error"
  }
}
```

The `code` matches the typed exception names from Section 3.7. The `request_id` correlates with audit-log entries. The `documentation_url` points to per-error pages in Phoenix's docs that explain root causes and recovery.

**Pagination convention.** Both list endpoints use **cursor-based pagination** with the cursor opaque to the client (server-encoded). Cursor-based works correctly on both SQLite and Postgres without semantic differences and handles concurrent inserts during pagination correctly — offset-based would double-count or skip when the underlying set changes mid-page. The opaque encoding lets the server change the cursor format without breaking client integrations. Every paginated response carries `next_cursor: str | null` and `prev_cursor: str | null` in its envelope; clients pass these unchanged to subsequent requests. Resolved from Section 11.3.1.

## 5.3 — WebSocket surface

WebSocket is for streaming use cases. Phoenix v1 ships three WebSocket endpoints, all backed by the same task lifecycle as REST.

**`/v1/ws/tasks/{task_id}/stream`** — subscribe to a long-running task's progress. Server emits typed events:

- `task.started` — task accepted, Trinity Core pipeline kicked off.
- `task.solver.complete` — Solver subsystem produced `CandidateAnswer`. Includes solver_id and error_bar_solver.
- `task.control.complete` — Control subsystem produced `VerifiedAnswer`. Includes DPDResult summary and error_bar_control.
- `task.orchestrate.progress` — Orchestrate subsystem update; for cloud providers, streams provider queue position changes.
- `task.verification.promoted` — verification gate promoted depth from rung R_n to R_n+1. Includes `from_rung`, `to_rung`, `promoting_axis`, `reason`. See Section 6.6.
- `task.verification.demoted` — verification gate demoted depth (axis budget already satisfied). Includes `from_rung`, `to_rung`, `reason`. See Section 6.6.
- `task.complete` — final Result available. Client can fetch via REST `/v1/tasks/{task_id}`.
- `task.failed` — error envelope identical to REST's standard error format.

**`/v1/ws/calibration/drift`** — subscribe to drift-monitoring events. Server emits a `drift.alert` event each time any of the three detectors fires (Section 1 Decision 17). Useful for ops dashboards.

**`/v1/ws/standing/{computation_id}`** — v2 streaming-real-time mode (Section 2.8). Bidirectional: client streams parameter updates and probe-result subscriptions; server streams continuous result updates. v1 reserves the endpoint shape but does not implement; clients calling it in v1 get a `503 NotImplementedYet` with `Phoenix-Version: 1.0` in the response. v1.x and v2 implement progressively.

**Authentication:** WebSocket connections authenticate via a short-lived bearer token obtained from REST. The client calls `POST /v1/identity/ws-token` (with full Actor signature), receives a token valid for 60 seconds, and uses it as the `Authorization: Bearer ...` header on the WS handshake. The token is single-use; once the WS connection establishes, the token is consumed. **SAFETY:** this avoids the long-running auth token problem — a leaked WS token expires fast and can't be replayed.

**SAFETY:** WebSocket connections respect the same rate limits as REST (Section 5.6) but with adjusted units — instead of "requests per minute" it's "active connections" and "events per second per connection." Misbehaving clients that subscribe to thousands of tasks get throttled.

**PERF:** WebSocket frame overhead is ~6 bytes per event. Phoenix's task lifecycle emits ~5-10 events per task, so per-task WS bandwidth is negligible. The drift WS may emit hundreds of events per day across detectors but is still trivial.

## 5.4 — CLI surface

The Phoenix CLI is `phoenix` (pip-installable) or the standalone-binary equivalent. It's a thin wrapper around REST, just like dr-frank-and-eddy's `frank3` CLI wraps its FastAPI backend.

**Top-level commands:**

```
phoenix task submit <task-spec>          # POST /v1/tasks
phoenix task get <task-id>               # GET /v1/tasks/{task_id}
phoenix task replay <task-id> --mode=strict  # POST /v1/tasks/{task_id}/replay
phoenix task stream <task-id>            # WebSocket subscribe, prints events to stdout

phoenix lora load <adapter-path>         # POST /v1/adapters
phoenix lora list                        # GET /v1/adapters
phoenix lora unload <adapter-id>         # DELETE /v1/adapters/{adapter_id}

phoenix identity show                    # GET /v1/identity
phoenix identity enroll <org-config>     # POST /v1/identity/enroll

phoenix providers list                   # GET /v1/providers
phoenix providers backends <provider>    # GET /v1/providers/{id}/backends

phoenix audit tail                       # GET /v1/audit/events (streaming)
phoenix audit verify                     # GET /v1/audit/ledger/verify

phoenix calibration status               # GET /v1/calibration/status
phoenix calibration run                  # Force a drift cycle now (admin only)

phoenix admin ...                        # Section 8 dev-ops commands
```

**Output formats:** every CLI command supports `--output=json` (default for scripting), `--output=text` (human-readable), and `--output=table` (column-aligned). `--output=text` is the default when stdout is a TTY; `--output=json` when piped.

**Configuration:** `~/.phoenix/config.yaml` carries the Phoenix install fingerprint, default REST endpoint, default reproducibility mode, default org membership. Per-command flags override config; environment variables (`PHOENIX_REST_URL`, `PHOENIX_REPRODUCIBILITY_MODE`, etc.) override config but are overridden by flags.

**SAFETY:** the CLI never accepts API keys via command-line argument (visible in `ps` output and shell history). Authentication is via the local install's keystore (DPAPI/Keychain/libsecret) — the CLI signs requests using the install's Ed25519 key, with no plaintext secret ever in user-visible state.

**Standalone binary distribution:** Section 1 Decision 29 commits to a Nuitka-compiled standalone binary as one of three release artifacts. The CLI binary is the same `phoenix` command but bundled with all Python dependencies. **The standalone binary bundles the Phoenix daemon by default**, with a `--external-daemon` flag for users who want to connect to an existing daemon instead. The standalone binary's audience is non-developer users who want zero deployment friction; bundling means double-clicking the binary just works (the binary boots NATS JetStream, the Phoenix daemon, and the CLI in one process tree). Sophisticated users running a long-lived daemon separately can pass `--external-daemon https://phoenix.host:8003` and use the binary as a thin client; pip-installed Phoenix is also a valid path for that audience. The bundled-binary's larger size (~150-250 MB depending on platform) is an acceptable cost for the zero-friction first-run experience. Resolved from Section 11.3.3.

## 5.5 — MCP surface

Phoenix's MCP server is the front door for agentic IDEs. It follows the pattern from dr-frank-and-eddy's `frankenstein/mcp_server/server.py` exactly: MCP tools are thin wrappers that call REST.

**Transport:** stdio (default, for Claude Code / Cursor / Cline) and HTTP+SSE (for browser-based MCP clients).

**Tool surface for v1 (mirrors the REST endpoints):**

The v1 MCP tool set covers the canonical task lifecycle:
- `phoenix_task_submit` — submit a task. Wraps `POST /v1/tasks`.
- `phoenix_task_get` — retrieve a task's status/result. Wraps `GET /v1/tasks/{task_id}`.
- `phoenix_task_replay` — replay a historical task. Wraps `POST /v1/tasks/{task_id}/replay`.
- `phoenix_provenance_get` — fetch a task's provenance trace. Wraps `GET /v1/tasks/{task_id}/provenance`.
- `phoenix_providers_list` — list providers and health. Wraps `GET /v1/providers`.
- `phoenix_calibration_status` — drift-monitoring status. Wraps `GET /v1/calibration/status`.
- `phoenix_health` — system health. Wraps `GET /v1/health`.
- `phoenix_audit_verify` — verify ledger integrity. Wraps `GET /v1/audit/ledger/verify`.

**Tool surface for v6.6 Sanskrit memory (vendored from dr-frank-and-eddy):**

When Phoenix vendors the Sanskrit MCP connector at v6.6, the tool surface includes the seven Sanskrit memory tools verbatim: `phoenix_memory_compress`, `phoenix_memory_decompress`, `phoenix_memory_recall`, `phoenix_memory_codec_status`, `phoenix_memory_grammar_generate`, `phoenix_memory_grammar_parse`, `phoenix_memory_propose_rule`. Vendored unchanged from `frankenstein/mcp_server/server.py`'s v6.6 plugin surface; tool names are renamed with `phoenix_` prefix to avoid collision when both Phoenix and dr-frank-and-eddy MCP servers are configured in the same client.

**Actor authentication via MCP:** the dr-frank-and-eddy v6.6 pattern requires `actor_payload` (HMAC-SHA256, 5-minute window) on every tool call. Phoenix preserves this exactly. The MCP client (Claude Code, Cursor, etc.) is configured at install time with the user's Phoenix install fingerprint and signing key path; every tool call signs the parameters before submission. Unsigned or wrong-signature tool calls return a typed error and don't reach the REST backend.

**Tool-level rate limiting:** the MCP server enforces tool-call rate limits per actor, mirroring the REST limits. **PERF:** MCP overhead is minimal — JSON encode/decode plus a local HTTP loopback to the REST backend. <2 ms P50 per tool call beyond what the underlying REST takes.

**SAFETY:** the MCP server runs in a separate process from the REST backend, with no direct filesystem or network access beyond the loopback. A misbehaving MCP transport cannot escalate into the REST backend's privileges. Mirrors the subprocess isolation pattern from Section 3.5 (LoRA adapter sandbox).

## 5.6 — Authentication, rate limiting, audit logging across protocols

All four protocols share the same security primitives. This is what makes the canonical-REST architecture sustainable.

**Authentication:** every request, on every protocol, carries an Actor signature (HMAC-SHA256, 5-minute window, per-install Ed25519 key per Section 1 Decision 10, or org-derived subkey per Decision 11). The signature wraps the request payload — REST body, WebSocket message, CLI request, MCP tool parameters all sign the same way. Constant-time signature compare per Section 1 Decision 12. Failures raise `AuthError` (Section 3.7) and never reach the task grammar layer.

**Rate limiting:** Phoenix runs a token-bucket rate limiter per actor across all four protocols. The default is 100 requests/minute for unauthenticated requests (which can only hit `/v1/health`), 1,000 requests/minute for authenticated actors, and unlimited for admin actors. Org-level rate limits aggregate across all installs in the org. **PERF:** the rate limiter is in-memory with periodic write-through to the state backend (SQLite/Postgres per Section 1 Decision 31); a single rate-check is sub-millisecond. Cross-protocol unification means a misbehaving CLI script doesn't get to bypass rate limits by switching to MCP.

**Audit logging:** every request, on every protocol, emits a structured event per Section 1 Decision 16. The event includes: actor fingerprint, protocol (rest/ws/cli/mcp), endpoint or tool name, parameters hash (not raw parameters — those go in the ledger entry if a task results), response status, request_id, request duration. Events flow to Phoenix's native event log; the OpenTelemetry adapter (Section 1 Decision 22) exports them to OTLP-compatible backends.

**Request correlation:** every request gets a `request_id` (UUID v7 for time ordering). The request_id is in REST response headers (`X-Phoenix-Request-Id`), in WebSocket events, in CLI output (when `--verbose`), in MCP tool responses, in audit-log events, and in any task's provenance trace. This is the thread that lets a debugging human or an investigator follow a single request across all four protocols and into the ledger.

## 5.7 — Versioning across protocols

Section 1 Decision 21 commits to three release artifacts (pip, Docker, standalone binary). Each artifact ships a single Phoenix version that all four protocols share. There is no separate "REST v1" / "MCP v2" — the REST API version *is* the Phoenix version, and the other protocols inherit it.

Backwards compatibility:

- **REST major version (`/v1/...`)** is stable for the lifetime of that major version. Phoenix supports the previous major version for one release cycle after a new one ships.
- **WebSocket events** add new event types in minor versions; existing events never change shape within a major version.
- **CLI commands and flags** add new commands and flags in minor versions; existing commands' contracts are stable within a major version. Removed commands raise a clear error one minor version before the removal.
- **MCP tools** follow the same contract as REST endpoints they wrap. New tools may be added; existing tools' parameter schemas are stable within a major version.

The single exception is **error code stabilization**: error codes within the standard error envelope (Section 5.2) are stable across the entire major version. Adding new error codes is a minor change; renaming or removing error codes is a major change. This matters because integrators write retry/recovery logic against error codes, and breaking that surface is the kind of change that erodes trust in the middleware.

## 5.8 — Performance budget

The front door's latency budget is part of Phoenix's overall real-time posture (Section 1 Decisions 26-28). All four protocols share the same backend; the differences are in the wire format and transport.

| Protocol | Per-request P50 overhead | Notes |
|---|---|---|
| REST | <3 ms | JSON encode/decode + auth + rate-check + audit emit |
| WebSocket | <1 ms per event | Pre-established connection; auth done at handshake |
| CLI | <5 ms (in-process) / <8 ms (separate daemon) | CLI parse + REST call + output format |
| MCP | <2 ms (loopback) | JSON encode + REST call |

**PERF:** a typical Phoenix solve has front-door overhead in the single-digit-millisecond range, well inside the v1 batch real-time target (Section 1 Decision 26: 10-100 ms loops). The dominant cost is the actual physics (Trinity Core), not the front door.

**PERF:** the CLI's standalone-binary path adds ~50-100 ms of startup overhead on first invocation if it boots a fresh Phoenix daemon. Long-running daemons (typical) amortize this away.

## 5.9 — How the front door interacts with the other layers

**Downstream:** every protocol delegates to the task grammar layer (Section 3) for input validation, then through Trinity Core (Section 2), the Router (Section 4), the verification gate (Section 6), and the safety gate (Section 7). The front door owns transport and auth, not validation or execution.

**Sibling concern — Section 6's verification gate:** WebSocket task lifecycle events include progress updates from the verification gate when wobble checks are running. The format is fixed in 5.3; Section 6 specifies what events are emitted.

**Sibling concern — Section 7's safety gate:** `FrontierPhysicsRefused` errors propagate through the standard error envelope on REST/MCP and through `task.failed` events on WebSocket. The actual permission check is in Section 7.

**Sibling concern — Section 8's dev ops backdoor:** `/v1/admin/...` endpoints route through Section 8's authorization layer rather than Section 7's standard actor flow. Admin tokens have different lifecycle and audit semantics.

**Sibling concern — Section 9's reference admin client:** the 5-co-author client (Section 1 Decision 25) is itself an MCP client of Phoenix. It uses `phoenix_task_submit` and friends like any other MCP-aware agent would. Phoenix has no special path for it.

```
=== SECTION 5 COMPLETE — AWAITING ADAM REVIEW ===
```

---

# Section 6 — The Verification Gate (Wobble Protocol Orchestration)

## What Section 6 covers

Section 6 specifies the layer that turns Phoenix's mandatory wobble verification (Section 1 Decision 13) into a concrete protocol. Section 2 sketched the three disagreement axes inside Trinity Core (cross-precision in Solver, cross-control in Control, cross-provider in Orchestrate). Section 6 specifies how those three signals get *composed* into a final agreement metric, how the adaptive-depth dial actually works, and what events flow out to the front door's WebSocket stream while verification is in progress.

The verification gate sits *around* Trinity Core, not inside it. Trinity Core's three subsystems each contribute their own disagreement signal as a side-effect of running; the verification gate orchestrates *how many cross-checks fire*, *which axes get exercised*, and *how the combined result envelope reads* to the user. Trinity Core is the engine; the verification gate is the discipline that runs the engine multiple times in just the right way.

Decisions referenced trace back to Section 1 (notably 13-14 on mandatory verification and adaptive depth, 15 on hashchained provenance, 17 on drift monitoring), Section 2 (the three Trinity Core subsystems and the `Result` envelope), and Section 4 (the Router which can be invoked twice for cross-provider checks).

Open design tensions encountered while writing Section 6 are flagged with `[OPEN: ...]` and tracked in Section 11.

## 6.1 — Why the verification gate is its own layer

The wobble protocol could in principle live inside Trinity Core's Orchestrate subsystem — Orchestrate is the last layer to run, so it could trigger the cross-checks at the end. Phoenix splits it out as a separate layer for three reasons.

First, *adaptive depth is a policy decision, not a physics one*. The user's stated `max_error_bar` translates into "run cross-precision but skip cross-control" or "run all three axes" or "run all three axes plus a fourth replication for tightest bars." That decision logic should not be in Trinity Core, the same way provider-selection logic should not be in Trinity Core (Section 4.1). Both are operational policy.

Second, *the verification gate needs to be able to call back into Trinity Core multiple times*. A single solve goes through Trinity Core once. A wobble-verified solve may go through Trinity Core 2-6 times depending on depth: twice through Solver at different grid resolutions, twice through Control at different probe strengths, twice through Router+Orchestrate at different providers. Trinity Core's subsystems are designed to be called repeatedly with different parameters; the verification gate is the layer that does the calling.

Third, *the verification gate is where provenance composition happens*. Each Trinity Core run produces its own contribution to the final `Result.provenance`. The verification gate stitches them together into the final hashchained ledger entry per Section 1 Decision 15. Trinity Core's individual runs don't know they're part of a multi-run verification; the gate knows.

## 6.2 — The vendored substrate: typed disagreement findings

Phoenix vendors the typed wobble substrate that already exists in dr-frank-and-eddy v6.6 at `wobble/disagreement_types.py` and `wobble/disagreement_classifier.py`. This is significantly more sophisticated than the scalar wobble formula referenced in earlier sections of this document — Phoenix gets the upgrade for free.

**Vendored types (verbatim from `wobble/disagreement_types.py`):**

```python
class DisagreementType(Enum):
    """Disagreement classification for wobble findings."""
    CONTRADICTION = "contradiction"        # Factual disagreement, ground-truth-lookupable
    AMBIGUITY = "ambiguity"                # Multiple valid interpretations
    INCOMPATIBLE_FRAMES = "frames"          # Different reference frames / units
    PARTIAL_CONVERGENCE = "partial"        # Subset agrees, others diverge
    UNKNOWN = "unknown"                    # Classifier uncertain

class SuggestedAction(Enum):
    """Recommended downstream action for a finding."""
    ACCEPT = "accept"                      # Disagreement within tolerance, ship the result
    RERUN_DEEPER = "rerun_deeper"          # Tighten cross-checks and try again
    HUMAN_REVIEW = "human_review"          # Surface to operator
    REJECT = "reject"                      # Disagreement exceeds tolerance, fail the task

@dataclass
class DisagreementFinding:
    """The wobble finding with structured uncertainty preserved (DO NOT COLLAPSE)."""
    agreement_type: DisagreementType
    classifier_confidence: float           # 0.0 to 1.0
    classifier_rationale: str
    classifier_evidence: List[str]         # specific differences that drove the call
    distance_matrix: np.ndarray            # full pairwise distances, never collapsed
    frame: Frame                           # context: what was compared, in what frame
    provenance: List[ProvenanceEntry]      # which runs contributed
    suggested_action: SuggestedAction
    wobble_score: float                    # backward-compat scalar
    metadata: Dict[str, Any]
```

The critical design property — flagged in the vendored code as "DO NOT COLLAPSE" — is that the full `distance_matrix` is preserved alongside the scalar `wobble_score`. The scalar exists for backward compatibility with consumers that still expect a number; the matrix exists because collapsing pairwise disagreements to a scalar throws away information about *which axes disagreed* and *how*. Phoenix's verification gate writes both into provenance, and downstream consumers (the dev-ops backdoor, audit log, replay path) can choose either depending on what they need.

This is dr-frank-and-eddy's resolution of an architectural limitation flagged in earlier project notes — the cognition wobble's "the wobble loop collapses rich disagreement to a scalar." v6.6 fixed it. Phoenix inherits the fix.

**What Phoenix builds on top:**

The cognition-wobble's `DisagreementFinding` was designed for *LLM responses comparing semantic content*. Phoenix's three-axis physics wobble produces disagreements between *numerical results*. The vendored `DisagreementType` enum needs an extension for the physics case:

```python
class DisagreementType(Enum):
    # Vendored values (cognition wobble) — kept for cross-compat
    CONTRADICTION = "contradiction"
    AMBIGUITY = "ambiguity"
    INCOMPATIBLE_FRAMES = "frames"
    PARTIAL_CONVERGENCE = "partial"
    UNKNOWN = "unknown"

    # Phoenix physics-wobble extensions
    CONVERGED = "converged"                # All axes agree within tolerance
    NUMERICAL_DRIFT = "numerical_drift"    # Cross-precision disagrees beyond grid-error expectation
    BACKACTION_SENSITIVE = "backaction"    # Cross-control disagrees; result depends on probe strength
    PROVIDER_DIVERGENT = "provider"        # Cross-provider disagrees beyond shot-noise expectation
    DEGRADED = "degraded"                  # All checks complete but one or more flagged drift_warning
    DEGRADED_BUDGET_BOUND = "degraded_budget"  # Verification truncated by cost ceiling (Section 4.7)
```

The `CONVERGED` value is what most successful Phoenix solves return. The four `*_DIVERGENT` values pinpoint *which axis* disagreed, so a downstream consumer (or the ops dashboard, or an autonomous agent) can decide what to do without re-running the full verification.

## 6.3 — The three-axis verification protocol

When a `PhysicsTask` enters the verification gate, the gate plans which axes to exercise based on the task's `tolerance.max_error_bar`. Then it orchestrates the runs.

**Axis 1 — Cross-precision (inside Solver).** Run Trinity Core's Solver subsystem twice with the same physics but different grid resolutions. Default: `N` grid points and `2N` grid points. The two `CandidateAnswer` objects produced are compared. Disagreement contributes `error_bar_solver` and a row to the final distance matrix.

**Axis 2 — Cross-control (inside Control).** Pass each `CandidateAnswer` through Trinity Core's Control subsystem with two different DPD probe strengths. Default: `ε₁ = 0.1` (weak) and `ε₂ = 0.5` (information-optimal). The two `VerifiedAnswer` objects produced are compared. Disagreement contributes `error_bar_control` and a row to the distance matrix.

**Axis 3 — Cross-provider (inside Orchestrate).** Submit the verified bundle to two providers — typically the chosen primary plus the local simulator. Both run the same circuit; the two `Result` candidates are compared. Disagreement contributes `error_bar_orchestrate` and a row to the distance matrix.

The full distance matrix has at most 6 rows (2 per axis), all preserved in provenance. The `wobble_score` scalar is computed as the standard wobble formula `sqrt(Var(matrix))` over the upper triangle, exactly the way the cognition wobble does it. The `agreement_type` is classified by inspecting which axes contributed which disagreements — `NUMERICAL_DRIFT` if Axis 1 dominated, `BACKACTION_SENSITIVE` if Axis 2 dominated, and so on.

**Combined error bar:** Section 2.2 specified quadrature: `error_bar = sqrt(error_bar_solver**2 + error_bar_control**2 + error_bar_orchestrate**2)`. The open question flagged there (whether quadrature is correct given non-independence of layer errors) remains open and is addressed in Section 11.

**WobbleAxis Protocol — parameterization for domain extensions.** The verification gate (`phoenix/verification/gate.py`) does not hard-code the three quantum axes as named methods. Instead, the gate accepts a list of `WobbleAxis` Protocol implementations and orchestrates whichever axes the active domain registers. v1 ships three concrete `WobbleAxis` impls in `phoenix/verification/wobble_axis.py`: `CrossPrecisionAxis` (Axis 1), `CrossControlAxis` (Axis 2), `CrossProviderAxis` (Axis 3). The Protocol contract is small and discipline-bearing:

```python
class WobbleAxis(Protocol):
    """A single disagreement axis the verification gate can orchestrate."""

    @property
    def name(self) -> str: ...
    """Stable identifier (e.g. 'cross_precision') used in provenance + the distance matrix."""

    def applies_to(self, task: PhysicsTask) -> bool: ...
    """Does this axis exercise meaningfully on the given task? (e.g. CrossProviderAxis
    returns False when the task's reproducibility mode forces a single-provider replay.)"""

    def run(self, task: PhysicsTask, depth: RungDepth) -> AxisResult: ...
    """Run the axis at the requested depth; return the row that lands in the distance matrix
    plus the axis's contribution to the combined error bar."""
```

This parameterization is the design intent for Phase 5 (verification gate). It enables Phoenix v1.x extensions — specifically the perception harness extension locked at v1.1 (`PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md`) — to register their own `WobbleAxis` impls (`CrossModalityAxis`, `CrossFrameAxis`, `CrossCanonicalAxis` per the perception plan's Phase 20) without forking the gate. Same gate, same machinery, different axes. **SAFETY:** the gate does NOT auto-discover axes; each domain registers its impls explicitly at startup, so a forgotten axis fails fast at registration rather than silently producing under-verified solves. The parameterization also makes Section 11.14.6 (perception verification axes count, currently three) trivially extensible if a fourth perception axis emerges in field testing — no architectural revision required, just a registration call.

## 6.4 — Adaptive depth: how `max_error_bar` becomes a depth decision

Section 1 Decision 14 committed to "non-zero rigor on every solve, but depth is determined by the user's stated error-bar tolerance — tighter bars demand more checks." Section 6.4 specifies the actual decision rule.

**The depth dial has five rungs**, each adding axes or replicates:

| Rung | Axes exercised | Wall-clock multiplier | Indicative `max_error_bar` |
|---|---|---|---|
| **R1 — Single-precision floor** | Single Solver run + single Control run + single Orchestrate run. Three subsystems each ran once; no cross-axis comparison. | 1× (baseline) | `max_error_bar > 1e-2` |
| **R2 — Cross-precision only** | Adds Axis 1. Two grid resolutions in Solver. Single Control + single Orchestrate. | ~1.7× | `1e-3 < max_error_bar ≤ 1e-2` |
| **R3 — Two axes** | Adds Axis 2. Two grids and two probe strengths. Single Orchestrate. | ~3× | `1e-4 < max_error_bar ≤ 1e-3` |
| **R4 — Three axes (default for instrument-grade)** | All three axes. Two grids, two probe strengths, two providers. | ~5-6× | `1e-6 < max_error_bar ≤ 1e-4` |
| **R5 — Three axes plus replication** | All three axes plus a replicated independent run for noise estimation. | ~10× | `max_error_bar ≤ 1e-6` |

Note the floor: even R1 has *non-zero rigor* in the sense that all three Trinity Core subsystems still run (Solver predicts, Control verifies via DPD, Orchestrate executes). What R1 skips is the *cross-axis* comparison. That's consistent with Section 1 Decision 13: "Mandatory means non-zero rigor on every solve; it does *not* mean a fixed protocol."

**The mapping from `max_error_bar` to rung is not a hard table.** It's a starting heuristic the verification gate uses; the gate can adjust dynamically based on observed disagreement. If R3 is initially selected and Axis 1 immediately shows disagreement larger than `max_error_bar / 2`, the gate auto-promotes to R4 because the cross-precision axis already used up half the budget. Conversely, if R4 is selected and Axes 1+2 both report agreement at <10% of the budget, the gate may skip Axis 3 because the budget is already comfortably met. This is the *adaptive* part.

**Promotion criteria:** if any axis's measured disagreement exceeds half the remaining error budget, promote one rung. The gate may promote at most twice per task (R3 → R4 → R5 is allowed; further promotion is not). Caps prevent runaway compute when a fundamentally noisy task keeps promoting.

**Promotion vs cost ceiling.** Section 4.7's per-solve cost ceiling is a hard bound on promotion. Before invoking the next axis, the gate calls `Router.estimate_axis_cost(axis, candidates)` and compares against the solve's remaining cost budget. If promotion would exceed the ceiling, the gate skips the promotion and records `agreement_type=DEGRADED_BUDGET_BOUND` with a `budget_bound_skipped_axis` field in the Result's provenance. The result still ships with whatever rigor was reachable inside the budget. **SAFETY:** this fails *forward* (ship-with-degraded-tag) rather than failing the solve outright, because losing the work paid for so far is operationally worse than a transparent degraded result. Users wanting the full verification re-submit with a higher `cost_ceiling_usd` or with explicit per-axis enable flags.

**Demotion criteria:** if cumulative disagreement across exercised axes is below 10% of `max_error_bar` and the user did not explicitly request "always exercise all three axes" (a flag on the task), the gate may demote. Demotion happens at most once per task and only at the boundary between Axis 1 and Axis 2, or Axis 2 and Axis 3 — Axis 1 always runs.

**[OPEN: should the rung table be configurable per Phoenix install? Some users may want to enforce R4 always; others may want R1 to be allowed to skip the local-simulator Orchestrate run too. v0 specifies the rungs as a fixed framework with promotion/demotion logic; per-install customization deferred to Section 11.]**

**Reproducibility-mode interaction:** in `strict` and `replay` modes (Section 1 Decisions 19-21), the rung selected on the original run is *recorded in the ledger entry* and replay uses the same rung. Replay never re-decides depth. This guarantees that a strict-mode result is reproducible to bit-exactness — including the verification depth.

## 6.5 — Drift integration: how wobble and drift detection cooperate

Section 1 Decision 17 commits to continuous calibration drift monitoring with three detectors (Tier-1 analytical battery, ML statistical, cross-version). Section 4.6 specified that drift signals feed *back into routing* (a drifted provider gets a lower estimated_fidelity score). Section 6.5 specifies the sibling integration: drift signals also feed into *verification depth*.

**Drift-aware promotion:** if the active calibration profile has any detector currently in `drift_warning` state at task submission time, the verification gate auto-promotes one rung beyond what `max_error_bar` would normally select. R1 → R2; R3 → R4; R5 stays at R5. The drift state is recorded in the task's provenance. **SAFETY:** this is a defense-in-depth measure — if drift is suspected, exercise more axes to either confirm the result is fine despite drift, or surface a `DEGRADED` agreement_type to the user.

**Drift-aware result tagging:** if any detector is in `drift_warning` state during a verification run, the final `Result.agreement_type` is `DEGRADED` regardless of whether the cross-axis checks all converged. The user gets the result but knows it carries provisional standing until calibration is reconfirmed. The dev-ops backdoor surfaces an alert per Section 1 Decision 17.

**Drift state in replay:** the drift state at the time of the original run is recorded in the ledger entry. Replay must verify that the *same* drift state is reproducible — which means replay reads the recorded drift signals from the ledger, not the current live drift state. Otherwise replays could spuriously diverge if drift state changes between original and replay.

## 6.6 — Streaming verification events to WebSocket

Section 5.3 specified the WebSocket task lifecycle event types. Section 6.6 specifies what each event carries when wobble is in progress.

| Event | Verification-gate fields |
|---|---|
| `task.started` | `rung_selected: str` (R1..R5), `axes_planned: List[str]` |
| `task.solver.complete` | `axis_1_disagreement: float`, `axis_1_status: "converged" \| "wobble"` |
| `task.control.complete` | `axis_2_disagreement: float`, `axis_2_status: "converged" \| "wobble" \| "skipped"` |
| `task.orchestrate.progress` | `axis_3_provider: str`, `axis_3_disagreement: float \| null` |
| `task.verification.promoted` (new) | `from_rung: str`, `to_rung: str`, `promoting_axis: str`, `reason: str` |
| `task.verification.demoted` (new) | `from_rung: str`, `to_rung: str`, `reason: str` |
| `task.complete` | full `DisagreementFinding`, `agreement_type`, `combined_error_bar`, `wobble_score` |

Verification-gate events let dashboards and ops monitors track depth decisions in real time, which is essential for diagnosing performance issues ("why did this batch of 100 tasks take 3× longer than usual" → "all 100 promoted to R5 because of a drift_warning state on the active calibration profile").

**PERF:** the events add ~20 bytes each over the base WebSocket frame overhead. Even an R5 task that emits ~12 events total is sending well under 1 KB to the WebSocket subscriber. Negligible at any reasonable concurrency.

## 6.7 — Provenance composition

Section 1 Decision 15 commits to hashchained provenance with bit-exact replay. Section 6.7 specifies what provenance fields the verification gate contributes to each ledger entry.

**Per-task provenance from the verification gate:**

```python
@dataclass
class VerificationProvenance:
    rung_selected: str                          # R1..R5
    axes_exercised: List[str]                   # Subset of ["solver_precision", "control_probe", "orchestrate_provider"]
    promotion_history: List[Dict[str, Any]]     # When/why depth was adjusted
    demotion_history: List[Dict[str, Any]]
    finding: DisagreementFinding                # Full vendored type with distance_matrix
    drift_state_at_submit: Dict[str, Any]       # Per-detector status snapshot
    rung_decision_inputs: Dict[str, Any]        # What max_error_bar / drift state / config drove the rung choice
    per_axis_runs: List[RunRecord]              # Hash + timing + provider + parameters of every Trinity Core run that contributed
```

The full `VerificationProvenance` lands in the ledger entry alongside the routing provenance (Section 4) and the Trinity Core trace (Section 2). The hashchain hash is computed over the canonical JSON of all three concatenated. **PERF:** provenance composition adds ~1 ms to the post-verification path; it's not on the user's wall-clock critical path because it happens after the result has been computed and is being written to the ledger asynchronously.

**Replay verification:** in `replay` mode (Section 5.2's `POST /v1/tasks/{task_id}/replay` endpoint), the replay re-executes every `RunRecord` and verifies hash-equality of each. Mismatches at any layer raise a typed `ReplayDivergence` error pinpointing which run diverged.

## 6.8 — Failure modes

The verification gate has a small set of failure modes, all surfaced as typed errors per Section 3.7's pattern.

| Failure | Typed exception | Cause |
|---|---|---|
| Axis 1 disagreement exceeds `max_error_bar` even at R5 | `IrreducibleNumericalDrift` | Cross-precision keeps disagreeing; problem is genuinely numerically unstable |
| Axis 2 disagreement exceeds budget at R5 | `IrreducibleBackactionSensitivity` | Result depends meaningfully on probe strength; physics is in a sensitive regime |
| Axis 3 disagreement exceeds budget at R5 | `ProviderDivergence` | Two providers fundamentally disagree; one or both are mis-calibrated |
| Promotion limit reached without convergence | `MaxRungReached` | R5 still wobbles; suggested action is human review |
| Drift state cannot be read | `DriftStateUnavailable` | Drift monitor failure; verification gate refuses to proceed in fail-closed mode |

Every failure logs a structured event to the audit log with the full `DisagreementFinding` and the `RunRecord` history. Failed verifications still have valid provenance — the user sees *why* the verification failed, with full evidence, and can make an informed decision about retrying with different parameters.

**SAFETY:** the verification gate is *fail-closed* on drift state: if drift cannot be read, no verification proceeds. This is explicit; a silent fail-open here would let drift masquerade as healthy state. Section 1 Decision 17's "never silently return a result while we know we're miscalibrated" extends to "never run verification while we don't know calibration state."

## 6.9 — Performance budget

The verification gate's overhead beyond the Trinity Core runs themselves is small.

| Stage | P50 overhead | Notes |
|---|---|---|
| Rung selection | <0.5 ms | Lookup against rung table + drift state |
| Per-axis dispatch | <0.5 ms per axis | Mostly in-process function calls |
| Promotion/demotion decisions | <1 ms per evaluation | Statistical comparison of running disagreements |
| Provenance composition | <1 ms | Dict construction |
| Distance matrix computation | <2 ms | NumPy operations on small matrices |
| Disagreement classification | <2 ms | Vendored classifier; mostly enum dispatch |
| **Verification-gate overhead total** | **<10 ms P50** | On top of the 1×-10× Trinity Core multiplier from rung selection |

The wall-clock cost of verification is dominated by the rung-driven Trinity Core multiplier, not by gate overhead. R1 is 1× baseline; R5 is ~10×. **PERF:** the dominant performance variable for wobble-verified tasks is the rung, not the gate. Optimizing gate code is a low-priority concern; getting rung selection right is high-priority.

## 6.10 — How the verification gate interacts with the other layers

**Upstream:** the task grammar layer (Section 3) hands the verification gate a validated `PhysicsTask` with a parsed `tolerance.max_error_bar`. The gate uses that to select the initial rung.

**Downstream:** the gate calls Trinity Core's three subsystems repeatedly per the rung's axes, and may invoke the Router (Section 4) twice for cross-provider checks. After Trinity Core completes, the gate composes the final `Result` with `DisagreementFinding` and provenance.

**Sibling concern — Section 4's router:** for Axis 3 (cross-provider), the gate issues a *second* `RoutingRequest` with `excluded_providers` including the primary's choice, asking for an alternate provider for the verification run. The router doesn't know it's a verification request; it just routes.

**Sibling concern — Section 5's WebSocket:** verification-gate events stream out as part of the task lifecycle. Two new event types (`task.verification.promoted`, `task.verification.demoted`) extend the base set from Section 5.3.

**Sibling concern — Section 7's safety gate:** if the verification gate reaches `MaxRungReached` and the gate's classifier judges `HUMAN_REVIEW` as the suggested action, the safety gate (Section 7) is consulted to decide whether the result still ships with a `DEGRADED` tag or is held for operator review. This depends on the actor's permissions.

**Sibling concern — Section 1 Decision 17 drift monitoring:** the verification gate consults drift state on every task to drive depth promotion. The drift monitor is a standing service; the gate is its primary consumer.

```
=== SECTION 6 COMPLETE — AWAITING ADAM REVIEW ===
```

---

# Section 7 — The Safety Gate (Authentication, Authorization, Frontier Physics, Rate Limiting)

## What Section 7 covers

Section 7 specifies how Phoenix enforces who can do what. The Actor authentication pattern from Section 1 (Decisions 10-12) was named there; Section 7 specifies how Phoenix actually validates Actors at the engine boundary, what permissions the Actor type carries, how frontier-physics gates are enforced, how rate limiting interacts with the Actor model, how operator override works for `HUMAN_REVIEW` cases coming from the verification gate (Section 6), and how org enrollment changes the picture without breaking single-install Phoenix users.

Decisions referenced trace back to Section 1 (notably 10-12 on identity and Actor pattern, 13-14 on mandatory verification, 17 on drift), Section 3 (the Actor-validated task grammar layer), Section 6 (the verification gate's `HUMAN_REVIEW` suggested action), and Section 8 (the dev-ops backdoor where admin-actor permissions land).

The threat model in this section is *honestly stated*. Phoenix's safety gate is defense-in-depth, not an airtight perimeter — and Section 7 says so plainly, in the same way the vendored Actor module's docstring does.

Open design tensions encountered while writing Section 7 are flagged with `[OPEN: ...]` and tracked in Section 11.

## 7.1 — Why the safety gate is its own layer

The safety gate sits between the task grammar layer (Section 3) and Trinity Core (Section 2). Every request that reaches Trinity Core has been through the safety gate; every request that's rejected by the safety gate never touches Trinity Core. This boundary is load-bearing for three reasons.

First, *Trinity Core trusts its inputs*. The 12 vendored solvers, the DPD engine, the SynQc TDS modules — they don't validate actor permissions, they don't check rate limits, they don't decide whether a user is allowed to run frontier physics. Putting validation here keeps the physics core fast and simple, the way it should be.

Second, *the safety gate is the layer that surfaces typed denials*. Section 3.7 enumerated the typed exceptions; Section 7 implements the policy that produces most of them. `AuthError`, `FrontierPhysicsRefused`, `PermissionDenied`, `RateLimitExceeded` — these are all safety-gate decisions, and they all need to produce the same audit-log structure (Section 1 Decision 16) regardless of which protocol delivered the request (Section 5).

Third, *the safety gate is where defense-in-depth shows up*. The same checks that the front door already did get re-validated at the engine boundary. A bug in the front door's auth shouldn't be a free pass into Trinity Core. The safety gate's redundant validation is *the* point — it's the layer that fails closed when an upstream layer gets it wrong.

## 7.2 — The vendored Actor pattern

Phoenix vendors the typed `Actor` value from `evolution/knowledge/actor.py` in dr-frank-and-eddy v6.6 unchanged. The vendored module's docstring is the explicit threat model; Phoenix inherits both the implementation and the honest framing.

**Vendored interface (verbatim):**

```python
@dataclass(frozen=True)
class Actor:
    """A verified human actor authorised to gate sensitive operations."""
    name: str                       # lowercase ascii: "adam", "ash", or org-derived name
    identity_fingerprint: str       # hex of install Ed25519 fingerprint
    issued_at: int                  # unix seconds, UTC
    signature: bytes                # HMAC-SHA256 over canonical payload

    @classmethod
    def sign(cls, name, master_key, fingerprint) -> "Actor": ...

    @classmethod
    def from_signed_payload(cls, payload, master_key) -> "Actor":
        """Parse + verify a wire payload. Constant-time HMAC compare;
        raises PermissionError on mismatch or expiry."""

    def is_valid_now(self) -> bool:
        """Timestamp-window check. ±300 seconds (5 minutes)."""

    def to_payload(self) -> dict:
        """Wire form for transport (base64 signature)."""
```

**Vendored constants:**

- `SIGNATURE_VALIDITY_SECONDS = 300` — 5-minute window, symmetric past+future to handle NTP skew.
- Canonical signing payload format: `f"{name}|{fingerprint}|{issued_at}"` UTF-8 bytes. The pipe separator is chosen because none of the three components can contain it.

**Threat model — what the Actor pattern catches:**

- *Misuse at the type system.* Engine boundaries accept ONLY `Actor` instances. Passing a raw string or dict raises `TypeError` before any policy check runs. The string-actor-spoofing loophole — where any local process posts `{"actor": "adam"}` to a mutation endpoint and lands as ACCEPTED — is closed structurally.
- *Cross-machine spoofing via forwarded port.* No master key on the remote machine means no valid signature.
- *Stale-replay attacks.* Timestamp window of 300 seconds caps replay viability.
- *Audit-trail provenance.* Every sealed event records the signature, so retroactively we can prove a DPAPI-credentialed (or Keychain/libsecret) process issued the request.

**Threat model — what the Actor pattern does NOT catch:**

- *A malicious local process running as the same OS user.* That process can read the install Ed25519 master key out of DPAPI/Keychain/libsecret and sign too. True per-app isolation requires OS-level ACLs or per-app credential stores; neither DPAPI nor Keychain provide that on their own.

This signing layer is **defense-in-depth, not an airtight gate**. Phoenix users running on multi-user machines or hostile environments need additional OS-level isolation — Phoenix doesn't pretend otherwise. The vendored module's docstring states this directly; Phoenix's documentation will state it too.

**[OPEN: should Phoenix optionally require OS-keychain attestation (Windows Hello, Touch ID, system password prompt) for `frontier_physics` actor signing? Adds a real interactive barrier that a silent malicious process can't bypass. v0 specifies the Actor pattern as-vendored; OS attestation deferred to Section 11.]**

## 7.3 — Permissions carried by the Actor

The vendored Actor type has four fields (`name`, `identity_fingerprint`, `issued_at`, `signature`). It carries no permissions inline — permissions are looked up from the install's permission registry by `(name, identity_fingerprint)`. Phoenix's safety gate is the layer that does this lookup.

**Permission registry (per install):**

```python
@dataclass
class ActorPermissions:
    name: str                              # matches Actor.name
    identity_fingerprint: str              # matches Actor.identity_fingerprint
    org_membership: Optional[str]          # org name if enrolled, else None

    # Capability flags
    can_submit_tasks: bool                 # default True for any verified actor
    can_replay_tasks: bool                 # default True
    can_load_adapter: bool                 # default False; privileged
    can_unload_adapter: bool               # default False; privileged
    frontier_physics: bool                 # default False; explicit grant required
    can_override_human_review: bool        # default False; for ops escalation
    is_admin: bool                         # default False; for dev-ops backdoor (Section 8)

    # Rate limit tier
    rate_limit_tier: str                   # "default" | "elevated" | "admin"

    # Audit tags
    granted_by: Optional[str]              # name of the actor who granted these (if not bootstrap)
    granted_at: int                        # unix seconds
    last_used_at: Optional[int]            # for permission-staleness tracking
```

**Default permissions by name:**

- `adam`, `ash` — the two bootstrap actors from dr-frank-and-eddy. Default permissions: all flags True, `rate_limit_tier="admin"`. Granted at install time.
- Any other actor name — default permissions: `can_submit_tasks=True`, `can_replay_tasks=True`, all other flags False, `rate_limit_tier="default"`. Granted via the org enrollment flow (Section 7.6) or the `/v1/admin/actors` endpoint by an existing admin.

**Storage:** the registry lives in Phoenix's state backend (SQLite by default, Postgres in org mode per Section 1 Decision 31). It's append-only — permission grants and revocations are events, not in-place edits. This makes "who had what permission when" auditable.

**[OPEN: permission inheritance for org-enrolled installs. If an org admin grants `can_load_adapter` to org members, does that propagate to every install that enrolls under that org, or does each install need an explicit grant? v0 specifies per-install grants; org-level templates deferred to Section 11.]**

## 7.4 — How the safety gate validates a request

A request enters the safety gate carrying a typed `Actor` (already extracted from the wire payload by the front door, Section 5.6) and a `PhysicsTask` (already validated by the task grammar layer, Section 3).

**Validation pipeline:**

1. **Type check.** Is the actor parameter actually an `Actor` instance? If not, raise `TypeError` immediately — this is the structural protection from Section 7.2. *Even though the front door already did this check*, the safety gate redoes it; defense-in-depth.

2. **Freshness check.** Call `actor.is_valid_now()`. If the timestamp window has expired, raise `PermissionError` with a typed `ActorExpired` cause. (Network delays between front door and engine can age out a payload that was fresh at HTTP arrival.)

3. **Signature re-verification.** Re-verify the HMAC against the install's master key. The front door already verified it; the safety gate does it again. Defense-in-depth against a front-door bug. **PERF:** HMAC verification is microseconds.

4. **Permission lookup.** Fetch the `ActorPermissions` for `(actor.name, actor.identity_fingerprint)`. Missing record means the actor name is signed with a valid key but has no permissions — raise `PermissionDenied` with cause `UnknownActor`.

5. **Capability check.** Match the request's required capability against the actor's flags:
   - `POST /v1/tasks` requires `can_submit_tasks=True`.
   - `POST /v1/tasks/{id}/replay` requires `can_replay_tasks=True`.
   - `POST /v1/adapters` requires `can_load_adapter=True`.
   - `DELETE /v1/adapters/{id}` requires `can_unload_adapter=True`.
   - Any task carrying a frontier-physics regime requires `frontier_physics=True`.
   - Override of a `HUMAN_REVIEW` finding requires `can_override_human_review=True`.
   - Any `/v1/admin/...` endpoint requires `is_admin=True`.

   Missing capability raises `PermissionDenied` with the specific missing flag named.

6. **Frontier physics deep check.** If the task's resolved Trinity Core regime is `WHEELER_DEWITT`, `GRAVITATIONAL_DECOHERENCE`, or `SEMICLASSICAL_GRAVITY` (the three solvers flagged with `frontier_physics: True` per dr-frank-and-eddy v6.6), the safety gate consults the actor's `frontier_physics` flag. Without it, raise `FrontierPhysicsRefused` with the regime named in the audit log.

7. **Rate limit check.** Apply the actor's `rate_limit_tier` against the token-bucket counter (Section 7.5). Tier-exceeded raises `RateLimitExceeded` with retry-after in seconds.

8. **Audit emit.** Every check that runs — pass or fail — emits a structured audit event (Section 1 Decision 16) with actor fingerprint, capability checked, decision, request_id. **PERF:** the emit is fire-and-forget to a buffered async writer; <50 µs overhead.

9. **Pass to Trinity Core.** On success, hand off to the verification gate (Section 6).

**Total safety-gate overhead: <2 ms P50.** Most checks are dict lookups and one HMAC verify. The rate-limit check is the slowest at ~500 µs in the contended case.

## 7.5 — Rate limiting policy

Section 5.6 sketched the rate limiter; Section 7.5 specifies the policy details.

**Token-bucket per actor:**

- Each actor has a bucket whose capacity and refill rate are determined by their `rate_limit_tier`.
- `default` tier: capacity 100 tokens, refill 1 token/second (60/minute, 3600/hour). Sustained throughput 60 req/min; bursts up to 100.
- `elevated` tier: capacity 1000, refill 16/second (~1000/minute, ~57600/hour). For research workflows that legitimately submit batches.
- `admin` tier: no rate limit. Mirrors how dr-frank-and-eddy treats `adam` and `ash`.

**Cost weighting:** not all requests cost the same. The token bucket charges variable costs:

- `GET /v1/health`, `/v1/identity` — free (zero tokens).
- `GET /v1/tasks/{id}` — 1 token.
- `POST /v1/tasks` with R1 verification rung — 5 tokens.
- `POST /v1/tasks` with R5 verification rung — 25 tokens. (Tighter wobble = more compute = more cost.)
- `POST /v1/tasks/{id}/replay` in `replay` mode — 50 tokens. (Replay verification is the heaviest user-visible operation.)
- `POST /v1/adapters` (load LoRA) — 10 tokens.

This makes rate limiting accurate to actual compute cost rather than just request count. **PERF:** cost lookup is a static dict; sub-microsecond.

**Org-level aggregation:** per Section 1 Decision 11, an org's installs share a parent identity. Section 7.5 extends rate limiting accordingly: the org has its own bucket, and an install's request charges *both* its own bucket and the org's bucket. Either being empty causes denial. This prevents a single misbehaving install from exhausting the org's budget for everyone.

**Audit:** every rate-limit decision (allow or deny) emits a structured event. Patterns of denials feed the dev-ops backdoor's anomaly detection (Section 8).

**SAFETY:** the rate limiter is in-memory per Phoenix process with periodic write-through to the state backend. On Phoenix restart, buckets reset to capacity. This is by design — startup grace is acceptable, and durable rate-limit state across restarts adds complexity disproportionate to the threat. Org-level limits enforced at the deployment layer (Section 1 Decision 35's Phoenix Cloud) get true durability.

## 7.6 — Org enrollment flow

Section 1 Decision 11 commits to opt-in org enrollment with HKDF-derived per-install subkeys. Section 7.6 specifies the enrollment ceremony.

**Bootstrap:**

1. An org root keypair is created out-of-band (e.g., on a secure bastion machine) and stored separately from any Phoenix install. The root keypair is the org's identity; losing it means losing the org.
2. The org root produces a *bootstrap token* — a signed, time-limited (24-hour default) capability that authorizes a single new install to enroll. The token contains the org name, the org public key, expiration timestamp, and a one-time nonce.
3. The bootstrap token is delivered to the new install operator via secure channel (out of band — a signed message, a USB stick, an enterprise key management system, etc. Phoenix doesn't prescribe).

**Enrollment:**

1. Operator runs `phoenix identity enroll <bootstrap-token>` (CLI) or `POST /v1/identity/enroll` (REST).
2. Phoenix verifies the bootstrap token's signature against the embedded org public key, checks the expiry, and ensures the nonce hasn't been seen before.
3. Phoenix derives the install's subkey via HKDF: `subkey = HKDF(master=org_public_key, salt=install_fingerprint, info="phoenix-install-subkey", length=32)`.
4. Phoenix stores the org name and the public part of the derived subkey in the install's identity record. The subkey *private* part lives in the install's keystore (DPAPI/Keychain/libsecret) the same way the install's own Ed25519 key does.
5. Phoenix's identity record now reflects org membership. Future `Actor.sign()` calls include the org name in the canonical payload.

**Revocation:**

- Org-level revocation: org root publishes a revocation entry to the Phoenix install (typically via `POST /v1/identity/revoke` from any admin actor); the install records the revocation and refuses to honor signatures from the revoked subkey.
- Install-level revocation: the install operator can revoke the install's own subkey via `phoenix identity revoke-self`. The install can no longer sign Actors after this; Phoenix is effectively decommissioned for that org.
- Audit: revocation events flow to the audit log and the ledger (Section 1 Decision 15).

**SAFETY:** the bootstrap token is single-use. A token observed once is consumed and cannot be replayed even if intercepted in transit. **SAFETY:** the bootstrap token does NOT contain the org's private key; it contains a derivation capability scoped to one install. Compromise of a bootstrap token allows one rogue install but does not give the attacker the org's identity.

**[OPEN: org membership rotation. If an org wants to move to a new root keypair (e.g., suspected compromise), how do existing installs migrate? v1 ships the bootstrap-and-enroll flow above; rotation flow deferred to Section 11.]**

## 7.7 — Operator override for `HUMAN_REVIEW`

Section 6.8 listed `MaxRungReached` as a verification-gate failure — when R5 still wobbles and the classifier suggests `HUMAN_REVIEW`. Section 7.7 specifies how a human operator with `can_override_human_review=True` actually overrides.

**Override flow:**

1. The verification gate's failed task lands in a `tasks_pending_review` queue, not the regular `tasks_complete` table. WebSocket subscribers receive `task.failed` with `agreement_type=HUMAN_REVIEW_REQUIRED`.
2. An operator with the `can_override_human_review` capability accesses the task via `GET /v1/admin/tasks-pending-review` (the dev-ops backdoor; Section 8). Inspects the `DisagreementFinding`, the per-axis distance matrix, the routing history, and the full provenance.
3. The operator decides one of three actions:
   - **`accept-with-warning`** — ship the result tagged `agreement_type=DEGRADED` plus an operator-supplied rationale string. The operator's actor signature is recorded in the ledger.
   - **`reject`** — fail the task definitively with the operator's rationale.
   - **`re-run-with-tighter-bounds`** — resubmit with `max_error_bar` halved, forcing R5+replication; the original task's ledger entry is closed and a new one opens.
4. The operator action is itself a signed Actor payload — operators don't bypass auth, they invoke a privileged operation that requires their actor.
5. Audit: every override is a top-priority audit event (Section 1 Decision 16) and lands in the Omega Ledger as a separate hashchain link tagged `OVERRIDE_BY_OPERATOR`.

**SAFETY:** override authority is *capability-gated*, not name-gated. Even `adam` can't override unless `can_override_human_review=True` for that install/org. The default for the bootstrap actors is True; for any other actor, False unless explicitly granted.

**SAFETY:** override never bypasses the wobble protocol — the operator can't say "skip verification entirely" on a task. They can only judge an already-verified-but-wobbling result. The verification machinery still runs.

## 7.8 — Failure modes

Safety-gate failures map cleanly to typed exceptions and HTTP statuses, consistent with Section 3.7's pattern.

| Failure | Typed exception | HTTP status | Cause |
|---|---|---|---|
| Wrong type for actor parameter | `TypeError` | 500 | Internal Phoenix bug; should never hit user |
| Actor expired | `PermissionError(ActorExpired)` | 401 | Wall-clock skew or stale request |
| HMAC verification failed | `PermissionError(SignatureInvalid)` | 401 | Wrong key, tampered payload |
| Actor name signed with valid key but unknown to permission registry | `PermissionDenied(UnknownActor)` | 403 | Actor was revoked or never granted |
| Capability missing | `PermissionDenied(MissingCapability)` | 403 | Actor lacks the specific flag for the request |
| Frontier physics refusal | `FrontierPhysicsRefused` | 403 | Task triggers a frontier solver; actor lacks `frontier_physics` |
| Rate limit exceeded | `RateLimitExceeded` | 429 | Token bucket empty; `Retry-After` header set |
| Permission registry unavailable | `PermissionRegistryUnavailable` | 503 | State backend (SQLite/Postgres) failure during lookup |

**SAFETY:** the safety gate is *fail-closed* on every failure mode above. A bug or misconfiguration that makes the registry unreadable produces 503, not silent allow-all. Mirrors the verification gate's fail-closed posture on drift state (Section 6.8).

## 7.9 — Performance budget

| Stage | P50 latency |
|---|---|
| Type check | <1 µs |
| Freshness check | <1 µs (clock comparison) |
| HMAC re-verification | ~50 µs |
| Permission registry lookup | <500 µs (cached) / <2 ms (DB miss) |
| Capability check | <1 µs (dict membership) |
| Frontier physics deep check | <5 µs |
| Rate limit check | <500 µs |
| Audit emit | <50 µs (buffered async) |
| **Total safety-gate overhead** | **<2 ms P50** |

The dominant cost is the permission registry lookup; cached in-memory it's sub-millisecond, on a DB miss it can hit 2 ms. **PERF:** the registry is heavily cacheable because permissions change rarely. The cache TTL is 30 seconds; revocations propagate within that window.

**PERF:** for the common case (warm cache, fresh actor, sufficient capability), safety-gate overhead is <500 µs, dominated by HMAC verify and rate-limit check.

## 7.10 — How the safety gate interacts with the other layers

**Upstream:** the front door (Section 5) extracts the Actor from the wire payload and the task grammar layer (Section 3) parses the `PhysicsTask`. Both perform their own validation; the safety gate redoes them defensively.

**Downstream:** validated tasks flow into the verification gate (Section 6) and Trinity Core (Section 2). The safety gate is the last layer before Trinity Core sees the task.

**Sibling concern — Section 6's verification gate:** when verification produces a `HUMAN_REVIEW` suggested action, the safety gate's override flow (Section 7.7) handles the escalation. The verification gate produces the diagnosis; the safety gate enforces the policy on what to do with it.

**Sibling concern — Section 8's dev-ops backdoor:** all `/v1/admin/...` endpoints route through the safety gate's `is_admin` capability check. The dev-ops backdoor is downstream of safety-gate validation; admin permission is just another capability flag.

**Sibling concern — Section 4's router:** the safety gate's frontier-physics check happens before the router runs. The router has its own defense-in-depth re-check (Section 4.4 stage 4), which is intentional redundancy.

**Sibling concern — Section 1 Decision 35's Phoenix Cloud:** when Phoenix is deployed under Phoenix Cloud's multi-tenant hosting layer, tenant isolation is enforced at the deployment layer, *not* by Phoenix itself. The safety gate still sees a single-tenant view per Phoenix process; the hosting layer ensures one tenant's requests can't reach another tenant's Phoenix process.

```
=== SECTION 7 COMPLETE — AWAITING ADAM REVIEW ===
```

---

# Section 8 — The Dev-Ops Backdoor

## What Section 8 covers

Section 8 specifies the layer that makes Phoenix *operable* in production. Every other section in this document covers what Phoenix does for users running quantum tasks; Section 8 covers what Phoenix exposes to the people who run *Phoenix itself* — system administrators, ops engineers, on-call responders, the developer (Adam) when something needs diagnosing without paging anyone else.

Phoenix's dev-ops backdoor is the single privileged surface for inspection, diagnostics, manual interventions, and the kill switch. It lives at `/v1/admin/...` (Section 5.2), is gated by the `is_admin` capability (Section 7.3), and ships the same audit-log discipline as user-facing endpoints (Section 1 Decision 16). Nothing here is a shortcut around safety; everything here is observability and controlled override.

The architectural principle is **Phoenix is operable without paging the developer**. An ops user with admin permission should be able to diagnose a failing task, see drift state, inspect provider health, override a stuck `HUMAN_REVIEW`, and trip the kill switch — all without reading source code.

Decisions referenced trace back to Section 1 (notably 13-14 on verification, 15-22 on commercial-grade capabilities, 35 on Phoenix Cloud), Section 4 (router internals exposed read-only), Section 6 (verification-gate state and pending-review queue), and Section 7 (operator override flow).

Open design tensions encountered while writing Section 8 are flagged with `[OPEN: ...]` and tracked in Section 11.

## 8.1 — Why the dev-ops backdoor is its own layer

The dev-ops backdoor could in principle be folded into the regular REST surface (Section 5) with capability-gated endpoints scattered through the user-facing routes. Phoenix splits it into its own layer for three reasons.

First, *the audience is genuinely different*. User-facing endpoints prioritize predictable contracts, stable error envelopes, and conservative pagination. Admin-facing endpoints prioritize raw access to internal state, expressiveness over stability, and the ability to expose diagnostic shapes that wouldn't make sense in a user contract. Mixing them dilutes both.

Second, *admin endpoints have different audit semantics*. Every admin call is a top-priority audit event with the operator's full actor identity, not a normal request. Some admin actions (kill switch, override) are themselves hashchained ledger entries — they sit alongside Phoenix's solve provenance in the Omega Ledger. User-facing endpoints don't need that level of audit weight.

Third, *the backdoor is where Phoenix is most honest about its internals*. It exposes raw data structures that user-facing endpoints abstract away. The router's full candidate-scoring breakdown, the verification gate's per-axis run records, the calibration drift detector's raw deviation values, the queue's actual depth across providers — all of these need to be visible to ops without going through the user-friendly summarization that the regular REST surface applies.

## 8.2 — The endpoint surface

All admin endpoints live under `/v1/admin/...` and require an actor with `is_admin=True` per Section 7.3. The standard error envelope from Section 5.2 applies; specific admin-only error codes are typed (`AdminPrivilegeRequired`, `KillSwitchEngaged`, etc.) and listed in Section 8.7.

**System health and inspection:**

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/admin/health/detailed` | Extended health beyond `/v1/health`. Includes per-subsystem status: Trinity Core readiness, all three drift detectors, NATS queue depth, state backend latency, every loaded LoRA adapter's status, every connected provider's last-checkin time. |
| `GET` | `/v1/admin/governor` | System resource snapshot. CPU%, RAM%, VRAM%, NPU utilization, thermal status. Inherits the pattern from dr-frank-and-eddy's `/api/governor` endpoint. |
| `GET` | `/v1/admin/inference-status` | LLM/adapter inference state. Per-adapter active sessions, queue depth, last successful round-trip timestamp, validation suite status. Inherits the pattern from dr-frank-and-eddy's `/api/inference-status` endpoint. |
| `GET` | `/v1/admin/budget` | Compute and cost tracking. Per-actor and org-level token-bucket state, cumulative provider spend YTD, per-provider rate consumption against contract limits. Inherits the pattern from dr-frank-and-eddy's `/api/budget` endpoint. |

**Calibration and drift drill-down:**

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/admin/calibration/detail` | Full state of all three drift detectors with raw deviation values, last-run timestamps, threshold configuration, and decision history. Goes deeper than the user-facing `/v1/calibration/status`. |
| `POST` | `/v1/admin/calibration/run` | Force a drift cycle to run immediately, regardless of cadence. Returns synchronously when the cycle completes. Used after maintenance (CUDA toolkit update, Phoenix release) to confirm clean baseline before resuming user traffic. |
| `GET` | `/v1/admin/calibration/history` | Drift cycle history with per-detector pass/fail status across the configured retention window. Useful for spotting "we've been gradually drifting for two weeks" patterns. |

**Router and provider inspection:**

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/admin/router/decisions` | Last N routing decisions with full `decision_provenance` from Section 4.4 — which candidates were considered, which were filtered at each stage, the scoring breakdown, the alternates list. |
| `GET` | `/v1/admin/providers/health-history` | Per-provider health state over time. Failover events, queue depth trends, calibration timestamps as they were at each point. |
| `POST` | `/v1/admin/providers/{id}/manual-quarantine` | Manually mark a provider as `DEGRADED` for a specified duration. Useful when ops has out-of-band knowledge ("IBM Quantum announced maintenance") that telemetry hasn't caught up to. |
| `POST` | `/v1/admin/providers/{id}/manual-restore` | Lift a manual quarantine. The provider returns to normal health-check polling. |

**Verification gate and pending-review queue:**

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/admin/tasks-pending-review` | List tasks waiting on `HUMAN_REVIEW` per Section 7.7. Each entry includes the `DisagreementFinding`, the routing history, and links to per-axis run records. |
| `POST` | `/v1/admin/tasks-pending-review/{task_id}/override` | Apply one of the three operator override actions per Section 7.7 (`accept-with-warning`, `reject`, `re-run-with-tighter-bounds`). Requires `can_override_human_review`. |
| `GET` | `/v1/admin/verification/rung-distribution` | Histogram of verification rungs (R1..R5) selected over the recent window. "Most tasks landing at R5 means error budgets are too tight or drift is suspected." |

**Kill switch:**

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/admin/kill-switch/engage` | Stop accepting new tasks. In-flight tasks complete normally; new submissions return `503 KillSwitchEngaged` with the operator's optional rationale string. Mentioned but unimplemented in dr-frank-and-eddy v6.6 (see `backend/dependencies.py`); Phoenix builds it. |
| `POST` | `/v1/admin/kill-switch/release` | Resume normal operation. Both engage and release require `is_admin=True`; both lead to top-priority audit events with the operator's actor signature. |
| `GET` | `/v1/admin/kill-switch/status` | Returns `{engaged: bool, rationale: str?, engaged_at: timestamp?, engaged_by: actor_name?}`. |

**Audit log and ledger inspection:**

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/admin/audit/replay` | Replay audit events with operator-facing time-window and filter parameters that go beyond `/v1/audit/events`. Includes events that aren't visible to non-admins (rate-limit denials of other actors, signature-verification failures, etc.). |
| `GET` | `/v1/admin/ledger/integrity-report` | Full hashchain walk plus per-link tag distribution (how many `OVERRIDE_BY_OPERATOR`, how many `PROPOSED_BY_AGENT`, how many normal solves) over the configured window. |

**Adapter management (privileged paths beyond Section 5.2):**

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/admin/adapters/{id}/force-revalidate` | Re-run inference-time validation against an already-loaded adapter. Useful when ops suspects an adapter has degraded. |
| `GET` | `/v1/admin/adapters/{id}/round-trip-history` | History of adapter validation results since load. Pinpoints when an adapter started failing if it has. |

## 8.3 — The kill switch

The kill switch is Phoenix's emergency-stop. dr-frank-and-eddy v6.6 referenced it but did not implement it (`backend/dependencies.py` flagged "out of scope for this build"). Phoenix v1 implements it because middleware running in production needs a stop-the-world mechanism that doesn't require restarting the process.

**Engage semantics:**

1. Operator with `is_admin=True` calls `POST /v1/admin/kill-switch/engage` with optional `rationale: str`.
2. Phoenix immediately:
   - Sets a process-wide `kill_switch_engaged=True` flag in shared state (atomic).
   - Refuses to accept new task submissions; `POST /v1/tasks` returns `503 KillSwitchEngaged` with the rationale in the error envelope.
   - Refuses new adapter loads, new replays, new override actions. The `POST /v1/admin/kill-switch/release` endpoint remains available — only an admin can lift the switch they (or another admin) engaged.
   - Drains in-flight tasks normally — running solves complete, their results land in the ledger as usual. Stopping mid-solve would corrupt provenance.
   - Emits a top-priority audit event with operator identity, timestamp, rationale.
   - Writes a `KILL_SWITCH_ENGAGED` entry to the Omega Ledger as a hashchain link.
3. WebSocket subscribers receive a `system.kill_switch.engaged` event with rationale. Existing connections aren't dropped — they continue receiving events for in-flight tasks they were watching.
4. The drift monitor and provider telemetry pollers continue running. The kill switch stops *user-driven workflow*, not internal observability.

**Release semantics:** mirror image. `POST /v1/admin/kill-switch/release` clears the flag, emits an audit event, writes a ledger link. New submissions are accepted again. The process-wide flag is checked at the safety gate (Section 7.4 stage 0, added before the type check) — every request sees the current state.

**Kill-switch persistence across process restart.** The kill switch exists for emergencies. An emergency that requires engaging the switch is exactly the state that should not silently lift on a side-effect restart. **Phoenix persists the engaged state in the state backend** (SQLite/Postgres per Section 1 Decision 31), written on engage and on release. On process startup, Phoenix reads the persisted `kill_switch_state` row before opening the front door. If state is `engaged_when_shutdown=True`, the front door starts in a refusing-new-tasks mode and an admin must explicitly call `POST /v1/admin/kill-switch/release` to begin accepting work. The "refuse to start accepting" posture is fail-closed in the right direction; the cost is one extra step for operators in an emergency, which they will gladly pay. **SAFETY:** the persisted row is itself a top-priority audit event and is written before the engage response is returned to the operator, so a crash mid-engage is observable. Resolved from Section 11.5.1.

**SAFETY:** the kill switch is *not* a substitute for OS-level shutdown. If Phoenix has a critical bug that the kill switch can't contain (e.g., an exec-the-attacker-input path that bypasses the safety gate entirely), the operator's correct action is to terminate the process at the OS level. The kill switch is for "stop accepting *normal* work cleanly"; OS-level stop is for "stop everything *now*."

**SAFETY:** the kill switch never bypasses audit. Every engage and release lands in the ledger. An operator who engages the switch maliciously to hide subsequent activity leaves a clear trail.

## 8.4 — The principle of read-only inspection by default

Section 8.2's endpoints divide cleanly into two groups: read endpoints (`GET ...`) and mutation endpoints (`POST ...`). The architectural principle is that read endpoints are *always* available to admins, and mutation endpoints are deliberately scarce.

The mutation surface in v1 is exactly seven endpoints:

1. `POST /v1/admin/calibration/run` — force a drift cycle.
2. `POST /v1/admin/providers/{id}/manual-quarantine` — quarantine a provider.
3. `POST /v1/admin/providers/{id}/manual-restore` — restore a provider.
4. `POST /v1/admin/tasks-pending-review/{task_id}/override` — override a `HUMAN_REVIEW`.
5. `POST /v1/admin/kill-switch/engage` — engage kill switch.
6. `POST /v1/admin/kill-switch/release` — release kill switch.
7. `POST /v1/admin/adapters/{id}/force-revalidate` — re-validate an adapter.

That's the entire admin mutation surface. Everything else is observation. **SAFETY:** keeping mutation paths small means the audit story stays clean — every admin mutation is exhaustively listed in Section 7's failure-mode table and ships with its own ledger-tag definition. Adding new admin mutations is a deliberate decision in future Phoenix releases, not a casual extension.

**Manual calibration baseline override — permanent NO.** Phoenix does not expose any endpoint that lets an admin override the drift detector's reference baseline at runtime. The risk-to-benefit ratio is wrong: the override is convenient for one deliberate workflow (post-release recalibration), but if used incorrectly it silently masks drift, which is exactly the failure mode Phoenix is supposed to prevent. The right way to handle post-release recalibration is to ship the new calibration profile as part of the next Phoenix release — it's already vendored from `vendor/calibration_profile.json` per Section 10.2 — not via runtime override. This is a permanent disposition, not a v1.x deferral; it stays "no" unless a future case forces revisiting at the architectural level. Resolved from Section 11.5.2.

## 8.5 — How dev-ops integrates with Phoenix Cloud (Section 1 Decision 35)

The Phoenix Cloud hosting layer sits *outside* Phoenix's process boundary and runs Phoenix as a single-tenant subprocess per tenant. Section 8.5 specifies how the dev-ops backdoor maps into that hosted environment.

**Per-tenant admin:** within a single hosted Phoenix instance, an admin user with `is_admin=True` sees that tenant's full admin surface. Their tenant's verification gate state, drift detectors, router decisions — all visible. They cannot see other tenants' data because their Phoenix instance is process-isolated.

**Cross-tenant ops (Phoenix Cloud operator):** the Phoenix Cloud hosting layer has its own admin interface that aggregates across tenant Phoenix instances. This is *not* part of Phoenix's `/v1/admin/...` surface — Phoenix doesn't know it's hosted. The hosting layer talks to each Phoenix instance via the same admin endpoints and aggregates the responses for the hosting operator.

**The kill switch in hosted mode:** a per-tenant kill switch stops that tenant's work. A hosting-layer kill switch (separate from Phoenix's, owned by the hosting infrastructure) stops *all* tenants. The two are orthogonal.

**Audit-log routing:** in hosted mode, the structured-event stream (Section 1 Decision 16) flows to the hosting layer's centralized log infrastructure for cross-tenant analysis. Per-tenant event streams continue to land in each Phoenix instance's local audit log too. **PERF:** the OpenTelemetry export adapter (Section 1 Decision 22) handles the hosted-mode forwarding without changing Phoenix's internal behavior.

This separation — Phoenix unaware of hosting; hosting layer treats Phoenix as a black box with admin endpoints — is what keeps single-install Phoenix users on the same code path as Phoenix Cloud tenants. No special hosted mode in the codebase.

## 8.6 — Observability beyond admin endpoints

The dev-ops backdoor is not the only observability surface. Phoenix's commercial-grade commitments (Section 1 Decisions 16, 17, 22) imply three additional standing observability streams:

**Audit-log stream (Section 1 Decision 16).** Every event in Phoenix — every Trinity Core layer transition, every router decision, every authentication check, every drift alert, every config change — emits a structured event. The native event format is exposed at `/v1/audit/events` (read by anyone with `can_submit_tasks`); the OpenTelemetry adapter (Section 1 Decision 22) exports to OTLP-compatible backends.

**Calibration drift telemetry (Section 1 Decision 17).** The drift monitor's three detectors run continuously; their output streams to telemetry independent of admin queries. Ops dashboards subscribe to `/v1/ws/calibration/drift` (Section 5.3) and see drift events in real time.

**Per-provider telemetry.** Each provider's `ProviderRegistry` entry (Section 4.6) is updated on the configured polling cadence. The current state is queryable via `/v1/admin/providers/...`; historical state is queryable via the audit log.

**Phoenix's own self-checks** (Section 6.5's drift-aware promotion, Section 4.5's failover protocol, Section 7's safety-gate decisions) all emit structured events. An operator without admin access to mutation endpoints can still see most of what's happening through these read streams.

**[OPEN: should Phoenix expose a Prometheus metrics endpoint at `/v1/metrics` in addition to the OpenTelemetry export? Prometheus is dominant in many ops environments; OpenTelemetry's metrics support is younger. v0 specifies OpenTelemetry as primary per Section 1 Decision 22; Prometheus deferred to Section 11.]**

## 8.7 — Failure modes specific to admin endpoints

| Failure | Typed exception | HTTP status | Cause |
|---|---|---|---|
| Non-admin actor calls admin endpoint | `AdminPrivilegeRequired` | 403 | Actor lacks `is_admin=True` |
| Kill switch engaged, request blocked | `KillSwitchEngaged` | 503 | Reflects current operator-engaged state |
| Manual quarantine duration exceeds policy max | `QuarantineDurationExceeded` | 400 | Default cap is 24 hours; configurable |
| Override action conflicts with task state | `TaskNotPendingReview` | 409 | Task already completed or already overridden |
| Calibration run requested while one is in flight | `CalibrationRunInProgress` | 409 | Drift cycle already running; wait or cancel via separate endpoint |
| Force-revalidate on unloaded adapter | `AdapterNotLoaded` | 404 | Adapter has been unloaded since the request was issued |

Every failure logs to the audit log with full operator context; admin failures are top-priority events.

## 8.8 — Performance budget

The admin surface is not in any user's hot path. Performance targets are looser than the user-facing endpoints (Section 5.8) but still meaningful for operator ergonomics.

| Endpoint class | P50 latency target | Notes |
|---|---|---|
| Health/governor/inference-status | <10 ms | Cached snapshots; refreshed in background |
| Calibration/router/provider drill-downs | <50 ms | Some involve aggregating from the audit log or ledger |
| `tasks-pending-review` listing | <30 ms | Bounded by review-queue size, typically <100 entries |
| `POST .../calibration/run` | duration of one drift cycle | Synchronous; ~5-7 minutes per Section 1 Decision 17 |
| `POST .../kill-switch/engage` | <20 ms | Sets atomic flag, emits audit, writes ledger link |
| `POST .../override` | <100 ms | Fetches task state, applies action, writes ledger link |
| `audit/replay` query | <500 ms for default window | Heavier queries warned in response with pagination cursor |

**PERF:** admin endpoints are deliberately cached more aggressively than user endpoints. Most read endpoints serve from a 5-second-TTL in-memory cache. Stale-by-5-seconds is acceptable for ops dashboards and explicit when the user is mid-investigation.

## 8.9 — How the dev-ops backdoor interacts with the other layers

**Upstream:** the front door (Section 5) routes `/v1/admin/...` requests through the safety gate (Section 7.4) like any other request. The `is_admin` capability check happens at stage 5 of the validation pipeline.

**Downstream:** admin read endpoints query the various subsystems' state (router, verification gate, drift monitor, provider registry, ledger). Admin write endpoints invoke privileged operations on those subsystems with operator-actor signatures.

**Sibling concern — Section 6's verification gate:** the `tasks-pending-review` queue is owned by the verification gate; admin endpoints just expose it. Override actions invoke the verification gate's typed acceptance/rejection paths.

**Sibling concern — Section 4's router:** the `/v1/admin/router/...` and `/v1/admin/providers/...` endpoints expose the router's `ProviderRegistry` and `decision_provenance`. The router maintains the data; admin endpoints just publish it.

**Sibling concern — Section 7's safety gate:** every admin endpoint goes through the same safety-gate validation pipeline; admin permission is just a flag on `ActorPermissions`. Section 7's failure-mode table and Section 8's failure-mode table are disjoint — Section 7 covers auth/permission failures, Section 8 covers admin-action-specific failures (kill switch already engaged, calibration already running, etc.).

**Sibling concern — Section 9's reference admin client:** the 5-co-author reference admin client (Section 1 Decision 25) drives Phoenix through the admin surface. Section 9 specifies that client; Section 8 specifies the surface it consumes.

```
=== SECTION 8 COMPLETE — AWAITING ADAM REVIEW ===
```

---

# Section 9 — The Reference Admin Client (5-Co-Author Pattern as Phoenix Consumer)

## What Section 9 covers

Section 9 specifies the reference admin client — an example MCP-based agent that drives Phoenix automatedly, demonstrating the consumer pattern any other client would follow. The 5-co-author Third Space pattern from dr-frank-and-eddy (Claude, Gemini, ChatGPT, Grok, Perplexity) gets repurposed here: in dr-frank-and-eddy, those agents *produce* curated cognitive answers via wobble-converged consensus; in Phoenix, they *consume* the platform via MCP tools to drive automated test-pilot workflows.

Section 9 is the smallest of the architectural sections because the reference admin client is *not* part of Phoenix v1 core (Section 1 Decision 25). It's a separate codebase, separate repository, separate release artifact. Phoenix doesn't ship the client; Phoenix ships the surface the client consumes (Sections 5 and 8). What Section 9 specifies is the architectural shape the client takes, the patterns it demonstrates, and the boundaries that keep it cleanly outside Phoenix's process.

Decisions referenced trace back to Section 1 (notably 17 on co-authors-as-MCP-clients, 25 confirming they're not core), Section 5 (the MCP surface they consume), and Section 8 (the admin endpoints they drive).

Open design tensions encountered while writing Section 9 are flagged with `[OPEN: ...]` and tracked in Section 11.

## 9.1 — Why the reference admin client exists at all

Phoenix v1 is shippable without a reference admin client. Users could write their own MCP clients, agent frameworks could integrate Phoenix directly, and the dev-ops backdoor (Section 8) is operable via curl or any HTTP client. So why ship one?

Three reasons.

First, *demonstration value*. The reference admin client shows the canonical way to drive Phoenix from an automated agent. Other developers building agent frameworks see how MCP tools are composed for non-trivial workflows, how Actor authentication flows through MCP, how reproducibility-mode flags are propagated, how WebSocket subscriptions integrate with task lifecycle. A working reference client is documentation that compiles.

Second, *test pilot for the platform*. dr-frank-and-eddy's 5-co-author Third Space pattern was built around getting wobble-converged agreement on cognitive questions. Phoenix's three-axis physics wobble (Section 6) is conceptually parallel but operates on numerical results from physics solves. Running the same 5 agents *as Phoenix consumers* — each issuing the same task and comparing results across them — exercises Phoenix's consumer surface in a way no synthetic test does. It's a real workload that surfaces real bugs.

Third, *Adam's lab bench continuity*. dr-frank-and-eddy stays Adam's personal lab where physics evolves and the 5-co-author pattern lives. Phoenix is the production middleware. The reference admin client is the *bridge* — a v1.0 artifact that lets Adam keep using the 5-agent pattern he's already comfortable with, but pointed at Phoenix as the engine instead of dr-frank-and-eddy's local synthesis pipeline. That continuity matters for the human workflow, not just the architecture.

## 9.2 — What the reference admin client is, structurally

The reference admin client is a Python package (separate from Phoenix), distributed via pip and as a Docker image, with a CLI entry point. It depends on Phoenix's MCP server but does not import Phoenix. Communication is purely over the MCP transport (stdio or HTTP+SSE) per Section 5.5.

**Package shape:**

- **`phoenix-reference-client`** — the pip-installable package name (a placeholder; actual published name TBD before v1).
- **CLI entry point**: `phoenix-ref` (placeholder name).
- **Configuration**: a YAML file at `~/.phoenix-ref/config.yaml` describing the 5 agents (model identifiers, API keys via env-var references), the connected Phoenix install's MCP endpoint, default workflows.

**Runtime architecture:**

```
┌─────────────────────────────────────────────────────┐
│  phoenix-ref CLI                                    │
│  ↓                                                  │
│  WorkflowOrchestrator (selects and runs workflows)  │
│  ↓                                                  │
│  Five AgentAdapter instances:                       │
│  ┌─Claude─┬─Gemini─┬─ChatGPT─┬─Grok─┬─Perplexity─┐ │
│  └────────┴────────┴─────────┴──────┴────────────┘ │
│  ↓                                                  │
│  MCP Client (talks to Phoenix's MCP server)        │
└─────────────────────────────────────────────────────┘
       ↓ stdio or HTTP+SSE
       ↓ Actor-signed payloads
┌─────────────────────────────────────────────────────┐
│  Phoenix MCP server                                 │
│  → Phoenix REST API (per Section 5.5 architecture)  │
└─────────────────────────────────────────────────────┘
```

The five agent adapters are themselves clients of external LLM APIs (Anthropic, Google, OpenAI, xAI, Perplexity). The reference client is the *broker* between LLM-generated requests and Phoenix execution.

**Dependencies the client takes on:**

- The MCP Python SDK (or equivalent in the language chosen for distribution).
- Anthropic, Google, OpenAI, xAI, and Perplexity SDKs for the five agent adapters.
- A YAML config parser.
- A WebSocket client for live subscription to task lifecycle events.

**Dependencies the client deliberately does NOT take on:**

- No direct Phoenix imports. The client treats Phoenix as a black-box service, the same way any third-party integrator would.
- No vendored dr-frank-and-eddy code. The client is a clean reimplementation of the *pattern* from dr-frank-and-eddy's Third Space, not a copy.
- No physics simulation libraries (NumPy, CuPy, etc.). The client orchestrates; Phoenix executes.

This separation is deliberate. The reference client must remain a *consumer* of Phoenix to be a valid demonstration. Letting it import Phoenix would make it a special case rather than an example.

## 9.3 — What the agents do, repurposed for Phoenix

In dr-frank-and-eddy, each of the five agents responds to a query with a candidate answer; the wobble detector measures pairwise disagreement across the five answers; the loop iterates until convergence or escalation to Adam/Ash. The agents are *answer producers*.

In Phoenix, the agents have a different job. Each agent receives the same task spec, and each one produces:

- A *Phoenix task submission*: the structured JSON or grammar-token form that Phoenix accepts (Section 3).
- A *suggested workflow*: which providers to prefer, what reproducibility mode to use, what `max_error_bar` to set.
- A *post-result interpretation*: given the `Result` Phoenix returned, the agent's read on what the answer means in scientific terms.

The reference admin client orchestrates these in three workflow patterns:

**Pattern 1 — Cross-agent task formulation.** All five agents receive the same natural-language scientific question. Each translates it into a Phoenix task submission. The orchestrator compares the five submissions, surfaces disagreements, and either picks the most-agreed shape or surfaces ambiguity to Adam. The Phoenix task is then submitted via MCP using whichever shape was selected. This validates that Phoenix's task grammar is robust enough that five independent translators agree on what a question means.

**Pattern 2 — Cross-agent result interpretation.** A single Phoenix task runs once and returns a single `Result`. The Result is shown to all five agents; each writes a scientific interpretation. The orchestrator runs the *cognition* wobble (the dr-frank-and-eddy v6.6 `DisagreementFinding` pattern, vendored) over the five interpretations. Agents disagreeing on what the same numerical result *means* surfaces a different kind of insight than physics wobble — it catches conceptual ambiguity in how Phoenix's results are presented.

**Pattern 3 — Adversarial probe of Phoenix.** One agent proposes a task; the other four are asked to find ways the task might be misleading or to suggest tighter wobble bounds. The "attacking" agents iterate for some bounded budget; the proposing agent's final submission is the one that survived their challenges. This is the Phoenix analog of dr-frank-and-eddy's "two heads" pattern (the Ash/Adam human axis crossed with the AI axis), but with all the heads being AI.

Pattern 1 is the v1 default. Patterns 2 and 3 are v1 too, but turned off by default — the client's config switches them on.

## 9.4 — Authentication and identity for the reference client

The reference client is itself an *Actor* from Phoenix's perspective (Section 7.2). When the client sends a task to Phoenix's MCP server, every tool call carries an Actor-signed payload. The Actor's identity is configurable in the client's config:

- **For Adam's personal use**: the Actor's name is `adam`, identity_fingerprint is the install fingerprint of Phoenix on Adam's machine, and the master key is read from DPAPI/Keychain just like dr-frank-and-eddy does. This is the bootstrap-actor case — Adam's reference client has admin permissions because his Actor does.
- **For other deployments**: a per-deployment Actor is granted `can_submit_tasks` plus optionally `can_replay_tasks` and `frontier_physics`. The reference client doesn't get admin permission by default — admin is reserved for human operators on the dev-ops backdoor (Section 8). An automated client running without a human in the loop should not have kill-switch authority.

**Per-agent attribution:** the Actor in the Phoenix task is the *client*, not the individual agent (Claude vs. Grok vs. etc.). But the client's submission *includes* an opaque metadata field naming which agent produced the request. Phoenix records this in the ledger as an audit field but doesn't use it for permission decisions. This lets ops trace "Grok was the one that submitted the task that hit `MaxRungReached`" without violating Phoenix's typed-Actor discipline.

**SAFETY:** the reference client never embeds API keys for the 5 LLM providers in the codebase or config. It reads them from env vars or a local credential store the user configures. Configuration documents this; the client's default config contains placeholders, never real keys.

## 9.5 — The MCP tool consumption pattern

The reference client uses Phoenix's MCP tools (Section 5.5) the way any other agent framework would. In v1, the client primarily consumes:

| Tool | Purpose |
|---|---|
| `phoenix_task_submit` | Submit each agent's translated task. |
| `phoenix_task_get` | Poll for completion when streaming isn't available. |
| `phoenix_provenance_get` | Fetch full provenance after completion for cross-agent comparison. |
| `phoenix_calibration_status` | Pre-flight check before submitting tasks. |
| `phoenix_providers_list` | Inform agent suggestions about which providers are healthy. |
| `phoenix_health` | Liveness check at startup. |

The client's WebSocket client subscribes to `/v1/ws/tasks/{task_id}/stream` for live event updates during long-running solves; these events drive the per-agent UI display in the client's CLI (text-mode progress bars per task, with rung promotion/demotion events visible in real time).

**[OPEN: should the reference client use the v6.6 Sanskrit memory tools (`phoenix_memory_compress`, `phoenix_memory_recall`, etc.) for its internal cross-agent memory? It would be a beautiful demonstration of vendored substrate composition, but it crosses into the agent-framework's persistence layer in ways v1 hasn't fully thought through. Defer to Section 11.]**

## 9.6 — What the reference client is explicitly NOT

These boundaries keep the client honest as a reference:

- **NOT a hosted service.** The client is a self-contained Python package. Users run it locally against their own Phoenix install. (Phoenix Cloud per Section 1 Decision 35 is its own separate product.)
- **NOT a UI framework.** The client is CLI-first, with structured-output options for piping into other tools. A GUI version is conceivable for v2 but not v1.
- **NOT bundled with Phoenix.** The client lives in its own repository, has its own release cadence, can be at v0.3 while Phoenix is at v1.0. This decoupling matters because the client iterates faster than Phoenix and shouldn't drag the platform's release schedule.
- **NOT a substitute for Phoenix's ledger.** The reference client maintains its own per-workflow log (which agents said what about which Phoenix task), but Phoenix's Omega Ledger is still authoritative for the actual solves. The client's log is a side-channel for cross-agent analysis, not a competing ground truth.

## 9.7 — Distribution and release model

The reference client is distributed independently from Phoenix:

- **Repository**: separate GitHub repo (e.g., `nah414/phoenix-reference-client`).
- **Packaging**: pip-installable Python package + Docker image. No standalone-binary in v1 (the client always has the 5 agent SDK dependencies, which would make a standalone binary heavy).
- **License**: Apache 2.0, matching Phoenix.
- **Versioning**: independent of Phoenix. The client tracks the Phoenix major version it requires; e.g. `phoenix-reference-client v0.5` requires `Phoenix >= 1.0, < 2.0`.

**[OPEN: does the reference client need to be open source? Phoenix is Apache 2.0 (Section 1 Decision 34). The reference client is a meaningful demonstration but also a piece of intellectual property if it gets sophisticated enough. v0 specifies Apache 2.0 to match Phoenix; reconsider at v1 release time. Tracked in Section 11.]**

**Release cadence:** the client may release more frequently than Phoenix. If a client release adds new patterns or refines existing ones without requiring Phoenix changes, no Phoenix release is needed. If a client release requires a new Phoenix capability, the client release is gated on the Phoenix release that ships that capability.

## 9.8 — Failure modes specific to the reference client

The reference client's failure modes are mostly upstream — agent SDK failures, network issues to LLM providers, Phoenix MCP timeouts. But three failure shapes are worth naming:

| Failure | Behavior |
|---|---|
| One of five agents unavailable | The client continues with the remaining four. Pattern 1 still works with as few as 2 agents (with reduced cross-agent confidence). Patterns 2 and 3 require at least 3. |
| Phoenix MCP server unreachable | The client waits with exponential backoff up to a configured timeout, then surfaces an error to the user. No silent task loss. |
| Agent SDK rate-limited | The client honors the SDK's retry-after and skips the affected agent for that workflow iteration. The skipped agent's contribution is recorded as missing in the cross-agent log. |
| All five agents disagree on task formulation in Pattern 1 | The client surfaces the five candidate submissions to Adam (or whoever is operating the client) for manual selection, rather than submitting any of them. |

These failure modes match the discipline of Phoenix itself — fail-closed where it matters, surface what happened with full provenance, never silently substitute a degraded result.

## 9.9 — How the reference client interacts with Phoenix

The interaction surface is exactly the public Phoenix surface from Sections 5 and 8. The reference client uses:

**REST and MCP** (Section 5) — the canonical task-submission and provenance-fetch paths. The client is a normal MCP consumer.

**WebSocket** (Section 5.3) — for live task lifecycle subscription during long-running solves.

**Admin endpoints** (Section 8) — *only* if the client's Actor has admin permission (default is no). When configured with admin permission, the client can drive automated calibration drills (e.g., run the Tier-1 battery before each major workflow iteration), query the verification gate's pending-review queue (and surface those to Adam), and access governor metrics (e.g., to back-pressure on the workflow when system resources are tight). The client never engages or releases the kill switch automatically — that's a human-only operation by client design, even when permission would allow it.

**No admin mutation by automated client.** This is a self-imposed restriction in the reference client beyond what Phoenix enforces. Phoenix lets any actor with `is_admin=True` engage the kill switch; the reference client refuses to call that endpoint regardless of permission. Override actions on `tasks-pending-review` are similarly gated to require human confirmation in the client's CLI before invocation. **SAFETY:** this is the Phoenix analog of "automation can read everything; humans authorize state changes."

```
=== SECTION 9 COMPLETE — AWAITING ADAM REVIEW ===
```

---

# Section 10 — File Layout, Vendoring Map, and Launcher

## What Section 10 covers

Section 10 specifies the concrete shape Phoenix takes on disk. Every architectural commitment from Sections 0-9 — Trinity Core's three subsystems, the task grammar layer, the router, the front door, the verification gate, the safety gate, the dev-ops backdoor, the reference admin client — needs a specific path under `C:\Phoenix\`. Section 10 is where those paths get assigned, where the vendoring map (what gets copied from where) becomes explicit, where the launcher script and desktop-shortcut behavior is specified, and where the per-section README plan that satisfies Adam's standing rule lands.

This section also specifies the v1 *acceptance criteria* — the concrete tests and benchmarks that have to pass before Phoenix v1 is releaseable. Without acceptance criteria, "Phoenix v1" stays an abstraction; with them, build guides have a clear finish line to verify against.

Decisions referenced trace back to Section 1 (notably 7-9 on substrate vendoring, 21-22 on distribution, 32-33 on the NATS+SQLite default, 38 on local C drive authority), Section 2 (Trinity Core path mapping), and the standing rules from the user memories (per-section README requirement, launcher updates whenever startup behavior changes, no OneDrive paths anywhere).

Open design tensions encountered while writing Section 10 are flagged with `[OPEN: ...]` and tracked in Section 11.

## 10.1 — Top-level directory structure

```
C:\Phoenix\
├── README.md                         # Project root README
├── LICENSE                           # Apache 2.0
├── pyproject.toml                    # Build config, dependencies, package metadata
├── requirements.lock                 # Pinned dep tree per Section 1 Decision 21
├── PHOENIX_ARCHITECTURE_v0.md        # This document
├── CHANGELOG.md
├── .gitignore
├── .github/                          # CI workflows, issue templates
│
├── phoenix/                          # The Python package itself
│   ├── __init__.py
│   ├── api/                          # Section 5 — front door (REST + WS)
│   ├── cli/                          # Section 5 — CLI surface
│   ├── mcp/                          # Section 5 — MCP server
│   ├── trinity/                      # Section 2 — Trinity Core
│   │   ├── solver/                   # Wraps vendored synthesis/equations
│   │   ├── control/                  # Wraps vendored synthesis/core (DPD)
│   │   └── orchestrate/              # Greenfield Phoenix code (SynQc TDS = design reference)
│   ├── grammar/                      # Section 3 — task grammar layer
│   ├── router/                       # Section 4 — provider routing
│   ├── verification/                 # Section 6 — wobble verification gate
│   ├── safety/                       # Section 7 — safety gate, Actor, permissions
│   ├── admin/                        # Section 8 — dev-ops backdoor handlers
│   ├── ledger/                       # Hashchained provenance (vendored Omega Ledger pattern)
│   ├── audit/                        # Structured event log + OTel adapter
│   ├── identity/                     # Per-install Ed25519 + org enrollment
│   ├── adapters/                     # LoRA adapter loading + sandbox
│   ├── providers/                    # Provider adapters (Frankenstein 1.0 pattern)
│   ├── state/                        # SQLite/Postgres state backend
│   ├── queue/                        # NATS JetStream client
│   └── _internal/                    # Logging, config, utilities
│
├── vendor/                           # Frozen frank-data substrate (read-only at runtime)
│   ├── VENDOR_VERSION.txt            # Pinned dr-frank-and-eddy commit + calibration hash
│   ├── synthesis/                    # 12 solvers + DPD engine + Lindblad
│   ├── grammar/                      # Sanskrit codec + grammar
│   ├── wobble/                       # DisagreementFinding + classifier
│   ├── actor/                        # Vendored Actor module
│   └── calibration_profile.json      # Per-solver calibration manifest
│
├── tests/                            # Test suite
│   ├── unit/
│   ├── integration/
│   ├── tier1/                        # Tier-1 analytical battery (HO-1, ISW-1, H1S-1, RABI-1, SCG-1)
│   ├── soak/                         # Soak tests across all four protocols
│   ├── invariants/                   # Vendored 31-test grammar invariants + Phoenix additions
│   └── conftest.py
│
├── docs/                             # User-facing documentation
│   ├── getting-started/
│   ├── api-reference/                # Generated from OpenAPI 3.1 schema
│   ├── reproducibility/
│   ├── frontier-physics/             # The honest threat model + how to grant the permission
│   ├── deployment/
│   └── examples/
│
├── scripts/                          # Operator and developer scripts
│   ├── launch.bat                    # Windows desktop-shortcut launcher
│   ├── launch.sh                     # macOS/Linux equivalent
│   ├── create_shortcut.ps1           # Windows shortcut installer
│   ├── vendor_sync.py                # Script that re-vendors substrate (Section 10.4)
│   ├── soak_test.py                  # Smoke test invokable from CI or manually
│   └── pricing_update.py             # Refresh providers/pricing_v1.json from upstream
│
└── .audit/                           # Local diagnostic output (gitignored)
```

The top-level layout follows three principles. *Phoenix package code* lives under `phoenix/`, organized by architectural layer (one directory per major Section). *Vendored substrate* lives under `vendor/`, frozen at the pinned frank-data commit and read-only at runtime. *Everything else* — tests, docs, scripts, audit artifacts — lives at the top level alongside.

## 10.2 — The vendoring map

Section 1 Decisions 7-9 commit to vendoring dr-frank-and-eddy at a pinned commit. SynQc TDS Core is a *design reference* (Decision 37) and is NOT vendored. Section 10.2 specifies the file-by-file mapping for the frank-data vendoring. Every path under `vendor/` corresponds to a specific source path that gets copied verbatim during the vendor sync (Section 10.4).

| Vendor path | Source path (dr-frank-and-eddy) | What's there |
|---|---|---|
| `vendor/synthesis/equations/` | `synthesis/equations/` | 12 solvers + base.py + registry.py + llm_context.py + specs/ |
| `vendor/synthesis/core/` | `synthesis/core/` | dpd_engine.py + lindblad_rk4.py + probe_model.py + hardware_backends.py |
| `vendor/synthesis/quantum/tensor_lindblad.py` | `synthesis/quantum/tensor_lindblad.py` | MPS/TJM path for v1.x medium-systems extension |
| `vendor/grammar/` | `evolution/knowledge/grammar/` | grammar_loader.py + generator.py + parser.py + physics_v1.yaml |
| `vendor/grammar/sanskrit_codec.py` | `evolution/knowledge/sanskrit_codec.py` (and supporting codec_*.py files) | Full Sanskrit codec |
| `vendor/wobble/` | `wobble/` | disagreement_types.py + disagreement_classifier.py + supporting files |
| `vendor/actor/actor.py` | `evolution/knowledge/actor.py` | Typed Actor + signature/verify |
| `vendor/calibration_profile.json` | Generated from frank-data's source-side calibration suite output | Per-solver calibration constants + tolerances |

**SynQc TDS Core is NOT vendored.** Trinity Core's Orchestrate subsystem is greenfield Phoenix code (Section 2.5 + Section 10.3). The architecture's prior v0 commitment to vendoring SynQc files (`scheduler.py`, `probes/`, `demod.py`, `adapt.py`, `provider_clients/`) was reversed in the 2026-05-06 revision after Phase 1 build-guide drafting found SynQc's actual source structure (FastAPI service with auth/Redis/agents/jobs) unsuitable for verbatim vendoring. SynQc serves as a design reference for Orchestrate's contracts; Phoenix authors all Orchestrate code natively.

**`vendor/VENDOR_VERSION.txt`** is the single source of truth for what's vendored. Format:

```
phoenix_release: 1.0.0.dev<N>
vendor_synced_at: 2026-MM-DDTHH:MM:SS+00:00
dr_frank_and_eddy_commit: <40-char SHA>
calibration_profile_hash: <sha256 of vendor/calibration_profile.json>
```

Every Phoenix release pins these. The replay path (Section 1 Decisions 19-21) reads this file to verify the running vendor snapshot matches the ledger entry's recorded versions. Note: there is no `synqc_tds_commit` field — Orchestrate is greenfield Phoenix code under `phoenix/trinity/orchestrate/`, version-stamped by Phoenix's own `__version__`, not by an external commit.

**[OPEN: should the vendored modules retain their dr-frank-and-eddy import paths internally, or get rewritten to import from `phoenix.vendor.*`? Rewriting is cleaner architecturally but adds churn to the vendor sync script. Defer to Section 11; v0 specifies "vendored verbatim including imports" as the simpler option for v1.]**

## 10.3 — The phoenix/ package layout, by Section

Each architectural Section gets its own subdirectory under `phoenix/`. The layout below is the v1 commitment; module names are stable across the v1.x series.

**`phoenix/api/`** — Section 5 front door (REST + WebSocket).
- `routes.py` — FastAPI routes for all `/v1/...` endpoints.
- `admin_routes.py` — `/v1/admin/...` endpoints (Section 8).
- `ws_handlers.py` — WebSocket handlers for streaming task lifecycle and drift events.
- `error_envelope.py` — typed error envelope per Section 5.2.
- `openapi.yaml` — OpenAPI 3.1 schema (committed source of truth for API).
- `README.md` — front-door layer doc.

**`phoenix/cli/`** — Section 5 CLI surface.
- `entry.py` — `phoenix` command entry point.
- `commands/` — one file per command group (task, lora, identity, providers, audit, calibration, admin).
- `output_formats.py` — JSON/text/table renderers.
- `config_loader.py` — `~/.phoenix/config.yaml` parser.
- `README.md` — CLI doc.

**`phoenix/mcp/`** — Section 5 MCP server.
- `server.py` — FastMCP server, mirroring dr-frank-and-eddy's pattern.
- `tools.py` — tool registrations (calls REST internally per Section 5.5).
- `transport.py` — stdio + HTTP+SSE transport handlers.
- `README.md` — MCP doc with tool catalog.

**`phoenix/trinity/`** — Section 2 Trinity Core.
- `solver/` — Solver subsystem.
  - `engine.py` — adapts the vendored EquationSolver registry into Trinity's pipeline.
  - `cross_precision.py` — Axis 1 wobble (cross-grid-resolution).
  - `README.md` — Solver subsystem doc.
- `control/` — Control subsystem.
  - `engine.py` — adapts the vendored DPDScheduler into Trinity's pipeline.
  - `cross_probe.py` — Axis 2 wobble (probe-strength sweep).
  - `README.md` — Control subsystem doc.
- `orchestrate/` — Orchestrate subsystem (greenfield Phoenix code per Section 2.5).
  - `engine.py` — top-level orchestrator: takes (`VerifiedAnswer`, `ProviderSelection`) → runs the orchestration pipeline → returns `Result`.
  - `bundle_builder.py` — translates a `VerifiedAnswer` into a provider-specific submission (Qiskit circuit, Braket task, IonQ shot batch, classical-sim Hamiltonian). Pure translation, no I/O.
  - `provider_client.py` — `BaseProviderClient` Protocol + dispatch into the per-provider concrete adapters under `phoenix/providers/`. Handles connection management, submission, polling, raw-result return.
  - `result_extractor.py` — provider-specific raw results → Phoenix-uniform observables and `KPIBundle` fields. Pure post-processing, no I/O.
  - `drift_feedback.py` — emits drift signals to the Router intelligence (Section 4.6) and the drift detector (Section 6.5) from the just-completed solve's measured KPIs.
  - `cross_provider.py` — Axis 3 wobble (provider divergence).
  - `kpi_bundle.py` — typed KPIBundle aggregator (`fidelity`, `latency_us`, `backaction`, `shots_used`, `shot_budget`, `status`).
  - `README.md` — Orchestrate subsystem doc.
- `data_model.py` — `PhysicsTask`, `CandidateAnswer`, `VerifiedAnswer`, `Result` dataclasses.
- `pipeline.py` — the three-subsystem pipeline orchestrator.
- `README.md` — top-level Trinity Core doc.

**`phoenix/grammar/`** — Section 3 task grammar layer.
- `schema_validator.py` — JSON-Schema validation for structured-JSON entry point.
- `translator.py` — grammar parse tree → PhysicsTask translation.
- `lora_runtime.py` — LoRA adapter runtime invocation + subprocess sandbox.
- `README.md` — task grammar doc.

**`phoenix/router/`** — Section 4 router.
- `decision.py` — the seven-stage routing algorithm.
- `provider_registry.py` — provider health, queue depth, calibration history.
- `intelligence.py` — three-source fidelity/latency/cost estimator.
- `failover.py` — multi-provider failover protocol.
- `pricing/` — per-provider cost estimators.
  - `pricing_v1.json` — versioned pricing data.
- `README.md` — router doc.

**`phoenix/verification/`** — Section 6 verification gate.
- `gate.py` — the wobble protocol orchestrator. Parameterized by a list of `WobbleAxis` Protocol impls (Section 6.3) so v1.x extensions can register their own axes without forking the gate.
- `wobble_axis.py` — the `WobbleAxis` Protocol contract plus v1's three concrete impls: `CrossPrecisionAxis` (Axis 1), `CrossControlAxis` (Axis 2), `CrossProviderAxis` (Axis 3). Perception extension at Phase 20 adds `CrossModalityAxis`, `CrossFrameAxis`, `CrossCanonicalAxis` to the same Protocol contract.
- `rung_table.py` — the five-rung adaptive depth dial.
- `promotion.py` — promotion/demotion logic.
- `agreement_classifier.py` — extends vendored DisagreementFinding with physics-wobble values.
- `provenance.py` — VerificationProvenance composer.
- `README.md` — verification gate doc.

**`phoenix/safety/`** — Section 7 safety gate.
- `gate.py` — the nine-stage validation pipeline.
- `permissions.py` — `ActorPermissions` registry + lookup.
- `rate_limiter.py` — token-bucket implementation with org aggregation.
- `enrollment.py` — org-enrollment ceremony with HKDF subkeys.
- `override.py` — operator override flow for `HUMAN_REVIEW`.
- `README.md` — safety gate doc.

**`phoenix/admin/`** — Section 8 dev-ops backdoor handlers.
- `health.py` — `/v1/admin/health/detailed`, `/governor`, `/inference-status`, `/budget`.
- `calibration.py` — `/v1/admin/calibration/...` drill-down.
- `router_inspect.py` — `/v1/admin/router/...` and `/v1/admin/providers/...` inspection.
- `verification_inspect.py` — `/v1/admin/tasks-pending-review` + override invocation.
- `kill_switch.py` — kill switch state + handlers.
- `audit_replay.py` — `/v1/admin/audit/replay` + `/v1/admin/ledger/integrity-report`.
- `README.md` — dev-ops backdoor doc.

**`phoenix/ledger/`** — hashchained provenance store.
- `omega_ledger.py` — vendored Omega Ledger pattern, extended for replay support.
- `entry_types.py` — typed ledger entry shapes (solve, override, kill-switch, enrollment, etc.).
- `replay_engine.py` — Section 1 Decision 19's replay path.
- `README.md` — ledger doc.

**`phoenix/audit/`** — structured event log.
- `event_format.py` — native Phoenix event format (typed dataclasses).
- `emitter.py` — fire-and-forget async event writer.
- `otel_adapter.py` — OpenTelemetry export per Section 1 Decision 22.
- `jsonl_writer.py` — default local-file destination.
- `README.md` — audit doc.

**`phoenix/identity/`** — per-install Ed25519 + org enrollment storage.
- `keystore.py` — DPAPI/Keychain/libsecret abstractions.
- `bootstrap.py` — first-run keypair generation.
- `org.py` — org subkey derivation + storage.
- `README.md` — identity doc.

**`phoenix/adapters/`** — LoRA adapter loading + sandbox.
- `protocol.py` — `LoRAAdapter` Protocol.
- `loader.py` — adapter discovery + load orchestration.
- `validator.py` — inference-time round-trip validation.
- `sandbox.py` — subprocess isolation + timeout enforcement.
- `README.md` — adapter doc.

**`phoenix/providers/`** — provider adapters.
- `base.py` — Frankenstein 1.0 `ProviderAdapter` ABC, vendored.
- `quantum/` — IBM, Braket, IonQ adapters.
- `classical/` — local NPU/GPU/CPU.
- `cognition/` — placeholder for v1.1 (Anthropic/OpenAI/Google).
- `cloud_gpu/` — placeholder for v1.1 (Lambda/RunPod).
- `README.md` — provider adapter pattern doc.

**`phoenix/state/`** — state backend.
- `backend_protocol.py` — abstract `StateBackend` interface.
- `sqlite_backend.py` — default zero-config implementation.
- `postgres_backend.py` — opt-in for org deployments.
- `migrations/` — schema migrations versioned with Phoenix releases. Tables include: `solve_cost_ledger` (Section 4.7 cost accounting), `kill_switch_state` (Section 8.3 persistence), `actor_permissions` (Section 7.3 registry), `audit_events` (Section 1 Decision 16), `pending_review_queue` (Section 7.7 operator override).
- `README.md` — state backend doc.

**`phoenix/queue/`** — NATS JetStream client.
- `nats_client.py` — connection management.
- `task_queue.py` — task submission and worker dispatch.
- `embedded_runner.py` — embedded NATS process launcher for solo deployments.
- `README.md` — queue doc.

**`phoenix/_internal/`** — utilities.
- `logging.py` — Phoenix's structured logger.
- `config.py` — config file parsing.
- `version.py` — Phoenix version constant + vendor version reader.
- `errors.py` — root exception hierarchy.
- `cloud_seams.py` — three Protocol definitions for the Phoenix Cloud abstraction seams (Section 1 Decision 35; specified concretely in Section 10.3.1 below).

Every directory has a `README.md`. The READMEs are the documentation surface ops users and integrators reach for first when something goes wrong; they are not optional.

## 10.3.1 — Phoenix Cloud abstraction seams (concrete spec)

Section 1 Decision 35 commits Phoenix v1 to ship three thin abstraction seams that let Phoenix Cloud (a future *separate* product) wrap Phoenix-the-middleware with multi-tenant hosting plus the commercial bundle (enterprise SSO, audit-log retention SLA, white-glove drift recalibration, dedicated provider rate contracts). The seams are useful even outside the hosted scenario, so v1 ships local default implementations that work for solo and on-prem org installs.

The three seams live in `phoenix/_internal/cloud_seams.py` as `typing.Protocol` definitions plus a default registry. Phoenix code calls the seam interfaces; the default implementations satisfy them; Phoenix Cloud (when it lands) replaces the implementations without modifying Phoenix.

**Seam 1 — `HttpAuthExtractor`.** Where the front door reads the Actor from a request's transport-level metadata. Default impl reads the `X-Phoenix-Actor` header carrying the typed Actor payload. Phoenix Cloud's impl reads the tenant-scoped session cookie set by the upstream SSO proxy, looks up the bound Actor in the hosting layer's identity store, and synthesizes an Actor signed by the tenant's HKDF subkey.

```python
class HttpAuthExtractor(Protocol):
    def extract_actor(self, request: Request) -> Actor:
        """Return a verified Actor from transport metadata, or raise AuthError.
        Called once per HTTP request, before Section 7's safety gate runs."""
```

**Seam 2 — `AuditLogExporter`.** Where structured audit events flow when emitted by Phoenix's `phoenix.audit.emitter`. Default impl appends to the local JSONL file plus the OTel adapter (Section 1 Decisions 16, 22). Phoenix Cloud's impl additionally writes to the hosting layer's tamper-evident long-term retention store with the SLA-bearing retention contract.

```python
class AuditLogExporter(Protocol):
    def export(self, event: AuditEvent) -> None:
        """Persist an audit event. MUST be fire-and-forget non-blocking; MUST NOT
        raise to the caller (errors go to Phoenix's internal error log instead).
        Called from the audit emitter's async writer."""

    def flush(self, timeout_s: float) -> bool:
        """Block until pending events are durably written, or timeout. Returns
        True if all events flushed, False if timeout reached. Called on graceful
        shutdown and on kill-switch engage (Section 8.3)."""
```

**Seam 3 — `JobBudgetController`.** Where per-job resource budgets are checked and decremented. Default impl reads from the local SQLite/Postgres `solve_cost_ledger` table and enforces the defaults from Section 4.7 (per-solve, per-actor-per-24h, per-org-per-24h ceilings). Phoenix Cloud's impl additionally consults the tenant's commercial contract — dedicated provider rate contracts, prepaid blocks of compute, etc. — and emits billing events to the hosting layer's billing system.

```python
class JobBudgetController(Protocol):
    def check_solve_budget(
        self,
        actor: Actor,
        estimated_cost_usd: float,
        reproducibility_mode: ReproducibilityMode,
    ) -> BudgetDecision:
        """Decide whether to allow the solve based on per-solve, per-actor-24h,
        per-org-24h ceilings. Returns BudgetDecision with allowed/denied,
        remaining budget, and rationale. Called by the Router before Stage 2."""

    def record_solve_cost(
        self,
        actor: Actor,
        request_id: str,
        actual_cost_usd: float,
        provenance: dict,
    ) -> None:
        """Write the measured solve cost to the ledger. Called post-solve from
        the Orchestrate subsystem with the final KPIBundle's cost figures."""
```

**Registry and replacement (generic, name-keyed):**

```python
class CloudSeams:
    """Generic name-keyed registry of cloud seam Protocol implementations.

    Default constructor registers Phoenix v1's three seams (`auth`, `audit`, `budget`)
    with their local default impls. Phoenix Cloud's process startup overrides specific
    seams via `register()` before opening the front door. Phoenix code reaches a seam
    through `phoenix._internal.cloud_seams.get(name)` — never by direct import of the
    default impls.

    Generic-by-design (not hardcoded with three named slots) so v1.x extensions can
    register additional seams without modifying core. The perception harness extension
    locked at v1.1 (`PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md` Section 2 substrate audit)
    plans an optional fourth seam (`canonical_library`) for hosted, retention-SLA-bearing
    canonical-example libraries that the perception phase 19 deliverable consumes.
    """

    def register(self, name: str, impl: Any) -> None:
        """Replace (or register) the implementation for `name`. Phoenix Cloud calls
        this once per seam at process startup before the front door opens."""

    def get(self, name: str) -> Any:
        """Return the active impl for `name`. Raises `UnknownSeam` if unregistered.
        Phoenix code calls this lazily, on demand."""

    def names(self) -> list[str]:
        """Return registered seam names (for dev-ops introspection only)."""
```

The three v1 seams (`auth` → `HttpAuthExtractor`, `audit` → `AuditLogExporter`, `budget` → `JobBudgetController`) are registered at startup via the default constructor's seam-loading routine. Their Protocol contracts are unchanged from the v1.0 spec; only the registry shape is refactored from named-slot fields to a generic dict.

**SAFETY:** the seam Protocols deliberately do *not* expose mutation surfaces beyond what each seam owns. A Phoenix Cloud implementation cannot inject a fake Actor for a different tenant via `HttpAuthExtractor` because the returned Actor is still HMAC-verified at Section 7's safety gate against the tenant's HKDF subkey — the seam can only return *some* Actor, not bypass signature verification. Similarly, `JobBudgetController` cannot suppress safety-gate denials; it can only deny solves that would otherwise be allowed by the safety gate. Adding a fourth seam at v1.x extends the registry but does NOT relax these guarantees — every new seam Protocol carries the same defense-in-depth constraint that the seam can only contribute information into the existing safety/audit gates, never bypass them. The `register()` API does not allow replacing the safety gate or the HMAC verifier.

**Extension discipline.** Registering an unknown name (e.g. `cloud_seams.register("canonical_library", MyImpl())`) succeeds silently in v1; consumers that don't know the seam exist won't call `get("canonical_library")`. v1.x extensions follow this pattern: the perception phase 12 build guide registers `canonical_library` at perception-startup and only the perception subsystem queries it. The core `phoenix._internal.cloud_seams` module knows nothing about perception.

**v1 acceptance:** Phoenix v1 ships the Protocols plus default impls plus a `tests/integration/test_cloud_seams.py` that swaps in a mock Phoenix-Cloud-shaped impl and verifies the three seams compose correctly without modifying Phoenix code. Specifically, the test confirms: (1) a request with a synthesized Actor from the mock auth extractor flows through the safety gate normally; (2) audit events written by Phoenix reach both the local JSONL writer *and* the mock Phoenix Cloud retention store; (3) a tenant-scoped budget denial from the mock budget controller surfaces as `CostCeilingExceeded` to the user with no leak of tenant-scoped state into Phoenix-the-middleware.

This is the seam-level "build it right once" guarantee. When Phoenix Cloud ships as a real product, its implementations replace the defaults, no Phoenix code changes, and the test suite still passes.

## 10.4 — The vendor sync script

`scripts/vendor_sync.py` is the script that produces `vendor/` from the upstream frank-data source. It's invoked manually before each Phoenix release and never at runtime — `vendor/` is committed to the repo so that fresh clones get the substrate without external dependencies.

**Inputs:**
- A path to a clean dr-frank-and-eddy clone at the desired commit.
- A target Phoenix version string.

(SynQc TDS Core is NOT a vendor source — Orchestrate is greenfield Phoenix code per Section 2.5 and Decision 37. Earlier v0 spec drafts named SynQc as a vendor input; the 2026-05-06 revision removed that.)

**Behavior:**
1. Validates that the source clone is at a known-good commit (manifest of accepted commit hashes ships with Phoenix).
2. Runs the dr-frank-and-eddy Tier-1 calibration suite on the source. Refuses to proceed if any calibration test fails.
3. Copies the file-by-file mapping from Section 10.2 into `vendor/`.
4. Generates `vendor/VENDOR_VERSION.txt` with the resulting commit hash and calibration profile hash.
5. Runs Phoenix's vendor-integrity tests (Tier-1 battery + grammar invariant suite + DPD self-test) against the freshly-vendored substrate.
6. Reports diff vs. previous vendor sync; any unexpected changes (file added/removed/renamed in the source) fail the script and require manual review.

**SAFETY:** the script never writes outside `vendor/`. **SAFETY:** `vendor_sync.py` requires admin permission on the running Phoenix install to invoke; this is a privileged operation tied to release engineering, not normal operation.

**[OPEN: should the vendor sync support multi-source vendoring (e.g., vendor parts from dr-frank-and-eddy v6.6 and parts from a hypothetical v6.8)? v0 specifies single-version vendoring per Phoenix release. Multi-source deferred to Section 11.]**

## 10.5 — Launcher and desktop shortcut

Per Adam's standing rule from the user memories ("guides also update the code that launched and runs Dr Frank and Eddy from my desktop shortcut"), Phoenix v1 ships its own desktop launcher chain. **dr-frank-and-eddy's launcher is not modified.** Phoenix gets its own.

**`scripts/launch.bat`** (Windows):

```bat
@echo off
REM Phoenix v1 launcher
REM Boots NATS JetStream + Phoenix daemon + opens default browser

cd /d C:\Phoenix

REM 1. Verify vendor integrity quickly
python -m phoenix.cli verify-vendor --quick

REM 2. Boot NATS JetStream (Section 1 Decision 32)
start /B nats-server -js -sd C:\Phoenix\.runtime\nats

REM 3. Boot Phoenix daemon (REST + WS + MCP server)
start /B python -m phoenix.api --port 8003

REM 4. Open browser to local docs
start http://localhost:8003/docs

REM 5. Wait for shutdown signal
echo Phoenix v1 running. Press Ctrl+C to stop.
pause
```

**`scripts/launch.sh`** is the macOS/Linux equivalent.

**`scripts/create_shortcut.ps1`** installs a Windows desktop shortcut pointing at `launch.bat`. The shortcut is created with a Phoenix-branded icon (a stylized rising-bird glyph; placeholder for v1) and the working directory set to `C:\Phoenix\`.

**Coexistence with dr-frank-and-eddy:** Phoenix runs on port 8003 by default; dr-frank-and-eddy runs on port 8002. The two can run concurrently on the same machine. Both desktop shortcuts can sit on Adam's desktop at the same time, each launching its own subsystem. **SAFETY:** the launcher refuses to start if port 8003 is already in use; Phoenix never silently bumps to a different port because that breaks the shortcut → URL → docs flow.

**Build-guide rule:** every future Phoenix build guide that touches startup behavior (new env-var handling, new warmup routine, NATS configuration changes, calibration drill on first run) must update `launch.bat`, `launch.sh`, and `create_shortcut.ps1` together. This is the same rule Adam applies to dr-frank-and-eddy build guides.

## 10.6 — README plan (per-section requirement)

Adam's standing rule ("Create an individual readme file for each section of this repository to give maximum context and notes on bug fixes and troubleshooting") shapes Phoenix's documentation surface. Every directory under `phoenix/` and `vendor/` has a `README.md`.

The README content template is consistent across modules:

```markdown
# phoenix/{module-name}

## Purpose
One-paragraph description of what this module does.

## Architectural reference
Link to the Section in PHOENIX_ARCHITECTURE_v0.md that specifies this module.

## Key files and their roles
Table mapping each file to its function.

## Vendored substrate (if applicable)
Which files are vendored from where; pointer to vendor/VENDOR_VERSION.txt for the pinned commit.

## Common failure modes
Bulleted list of typical bugs encountered in this module, root causes, and resolutions.

## Troubleshooting
How to diagnose when this module misbehaves: logs to check, environment variables to set, audit-event types to filter on.

## Tests
Pointer to the test files that exercise this module.

## Recent changes
Per Adam's troubleshooting-log discipline from dr-frank-and-eddy: a chronological list of significant changes (and the build guide that produced them).
```

This makes every README scannable in seconds and useful to anyone (human or AI agent) trying to understand what a specific module does or why something is failing.

## 10.7 — Acceptance criteria for Phoenix v1

A Phoenix v1 release is not complete until all of the following pass:

**Vendor integrity:**
- `vendor/VENDOR_VERSION.txt` is populated with valid hashes.
- All 12 vendored solvers pass their calibration tests against the v6.6 calibration profile.
- The vendored grammar's 31-test invariant suite passes.
- The vendored DPD engine's self-test passes.

**Trinity Core pipeline:**
- `tests/tier1/` — all five Tier-1 benchmarks (HO-1, ISW-1, H1S-1, RABI-1, SCG-1) execute end-to-end through Trinity Core (Solver → Control → Orchestrate) at all three reproducibility modes.
- A Tier-1 benchmark in `default` mode completes within 100 ms wall-clock on local hardware (Section 1 Decision 26's batch real-time target).
- The same benchmark in `replay` mode completes within 250 ms wall-clock and bit-exact-matches the original Result.

**Three-axis wobble:**
- A task with `max_error_bar=1e-6` selects rung R5 and exercises all three axes.
- A task with `max_error_bar=1e-2` selects R1 and skips cross-axis comparison.
- Promotion and demotion behavior matches Section 6.4's table.
- The `DisagreementFinding` includes the full distance matrix, never collapsed.

**Front door:**
- All four protocols (REST, WebSocket, CLI, MCP) successfully submit a Tier-1 benchmark and retrieve the Result.
- Cross-protocol audit-log correlation works (a single `request_id` is visible across REST → audit-log → ledger → MCP).
- The standalone-binary distribution boots and serves a task within 10 seconds of launch.

**Safety:**
- An unauthenticated request fails with `401 AuthError` and emits a structured audit event.
- A non-`frontier_physics` actor submitting a Wheeler-DeWitt task fails with `403 FrontierPhysicsRefused`.
- Rate limiting denies the 101st request from a default-tier actor in a 60-second window.
- Org enrollment via bootstrap token derives a working subkey.

**Provider routing:**
- Routing across at least three providers (local sim + IBM Quantum + AWS Braket) demonstrates the full decision algorithm.
- Failover from a manually-quarantined provider correctly proceeds to the alternate.
- Cost estimation for at least IBM and Braket pricing matches expected values within 5%.

**Verification + drift:**
- The continuous drift monitor's three detectors run on schedule.
- A simulated drift (deliberately injected miscalibration) triggers `drift_warning` within 6 hours of injection.
- A drifted state triggers automatic rung promotion on subsequent tasks.

**Cost-ceiling enforcement (added 2026-05-06):**
- The Section 4.7 default budgets are wired through the Router's Stage 2 filter, the verification gate's promotion check, and the post-solve accounting writer.
- A solve estimated to exceed the per-solve ceiling is rejected with `CostCeilingExceeded` (not `NoEligibleProvidersError`).
- A solve whose verification promotion would exceed the ceiling ships with `agreement_type=DEGRADED_BUDGET_BOUND` and a `budget_bound_skipped_axis` provenance field.
- An admin actor's `POST /v1/admin/budget/override` grants a temporary budget bump that decays at the specified `expires_at`; override events land as top-priority audit events and Omega Ledger links.

**Cloud-quantum reproducibility honesty (added 2026-05-06):**
- Every Result whose pipeline touched a cloud-quantum provider has `provenance.cloud_shots_recorded=True`.
- CLI/MCP/WebSocket surfaces display the asterisk prominently (not buried in a sub-field).
- `docs/reproducibility/` opens with the asterisk explained, before the strict/replay mode descriptions.

**Phoenix Cloud abstraction seams (added 2026-05-06):**
- `phoenix/_internal/cloud_seams.py` ships with `HttpAuthExtractor`, `AuditLogExporter`, and `JobBudgetController` Protocol definitions plus default local implementations.
- `tests/integration/test_cloud_seams.py` swaps mock Phoenix-Cloud-shaped impls and verifies all three seams compose without modifying Phoenix code (per Section 10.3.1).

**Compositional fail-closed test ("panic mode," added 2026-05-06):**
- A deliberate test that simultaneously: (a) crashes the NATS JetStream connection, (b) makes the state backend (SQLite/Postgres) unreachable, and (c) marks one of the three drift detectors as unavailable.
- Phoenix must fail fast and loud with typed errors (`QueueUnavailable`, `StateBackendUnavailable`, `DriftStateUnavailable`) — never silently degrade or return a result without verification.
- The test verifies that fail-closed posture composes: multiple simultaneous failures produce a deterministic error response naming the *first* failing condition, not a confused mixture.

**Long-window replay test (added 2026-05-06):**
- Take a v1.0 ledger entry whose solve completed cleanly, store it for 6+ simulated months, then run `POST /v1/tasks/{id}/replay` against v1.0 (or v1.0+patch) and confirm bit-exact match.
- Verifies the `requirements.lock` + `vendor/VENDOR_VERSION.txt` + per-RNG-seed + FP-environment discipline (Section 1 Decision 21) actually composes for long-window reproducibility — not just same-day replay.
- Failure modes the test must catch: a vendored library was upgraded silently in CI; a RNG seed was not recorded; the FP environment differs across machines. The test runs on CI's typical hardware *plus* a clean Linux container *plus* a clean macOS runner, so platform-specific drift is also caught.

**Reference admin client:**
- *(Acceptance moved to v1.1 — see Section 10.8.)*

**Documentation:**
- Every directory under `phoenix/` and `vendor/` has a non-empty `README.md`.
- The OpenAPI schema validates against the OpenAPI 3.1 spec.
- The user-facing `docs/getting-started/` walks a new user from pip install to first solve in <10 minutes.

**Distribution:**
- All three release artifacts (pip wheel, Docker image, Nuitka binary) build cleanly in CI.
- All three artifacts pass the full integration suite.

These criteria are the floor. Build guides for v1 implementation will reference Section 10.7 as the definition-of-done; every closing build guide ends with a verification pass against these criteria.

## 10.8 — Acceptance criteria for Phoenix v1.1

Section 10.7 is the floor for v1. Section 10.8 captures items that are deferred from v1 — finishing v1 should not be gated on them — but commit to v1.1 once v1 ships and stabilizes. The list grows as defer-to-v1.x dispositions in Section 11 land their resolutions.

**Reference admin client (moved from v1, 2026-05-06):**
- The reference admin client (separate repo, e.g. `nah414/phoenix-reference-client`) successfully exercises Patterns 1, 2, and 3 (Section 9.3) against Phoenix v1 with the bootstrap actor.
- Justification for moving: the client is a separate codebase, separate release cadence, and finishing it does not gate a Phoenix release. v1 ships when Phoenix v1 is ready; the reference client follows on its own track and earns acceptance against v1.1 (or against v1.0 retroactively, whichever lands first).

**Perception harness extension (added 2026-05-07 in v1.1):**
- All perception phases (Phase 12 through Phase 22) shipped per `PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md`. Phase numbering continues v1's sequence; perception phases land after v1's Phase 11 release.
- Perception's Tier-1 calibration battery (canonical examples per weather mode) passes for the supported modes (`clear`, `light_rain`, `heavy_rain`, `light_snow`, `heavy_snow`, `fog`, `night_clear`).
- Three-axis perception wobble verification (cross-modality, cross-frame, cross-canonical) produces typed `Result(value, error_bar, sigma, agreement_type)` for perception solves, mirroring v1's Decision 13 contract.
- Penrose temporal pulse coding simulator (Phase 16) demonstrates ≥20% reduction in residual-error point cloud reconstruction error vs M-sequence baseline at 20% rain-induced corruption rate. Phase 16 also passes the Q5.2 scalability gate: a mock hardware driver implementing the same `LidarTransmitter`, `LidarReceiver`, and `InterferenceModel` Protocols can be swapped for the simulator implementations and produce structurally compatible outputs without modifying decoder or interference-model code.
- Front-door endpoints for perception (REST `/v1/perception/*`, WebSocket streaming, CLI `phoenix perception`, MCP perception tools) all exercise the perception pipeline end-to-end.
- Cross-protocol audit-log correlation works for perception requests (single `request_id` traces across REST → audit-log → ledger → MCP), mirroring v1's existing acceptance test pattern.
- Justification for landing in v1.1 rather than v1: the perception extension reuses 70-80% of v1's substrate (vendored Sanskrit codec, grammar substrate, wobble framework, Actor authentication, Omega Ledger pattern, cloud seams) and cleanly extends the existing architecture as Phase 12+ work. Including perception in v1 acceptance would block v1 release on substantial additional work; positioning as v1.1 lets v1's quantum-accuracy core ship cleanly while perception planning is locked and ready for execution after v1 reaches its Phase 5 milestone.

**Items deferred to v1.x per Section 11.9 (acceptance is shaped at the time the disposition resolves):**
- Error-bar combiner refinement based on empirical covariance data (S11.1.1).
- MPS truncation axis decision based on v1.x medium-systems data (S11.1.2).
- Adaptive-depth threshold tuning based on production usage (S11.1.3).
- Per-install rung-table customization (S11.3.2).
- OS keychain attestation for frontier-physics signing (S11.4.1).
- Org-level permission inheritance (S11.4.2).
- Org root keypair rotation (S11.4.3).
- Prometheus metrics endpoint (S11.5.3).

Each of these is a separate v1.x build guide; none of them block v1.

```
=== SECTION 10 COMPLETE — AWAITING ADAM REVIEW ===
```

---

# Section 11 — Open Design Tensions

## What Section 11 covers

Sections 2 through 10 introduced architectural commitments and, where decisions could not be made cleanly with the information available, flagged the tension with `[OPEN: ...]` markers. Section 11 consolidates every one of those tensions in a single place, organized by category, with framing for each tension, the v0 placeholder behavior, the resolution path, and a recommended disposition.

The architectural principle here is **explicitness**. Phoenix is being designed as instrument-grade middleware, which means deferred decisions cannot be hidden in the doc's prose — they need to be visible, traceable, and resolved deliberately rather than absorbed silently into implementation. Section 11 is what makes that possible.

The dispositions presented here are *recommendations*. Adam decides which ones to accept, modify, or reject. Once a disposition lands, the open tension converts into either a locked decision (folded into Section 1) or a tracked-for-later item (flagged for v1.x or research work).

For traceability, Section 11.13 contains a table mapping every `[OPEN: ...]` marker in Sections 2-10 to its catalog entry below.

## 11.1 — Correctness and algorithm tensions

These tensions concern numerical or algorithmic correctness of the physics machinery itself. They cannot be resolved purely from architecture — they need empirical data from running Phoenix to know the answer.

### 11.1.1 — Error-bar combiner formula (RESOLVED in Phoenix v1.1)

**Origin:** Section 2.2.

**Tension:** Section 2.2 specified that the combined error bar across the three Trinity Core layers is the quadrature sum: `error_bar = sqrt(error_bar_solver**2 + error_bar_control**2 + error_bar_orchestrate**2)`. Quadrature is correct *if* the three layer errors are statistically independent. They are *approximately* independent — Solver's grid-truncation error has nothing to do with Control's probe back-action, which has nothing to do with Orchestrate's provider shot noise — but not exactly, because all three operate on the same underlying Hamiltonian and a pathologically constructed problem could correlate them.

**v0 placeholder:** Quadrature.

**Recommended disposition (v1):** Ship with quadrature; instrument per-axis error bars in every ledger entry; revisit at v1.1 once enough Phoenix solves exist for real covariance analysis.

**Resolution (Phoenix v1.1, approved by Adam 2026-05-20):** Adopt quadrature as the locked v1.1 disposition. Phase 7's Omega Ledger entry shape (`SolveEntry.verification_provenance`) already records per-axis error bars in every solve, satisfying the instrumentation prerequisite. Empirical covariance analysis remains a v1.x activity that depends on accumulated ledger history. The quadrature placeholder is safe-direction (overestimates error when correlations exist), which is the right error-bar polarity for an instrument-grade product. **Phase 13 hindsight:** cognition wobble axes (`CrossModelAxis`, `SelfConsistencyAxis`, `PromptPerturbationAxis`) emit a single distance metric per axis rather than three independent error bars, so the quadrature-vs-correlated tension does not apply to the cognition path — cognition axes are aggregated by the existing `CognitionDisagreementMetric` shape, not by quadrature combination.

**Cross-reference:** Section 2.2's quadrature formula stands; revisit ticket logged for v1.x once empirical covariance data exists.

### 11.1.2 — MPS truncation error as a fourth wobble axis (RESOLVED in Phoenix v1.1)

**Origin:** Section 2.8.

**Tension:** Section 1 Decision 27 commits to v1.x extending Solver to medium-system tensor-network execution via MPS/TJM. Tensor networks introduce a *bond-dimension truncation error* that doesn't exist in dense-matrix solvers. This error is well-understood theoretically — it scales with the Schmidt-coefficient cutoff — but it is its own disagreement axis distinct from cross-precision (Axis 1).

**v0 placeholder:** Treat MPS truncation error as part of `error_bar_solver` (rolled into Axis 1).

**Recommended disposition (v1):** Roll into Axis 1 for v1; revisit when v1.x lands MPS.

**Resolution (Phoenix v1.1, approved by Adam 2026-05-20):** Roll into Axis 1 as the locked v1.1 disposition. **Phase 13 hindsight:** the `WobbleAxis` Protocol (Phase 2) plus the v1.1 follow-up generalization (axes plug in by name rather than fixed slots) means adding a fourth physics axis later is purely additive — no Trinity Core refactor required. The decision to roll-vs-promote is now data-driven: when v1.x ships the MPS path, the rolled-up Axis 1 will surface meaningful MPS-vs-dense disagreement if it exists, and the empirical case for a fourth axis becomes either obvious (promote) or absent (keep rolled). This disposition reaffirms that decision posture and removes the tension from v1's open list.

**Cross-reference:** Section 2.8 stands. The MPS-axis decision is properly a v1.x build-guide question that depends on the MPS extension actually landing.

### 11.1.3 — Adaptive-depth threshold tuning (RESOLVED in Phoenix v1.1)

**Origin:** Sections 2.6 and 6.4.

**Tension:** Section 6.4 specified the five-rung adaptive depth table with concrete `max_error_bar` thresholds (R1 for `>1e-2`, R2 for `1e-3 to 1e-2`, etc.) and promotion/demotion criteria. The table is a starting heuristic; the right thresholds are empirically determined by running real workloads.

**v0 placeholder:** The thresholds in Section 6.4.

**Recommended disposition (v1):** Ship with Section 6.4 thresholds unchanged; instrument rung selection + promotion/demotion in the audit log; expose tuning via config in v1.1 once data exists.

**Resolution (Phoenix v1.1, approved by Adam 2026-05-20):** Adopt Section 6.4 thresholds as the locked v1.1 disposition. Phase 7's audit emitter already records rung selection + promotion/demotion events as structured audit events, satisfying the instrumentation prerequisite. Per-install config-driven threshold tuning is folded into the same v1.x deferral as 11.3.2 (per-install rung-table customization) — when one ships, the other ships, because they're the same surface from the user's perspective. **Phase 13 hindsight:** cognition tasks bypass the rung table entirely (they route through safety-gate stage 6b and are aggregated by `CognitionDisagreementMetric` rather than the five-rung physics flow), so this disposition concerns the physics path only.

**Cross-reference:** Section 6.4 stands. Per-install tuning ships at v1.x alongside 11.3.2.

### 11.1.4 — LoRA adapter validation suite content (RESOLVED in Phoenix v1)

**Origin:** Sections 2.7 and 3.5.

**Tension:** When a LoRA adapter is loaded, Phoenix runs an inference-time validation: the adapter must round-trip a small set of canonical grammar statements correctly. Sections 2.7 and 3.5 commit to this gate but do not specify *which* canonical statements are in the suite.

**v0 placeholder:** Suite content unspecified; defaults to "8-16 grammar statements" with the specific selection deferred.

**Recommended disposition (v0):** Build-guide territory — specify the suite during build-guide work for the adapter loader.

**Resolution (Phoenix v1, Phase 9 shipped 2026-05-12):** Suite content lives in `phoenix/adapters/validator.py` per Phase 9's LoRA adapter subsystem build. The validator implements the inference-time round-trip gate against a curated canonical set covering the major `physics_v1.yaml` non-terminals. Test vectors are an implementation detail of `phoenix/adapters/validator.py`; the architecture-level contract (round-trip must succeed; load fails fast on regression) is locked in Sections 2.7 + 3.5.

**Cross-reference:** `phoenix/adapters/validator.py` + Phase 9 build guide (`BUILDGUIDE_phoenix_v1_phase9_adapters_mcp_cli.md`).

## 11.2 — Provider routing tensions

### 11.2.1 — Provider equivalence rules for failover (RESOLVED in Phoenix v1)

**Origin:** Sections 2.5 and 4.5.

**Tension:** When Phoenix fails over from a degraded provider to an alternate, it needs to know that the alternate is *equivalent enough* to produce comparable results. The general question — when is "circuit X on IBM Eagle" equivalent to "circuit X on IBM Brisbane" or "circuit X on IonQ Forte" — is genuinely a research problem. Different qubit topologies, different gate sets, different fidelity profiles all matter.

**v0 placeholder:** Conservative equivalence defaults — same `quantum_technology` enum value AND same number of qubits AND fidelity within 10% of original — plus a manual override interface for users who know better.

**Recommended disposition (v0):** Ship with conservative defaults + manual override; `phoenix/router/equivalence_registry.py` accepts user-curated rules; learned-equivalence mode deferred to v1.x.

**Resolution (Phoenix v1, Phase 4 shipped 2026-05-08):** Conservative equivalence defaults shipped in `phoenix/router/equivalence_registry.py` per Phase 4's Router subsystem build. The registry holds user-curated rules per circuit class; `/v1/admin/router/...` exposes inspection. Learned-equivalence mode (mining ledger history for empirically-validated equivalence rules) remains v1.x scope. **PERF:** conservative defaults trigger more failovers than a learned mode would; manual overrides are the user's escape hatch. That's the locked tradeoff.

**Cross-reference:** `phoenix/router/equivalence_registry.py` + Sections 2.5, 4.5, 8.2. Learned-equivalence ships at v1.x.

### 11.2.2 — Provider pricing update cadence (RESOLVED in Phoenix v1)

**Origin:** Section 4.7.

**Tension:** Provider pricing data ships as `phoenix/router/pricing/pricing_v1.json` per Phoenix release. Provider pricing changes roughly quarterly; Phoenix releases ship more often than that. When pricing data lags behind reality, what should Phoenix do — warn, hard-error, or silently best-effort?

**v0 placeholder:** Unspecified; flagged as needing decision.

**Resolution (Phoenix v1, approved by Adam 2026-05-06):** Soft warn on stale pricing, never hard-error. Pricing data going stale doesn't make Phoenix produce wrong physics; it makes cost estimates inaccurate. Hard error would deny user work because of a number that's a hint, not a constraint; silent best-effort would let users spend money assuming an inaccurate estimate. Implementation: `pricing_data_staleness_days` field in every routing decision's `decision_provenance`; warning fires when staleness exceeds 90 days; `phoenix admin pricing-update` admin command refreshes out-of-band.

**Cross-reference:** Section 4.7 now contains the locked decision.

### 11.2.3 — Multi-source vendoring (RESOLVED in Phoenix v1.1)

**Origin:** Section 10.4.

**Tension:** Phoenix vendors dr-frank-and-eddy and SynQc TDS. Could a future Phoenix release vendor parts of dr-frank-and-eddy v6.6 *and* parts of dr-frank-and-eddy v6.8 (e.g., new physics from v6.8 but the proven calibration profile from v6.6)?

**v0 placeholder:** Single-version vendoring per Phoenix release.

**Recommended disposition (v0):** Single-version through v1 and v1.x; revisit when a compelling concrete need emerges.

**Resolution (Phoenix v1.1, approved by Adam 2026-05-20):** Single-version vendoring locked through v1 and v1.x. **Phase 13 hindsight:** the cognition substrate added `vendor/cognition_wobble/` alongside `vendor/{actor, grammar, ml, omega, synthesis, wobble}/` — a sixth+seventh vendored substrate without straining the single-version discipline. The substrate count went up; the per-substrate version discipline stayed clean. Multi-source vendoring (different *versions of the same substrate* in one release) is architecturally distinct from multi-substrate vendoring and remains out of scope.

**Cross-reference:** Section 10.4 stands. Single-version discipline carries into v1.x.

## 11.3 — Configuration knob tensions

### 11.3.1 — Pagination conventions for list endpoints (RESOLVED in Phoenix v1)

**Origin:** Section 5.2.

**Tension:** `/v1/audit/events` and `/v1/providers/{id}/backends` return potentially-large result sets. Pagination can be cursor-based or offset-based. Each has different semantics under SQLite vs. Postgres backends.

**v0 placeholder:** Unspecified.

**Resolution (Phoenix v1, approved by Adam 2026-05-06):** Cursor-based pagination, with the cursor opaque to the client (server-encoded). Works correctly on both SQLite and Postgres without semantic differences and handles concurrent inserts during pagination correctly. Server-encoded opacity lets the cursor format evolve without breaking client integrations. Every paginated response carries `next_cursor` and `prev_cursor` in its envelope; clients pass them unchanged.

**Cross-reference:** Section 5.2 now contains the locked decision.

### 11.3.2 — Per-install rung-table customization (RESOLVED in Phoenix v1.1)

**Origin:** Section 6.4.

**Tension:** Section 6.4's five-rung table is fixed. Some users may want different defaults — e.g., a research lab that always wants R4, or an exploration-mode user who's fine with R1 always.

**v0 placeholder:** Fixed table, no per-install customization.

**Recommended disposition (v0):** Defer to v1.x once user feedback clarifies the right configuration shape.

**Resolution (Phoenix v1.1, approved by Adam 2026-05-20):** Fixed table locked for v1.1. Per-install customization ships at v1.x alongside 11.1.3 (adaptive-depth threshold tuning); both are the same surface from the user's perspective (config-driven physics-task verification depth). The v1.1 lock is "predictable behavior across installs"; customization adds opt-in surface, not changed defaults.

**Cross-reference:** Section 6.4 stands. Bundled with 11.1.3 for v1.x ship.

### 11.3.3 — Standalone binary daemon bundling (RESOLVED in Phoenix v1)

**Origin:** Section 5.4.

**Tension:** Section 1 Decision 29 commits to a Nuitka standalone binary. The binary can either *bundle* the Phoenix daemon (heavier artifact, simpler deployment) or always *connect* to a separately-running daemon (lighter binary, requires the daemon to be running).

**v0 placeholder:** Unspecified.

**Resolution (Phoenix v1, approved by Adam 2026-05-06):** Bundle the daemon by default in the standalone binary; expose `--external-daemon <url>` for users who want to connect to an existing daemon instead. Audience for the binary is non-developer users wanting zero deployment friction; bundling means double-clicking the binary just works. Sophisticated users running long-lived daemons can use `--external-daemon` (or pip-installed Phoenix). Binary size ~150-250 MB depending on platform is acceptable for the zero-friction first-run.

**Cross-reference:** Section 5.4 now contains the locked decision.

## 11.4 — Security and authentication tensions

### 11.4.1 — OS keychain attestation for frontier-physics signing (RESOLVED in Phoenix v1.1)

**Origin:** Section 7.2.

**Tension:** The vendored Actor pattern is "defense-in-depth, not airtight" — a malicious local process running as the same OS user can read the master key and sign Actors. For frontier-physics requests (Wheeler-DeWitt, gravitational), this loophole is extra concerning.

**v0 placeholder:** Actor pattern as vendored, no additional OS attestation.

**Recommended disposition (v0):** Defer to v1.x as a hardening pass.

**Resolution (Phoenix v1.1, approved by Adam 2026-05-20):** Actor pattern locked for v1.1; OS keychain attestation (Windows Hello / Touch ID / libsecret-with-prompt) deferred to v1.x as a focused hardening phase. **Phase 13 hindsight:** the new privacy-bearing capabilities (`can_store_prompt_verbatim`, `can_store_prompt_encrypted`) are admin-granted via a ledger-recorded `PermissionGrantEntry`, which adds an on-chain audit story for high-trust permission changes but does NOT change the underlying Actor signing surface. The OS keychain hardening would benefit both physics frontier-regime signing AND cognition-permission grants, so a single v1.x hardening pass covers both. **Threat model honesty preserved:** v1.1's documented stance remains "don't run Phoenix on a shared user account if you need stronger isolation than defense-in-depth."

**Cross-reference:** Section 7.2 stands. v1.x hardening pass ships once a concrete platform-specific implementation lands.

### 11.4.2 — Org-level permission inheritance (RESOLVED in Phoenix v1.1)

**Origin:** Section 7.3.

**Tension:** When an org admin grants `can_load_adapter=True` to org members, does that propagate automatically to every install enrolled under that org, or does each install need its own explicit grant?

**v0 placeholder:** Per-install grants only.

**Recommended disposition (v0):** Per-install grants in v1; org-level templates in v1.x driven by real customer feedback.

**Resolution (Phoenix v1.1, approved by Adam 2026-05-20):** Per-install grants locked for v1.1. **Phase 13 hindsight:** the seven new Phase 13 permission flags (cognition + MCP + privacy + streaming) are also per-install — `can_store_prompt_verbatim`, `can_call_mcp_server`, etc. all carry the same v1 discipline. Org-level inheritance would benefit those just as much as the original Section 7.3 flags. The Phase 13 `PermissionGrantEntry` ledger row (Step 9) gives the audit substrate that org-level rollouts will need; the org-level template surface is additive on top. Phoenix Cloud (Section 1 Decision 35) is the natural driver here — when org-level deployment becomes real, the template format will be informed by tenant requirements rather than speculative shape-design.

**Cross-reference:** Section 7.3 stands. Org-level templates ship at v1.x driven by Phoenix Cloud requirements.

### 11.4.3 — Org root keypair rotation (RESOLVED in Phoenix v1.1)

**Origin:** Section 7.6.

**Tension:** If an org wants to rotate its root keypair (e.g., suspected compromise), how do existing enrolled installs migrate?

**v0 placeholder:** No rotation flow specified.

**Recommended disposition (v0):** Defer to v1.x; v1 ships with the "create new org if compromise suspected" workaround.

**Resolution (Phoenix v1.1, approved by Adam 2026-05-20):** No rotation flow in v1.1; "create new org" workaround stands. Properly designing the rotation flow requires real org deployment experience to validate the audit-continuity requirements. Bundled with 11.4.2 (org-level templates) for v1.x — both are Phoenix-Cloud-driven surfaces that need tenant requirements before specification. **Phase 13 hindsight:** the Omega Ledger's hashchained provenance gives the audit-continuity primitive that a rotation flow will need (each rotation event becomes a chained ledger entry signed by the old root, then a new genesis entry for the new root). The cryptographic substrate exists; the operational flow design awaits real customer pressure.

**Cross-reference:** Section 7.6 stands. Rotation flow ships at v1.x alongside 11.4.2.

## 11.5 — Operational discipline tensions

### 11.5.1 — Kill switch persistence across process restart (RESOLVED in Phoenix v1)

**Origin:** Section 8.3.

**Tension:** The kill-switch flag is in-memory. Operator engages it, restarts Phoenix for an unrelated reason, switch is silently released.

**v0 placeholder:** In-memory only.

**Resolution (Phoenix v1, approved by Adam 2026-05-06):** Persist the engaged state in the state backend (SQLite/Postgres per Decision 31), written on engage and on release. On process startup, Phoenix reads the persisted `kill_switch_state` row before opening the front door; if `engaged_when_shutdown=True`, the front door starts in a refusing-new-tasks mode and an admin must explicitly call `POST /v1/admin/kill-switch/release` to begin accepting work. Fail-closed in the right direction; one extra step in an emergency is a fair cost. The persisted row is itself a top-priority audit event, written before the engage response returns to the operator, so a crash mid-engage is observable.

**Cross-reference:** Section 8.3 now contains the locked decision.

### 11.5.2 — Manual calibration baseline override (RESOLVED in Phoenix v1 — permanent NO)

**Origin:** Section 8.4.

**Tension:** Should admins be able to manually override the drift detector's reference baseline? Useful for "we shipped Phoenix v1.1 and the new measurements are intentionally different from v1.0; treat today's measurements as the new baseline." Risky because it can mask real drift.

**v0 placeholder:** No such endpoint.

**Resolution (Phoenix v1, approved by Adam 2026-05-06 — permanent NO, not a v1.x deferral):** No runtime override endpoint, ever. The risk-to-benefit ratio is wrong: convenient for one deliberate workflow (post-release recalibration), silently masks real drift if misused — which is exactly the failure mode Phoenix is supposed to prevent. Post-release recalibration ships as the new `vendor/calibration_profile.json` in the next Phoenix release via the vendor sync script (Section 10.4), not via runtime mutation. This is a permanent disposition; revisiting requires architectural-level reconsideration, not feature-flag toggling.

**Cross-reference:** Section 8.4 now contains the locked decision.

### 11.5.3 — Prometheus metrics endpoint (RESOLVED in Phoenix v1.1)

**Origin:** Section 8.6.

**Tension:** Section 1 Decision 22 commits to OpenTelemetry as the audit-log export standard. Prometheus is dominant in many ops environments; should Phoenix expose a Prometheus metrics endpoint at `/v1/metrics` in addition?

**v0 placeholder:** OpenTelemetry only.

**Recommended disposition (v0):** Defer to v1.1; add `/v1/metrics` if user feedback indicates OTel-via-collector friction.

**Resolution (Phoenix v1.1, approved by Adam 2026-05-20):** OpenTelemetry remains the sole metrics export surface for v1.1. No user feedback to date indicates the OTel-via-collector path is friction; deferred until concrete demand emerges. Native `/v1/metrics` is additive surface, not blocking — when added, it ships as a Phoenix Cloud seam (parallel to the `AuditLogExporter` Protocol in Phase 10) so the metrics-exposition policy is pluggable per-tenant rather than a fixed core endpoint.

**Cross-reference:** Section 8.6 stands. `/v1/metrics` ships at v1.x as a Cloud seam when demand emerges.

## 11.6 — Reference admin client tensions

### 11.6.1 — Sanskrit memory tool composition (RESOLVED in Phoenix v1.1)

**Origin:** Section 9.5.

**Tension:** Should the reference client use Phoenix's vendored Sanskrit memory tools (`phoenix_memory_compress`, `phoenix_memory_recall`, etc.) for its own internal cross-agent memory? Architecturally it would be a beautiful demonstration of vendored substrate composition.

**v0 placeholder:** Reference client uses its own internal storage, no Phoenix memory tools.

**Recommended disposition (v0):** Defer to reference client's v0.2 release; it's a reference-client decision, not a Phoenix-architecture decision.

**Resolution (Phoenix v1.1, approved by Adam 2026-05-20):** Out of scope for Phoenix's architecture document. The reference client is a separate codebase (Section 9; per README "Deferred to v1.1 — separate repo"); its memory-composition choices belong in that repo's own design doc when it lands. Phoenix exports the Sanskrit memory tool surface via the existing `vendor/grammar/codec_*.py` modules; any client (reference or third-party) can compose against that. The architectural reservation Phoenix needs is just "the tools are exported and stable" — that's already true.

**Cross-reference:** Section 9.5 stands. Reference-client memory composition is owned by the reference client's own repo when it ships.

### 11.6.2 — Reference client license posture (RESOLVED in Phoenix v1.1)

**Origin:** Section 9.7.

**Tension:** Phoenix is Apache 2.0. The reference client is Apache 2.0 to match by default, but if the client becomes sophisticated enough to be commercially valuable on its own, a different license might be warranted.

**v0 placeholder:** Apache 2.0 to match Phoenix.

**Recommended disposition (v0):** Apache 2.0 stays default through v1; reconsider only when specific commercial considerations emerge.

**Resolution (Phoenix v1.1, approved by Adam 2026-05-20):** Apache 2.0 locked as the reference-client default. Mirrors Phoenix's own 13-D1 (license: stay Apache 2.0). When the reference client ships as its own repo, its README explicitly states "Apache 2.0 matching Phoenix" and any future license-change conversation happens in that repo's PR review, not this architecture doc.

**Cross-reference:** Section 9.7 stands. Out-of-scope for further Phoenix architecture revisions; lives in the reference client's repo when it lands.

## 11.7 — Distribution and packaging tensions

### 11.7.1 — Vendored module import paths (RESOLVED in Phoenix v1.1)

**Origin:** Section 10.2.

**Tension:** Should vendored modules retain their dr-frank-and-eddy import paths internally (e.g., `from synthesis.equations.base import EquationSolver`), or get rewritten to import from `phoenix.vendor.*`?

**v0 placeholder:** Vendor verbatim including imports.

**Recommended disposition (v0):** Verbatim through v1; revisit if maintenance burden of path-shadowing exceeds rewriting cost.

**Resolution (Phoenix v1.1, approved by Adam 2026-05-20):** Verbatim vendoring with `sys.path` manipulation in `phoenix/__init__.py` locked through v1.1. **Phase 13 hindsight:** added `vendor/cognition_wobble/` as a Phoenix-authored vendored substrate (not from dr-frank-and-eddy). It uses the same verbatim-import-path discipline — code imports `from cognition_wobble.classifier import CognitionClassifier`, not `from phoenix.vendor.cognition_wobble...`. The discipline scales cleanly to new vendored substrates. Phase 12's wheel packaging (the namespace-packages-as-siblings fix) confirmed the verbatim approach works for non-editable installs too. No re-vendor pain observed across Phases 0-13; verbatim approach validated.

**Cross-reference:** Section 10.2 stands. Phase 12's wheel-packaging note about namespace-packages-as-siblings is the operational footnote.

### 11.7.2 — Phoenix branded launcher icon (RESOLVED in Phoenix v1.1 — out of scope)

**Origin:** Section 10.5.

**Tension:** `create_shortcut.ps1` references a Phoenix-branded icon — described as "a stylized rising-bird glyph; placeholder for v1." A real icon should land before public release.

**v0 placeholder:** Generic icon for v0; placeholder mentioned.

**Recommended disposition (v0):** Cosmetic; get a designed icon before public release; not architecturally blocking.

**Resolution (Phoenix v1.1, approved by Adam 2026-05-20):** Reclassified as non-architectural design asset. Out of scope for Section 11's tension catalog going forward. Get a designed icon when convenient before any public-release announcement; do not track in this catalog.

**Cross-reference:** Section 10.5 stands. Design-asset work is owned outside the architecture doc.

## 11.8 — The translator handler set (RESOLVED in Phoenix v1.1)

**Origin:** Section 3.6.

**Tension:** Section 3.6 specified the *contract* for grammar-to-PhysicsTask translation but deferred the per-non-terminal handler implementations as build-guide territory.

**v0 placeholder:** Contract locked, handler implementations deferred.

**Recommended disposition (v0):** Build-guide territory; specify handler-by-handler during `phoenix/grammar/translator.py` build.

**Resolution (Phoenix v1.1, approved by Adam 2026-05-20):** Grammar-token entry path effectively deprecated in favor of the structured-JSON entry point. Phoenix v1 shipped `POST /v1/tasks` accepting structured JSON directly into a `PhysicsTask` (Phase 3); the grammar-token-via-translator path was never built. LoRA adapters (Phase 9) bridge natural language → structured JSON directly, bypassing the grammar-token intermediate. The translator handler set therefore remains unimplemented and is reclassified as **deferred-with-cause:** the architectural intent (three entry points: structured JSON / grammar tokens / natural language via LoRA) reduced to two in practice (structured JSON + natural language via LoRA), and that's working in production. If a concrete user demand for the grammar-token entry path emerges, the translator becomes a focused v1.x build-guide; until then, this tension stays resolved-as-deprecated rather than open. **Phase 13 hindsight:** the cognition substrate accepts canonical `Prompt` JSON shapes directly, reinforcing the structured-payload pattern.

**Cross-reference:** Section 3.6's contract stands as architectural reservation. `phoenix/grammar/__init__.py` is the empty-on-purpose stub; `phoenix/api/routes.py` is the actual JSON-to-PhysicsTask path.

## 11.9 — Summary of dispositions

The 19 open tensions cataloged in Sections 11.1 through 11.8 break down as:

- **5 dispositions RESOLVED in Phoenix v1 (approved by Adam 2026-05-06):** pricing update cadence (11.2.2, soft warn), pagination convention (11.3.1, cursor-based), standalone binary daemon bundling (11.3.3, bundle with override flag), kill-switch persistence (11.5.1, persist with explicit release after restart), manual calibration baseline override (11.5.2, permanent no). Resolutions are now folded into Sections 4.7, 5.2, 5.4, 8.3, 8.4 respectively.
- **8 dispositions deferred to v1.x:** error-bar combiner refinement, MPS truncation axis, adaptive-depth threshold tuning, rung-table customization, OS keychain attestation, org permission inheritance, org root key rotation, Prometheus metrics endpoint.
- **2 dispositions deferred to research/empirical work:** provider equivalence rules, multi-source vendoring.
- **2 dispositions assigned to build-guide territory:** LoRA validation suite content, translator handler set.
- **2 dispositions classified as cosmetic/non-architectural:** reference client Sanskrit composition (its own release cadence), Phoenix branded launcher icon.

**Open-tension count after the 2026-05-06 resolution round: 14** (down from 19).

**v1.1 update (2026-05-07):** 7 new tensions catalogued in Section 11.14 from the perception harness extension plan (`PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md`). 4 RESOLVED in v1.1 (11.14.1, 11.14.3, 11.14.4, 11.14.5); 1 deferred to build-guide territory (11.14.2); 1 deferred to v1.x perception milestone (11.14.6); 1 with recommended disposition for the v1.1 architecture (11.14.7).

**v1.1 follow-up (2026-05-08):** 11.14.7 resolved — the `LatencyTier` enum (introduced in Section 1 post-Decision-28) locks the perception real-time tier alongside batch and streaming. **Open-tension count after the v1.1 follow-up: 16** (14 from v1.0 + 2 unresolved from v1.1: 11.14.2 and 11.14.6).

**v1.1 second resolution round (2026-05-20, post-Phase-13):** Adam reviewed and locked the remaining 14 v1.0 open tensions. Each now carries a `(RESOLVED in Phoenix v1.X)` title marker and a `**Resolution**` block:

- **2 RESOLVED-and-shipped during v1 (Phase landings):** 11.1.4 LoRA adapter validation suite (`phoenix/adapters/validator.py`, Phase 9); 11.2.1 provider equivalence (`phoenix/router/equivalence_registry.py`, Phase 4). These were "shipped first, documented later" — the v1.1 doc update reconciles the architecture catalog with the actual code.
- **9 RESOLVED-as-locked-deferrals to v1.x:** 11.1.1 (error-bar combiner — keep quadrature, empirical-data-driven revisit), 11.1.2 (MPS axis — roll into Axis 1 until MPS path ships), 11.1.3 (adaptive-depth thresholds — keep Section 6.4 defaults), 11.2.3 (multi-source vendoring — single-version stays), 11.3.2 (per-install rung table — bundled with 11.1.3), 11.4.1 (OS keychain attestation — hardening pass), 11.4.2 (org permission inheritance — Phoenix Cloud-driven), 11.4.3 (org root key rotation — Phoenix Cloud-driven), 11.5.3 (Prometheus endpoint — ships as Cloud seam when demand emerges). Each lock includes Phase 13 hindsight where applicable.
- **1 RESOLVED-as-deprecated:** 11.8 translator handler set — the grammar-token entry path was never built; structured-JSON + LoRA-natural-language are the two shipped entry points. If concrete demand for grammar-token entry emerges, the translator becomes a focused v1.x build-guide.
- **2 RESOLVED-as-out-of-scope:** 11.6.1 Sanskrit memory composition (reference-client decision in the reference client's repo); 11.6.2 reference client license (Apache 2.0 stays); 11.7.1 vendored module import paths (verbatim discipline validated through Phase 13's `vendor/cognition_wobble/` addition); 11.7.2 launcher icon (reclassified as design-asset work).

**Open-tension count after the 2026-05-20 v1.1 second-round resolution: 2** (both from v1.1 perception: 11.14.2 and 11.14.6; both correctly stay open until perception build-guide drafting begins).

The defer-to-v1.x items are tracked as a forward roadmap. They don't block v1 implementation; they shape v1.1+ planning.

The defer-to-research items genuinely need empirical or theoretical work that v1 implementation cannot do alone. They stay open through v1 and beyond, with placeholders that are conservative (don't produce wrong results, just possibly suboptimal ones).

The build-guide territory items are bounded specification work that lands during implementation, not architecture.

## 11.10 — How Section 11 ages

Section 11 is not a static document. Each Phoenix release is expected to:

1. Resolve at least one tension flagged here, by either landing the implementation or making a deliberate disposition.
2. Add new tensions that emerge from implementation (a build guide discovers an architectural question that the original spec did not anticipate).
3. Re-evaluate deferred tensions against new empirical or research data.

A Phoenix release notes section reads "tensions resolved this release: X, Y. New tensions opened: Z." This makes the open-tensions catalog a living artifact rather than a one-time inventory.

**SAFETY:** the existence of this section is itself a safety property. A new contributor or AI agent reading the architecture document encounters every deferred decision explicitly; nothing is hidden in implementation comments or scattered across files. The discipline of consolidating open tensions in one place is what makes Phoenix's architecture *honestly incomplete* rather than *secretly incomplete*.

## 11.11 — Out-of-scope items explicitly named

For completeness, Section 11 also names items that are *not* open tensions because they are explicitly out of scope for v0 — they are work for future Phoenix versions or for entirely separate products. Naming them prevents future-Adam or future-contributor from raising them as oversights.

- **Phoenix Cloud** (Section 1 Decision 35) — the multi-tenant hosted SaaS layer is a separate product with its own architecture document. Not part of v0.
- **The reference admin client's full architecture** (Section 9) — Section 9 specifies the client's relationship to Phoenix; the client's own internal architecture lives in its own repo's documentation.
- **Trinity Core's solver-by-solver implementation details** — covered by `vendor/synthesis/equations/`'s vendored READMEs and dr-frank-and-eddy's own architecture doc, not Phoenix's.
- **Phoenix v2's streaming-real-time API design** (Section 1 Decision 28) — when v2 is on the horizon, that's a v2 architecture document. v0 reserves the namespace and notes the architectural support, but doesn't specify the API.
- **Calibration evolution flows** — adding new solvers, retiring old ones, the lifecycle of calibration profiles. This is dr-frank-and-eddy's territory; Phoenix vendors a snapshot at a moment in time and re-vendors per release.

## 11.12 — Revisions to Section 11 itself

When a tension cataloged here resolves (either via the recommended disposition being approved, or via empirical/research work landing), the resolution lands in Section 11 as an update entry, and the relevant Section in the architecture document gains the now-locked decision.

The update format:

```
### 11.X.Y — [Title] (RESOLVED in Phoenix vN.M)

[Original framing kept for historical context]

**Resolution (vN.M):** [What was decided, by whom, when]
**Cross-reference:** Section [N.M] now contains the locked decision.
```

This preserves the audit trail of why a decision was made the way it was, even after the architecture document has moved on.

## 11.13 — Cross-reference table

Every `[OPEN: ...]` marker in Sections 2-10 maps to a catalog entry in Section 11. This table is the lookup for any reader who encounters a marker and wants to find its resolution status.

| Section | Marker location | Catalog entry | Disposition |
|---|---|---|---|
| 2.2 | Quadrature combiner | 11.1.1 | **RESOLVED v1.1**: quadrature locked; empirical covariance revisit at v1.x |
| 2.5 | Provider equivalence | 11.2.1 | **RESOLVED v1**: `equivalence_registry.py` (Phase 4) ships conservative defaults + manual override |
| 2.6 | Adaptive depth formula | 11.1.3 | **RESOLVED v1.1**: Section 6.4 thresholds locked; config-tuning at v1.x alongside 11.3.2 |
| 2.7 | LoRA validation suite | 11.1.4 | **RESOLVED v1**: `phoenix/adapters/validator.py` (Phase 9) |
| 2.8 | MPS truncation axis | 11.1.2 | **RESOLVED v1.1**: roll into Axis 1; promote-vs-roll decision deferred until MPS path ships |
| 3.5 | LoRA validation suite | 11.1.4 (same as 2.7) | **RESOLVED v1**: see 2.7 |
| 3.6 | Translator handler set | 11.8 | **RESOLVED v1.1**: deprecated-in-practice; structured-JSON + LoRA-NL are the shipped entry points |
| 4.5 | Provider equivalence | 11.2.1 (same as 2.5) | **RESOLVED v1**: see 2.5 |
| 4.7 | Pricing update policy | 11.2.2 | **RESOLVED v1**: soft warn (90-day threshold; `pricing_data_staleness_days` provenance field) |
| 5.2 | Pagination convention | 11.3.1 | **RESOLVED v1**: cursor-based, server-encoded, `next_cursor`/`prev_cursor` envelope |
| 5.4 | Standalone binary daemon | 11.3.3 | **RESOLVED v1**: bundle daemon by default; `--external-daemon` flag for opt-out |
| 6.4 | Per-install rung table | 11.3.2 | **RESOLVED v1.1**: fixed-table locked; config-tuning at v1.x alongside 11.1.3 |
| 7.2 | OS keychain attestation | 11.4.1 | **RESOLVED v1.1**: defer to v1.x focused hardening pass |
| 7.3 | Org permission inheritance | 11.4.2 | **RESOLVED v1.1**: per-install locked; org-level templates at v1.x driven by Phoenix Cloud |
| 7.6 | Org root key rotation | 11.4.3 | **RESOLVED v1.1**: "create new org" workaround stands; rotation flow at v1.x with 11.4.2 |
| 8.3 | Kill switch persistence | 11.5.1 | **RESOLVED v1**: persist in state backend; refuse-to-start-accepting after restart-while-engaged |
| 8.4 | Calibration baseline override | 11.5.2 | **RESOLVED v1 (permanent NO)**: ship recalibration via vendor sync, never via runtime override |
| 8.6 | Prometheus metrics endpoint | 11.5.3 | **RESOLVED v1.1**: OTel-only for v1.1; `/v1/metrics` ships as Cloud seam at v1.x when demand emerges |
| 9.5 | Sanskrit memory composition | 11.6.1 | **RESOLVED v1.1**: out of scope; owned by reference-client repo when it lands |
| 9.7 | Reference client license | 11.6.2 | **RESOLVED v1.1**: Apache 2.0 stays default (mirrors 13-D1) |
| 10.2 | Vendored import paths | 11.7.1 | **RESOLVED v1.1**: verbatim discipline locked; validated through Phase 13's `vendor/cognition_wobble/` addition |
| 10.4 | Multi-source vendoring | 11.2.3 | **RESOLVED v1.1**: single-version through v1.x; multi-substrate ≠ multi-source |
| 10.5 | Launcher icon | 11.7.2 | **RESOLVED v1.1 (out of scope)**: reclassified as non-architectural design asset |

## 11.14 — Perception extension tensions (added in v1.1, 2026-05-07)

These tensions arise from the perception harness extension proposed and locked in `PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md`. They are catalogued here per Section 11's discipline of explicit visibility for deferred or open architectural decisions, following the living-artifact protocol described in 11.10.

### 11.14.1 — Perception extension placement (RESOLVED in Phoenix v1.1)

**Origin:** PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md Section 4.

**Tension:** Whether perception ships as v1.x extension to existing Phoenix v1 architecture (Option I), as a v2 reorganization (Option II), or as parallel v1.x and v2 tracks (Option III).

**v0 placeholder:** Unspecified; surfaced for review.

**Resolution (Phoenix v1.1, approved by Adam 2026-05-07):** Option I — v1.x extension. Perception extends the v1 substrate at Phase 12 onwards, after v1 ships at Phase 11. Justification: substrate audit showed 70-80% reuse of existing v1 layers (vendored Sanskrit codec at `vendor/grammar/sanskrit_codec.py`, grammar substrate at `vendor/grammar/`, wobble framework at `vendor/wobble/`, Actor authentication at `vendor/actor/`, Omega Ledger pattern at `phoenix/ledger/`, cloud seams at `phoenix/_internal/cloud_seams.py`). v2 reorganization is unnecessary and would dilute v1 implementation attention.

**Cross-reference:** PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md Section 4.

### 11.14.2 — Perception substrate vendoring scope

**Origin:** PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md Section 6 question 6.3.

**Tension:** What gets vendored at `vendor/perception_substrate/`? Possibilities include nuScenes annotation transforms, reference perception models, Penrose substitution-rule generators (proprietary IP), pre-curated canonical example libraries.

**v0 placeholder:** Directory commits architecturally; specific contents unspecified.

**Resolution path:** Specify during Phase 12 build-guide drafting. Adam reviews the proposed vendor manifest at that time.

**Recommended disposition:** Build-guide territory. Architecturally the directory commits; specific contents land at Phase 12 build-guide drafting time.

### 11.14.3 — Sensor ingest layer placement (RESOLVED in Phoenix v1.1)

**Origin:** PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md Section 6 question 6.1.

**Tension:** Whether sensor ingest lives at top-level `phoenix/sensors/` or nested at `phoenix/perception/sensors/`.

**Resolution (Phoenix v1.1, approved by Adam 2026-05-07):** Top-level `phoenix/sensors/`. Justification: sensor ingest is generally useful infrastructure that future non-perception domains may need. Top-level placement avoids future refactor.

**Cross-reference:** PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md Section 6.

### 11.14.4 — Canonical example library storage (RESOLVED in Phoenix v1.1)

**Origin:** PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md Section 6 question 6.2.

**Tension:** Storage strategy for canonical example libraries (Phase 19 deliverable) — in-tree, git-LFS, or external storage. Libraries hold sensor data (point cloud samples, frame samples) which is the bulk of perception package storage size.

**Resolution (Phoenix v1.1, approved by Adam 2026-05-07):** git-LFS. Standard pattern for large in-repo binary artifacts; lets the architecture stay in one repo while storage is handled appropriately.

**Cross-reference:** PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md Section 6.

### 11.14.5 — Penrose temporal pulse coding hardware integration (RESOLVED in Phoenix v1.1)

**Origin:** PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md Section 5 question 5.2 and Phase 16.

**Tension:** Phase 16 simulator vs live hardware integration for the Penrose temporal pulse coding work — the IP-defensible novel contribution apparent from the public literature gap (May 2026 web search confirmed M-sequences, Gold codes, and true-random Geiger-mode coding are published; deterministic substitution-rule pulse coding is not).

**Resolution (Phoenix v1.1, approved by Adam 2026-05-07):** Simulator-only for v1.x; live hardware deferred to Phoenix v2 contingent on lidar vendor partnership. **Adam's specific Q5.2 expansion:** the simulator must be architected so that hardware integration in v2 lands as a new driver implementing the same Protocol interfaces, not as a rewrite. Phase 16 deliverables explicitly include scalability-to-hardware design constraints binding from Phase 16 day one: Protocol-based interface contracts (`LidarTransmitter`, `LidarReceiver`, and `InterferenceModel` Protocols defined in `phoenix/perception/penrose/temporal/interfaces.py`), hardware-realistic signal formats (timing precision, amplitude levels), pluggable interference models (rain modeled mathematically in sim; real hardware sees rain physically), and decoder paths that don't assume simulator-specific signal characteristics. Phase 16's stop gate explicitly tests Protocol compatibility via a mock hardware driver swap.

**Cross-reference:** PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md Section 5, Phase 16.

### 11.14.6 — Perception verification axes count

**Origin:** PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md Section 8 Phase 20.

**Tension:** Phase 20 commits to three perception wobble axes (cross-modality, cross-frame, cross-canonical) mirroring v1's three quantum wobble axes (cross-precision, cross-control, cross-provider). Whether perception should adopt a fourth axis (e.g., cross-temporal-window, cross-latency-tier) is open.

**v0 placeholder:** Three axes mirroring quantum wobble.

**Resolution path:** Empirical — once perception ships and accumulates real-world solves, evidence of fourth-axis necessity may emerge.

**Recommended disposition:** Ship Phase 20 with three axes; revisit at v1.x perception milestone after empirical data exists. Mirrors the disposition pattern from 11.1.2 (MPS truncation as fourth quantum axis).

### 11.14.7 — Perception real-time latency tier (RESOLVED in Phoenix v1.1, follow-up 2026-05-08)

**Origin:** PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md Section 1.

**Tension:** Phoenix v1 ships batch real-time at 10-100ms (Section 1 Decision 26). Perception requires sub-100ms hard real-time per sensor frame. This is a different latency tier from v1's batch real-time and v2's planned streaming real-time (Decision 28).

**v0 placeholder:** Each phase's PERF callout commits to its specific latency budget.

**Resolution (Phoenix v1.1 follow-up, locked 2026-05-08):** The three latency tiers are encoded as a single `LatencyTier` enum (`BATCH_REALTIME`, `STREAMING_REALTIME`, `PERCEPTION_REALTIME`) defined in `phoenix/_internal/latency.py` per the post-Decision-28 paragraph in Section 1. v1 routes only `BATCH_REALTIME`; the other two values exist in the enum so v1's Router and front door accept the tier as a parameter from day one and raise typed `LatencyTierNotImplemented` for non-routable tiers. The enum is the canonical encoding of Decisions 26–28; the perception harness extension at Phase 12+ implements `PERCEPTION_REALTIME` routing without enum churn or retroactive caller changes.

**Cross-reference:** Section 1 (post-Decision-28 `LatencyTier` enum paragraph), Section 4 (Router accepts tier parameter; `LatencyTierNotImplemented` typed error).

```
=== SECTION 11 COMPLETE — AWAITING ADAM REVIEW ===
```

---

# What happens after Section 11

Section 11 closes Phoenix Architecture Specification v1. The full document is `C:\Phoenix\PHOENIX_ARCHITECTURE_v1.md`, with every architectural commitment grounded in real code reads against `C:\frank-data\` where applicable.

The document is a *blueprint*, not an *implementation guide*. Build guides for Claude Code come next, citing this v1 spec.

**v0 → v1 transition log (for historical reference):**

1. **2026-05-05** — v0 drafted across the 12 sections (0 through 11). 19 open tensions catalogued in Section 11.
2. **2026-05-06** — Adam reviewed Section 11's recommended in-document dispositions and approved all five (pricing soft-warn, cursor pagination, bundle daemon, persist kill switch, permanent-no on baseline override). Folded into Sections 4.7, 5.2, 5.4, 8.3, 8.4.
3. **2026-05-06** — Surgical additions per the v0 review: Phoenix Cloud commercial-bundle scope clarified under Decision 35; cost-ceiling enforcement specified end-to-end (Section 4.7 + Section 6.4 + new `DEGRADED_BUDGET_BOUND` agreement type); cloud-quantum reproducibility asterisk surfaced on the Result envelope (Section 2.2 `cloud_shots_recorded` field); Phoenix Cloud abstraction seams specified concretely as Protocol definitions (Section 10.3.1); reference admin client moved to v1.1 acceptance (new Section 10.8); two new v1 acceptance tests added (panic mode + long-window replay). Open tension count: 14.
4. **2026-05-06** — v0 holistic pass complete. File renamed `PHOENIX_ARCHITECTURE_v0.md` → `PHOENIX_ARCHITECTURE_v1.md` and committed to the local repo at `C:\Phoenix\`. Push to `nah414/Phoenix` follows when Adam authorizes.

**Sequence from here:**

5. The first build guide (`BUILDGUIDE_phoenix_v1_phase0_skeleton.md`) drafts and ships, executing against v1's locked decisions. The build guide enforces every standing rule — phase gates with Adam review, stop-and-ask on ambiguity, PERF/SAFETY callouts, per-section READMEs, launcher updates where startup behavior changes, no OneDrive paths.
6. Implementation work proceeds in build-guide phases, each clearing through Adam's review before advancing.
7. Section 11's living-artifact protocol (11.10, 11.12) governs how new tensions and resolutions land in subsequent v1.x and v2 releases.

Phoenix v1 is not yet built. Phoenix v1's blueprint is now stable enough for a build guide to start against it — and that is what the v0→v1 work delivered.
