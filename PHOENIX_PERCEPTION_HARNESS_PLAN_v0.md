# Phoenix Perception Harness — Extension Plan v0

**Status:** DRAFT — planning-stage document for Adam's review. **NOT** part of Phoenix v1 locked architecture. **NOT** included in v1 acceptance criteria. This document proposes future extension work; nothing here modifies the locked v1 spec or its existing build guide pipeline.
**Authoritative location:** `C:\Phoenix\PHOENIX_PERCEPTION_HARNESS_PLAN_v0.md`
**Architectural anchor:** `C:\Phoenix\PHOENIX_ARCHITECTURE_v1.md` (locked 2026-05-06)
**Target release:** Phoenix v1.x extension or v2 — to be decided per Section 13 below
**Date opened:** 2026-05-07
**Author of record:** Adam (with Claude as design partner)

---

## What this document is — and what it is not

This is a **planning document** for a proposed extension to Phoenix that adds perception-harness capability for autonomous-system middleware (the original thought experiment was adverse-weather perception for autonomous vehicles like Waymo). It is the same v0-style shape Adam used for the original Phoenix architecture before it locked at v1: capture decisions, surface tensions, recommend dispositions, but commit nothing until Adam signs off.

This document **does not**:

- Modify `PHOENIX_ARCHITECTURE_v1.md`. The v1 architecture is locked at quantum-accuracy middleware. This plan adds capability above and to the side of v1, not inside it.
- Modify `BUILDGUIDE_phoenix_v1_phase0_skeleton.md` or `BUILDGUIDE_phoenix_v1_phase1_vendor_sync.md`. The v1 build pipeline (Phases 0 through 11) remains unchanged.
- Add to Section 10.7 (v1 acceptance criteria). Perception capability is a v1.x or v2 deliverable.
- Pre-empt any of the 14 open tensions catalogued in v1 Section 11. New tensions arising from this plan are flagged in Section 9 of this document for future addition to Section 11 if Adam accepts the plan.

This document **does**:

- Position perception as a clean architectural extension that respects v1's locked decisions
- Specify exactly what's reused from v1 (vendored Sanskrit codec, grammar substrate, wobble pattern, Omega Ledger, Actor authentication, cloud seams)
- Specify exactly what's new (perception-specific grammar, scene-graph schema, sensor ingest, real-time pipeline)
- Propose phase structure for the eventual build guides
- Surface the open questions that Adam needs to decide before any build guide gets written

When this plan is approved (or revised and re-approved), the immediate next steps are listed in Section 13.

## Companion documents

| Document                                                          | Status   | Purpose                                              |
|-------------------------------------------------------------------|----------|------------------------------------------------------|
| `C:\Phoenix\PHOENIX_ARCHITECTURE_v1.md`                           | Locked   | v1 architecture spec — anchor for this plan          |
| `C:\Phoenix\BUILDGUIDE_phoenix_v1_phase0_skeleton.md`             | Drafted  | Phase 0 build guide — unchanged by this plan         |
| `C:\Phoenix\BUILDGUIDE_phoenix_v1_phase1_vendor_sync.md`          | Drafted  | Phase 1 build guide — unchanged by this plan         |
| `Sanskrit_Memory_Compression_for_Ash.md`                          | Reference| Codec we shipped in dr-frank-and-eddy                |
| `Sanskrit_Memory_Compression_Wider_Toolkit_for_Ash.md`            | Reference| Broader Sanskrit toolkit survey                       |
| `Grok_Waymo_Penrose_Rain_Session_Full.md`                         | Reference| Initial AV perception thought experiment             |

---

## Table of Contents

1. [Why a Perception Harness Extension](#section-1)
2. [What's Reused From Phoenix v1 — The Substrate Audit](#section-2)
3. [Strategic Positioning — Parallel Enhancement, Not Critical Path](#section-3)
4. [Architectural Placement — v1.x Extension vs v2](#section-4)
5. [Technical Scope — Six Sanskrit Techniques Plus Penrose](#section-5)
6. [Proposed Package Layout — `phoenix/perception/`](#section-6)
7. [Proposed Phase Structure — Phase 12 Onwards or v1.x Track](#section-7)
8. [Phase-by-Phase Detail With Explicit Stop Gates](#section-8)
9. [New Open Tensions Proposed for Section 11 of v1 Architecture](#section-9)
10. [Honest Limits and Risks](#section-10)
11. [Commercial Framing — Phoenix Perception as a Product](#section-11)
12. [Open Questions Requiring Adam's Decision](#section-12)
13. [Recommended Next Step After Plan Review](#section-13)

---

<a id="section-1"></a>

## 1. Why a Perception Harness Extension

The thought experiment that triggered this plan: an AI agent operating Phoenix as a middleware harness, sitting on top of an autonomous vehicle's sensor stack, helping the vehicle "see" better in heavy rain or snow. Waymo and other Level-4 robotaxi services restrict their operational design domain in adverse weather not because the vehicles are unsafe but because the *sensor stack* degrades — lidar suffers Mie scattering off raindrops, radar gets cluttered, cameras lose contrast — and the certified perception path conservatively pulls the vehicle off the road.

The opening: a middleware harness that runs *parallel to* the certified perception path, applies structured grammar-based interpretation to the same sensor inputs, and offers the planner an additional second-opinion confidence channel that becomes most valuable specifically when the primary path is least confident.

This shape — middleware-grade, downloadable, integrator-consumable, advisory-style, with provenance and error-bar discipline — **is exactly Phoenix's architecture**. Phoenix v1's identity (per Section 0 of the locked architecture) is "downloadable software that AI agents and integrators call to get validated computation, hardware-aware routing, and physics-grounded verification of results — with provenance, error bars, and honest reporting of uncertainty." Replace "validated computation" with "validated perception" and the value proposition transfers directly.

What changes between v1's quantum-accuracy domain and the perception domain:

- The substrate Phoenix vendors (v1 vendors physics solvers; perception extension would vendor or build perception-specific machinery).
- The pipeline shape (v1's Trinity Core has Solver→Control→Orchestrate; perception's pipeline has Sensors→Grammar→Scene→Verification).
- The verification axes (v1's three-axis wobble is cross-precision/cross-control/cross-provider; perception's three-axis wobble would be cross-modality/cross-frame/cross-canonical-example).
- The latency tier (v1 ships batch real-time at 10-100ms; perception requires sub-100ms hard real-time per sensor frame).

What stays the same:

- The Sanskrit codec and grammar substrate already vendored at `vendor/grammar/`.
- The Actor authentication pattern already vendored at `vendor/actor/`.
- The wobble disagreement framework already vendored at `vendor/wobble/`.
- The Omega Ledger hashchain pattern (extended in Phoenix v1's `phoenix/ledger/`).
- The cloud seams pattern at `phoenix/_internal/cloud_seams.py`.
- The phase-gate-with-stop-gates build guide methodology.
- The `[OPEN: ...]` tension tracking in Section 11.
- Apache 2.0 licensing.

This is what makes the perception extension architecturally clean: it sits above and to the side of v1, reusing the substrate, mirroring the patterns, and adding a parallel pipeline that doesn't disturb the locked quantum-accuracy core.

---

<a id="section-2"></a>

## 2. What's Reused From Phoenix v1 — The Substrate Audit

A live read of `C:\Phoenix\` confirms the following are present and reusable for the perception extension. Each row maps to v1's locked architecture and to the existing on-disk state.

| Asset                                       | On-disk path                                | v1 Architecture ref          | Reuse for perception                            |
|---------------------------------------------|---------------------------------------------|------------------------------|-------------------------------------------------|
| Sanskrit codec                              | `vendor/grammar/sanskrit_codec.py`          | Decision 7, Section 10.2     | Direct reuse for perception-domain encoding     |
| Grammar substrate (loader, parser, generator)| `vendor/grammar/`                          | Decision 7, Section 10.2     | Same machinery, perception-domain rule set     |
| `physics_v1.yaml` grammar                   | `vendor/grammar/physics_v1.yaml`            | Section 3, Section 10.2      | Reference pattern; perception_v1.yaml mirrors  |
| Wobble disagreement framework               | `vendor/wobble/`                            | Decision 13, Section 10.2    | Three-axis wobble reused for perception axes   |
| Actor authentication                        | `vendor/actor/actor.py`                     | Decision 12, Section 10.2    | Same auth, perception API uses same Actor      |
| Calibration profile schema                  | `vendor/calibration_profile.json`           | Section 10.2                 | Perception calibration follows same schema     |
| Hashchained Omega Ledger pattern            | `phoenix/ledger/` (per layout)              | Decision 15, Section 10.3    | Perception solves seal to same ledger          |
| Audit event format + OTel adapter           | `phoenix/audit/`                            | Decision 16, 22, Section 10.3| Perception events flow through same emitters   |
| Cloud seams (auth, audit, budget)           | `phoenix/_internal/cloud_seams.py`          | Decision 35, Section 10.3.1  | Perception extends with optional 4th seam      |
| Phase-gated build guide methodology         | `BUILDGUIDE_phoenix_v1_phase{0,1}_*.md`     | Section 1 conventions        | Same shape for perception build guides         |
| State backend + queue                       | `phoenix/state/`, `phoenix/queue/`          | Decision 31, 32              | Perception job queue uses same NATS            |
| Front door (REST, WebSocket, CLI, MCP)      | `phoenix/api/`, `cli/`, `mcp/`              | Section 5                    | Perception adds endpoints, not new transports  |
| Vendor sync discipline                      | `scripts/vendor_sync.py`                    | Section 10.4                 | Extends to vendor perception substrate too     |
| Launcher chain                              | `scripts/launch.{bat,sh}`                   | Section 10.5                 | Same launcher, perception daemon registered    |

**The point:** the perception extension is not a new product. It is a domain extension of an existing product whose substrate is already 70-80% applicable. The work specific to perception is the new domain grammar, the new scene-graph schema, the new pipeline shape, and the real-time sensor ingest layer. Everything else is `import phoenix.{ledger,audit,identity,state,queue,_internal}` and `from phoenix.vendor.{grammar,wobble,actor} import ...`.

---

<a id="section-3"></a>

## 3. Strategic Positioning — Parallel Enhancement, Not Critical Path

Critical decision that shapes everything downstream.

### The two placements

**Placement A — Safety-critical path.** Phoenix Perception sits between sensors and planner. Every perception decision flows through it. Phoenix Perception's failure means the vehicle's perception fails. Requires ASIL-D certification under ISO 26262, plus ISO 21448 SOTIF analysis, plus FMVSS conformance, plus extensive on-road validation. Realistic timeline: 4–7 years and substantial validation investment.

**Placement B — Parallel enhancement layer.** Phoenix Perception runs alongside the existing certified perception stack, takes the same sensor inputs, produces its own scene interpretation, and offers it to the planner as an *additional* confidence channel. The planner can consume or ignore Phoenix Perception's output. The certified path remains the safety-critical authority. Phoenix Perception's value compounds with the primary system's degradation rather than replacing it. Certification burden lower (advisory-system precedent from aviation: DO-178C software level C/D), validation effort: months not years, time-to-market: 12–24 months for a real customer pilot.

### Why placement B is the correct first deployment

1. **The primary value is in the degraded regime.** Heavy rain and heavy snow are exactly when Waymo's certified path reports low confidence. A parallel layer that becomes most informative there is the maximum value-per-integration-effort scenario.

2. **The integration story sells itself.** "Add a redundant grammar-based confidence channel that lets your existing certified stack operate longer in degraded weather, with zero risk to your certified path because we're not in it" is a much easier conversation with a customer's safety org than "let us replace your certified perception."

3. **It matches Phoenix v1's existing identity.** v1 is already advisory-shaped — the architecture's own language describes Phoenix as "more accurate and more honest about uncertainty," not "the only authority." Decision 6 commits Phoenix v1's solves to traverse all three Trinity Core layers by default but explicitly allows opt-out per layer with widened error bars. Perception extension inherits this advisory posture naturally.

This plan therefore commits to placement B. Placement A remains a long-term ambition contingent on field-validation evidence accumulated under placement B.

**Open question 3.1:** Adam accepts placement B as the first-deployment commitment? If not, the entire phase structure changes.

**Open question 3.2:** Placement A as long-term ambition (5+ years), or permanent placement B?

---

<a id="section-4"></a>

## 4. Architectural Placement — v1.x Extension vs v2

The next consequential decision: when does perception ship relative to Phoenix v1?

### Three options

**Option I — v1.x extension (lands after v1 release).** Phoenix v1 ships first per its locked phase pipeline (Phase 0 through Phase 11). Once v1 is stable in users' hands, perception capability lands as v1.1, v1.2, etc., extending the existing codebase. Section 10.8 (v1.1 acceptance criteria) gains perception items as deferred-from-v1 entries.

**Option II — Greenfield v2 with v1 as substrate.** Phoenix v1 ships, then a v2 release reorganizes around the new pipeline shape. Perception ships in v2 as a first-class concern alongside the quantum-accuracy core. The architecture document gets a v2 spec; v1 stays frozen.

**Option III — Parallel v1.x and v2 tracks.** Light perception capability (Phases 12, 13) lands as v1.x extension. Heavy perception capability (real-time pipeline, hardware integration) lands as v2. Splits the work over a longer window.

### Recommendation

**Option I (v1.x extension)** is the right choice for the first 12-24 months of perception work. Reasoning:

- Phoenix v1 is locked but not yet shipped. Phase 0 build guide is drafted; implementation is pending. Diluting v1 attention with v2 design work risks neither shipping cleanly.
- The substrate audit (Section 2) shows perception reuses 70-80% of v1's machinery. There is no architectural reason to greenfield v2; perception cleanly extends.
- v1.x extension lets perception ship to early customers (industrial/defense first, per Section 11) on a 12-month horizon while Phoenix v1's quantum-accuracy core continues its own evolution.
- v2 remains available as a future option once v1.x perception data and v1 production usage produce concrete evidence about whether the unified architecture wants reorganization.

**Open question 4.1:** Adam accepts Option I (v1.x extension) as the placement?

**Open question 4.2:** If Option I, what's the earliest Phase number for perception work? My recommendation: **Phase 12 — Perception Foundation** lands AFTER Phase 11 (v1 release). Perception phases would be Phases 12, 13, 14, etc. This is clean because v1 ships through Phase 11; v1.x extension begins at Phase 12.

---

<a id="section-5"></a>

## 5. Technical Scope — Six Sanskrit Techniques Plus Penrose

The targeted subset of Sanskrit grammatical techniques that earn their keep on the perception domain. This is the focused-scope answer to "do we implement the entire Sanskrit base" — implement the patterns that map to the problem, not the entire Aṣṭādhyāyī.

### The six techniques and their roles

**Kāraka (semantic role labeling).** Replaces flat `(object, position, velocity)` scene representation with typed action structures. Every dynamic agent in the scene is the kartṛ (agent) of an action with the action's other participants assigned to remaining roles (karman, karaṇa, sampradāna, apādāna, adhikaraṇa). Enables structural queries impossible against flat representation.

**Chanda (meter as integrity check).** A finite-state machine watches the polyrhythm of incoming sensor streams. Detects degraded operating mode by *meter break* — pattern-rhythm violation — rather than by per-sensor value comparison. Cheap, structural, diagnostic.

**Vivakṣā (speaker intent / per-sensor framing preservation).** Maintains per-sensor scene graphs in parallel to the fused consensus graph. Lets downstream consumers query "show me the scene as the radar sees it, ignoring lidar" without re-running fusion. Critical for graceful degradation.

**Anuvṛtti (context inheritance / frame-to-frame compression).** Most of a driving scene doesn't change frame-to-frame. Anuvṛtti represents each new frame as `inherited_from(previous_frame) + delta`, compressing the perception stream substantially in steady-state.

**Paribhāṣā (meta-rules for sensor-conflict resolution).** Explicit rules for what to do when sensors disagree. Per-mode sensor precedence rules (in heavy_rain mode, radar dominates lidar for moving objects, lidar dominates radar for static geometry, etc.). Auditable typed precedence rather than implicit "which sensor wins" logic.

**Lakṣaṇa-lakṣya (rule-example pairs / canonical examples per mode).** Every weather mode (`clear`, `light_rain`, `heavy_rain`, `light_snow`, `heavy_snow`, `fog`, `night_*`) carries a curated library of canonical examples. Mode classification matches incoming frames against canonical examples. Self-documenting, regression-testable, calibration-friendly — the exact same shape as v1's Tier-1 calibration battery (HO-1, ISW-1, H1S-1, RABI-1, SCG-1) extended to perception.

### The Penrose layer — spatial and temporal

**Spatial Penrose (established literature).** Aperiodic sampling grids and aperiodic phased-array layouts for noise-robust feature extraction and grating-lobe suppression. Established work; we adopt and adapt.

**Temporal Penrose (apparent literature gap).** Deterministic substitution-rule pulse-train coding for lidar, distinct from M-sequences, Gold codes, and true-random Geiger-mode coding. Web search of public literature (May 2026) confirms no published implementation of this specific approach. The substitution-rule property gives both detection-by-correlation *and* reconstruction-by-rule-projection; existing pulse-coding schemes give detection but not reconstruction.

### What this scope explicitly does not include for v1.x

The following techniques from the wider Sanskrit toolkit are *not* in the v1.x perception scope, because their payoff for the specific problem is small or requires substrate not yet available: samāsa compound types, dhātu/pratyaya generative morphology, kaṭapayādi numerical encoding, svara pitch accent, multiple sandhi families beyond chanda, vipratiṣedha rule precedence beyond paribhāṣā coverage.

These remain in the long-term roadmap and may earn their way in for future domains.

**Open question 5.1:** Adam approves the six-techniques-plus-Penrose scope?

**Open question 5.2:** Penrose temporal pulse coding requires lidar hardware control or close vendor partnership. Without it, the technique is dormant on the receiver-side only. Phase 16 (proposed) is the simulator-only deliverable; live hardware integration deferred to Phoenix v2.

---

<a id="section-6"></a>

## 6. Proposed Package Layout — `phoenix/perception/`

Mirrors the existing Phoenix v1 package layout convention specified in v1 architecture Section 10.3. New top-level subdirectory `phoenix/perception/` lives alongside the existing `phoenix/grammar/`, `phoenix/trinity/`, etc. Each subdirectory contains its own `README.md` per Decision 38 and Section 10.6.

```
C:\Phoenix\
├── phoenix/
│   ├── perception/                    # NEW — perception extension top-level
│   │   ├── README.md
│   │   ├── harness.py                 # Integrated entry point for perception solves
│   │   ├── data_model.py              # PerceptionTask, SceneGraph, Result analogues
│   │   ├── pipeline.py                # Sensors → Grammar → Scene → Verification
│   │   │
│   │   ├── grammar/                   # Phase 12 — perception domain grammar
│   │   │   ├── README.md
│   │   │   ├── perception_v1.yaml     # Domain grammar (parallel to vendor/grammar/physics_v1.yaml)
│   │   │   ├── translator.py          # Parse tree → SceneGraph translation
│   │   │   └── schema_validator.py    # JSON-Schema for perception API entry
│   │   │
│   │   ├── chanda/                    # Phase 13 — meter integrity FSM
│   │   │   ├── README.md
│   │   │   ├── meter_monitor.py
│   │   │   ├── meter_definitions.yaml
│   │   │   └── degradation_policies.py
│   │   │
│   │   ├── karaka/                    # Phase 14 — typed scene graph
│   │   │   ├── README.md
│   │   │   ├── scene_graph.py
│   │   │   ├── role_inference.py
│   │   │   ├── role_schema.py
│   │   │   └── query_engine.py
│   │   │
│   │   ├── vivaksha/                  # Phase 15 — per-sensor preservation
│   │   │   ├── README.md
│   │   │   ├── per_sensor_graphs.py
│   │   │   ├── fusion.py
│   │   │   └── view_queries.py
│   │   │
│   │   ├── anuvritti/                 # Phase 17 — frame inheritance
│   │   │   ├── README.md
│   │   │   ├── frame_inheritance.py
│   │   │   ├── delta_compression.py
│   │   │   └── motion_prior.py
│   │   │
│   │   ├── paribhasha/                # Phase 18 — mode + precedence
│   │   │   ├── README.md
│   │   │   ├── mode_selector.py
│   │   │   ├── precedence_rules.yaml
│   │   │   └── conflict_resolver.py
│   │   │
│   │   ├── penrose/                   # Phase 16 — Penrose pulse + tiling
│   │   │   ├── README.md
│   │   │   ├── spatial/
│   │   │   │   ├── tiling.py
│   │   │   │   └── feature_extraction.py
│   │   │   └── temporal/
│   │   │       ├── pulse_train.py
│   │   │       ├── decoder.py
│   │   │       └── simulator.py
│   │   │
│   │   ├── laksana_laksya/            # Phase 19 — canonical libraries
│   │   │   ├── README.md
│   │   │   ├── canonical_library.py
│   │   │   ├── matcher.py
│   │   │   └── canonical_examples/    # Per-mode example trees (git-LFS)
│   │   │       ├── clear/
│   │   │       ├── light_rain/
│   │   │       ├── heavy_rain/
│   │   │       ├── light_snow/
│   │   │       ├── heavy_snow/
│   │   │       ├── fog/
│   │   │       └── night_clear/
│   │   │
│   │   ├── verification/              # Phase 20 — three-axis wobble for perception
│   │   │   ├── README.md
│   │   │   ├── gate.py                # Mirrors phoenix/verification/gate.py
│   │   │   ├── cross_modality.py      # Axis 1 (analogue of cross_precision)
│   │   │   ├── cross_frame.py         # Axis 2 (analogue of cross_probe)
│   │   │   ├── cross_canonical.py     # Axis 3 (analogue of cross_provider)
│   │   │   └── agreement_classifier.py # Extends vendored DisagreementFinding
│   │   │
│   │   └── api/                       # Phase 21 — perception endpoints
│   │       ├── README.md
│   │       ├── routes.py              # /v1/perception/... endpoints
│   │       └── ws_handlers.py         # Streaming scene-graph updates
│   │
│   ├── sensors/                       # NEW — top-level sensor ingest (factored out per question 6.2)
│   │   ├── README.md
│   │   ├── lidar_ingest.py
│   │   ├── radar_ingest.py
│   │   ├── camera_ingest.py
│   │   ├── imu_ingest.py
│   │   ├── gnss_ingest.py
│   │   └── stream_router.py           # Routes streams to perception pipeline
│   │
│   ├── api/                           # EXISTING — extended with perception routes
│   ├── cli/                           # EXISTING — extended with perception commands
│   ├── mcp/                           # EXISTING — extended with perception tools
│   ├── trinity/                       # EXISTING — unchanged
│   ├── grammar/                       # EXISTING — unchanged (physics task grammar)
│   ├── router/                        # EXISTING — unchanged
│   ├── verification/                  # EXISTING — unchanged (physics wobble)
│   ├── safety/                        # EXISTING — unchanged
│   ├── admin/                         # EXISTING — extended with perception endpoints
│   ├── ledger/                        # EXISTING — unchanged (perception solves seal here)
│   ├── audit/                         # EXISTING — unchanged (perception events flow here)
│   ├── identity/                      # EXISTING — unchanged
│   ├── adapters/                      # EXISTING — unchanged
│   ├── providers/                     # EXISTING — unchanged
│   ├── state/                         # EXISTING — unchanged
│   ├── queue/                         # EXISTING — unchanged
│   └── _internal/                     # EXISTING — extended with optional 4th cloud seam
│
├── vendor/                            # EXISTING — extended with perception substrate
│   ├── grammar/                       # EXISTING — Sanskrit codec already vendored
│   ├── wobble/                        # EXISTING — reused for perception axes
│   ├── actor/                         # EXISTING — reused for perception API auth
│   ├── synthesis/                     # EXISTING — physics solvers (untouched)
│   ├── perception_substrate/          # NEW — vendored perception-specific tools (TBD)
│   └── VENDOR_VERSION.txt             # EXISTING — extended with perception_substrate fields
│
├── tests/                             # EXISTING — extended with perception test trees
│   ├── perception/                    # NEW
│   │   ├── unit/
│   │   ├── integration/
│   │   ├── tier1/                     # Per-mode canonical-example battery
│   │   └── invariants/
│
├── scripts/                           # EXISTING — extended
│   ├── vendor_sync.py                 # EXTENDED — handles perception_substrate too
│   └── ...
│
├── PHOENIX_ARCHITECTURE_v1.md         # EXISTING — unchanged
├── PHOENIX_ARCHITECTURE_v2.md         # FUTURE — only if Option II chosen
├── PHOENIX_PERCEPTION_HARNESS_PLAN_v0.md  # THIS DOCUMENT
├── BUILDGUIDE_phoenix_v1_phase0_skeleton.md  # EXISTING — unchanged
├── BUILDGUIDE_phoenix_v1_phase1_vendor_sync.md  # EXISTING — unchanged
├── BUILDGUIDE_phoenix_v1_phase12_perception_foundation.md  # FUTURE
└── BUILDGUIDE_phoenix_v1_phase{13..22}_*.md   # FUTURE — perception phases
```

The CLI, MCP, and API top-level subsystems gain perception-related commands and endpoints, but the perception-specific logic lives in `phoenix/perception/`. This matches Phoenix v1's existing separation between transport layers and domain logic.

**Open question 6.1:** Sensor ingest top-level (`phoenix/sensors/`) or nested (`phoenix/perception/sensors/`)? Recommendation: top-level, since sensor ingest is generally useful infrastructure that future non-perception domains may need.

**Open question 6.2:** Canonical example library at `phoenix/perception/laksana_laksya/canonical_examples/` will hold real sensor data — point cloud samples, frame samples, etc. This will be the bulk of the perception package by storage size. Should this be in-tree, git-LFS, or external storage? Recommendation: git-LFS, mirroring how large model weights are typically handled.

**Open question 6.3:** The `vendor/perception_substrate/` directory — what gets vendored there? Possibilities include nuScenes annotation transforms, Penrose substitution-rule generators (proprietary IP), reference perception models. To be specified during phase 12 build-guide drafting. Architecturally, the directory exists; its contents are TBD.

---

<a id="section-7"></a>

## 7. Proposed Phase Structure — Phase 12 Onwards or v1.x Track

Eleven new phases (Phase 12 through Phase 22), following Phoenix v1's established phase-gate-with-stop-gates methodology. Each phase has its own build guide produced and reviewed independently. Phase numbering continues v1's sequence; perception lands as v1.1, v1.2, etc. extensions per Option I.

### Phase Summary Table

| Phase | Focus                                              | Estimated effort | Stop-gate criterion                                          |
|-------|----------------------------------------------------|-----------------:|--------------------------------------------------------------|
| 12    | Perception Foundation — directory skeleton, RTOS shape| 2–3 weeks    | `phoenix/perception/` skeleton; smoke test passes            |
| 13    | Chanda meter monitor (Prototype A)                 |       3–4 weeks  | Detects ≥95% of injected sensor degradations                 |
| 14    | Kāraka scene graph (Prototype B)                   |       4–6 weeks  | nuScenes annotations re-typified at ≥90% role assignment     |
| 15    | Vivakṣā multi-modal fusion                         |       3–4 weeks  | All six per-sensor scene graphs maintained in parallel       |
| 16    | Penrose pulse-train simulator (Prototype C)        |       6–10 weeks | Numerical demonstration of reconstruction advantage          |
| 17    | Anuvṛtti frame inheritance                         |       3–4 weeks  | Frame-stream compression ≥10× in steady-state                |
| 18    | Paribhāṣā mode selector + precedence               |       2–3 weeks  | Mode classification ≥95% accurate vs ground-truth weather    |
| 19    | Lakṣaṇa-lakṣya canonical library                   |       4–6 weeks  | ≥50 canonical examples per mode, all modes covered           |
| 20    | Three-axis perception wobble verification           |       3–4 weeks  | Cross-modality, cross-frame, cross-canonical axes integrated |
| 21    | API + CLI + MCP perception endpoints               |       3–4 weeks  | All four protocols expose perception capability              |
| 22    | Validation battery + commercial brief              |       3–4 weeks  | Performance benchmarks documented; customer brief draft      |

Total estimated effort: 36–52 weeks single-track (8–12 months of dedicated build effort).

### Why this phase ordering

Phase 12 (foundation) is the absolute-minimum directory skeleton plus daemon-extension shape. Mirrors v1's Phase 0.

Phases 13, 14 are the two highest-value standalone deliverables (chanda + kāraka), buildable in parallel. They produce demo-able value independently.

Phase 15 (vivakṣā) layers on top of phase 14's scene graph.

Phase 16 (Penrose) is the IP-defensible novel piece; placed mid-track because the simulator can develop in parallel with the harness skeleton work.

Phase 17 (anuvṛtti) is compression and depends on phases 14-15 settling.

Phase 18 (paribhāṣā) is mode-switching and depends on phase 17's framing of state and phase 13's chanda monitor.

Phase 19 (lakṣaṇa-lakṣya) is annotation-heavy; placed late because the canonical example *schema* depends on phases 12-18 settling.

Phase 20 (verification) integrates the three perception axes — the analogue of v1's three-axis wobble. Mirrors v1 Section 6.

Phase 21 (API/CLI/MCP) extends the front door — analogous to v1 Phase 9 + Phase 10 work.

Phase 22 (validation + commercial) is the customer-facing wrap-up.

**Open question 7.1:** Adam approves the 11-phase structure?

**Open question 7.2:** Adam approves the proposed ordering?

**Open question 7.3:** Single-track or parallel-track execution? Phases 13 and 14 are explicitly parallel-trackable; phase 16 is parallel-trackable from phase 14 onward. Parallel execution shaves weeks but increases concurrent context burden.

---

<a id="section-8"></a>

## 8. Phase-by-Phase Detail With Explicit Stop Gates

For each phase: scope, deliverables, on-disk paths, stop-gate criterion, ⚡ PERF callout, 🛡️ SAFETY callout, README updates, and explicit open questions. Format mirrors `BUILDGUIDE_phoenix_v1_phase0_skeleton.md`.

### Phase 12 — Perception Foundation

**Scope.** Create `phoenix/perception/` directory skeleton. Wire perception module imports. Extend daemon to recognize perception requests. Add per-section READMEs per Section 10.6 template. Mirror v1 Phase 0's discipline.

**Deliverables.**
- `phoenix/perception/__init__.py`
- `phoenix/perception/{harness,data_model,pipeline}.py` — minimal stubs
- `phoenix/perception/README.md` — top-level perception subsystem doc
- `phoenix/sensors/__init__.py` + `phoenix/sensors/README.md`
- One smoke test: `tests/perception/unit/test_perception_smoke.py`
- Updates to `phoenix/__init__.py` (perception module discoverable)
- Updates to `phoenix/api/routes.py` — `/v1/perception/health` returning `{"status": "ok"}`
- Update to `phoenix/cli/commands/` — `phoenix perception --version` command

**Stop-gate criterion.** `python -c "import phoenix.perception"` succeeds. `phoenix perception --version` prints version. `GET /v1/perception/health` returns 200. `pytest tests/perception/unit/test_perception_smoke.py` passes. Per-section READMEs exist for `phoenix/perception/`, `phoenix/sensors/`.

**⚡ PERF.** Foundation phase has minimal perf concerns. Smoke test should complete in <1s.

**🛡️ SAFETY.** Foundation phase introduces no new attack surface. The new endpoint `/v1/perception/health` is unauthenticated (matches `/v1/health` precedent) and exposes no sensitive data.

**README updates.** New `phoenix/perception/README.md`, `phoenix/sensors/README.md` per Section 10.6 template.

**Launcher updates.** None — foundation phase doesn't change startup behavior.

### Phase 13 — Chanda Meter Monitor

**Scope.** Build the polyrhythm integrity FSM. Independent of other perception techniques — smallest standalone deliverable that demonstrates value.

**Deliverables.**
- `phoenix/perception/chanda/meter_monitor.py`
- `phoenix/perception/chanda/meter_definitions.yaml` — expected meter per mode
- `phoenix/perception/chanda/degradation_policies.py`
- `phoenix/perception/chanda/README.md`
- `tests/perception/unit/test_chanda_meter_monitor.py` with synthetic degradation injection

**Stop-gate criterion.** Meter monitor detects ≥95% of injected sensor degradations (frame drops, late arrivals, out-of-order arrivals) on a synthetic stream derived from a real nuScenes scene. False positive rate ≤1% on healthy streams.

**⚡ PERF.** Meter monitor must run O(1) per sensor sample. CPU budget: ≤0.5% of a single core.

**🛡️ SAFETY.** Meter monitor failures must not propagate to the planner. Wrap in watchdog that escalates to "advisory unavailable" if the monitor itself is unhealthy. Audit-log every meter-break event.

**README updates.** New `phoenix/perception/chanda/README.md`.

**Launcher updates.** None.

**Open question 13.1:** Meter monitor telemetry — emit always or query-on-demand? Recommendation: query-on-demand for production; emit-always in dev mode via env var.

### Phase 14 — Kāraka Scene Graph

**Scope.** Define six kāraka role types as Python typed dataclasses. Build scene-graph data structure. Re-typify nuScenes annotations into kāraka form (the bulk of the work). Build structured query engine.

**Deliverables.**
- `phoenix/perception/karaka/scene_graph.py`
- `phoenix/perception/karaka/role_schema.py` — six role types as dataclasses
- `phoenix/perception/karaka/role_inference.py`
- `phoenix/perception/karaka/query_engine.py`
- `phoenix/perception/karaka/README.md`
- `scripts/perception/retypify_nuscenes.py`
- `data/perception/nuscenes_karaka/` — re-typified subset (git-LFS)
- `tests/perception/unit/test_karaka_*.py`

**Stop-gate criterion.** ≥90% of nuScenes scene annotations successfully re-typified to kāraka form (manual spot-check). Query engine answers canonical query suite correctly on held-out split.

**⚡ PERF.** Query engine indexed by `(action_type, role_type)` for O(1) common-pattern lookup. Avoid full graph scans.

**🛡️ SAFETY.** Role inference is the riskiest piece. Wrong role assignments produce wrong queries. Need explicit confidence reporting; refuse to assign low-confidence roles rather than guessing.

**README updates.** New `phoenix/perception/karaka/README.md`.

**Launcher updates.** None.

**Open question 14.1:** Annotation re-mapping strategy — rules-plus-spot-check on 100 scenes for v0, or full re-annotation? Recommendation: rules + spot-check.

### Phase 15 — Vivakṣā Multi-Modal Fusion

**Scope.** Maintain N parallel scene graphs (one per sensor + one fused). Implement vivakṣā-preserving fusion. Provide query API.

**Deliverables.**
- `phoenix/perception/vivaksha/per_sensor_graphs.py`
- `phoenix/perception/vivaksha/fusion.py`
- `phoenix/perception/vivaksha/view_queries.py`
- `phoenix/perception/vivaksha/README.md`
- Tests including "lidar degraded, query radar view" scenario

**Stop-gate criterion.** All six per-sensor scene graphs (lidar, radar, three cameras, IMU+GNSS) maintained in parallel from a real nuScenes scene. Per-sensor view queries return correct subsets.

**⚡ PERF.** Maintaining N parallel graphs is potentially N× memory. Use shared structure for static parts.

**🛡️ SAFETY.** When per-sensor graphs disagree, do not silently average. Disagreement is itself a syndrome to report.

**README updates.** New `phoenix/perception/vivaksha/README.md`.

**Launcher updates.** None.

### Phase 16 — Penrose Pulse-Train Simulator

**Scope.** Numerical lidar simulator. Three pulse-coding strategies: M-sequence baseline, true-random Geiger-mode baseline, Penrose-substitution novel piece. Inject rain interference at varying densities. Measure detection-and-reconstruction performance. **THE IP-DEFENSIBLE NOVEL CONTRIBUTION.**

**Deliverables.**
- `phoenix/perception/penrose/temporal/pulse_train.py`
- `phoenix/perception/penrose/temporal/decoder.py`
- `phoenix/perception/penrose/temporal/simulator.py`
- `phoenix/perception/penrose/spatial/tiling.py`
- `phoenix/perception/penrose/spatial/feature_extraction.py`
- `phoenix/perception/penrose/README.md`
- Research note `docs/perception/penrose_pulse_research_note.md` — paper-grade documentation

**Stop-gate criterion.** Penrose pulse train demonstrates ≥20% reduction in residual-error point cloud reconstruction error vs M-sequence baseline at 20% rain-induced corruption rate. (Threshold provisional — Adam may set higher or lower.)

**⚡ PERF.** Pulse train generation O(n) — easy.

**🛡️ SAFETY.** Phase produces a *simulator*, not real lidar. Simulator findings inform future hardware partnership; nothing here deployed in real vehicles.

**README updates.** New `phoenix/perception/penrose/README.md` plus research note in `docs/`.

**Launcher updates.** None.

**Open question 16.1:** Stop-gate threshold (≥20% reduction). Adam's call — higher = harder phase but stronger commercial story.

**Open question 16.2:** IP strategy — file provisional patent before publishing simulator results, or publish defensively as prior art? Recommendation: file provisional, publish defensively.

### Phase 17 — Anuvṛtti Frame Inheritance

**Scope.** Compute frame deltas. Represent each frame as `inherited_from(prior_frame) + delta`. Compress perception stream. Provide motion-prior model.

**Deliverables.**
- `phoenix/perception/anuvritti/frame_inheritance.py`
- `phoenix/perception/anuvritti/delta_compression.py`
- `phoenix/perception/anuvritti/motion_prior.py`
- `phoenix/perception/anuvritti/README.md`
- Tests measuring compression ratio on real driving sequences

**Stop-gate criterion.** Frame-stream compression ≥10× in steady-state highway driving. Compression maintains lossless reconstruction.

**⚡ PERF.** Delta computation O(|delta|) not O(|frame|). Use spatial indexing.

**🛡️ SAFETY.** Frame inheritance creates dependency chain — corrupted frame N corrupts everything downstream. Need periodic anchor frames (analogous to MPEG I-frames) to limit corruption propagation.

**README updates.** New `phoenix/perception/anuvritti/README.md`.

**Launcher updates.** None.

### Phase 18 — Paribhāṣā Mode Selector + Precedence

**Scope.** Determine current weather/operating mode. Apply per-mode sensor precedence rules. Resolve conflicts.

**Deliverables.**
- `phoenix/perception/paribhasha/mode_selector.py`
- `phoenix/perception/paribhasha/precedence_rules.yaml`
- `phoenix/perception/paribhasha/conflict_resolver.py`
- `phoenix/perception/paribhasha/README.md`

**Stop-gate criterion.** Mode classification ≥95% accurate vs ground-truth weather labels. Precedence rules produce expected sensor weighting per mode.

**⚡ PERF.** Mode selection runs every frame; budget ≤1ms.

**🛡️ SAFETY.** Mode flapping is real risk. Implement hysteresis — N consecutive frames in new mode required before flip.

**README updates.** New `phoenix/perception/paribhasha/README.md`.

**Launcher updates.** None.

### Phase 19 — Lakṣaṇa-Lakṣya Canonical Library

**Scope.** Curate canonical examples per weather mode. Build matcher.

**Deliverables.**
- `phoenix/perception/laksana_laksya/canonical_library.py`
- `phoenix/perception/laksana_laksya/canonical_examples/{mode}/` — file tree (git-LFS)
- `phoenix/perception/laksana_laksya/matcher.py`
- `phoenix/perception/laksana_laksya/README.md`

**Stop-gate criterion.** ≥50 canonical examples per weather mode, all modes covered. Matcher correctly classifies held-out frames at ≥95%.

**⚡ PERF.** Matcher uses fast feature embedding for nearest-neighbor lookup, not pixel-level comparison.

**🛡️ SAFETY.** Canonical library curated against ground-truth weather labels by human. Auto-curation produces feedback loops where system learns its own biases.

**README updates.** New `phoenix/perception/laksana_laksya/README.md`.

**Launcher updates.** None.

### Phase 20 — Three-Axis Perception Wobble Verification

**Scope.** Mirror v1's three-axis wobble (Section 6 of v1 architecture) for perception. Three independent axes:
- **Cross-modality** (analogue of cross-precision): same scene observed by lidar vs radar vs camera, agreement scored
- **Cross-frame** (analogue of cross-probe): scene at frame N vs reconstructed-from-N-1 anuvṛtti delta, agreement scored
- **Cross-canonical** (analogue of cross-provider): incoming frame vs nearest canonical example match, agreement scored

Reuse vendored `vendor/wobble/` framework.

**Deliverables.**
- `phoenix/perception/verification/gate.py` — mirrors `phoenix/verification/gate.py`
- `phoenix/perception/verification/cross_modality.py`
- `phoenix/perception/verification/cross_frame.py`
- `phoenix/perception/verification/cross_canonical.py`
- `phoenix/perception/verification/agreement_classifier.py` — extends DisagreementFinding
- `phoenix/perception/verification/README.md`

**Stop-gate criterion.** Three axes integrated. Agreement metrics produce typed `Result(value, error_bar, sigma, agreement_type)` for perception solves, mirroring v1's contract.

**⚡ PERF.** Verification adds fixed overhead per solve. Budget ≤20% of total perception solve latency.

**🛡️ SAFETY.** Mandatory verification on every perception solve, mirroring v1 Decision 13. Adaptive depth tunable per `max_error_bar` parameter.

**README updates.** New `phoenix/perception/verification/README.md`.

**Launcher updates.** None.

### Phase 21 — API + CLI + MCP Perception Endpoints

**Scope.** Extend Phoenix's existing front door (Section 5 of v1 architecture) with perception capability across all four transports: REST, WebSocket, CLI, MCP. Reuse Actor authentication.

**Deliverables.**
- Extensions to `phoenix/api/routes.py` — `/v1/perception/*` endpoints
- Extensions to `phoenix/api/ws_handlers.py` — streaming scene-graph updates
- Extensions to `phoenix/cli/commands/` — `phoenix perception <action>` commands
- Extensions to `phoenix/mcp/tools.py` — perception MCP tools (`perception_classify_weather_mode`, `perception_meter_status`, `perception_query_scene_graph`, `perception_get_per_sensor_view`, `perception_compress_frame`)
- Updates to `phoenix/api/openapi.yaml` — perception endpoint specs
- Tests across all four protocols

**Stop-gate criterion.** All four protocols expose perception capability. Cross-protocol audit-log correlation works (single `request_id` traces across REST → audit-log → ledger → MCP).

**⚡ PERF.** API endpoints add minimal overhead. WebSocket streaming budget ≤10ms per scene-graph update.

**🛡️ SAFETY.** All perception endpoints require Actor authentication, mirroring v1 (Decision 12). Rate limiting via existing `phoenix/safety/rate_limiter.py`. Audit-log every perception request.

**README updates.** Updates to `phoenix/api/README.md`, `phoenix/cli/README.md`, `phoenix/mcp/README.md`.

**Launcher updates.** None — daemon already handles `/v1/*` routing pattern.

### Phase 22 — Validation Battery + Commercial Brief

**Scope.** Performance benchmarks on real adverse-weather datasets. Customer-facing technical brief. Phoenix Perception positioning document.

**Deliverables.**
- `docs/perception/validation_report_v0.md` — quantitative benchmark results
- `docs/perception/commercial_brief_v0.md` — customer-facing positioning
- `docs/perception/ip_inventory_v0.md` — patentable contributions catalog

**Stop-gate criterion.** Validation report shows specific quantitative gains (operational-domain extension hours, false-positive rate reduction, scene graph query latency) on adverse-weather datasets. Commercial brief review-ready for target customer.

**⚡ PERF.** Validation benchmarks include latency profiles, not just accuracy.

**🛡️ SAFETY.** Validation report explicitly addresses failure modes catalogued in phase 12's literature review (deferred from phase 12 to here).

**README updates.** New `docs/perception/README.md` index.

**Launcher updates.** None.

---

<a id="section-9"></a>

## 9. New Open Tensions Proposed for Section 11 of v1 Architecture

If Adam accepts this plan, the following new entries should be added to Section 11 of `PHOENIX_ARCHITECTURE_v1.md` as part of the v1.1 architecture revision. They follow Section 11's existing format and category structure.

### 11.6 — Perception extension tensions (new category)

**11.6.1 — Perception extension placement.** Whether perception ships as v1.x extension (Option I in this plan, Section 4) or v2 reorganization (Option II). Recommended disposition: Option I, defer Option II evaluation to post-v1-release.

**11.6.2 — Perception substrate vendoring scope.** What gets vendored at `vendor/perception_substrate/`? nuScenes annotation transforms? Reference perception models? Penrose substitution-rule generators (proprietary)? Recommended disposition: specify during phase 12 build-guide drafting; the directory exists architecturally, contents TBD.

**11.6.3 — Sensor ingest top-level vs nested.** Top-level `phoenix/sensors/` vs `phoenix/perception/sensors/`. Recommended disposition: top-level, since sensor ingest is generally useful infrastructure.

**11.6.4 — Canonical example library storage.** In-tree, git-LFS, or external storage for the canonical example libraries (Phase 19 deliverable)? Recommended disposition: git-LFS.

**11.6.5 — Penrose temporal pulse coding hardware integration.** Phase 16 simulator vs live hardware integration. Recommended disposition: simulator-only for v1.x; live hardware deferred to Phoenix v2 contingent on hardware partner.

**11.6.6 — Perception verification axes count.** Three axes (cross-modality, cross-frame, cross-canonical) mirrors v1's quantum wobble. Should perception adopt a fourth axis (e.g., cross-temporal-window)? Recommended disposition: ship with three; revisit after v1.x perception ships.

**11.6.7 — Real-time latency target.** v1's batch real-time is 10-100ms (Decision 26). Perception requires sub-100ms hard real-time per frame. Different latency tier; specify per-phase budgets in build guides. Recommended disposition: each phase's PERF callout commits to its specific budget; the v1.x architecture revision adds a "perception real-time" target alongside v1's batch real-time.

### Update to Section 10.8 (v1.1 acceptance criteria)

If Adam accepts this plan, Section 10.8 should add: "**Perception harness extension:** All perception phases (12-22) shipped per `PHOENIX_PERCEPTION_HARNESS_PLAN_v0.md`. Tier-1 perception calibration battery passes (canonical examples per weather mode). Three-axis perception wobble verification produces typed Results matching v1's contract pattern."

---

<a id="section-10"></a>

## 10. Honest Limits and Risks

What this approach cannot do, said explicitly.

**Phoenix Perception does not create photons.** When rain is severe enough that no useful sensor return arrives, no amount of grammar reconstructs information that wasn't there. Phoenix Perception extends operational envelope; it does not make weather irrelevant.

**The grammar is only as good as its training corpus.** kāraka role assignment templates, chanda meter expectations, lakṣaṇa-lakṣya canonical examples — all built from real driving data across the weather range. Phoenix Perception trained only on California sunny weather will fail in Buffalo lake-effect snow. Substantial annotation/curation effort required.

**Penrose pulse-train work requires lidar hardware partnership for production deployment.** Phase 16's simulator establishes the IP. Real-vehicle deployment requires close vendor partnership, multi-year horizon.

**Safety certification is real even for placement B (parallel enhancement).** Aviation advisory-system precedent (DO-178C software level C/D) applies. Bar lower than ASIL-D; not zero.

**Snow is not just rain.** Snow accumulation on sensor windows, snow-covered lane lines, changing surface friction affecting IMU baselines — different failure modes from rain. Each needs own grammar mode and canonical examples. Cannot ship "rain mode" and assume snow extends.

**Validation dataset bias.** nuScenes, Waymo Open, Argoverse over-represent California, Pittsburgh, Phoenix conditions. Adverse-weather datasets (CADC, DENSE, Boreas) smaller and have own biases. Phase 22 validation must include explicit bias discussion.

**Technique probation.** Some of the six techniques may not earn their keep on the perception domain. anuvṛtti compression gains may be smaller than projected for fast-moving urban driving. vivakṣā per-sensor preservation introduces query latency that may not justify it in real-time deployment. Phase stop gates explicitly allow technique deprecation if empirical case doesn't hold.

**Risk of over-claiming.** Sanskrit framing is intellectually rich and tells a compelling story, but engineering wins (if any) must come from the math, not the cultural connection. The product is the math. The inspiration is the framing. We must avoid the failure mode where we sell "Pāṇinian-inspired" as if inspiration is itself the product.

**Phoenix v1 attention dilution.** Working on perception planning while v1 implementation is still pending Phase 0 completion risks neither shipping cleanly. This plan is therefore explicitly scoped to *planning* work that does not consume v1 implementation effort. Build-guide drafting for perception phases happens only after v1 reaches a defined milestone (recommendation: Phase 5 verification gate complete, around mid-track of v1 implementation).

---

<a id="section-11"></a>

## 11. Commercial Framing — Phoenix Perception as a Product

### Two product positions

**Phoenix Perception for Adverse Weather AV Operations.** Sold to AV programs as parallel enhancement layer. Value proposition: extend safe operating domain by 30–60% more weather-hours per year. Target customers: Waymo, Cruise (Vulcan), Aurora, Mobileye, Pony.AI, Motional, Apollo. Pricing: per-vehicle license, possibly outcome-based on operational-domain-extension metrics.

**Phoenix Perception for Industrial and Defense.** Lower safety bar than automotive, broader sensor diversity, different threat models. Target customers: agricultural autonomy (John Deere, AGCO), mining (Caterpillar, Komatsu), maritime (Saildrone, Ocean Aero), defense (drone navigation, autonomous ground vehicles). Faster prove-out, more permissive regulatory environment.

### Recommended go-to-market sequence

Industrial/defense first. Lower regulatory friction means faster prove-out, more customer tolerance for novel approaches, and customers who specifically need to operate in degraded conditions. Accumulate field data and validation evidence in industrial/defense, then carry into automotive.

This is the playbook NVIDIA used (gaming and industrial first, then automotive). Path to revenue on 12-18 month timeline rather than 4-7 year automotive-direct timeline.

### Phoenix Perception relative to Phoenix v1 Core

Phoenix v1 Core (the existing locked architecture) is the substrate. Phoenix Perception is a domain extension that may ship separately, may price separately, may even license separately.

Per Decision 34, Phoenix v1 ships under Apache 2.0. The cleanest extension licensing follows the same Apache 2.0 (mirroring v1's posture) and monetizes via Phoenix Cloud's commercial bundle (Decision 35) extended to include perception-specific contracts: enterprise SSO, audit-log retention SLA, white-glove drift recalibration on canonical example libraries, dedicated provider rate contracts for cloud-perception-compute. Same pattern, different domain.

**Open question 11.1:** GTM sequence — industrial/defense first or AV-direct? Recommendation: industrial/defense first.

**Open question 11.2:** Licensing — Perception under Apache 2.0 mirroring v1, or commercial-only? Recommendation: Apache 2.0, monetize via Phoenix Cloud commercial bundle.

**Open question 11.3:** IP strategy on Penrose temporal pulse coding (Phase 16). File patents or publish defensively? Recommendation: file provisional, publish defensively.

---

<a id="section-12"></a>

## 12. Open Questions Requiring Adam's Decision

Consolidated catalog of every open question raised in this plan. Each requires Adam's decision before any build guide gets written.

| #    | Question                                                                       | My recommendation                          |
|------|--------------------------------------------------------------------------------|--------------------------------------------|
| 3.1  | Accept placement B (parallel enhancement) as first-deployment commitment?      | Accept                                     |
| 3.2  | Placement A as long-term ambition, or permanent placement B?                   | Permanent placement B until 5+ years field data |
| 4.1  | Accept Option I (v1.x extension) for perception placement?                     | Accept                                     |
| 4.2  | Earliest Phase number for perception work?                                     | Phase 12 (after v1 release at Phase 11)    |
| 5.1  | Approve six-techniques-plus-Penrose scope?                                     | Approve                                    |
| 5.2  | Penrose temporal pulse coding in phase 16 simulator-only or hardware?          | Simulator-only for v1.x; hardware in v2    |
| 6.1  | Sensor ingest in `phoenix/sensors/` (top-level) or nested under perception?    | Top-level                                  |
| 6.2  | Canonical example library storage — in-tree, git-LFS, external?                | Git-LFS                                    |
| 6.3  | What gets vendored at `vendor/perception_substrate/`?                          | TBD during phase 12 build-guide drafting   |
| 7.1  | Approve 11-phase structure (Phase 12 through Phase 22)?                        | Approve                                    |
| 7.2  | Approve proposed phase ordering?                                               | Approve                                    |
| 7.3  | Single-track or parallel-track execution?                                      | Single-track for v0; parallelize after Phase 14 |
| 13.1 | Meter monitor telemetry — emit always or query-on-demand?                      | Query-on-demand prod; always-on dev mode   |
| 14.1 | nuScenes annotation re-mapping — rules+spot-check or full re-annotation?       | Rules + spot-check on 100 scenes for v0    |
| 16.1 | Phase 16 stop-gate threshold (≥20% reduction in reconstruction error)?         | Confirm 20%                                |
| 16.2 | Phase 16 IP strategy — file provisional or publish defensively?                | File provisional, publish defensively      |
| 9.1  | Add perception-extension tensions (11.6.1-11.6.7) to v1 architecture Section 11? | Add                                      |
| 9.2  | Update Section 10.8 v1.1 acceptance with perception harness criterion?         | Add                                        |
| 11.1 | GTM sequence — industrial/defense first or AV-direct?                          | Industrial/defense first                   |
| 11.2 | Perception licensing — Apache 2.0 mirroring v1, or commercial-only?            | Apache 2.0; monetize via Cloud bundle      |
| 11.3 | Penrose pulse coding IP strategy?                                              | File provisional, publish defensively      |

Adam's review of this plan should produce a decision on each of the 21 questions. Where Adam wants to override my recommendation, that override binds the plan. Where Adam accepts my recommendation, that recommendation binds the plan.

---

<a id="section-13"></a>

## 13. Recommended Next Step After Plan Review

When this plan is approved (or revised and re-approved):

1. **Adam's decisions on the 21 open questions in Section 12 are recorded.**
2. **Architecture revision lands.** `PHOENIX_ARCHITECTURE_v1.md` gains the 11.6.x entries proposed in Section 9. Section 10.8 gains the perception harness criterion. The architecture revises from v1.0 to v1.1 (a documentation-only revision; no v1 implementation impact).
3. **First perception build guide drafted.** `BUILDGUIDE_phoenix_v1_phase12_perception_foundation.md` lands at `C:\Phoenix\` once Phoenix v1 reaches a defined milestone (recommendation: v1 Phase 5 verification gate complete) so v1 implementation is not diluted.
4. **Phase 12 work begins** under Claude Code, with the standard standing rules: phase gate review, stop-and-ask on ambiguity, PERF/SAFETY callouts, per-section READMEs, no OneDrive paths, launcher updates if relevant.

If this plan is rejected or substantially revised, no architecture revision and no perception build guide are written. Phoenix v1 implementation continues unchanged through Phases 0-11.

If this plan is approved with revisions, specific Sections are revised and re-reviewed before any architecture or build-guide work proceeds.

---

## Appendix A — Cross-Reference Map

How this plan maps to v1 architecture sections:

| Section here              | v1 Architecture reference                            |
|---------------------------|------------------------------------------------------|
| §1 Why extension          | v1 Section 0 (identity), Section 1 Decision 1-3      |
| §2 Substrate audit        | v1 Section 10.2 (vendoring map), Section 10.3        |
| §3 Strategic positioning  | v1 Section 1 Decision 6 (advisory posture)           |
| §4 Architectural placement| v1 Section 10.7 (v1 acceptance), Section 10.8 (v1.1) |
| §5 Technical scope        | New work; not in v1                                  |
| §6 Package layout         | v1 Section 10.3 layout pattern                       |
| §7 Phase structure        | v1 Phase 0-11 pattern; perception extends as 12-22   |
| §8 Phase detail           | v1 Phase 0 build guide format                        |
| §9 New tensions           | v1 Section 11 catalog format                         |
| §10 Honest limits         | New disclosure                                       |
| §11 Commercial framing    | v1 Decision 34 (Apache 2.0), Decision 35 (Cloud)     |

## Appendix B — Glossary of Sanskrit Terms

(Full definitions in `Sanskrit_Memory_Compression_Wider_Toolkit_for_Ash.md`.)

- **Anuvṛtti** — context inheritance; in this plan, frame-to-frame inheritance for stream compression
- **Aṣṭādhyāyī** — Pāṇini's foundational grammar of Sanskrit
- **Chanda** — meter; in this plan, polyrhythm integrity check on sensor streams
- **Kāraka** — semantic role; in this plan, typed roles for scene-graph entities
- **Lakṣaṇa-lakṣya** — rule-example pair; in this plan, canonical examples per weather mode
- **Paribhāṣā** — meta-rule; in this plan, sensor-conflict precedence rules
- **Vivakṣā** — speaker intent; in this plan, per-sensor framing preservation

---

**End of Phoenix Perception Harness Extension Plan v0**

**Status:** DRAFT — awaiting Adam's review and decision on the 21 open questions in Section 12.

**No build guide drafted. No architecture modified. No code written. The locked v1 architecture and its existing build guide pipeline remain untouched.**

When ready, Adam should:
1. Read this plan in full
2. Mark decisions on the 21 questions in Section 12
3. Note any architectural revisions in the relevant Sections
4. Approve, approve-with-revisions, or reject

— Adam & Claude
*Phoenix / Dr. Frank & Eddy / Third Space*
*May 7, 2026*
