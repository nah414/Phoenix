# Phoenix Perception Harness — Extension Plan v1

**Status:** **LOCKED v1 — 2026-05-07.** All 21 decisions recorded; plan is the basis for v1.1 architecture revision and eventual Phase 12+ build guides.
**Authoritative location:** `C:\Phoenix\PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md`
**Architectural anchor:** `C:\Phoenix\PHOENIX_ARCHITECTURE_v1.md` (locked 2026-05-06; revising to v1.1 on 2026-05-07 per this plan's Section 9 dispositions)
**Target release:** Phoenix v1.x extension (Option I per Section 4) — perception phases land as Phases 12 through 22, after v1 release at Phase 11
**Date opened:** 2026-05-07
**Date locked:** 2026-05-07
**Author of record:** Adam (with Claude as design partner)

---

## v0 → v1 transition

v0 (drafted 2026-05-07) presented 21 open questions for Adam's review. v1 (locked 2026-05-07) records Adam's dispositions on all 21 questions in Section 12, expands Section 5 (Technical scope) to capture the scalability-on-top constraint Adam added to Q5.2, and expands Section 8 Phase 16 with explicit Protocol-based interface contracts and hardware-integration design constraints. v0 is preserved as historical record at `PHOENIX_PERCEPTION_HARNESS_PLAN_v0.md` and may be deleted at Adam's discretion.

The substantive change from v0 to v1 beyond decision recording: the Penrose temporal pulse-coding work (Phase 16) commits to a simulator-first deliverable architected so that hardware integration in v2 lands as a new driver implementing the same Protocol interfaces, not as a rewrite of the simulator. This is Adam's explicit Q5.2 disposition and the architectural principle binding Phase 16's design from the start.

---

## What this document is — and what it is not

This is the **locked v1 plan** for a Phoenix extension that adds perception-harness capability for autonomous-system middleware (the original thought experiment was adverse-weather perception for autonomous vehicles like Waymo). It is the same v0→v1 shape Adam used for the original Phoenix architecture: capture decisions, surface tensions, recommend dispositions, lock the plan after Adam's review.

This document **does not**:

- Modify the locked Phoenix v1.0 architecture's load-bearing structure. v1.1 of the architecture (the revision triggered by this plan's lock) is documentation-only — adds Section 11.14 and extends Section 10.8, no v1 implementation impact.
- Modify `BUILDGUIDE_phoenix_v1_phase0_skeleton.md` or `BUILDGUIDE_phoenix_v1_phase1_vendor_sync.md`. The v1 build pipeline (Phases 0 through 11) remains unchanged.
- Add to Section 10.7 (v1 acceptance criteria). Perception capability is a v1.1 acceptance commitment per Section 10.8 (extended in v1.1).

This document **does**:

- Position perception as a clean v1.x extension that respects v1.0's locked decisions
- Specify exactly what's reused from v1 (vendored Sanskrit codec, grammar substrate, wobble pattern, Omega Ledger, Actor authentication, cloud seams)
- Specify exactly what's new (perception-specific grammar, scene-graph schema, sensor ingest, real-time pipeline, Penrose pulse-train simulator with Protocol-based hardware-scaling interfaces)
- Specify the phase structure for the eventual build guides (Phase 12 through Phase 22)
- Lock the 21 decisions Adam approved on 2026-05-07

## Companion documents

| Document                                                          | Status   | Purpose                                              |
|-------------------------------------------------------------------|----------|------------------------------------------------------|
| `C:\Phoenix\PHOENIX_ARCHITECTURE_v1.md`                           | Locked v1.0; revising to v1.1 | Architecture spec — anchor for this plan |
| `C:\Phoenix\BUILDGUIDE_phoenix_v1_phase0_skeleton.md`             | Drafted  | Phase 0 build guide — unchanged by this plan         |
| `C:\Phoenix\BUILDGUIDE_phoenix_v1_phase1_vendor_sync.md`          | Drafted  | Phase 1 build guide — unchanged by this plan         |
| `C:\Phoenix\PHOENIX_PERCEPTION_HARNESS_PLAN_v0.md`                | Superseded | Historical record of v0 questions               |
| `Sanskrit_Memory_Compression_for_Ash.md`                          | Reference| Codec we shipped in dr-frank-and-eddy                |
| `Sanskrit_Memory_Compression_Wider_Toolkit_for_Ash.md`            | Reference| Broader Sanskrit toolkit survey                       |
| `Grok_Waymo_Penrose_Rain_Session_Full.md`                         | Reference| Initial AV perception thought experiment             |

---

## Table of Contents

1. [Why a Perception Harness Extension](#section-1)
2. [What's Reused From Phoenix v1 — The Substrate Audit](#section-2)
3. [Strategic Positioning — Parallel Enhancement, Not Critical Path](#section-3)
4. [Architectural Placement — v1.x Extension](#section-4)
5. [Technical Scope — Six Sanskrit Techniques Plus Penrose](#section-5)
6. [Package Layout — `phoenix/perception/`](#section-6)
7. [Phase Structure — Phase 12 Onwards](#section-7)
8. [Phase-by-Phase Detail With Explicit Stop Gates](#section-8)
9. [New Open Tensions Added to Section 11.14 of v1.1 Architecture](#section-9)
10. [Honest Limits and Risks](#section-10)
11. [Commercial Framing — Phoenix Perception as a Product](#section-11)
12. [Decisions Recorded (v1, 2026-05-07)](#section-12)
13. [Next Steps After v1 Lock](#section-13)

---

<a id="section-1"></a>

## 1. Why a Perception Harness Extension

The thought experiment that triggered this plan: an AI agent operating Phoenix as a middleware harness, sitting on top of an autonomous vehicle's sensor stack, helping the vehicle "see" better in heavy rain or snow. Waymo and other Level-4 robotaxi services restrict their operational design domain in adverse weather not because the vehicles are unsafe but because the *sensor stack* degrades — lidar suffers Mie scattering off raindrops, radar gets cluttered, cameras lose contrast — and the certified perception path conservatively pulls the vehicle off the road.

The opening: a middleware harness that runs *parallel to* the certified perception path, applies structured grammar-based interpretation to the same sensor inputs, and offers the planner an additional second-opinion confidence channel that becomes most valuable specifically when the primary path is least confident.

This shape — middleware-grade, downloadable, integrator-consumable, advisory-style, with provenance and error-bar discipline — **is exactly Phoenix's architecture**. Phoenix v1's identity (per Section 0 of the locked architecture) is "downloadable software that AI agents and integrators call to get validated computation, hardware-aware routing, and physics-grounded verification of results — with provenance, error bars, and honest reporting of uncertainty." Replace "validated computation" with "validated perception" and the value proposition transfers directly.

What changes between v1's quantum-accuracy domain and the perception domain:

- The substrate Phoenix vendors (v1 vendors physics solvers; perception extension vendors or builds perception-specific machinery).
- The pipeline shape (v1's Trinity Core has Solver→Control→Orchestrate; perception's pipeline has Sensors→Grammar→Scene→Verification).
- The verification axes (v1's three-axis wobble is cross-precision/cross-control/cross-provider; perception's three-axis wobble is cross-modality/cross-frame/cross-canonical-example).
- The latency tier (v1 ships batch real-time at 10-100ms; perception requires sub-100ms hard real-time per sensor frame — a new latency tier flagged in Section 11.14.7 of v1.1 architecture).

What stays the same:

- The Sanskrit codec and grammar substrate already vendored at `vendor/grammar/`.
- The Actor authentication pattern already vendored at `vendor/actor/`.
- The wobble disagreement framework already vendored at `vendor/wobble/`.
- The Omega Ledger hashchain pattern (extended in Phoenix v1's `phoenix/ledger/`).
- The cloud seams pattern at `phoenix/_internal/cloud_seams.py`.
- The phase-gate-with-stop-gates build guide methodology.
- The `[OPEN: ...]` tension tracking in Section 11 (now extended with 11.14 for perception).
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
| Hashchained Omega Ledger pattern            | `phoenix/ledger/`                           | Decision 15, Section 10.3    | Perception solves seal to same ledger          |
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

**Decision recorded (Q3.1, Q3.2, 2026-05-07):** Placement B (parallel enhancement layer, never safety-critical) is the first-deployment commitment. Placement A is *not* an explicit roadmap target; it remains available only contingent on 5+ years of field-validation evidence.

### The two placements (recorded for posterity)

**Placement A — Safety-critical path.** Phoenix Perception sits between sensors and planner; every perception decision flows through it. Requires ASIL-D certification under ISO 26262, ISO 21448 SOTIF analysis, FMVSS conformance. Realistic timeline: 4–7 years and substantial validation investment.

**Placement B — Parallel enhancement layer (LOCKED).** Phoenix Perception runs alongside the existing certified perception stack, takes the same sensor inputs, produces its own scene interpretation, and offers it to the planner as an *additional* confidence channel. The certified path remains safety-critical authority. Phoenix Perception's value compounds with the primary system's degradation rather than replacing it. Certification burden: lower (advisory-system precedent from aviation: DO-178C software level C/D). Time-to-market: 12–24 months for a real customer pilot.

### Why placement B was selected

1. **The primary value is in the degraded regime.** Heavy rain and heavy snow are exactly when Waymo's certified path reports low confidence. A parallel layer that becomes most informative there is the maximum value-per-integration-effort scenario.

2. **The integration story sells itself.** "Add a redundant grammar-based confidence channel" is a much easier conversation with a customer's safety org than "let us replace your certified perception."

3. **It matches Phoenix v1's existing identity.** v1 is already advisory-shaped. Decision 6 commits Phoenix v1's solves to traverse all three Trinity Core layers by default but explicitly allows opt-out per layer with widened error bars. Perception extension inherits this advisory posture naturally.

---

<a id="section-4"></a>

## 4. Architectural Placement — v1.x Extension

**Decision recorded (Q4.1, Q4.2, 2026-05-07):** Option I — v1.x extension. Perception phases land as Phase 12 onwards, after Phoenix v1 ships at Phase 11. Phoenix v1's quantum-accuracy core is not reorganized; perception extends the existing architecture.

### Three options considered (recorded for posterity)

**Option I — v1.x extension (LOCKED).** Phoenix v1 ships first per its locked phase pipeline (Phase 0 through Phase 11). Perception capability lands as v1.x extensions starting at Phase 12. Section 10.8 (v1.1 acceptance criteria) gains perception items per the v1.1 architecture revision.

**Option II — Greenfield v2 with v1 as substrate.** Phoenix v1 ships, then a v2 release reorganizes around the new pipeline shape. *Rejected:* substrate audit showed 70-80% reuse; greenfield v2 is unnecessary work.

**Option III — Parallel v1.x and v2 tracks.** *Rejected:* unnecessary complexity given Option I cleanly extends v1.

---

<a id="section-5"></a>

## 5. Technical Scope — Six Sanskrit Techniques Plus Penrose

**Decision recorded (Q5.1, 2026-05-07):** The six-techniques-plus-Penrose scope is approved. Other Sanskrit techniques (samāsa, dhātu/pratyaya, kaṭapayādi, svara, etc.) deferred to future domains.

**Decision recorded (Q5.2, 2026-05-07):** Penrose temporal pulse coding ships as simulator-only in v1.x; live hardware integration deferred to Phoenix v2. **Critical scalability constraint per Adam's expansion:** the simulator must be architected so that hardware integration in v2 lands as a new driver implementing the same Protocol interfaces, not as a rewrite. Specifically, Phase 16's deliverables include Protocol-based interface contracts (`LidarTransmitter` and `LidarReceiver` Protocols defined in `phoenix/perception/penrose/temporal/interfaces.py`), hardware-realistic signal formats (timing precision, amplitude levels), pluggable interference models (rain modeled mathematically in sim; real hardware sees rain physically), and decoder paths that don't assume simulator-specific signal characteristics. This constraint is binding from Phase 16 day one, not a v2 retrofit.

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

---

<a id="section-6"></a>

## 6. Package Layout — `phoenix/perception/`

**Decisions recorded (Q6.1, Q6.2, Q6.3, 2026-05-07):**
- Q6.1 — Top-level `phoenix/sensors/` (sensor ingest is generally useful infrastructure; not nested under perception).
- Q6.2 — Canonical example library storage uses git-LFS (standard pattern for large in-repo binary artifacts).
- Q6.3 — Vendor manifest for `vendor/perception_substrate/` deferred to Phase 12 build-guide drafting (architecturally the directory commits; specific contents land at drafting time).

The layout below mirrors the existing Phoenix v1 package layout convention specified in v1 architecture Section 10.3. New top-level subdirectory `phoenix/perception/` lives alongside the existing `phoenix/grammar/`, `phoenix/trinity/`, etc. Each subdirectory contains its own `README.md` per Decision 38 and Section 10.6.

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
│   │   ├── karaka/                    # Phase 14 — typed scene graph
│   │   ├── vivaksha/                  # Phase 15 — per-sensor preservation
│   │   ├── penrose/                   # Phase 16 — Penrose pulse + tiling
│   │   │   ├── README.md
│   │   │   ├── spatial/
│   │   │   │   ├── tiling.py
│   │   │   │   └── feature_extraction.py
│   │   │   └── temporal/
│   │   │       ├── interfaces.py      # NEW — LidarTransmitter, LidarReceiver Protocols (Q5.2 scalability)
│   │   │       ├── pulse_train.py     # Substitution-rule pulse generator (sim impl of LidarTransmitter)
│   │   │       ├── decoder.py         # Substitution-rule decoder (sim impl of LidarReceiver)
│   │   │       ├── interference_model.py  # Pluggable interference (rain/snow/fog math models)
│   │   │       └── simulator.py       # Numerical lidar simulator wrapping the Protocols
│   │   │
│   │   ├── anuvritti/                 # Phase 17 — frame inheritance
│   │   ├── paribhasha/                # Phase 18 — mode + precedence
│   │   ├── laksana_laksya/            # Phase 19 — canonical libraries (git-LFS)
│   │   ├── verification/              # Phase 20 — three-axis wobble for perception
│   │   └── api/                       # Phase 21 — perception endpoints
│   │
│   ├── sensors/                       # NEW — top-level sensor ingest (Q6.1 disposition)
│   │   ├── README.md
│   │   ├── lidar_ingest.py
│   │   ├── radar_ingest.py
│   │   ├── camera_ingest.py
│   │   ├── imu_ingest.py
│   │   ├── gnss_ingest.py
│   │   └── stream_router.py
│   │
│   └── (all existing v1 subdirectories unchanged)
│
├── vendor/
│   ├── perception_substrate/          # NEW — vendor manifest TBD per Q6.3
│   └── (existing v1 vendor subdirectories unchanged)
│
└── (existing v1 top-level files unchanged plus this plan's v1)
```

The CLI, MCP, and API top-level subsystems gain perception-related commands and endpoints, but the perception-specific logic lives in `phoenix/perception/`. This matches Phoenix v1's existing separation between transport layers and domain logic.

---

<a id="section-7"></a>

## 7. Phase Structure — Phase 12 Onwards

**Decisions recorded (Q7.1, Q7.2, Q7.3, 2026-05-07):**
- Q7.1 — 11-phase structure (Phases 12-22) approved.
- Q7.2 — Proposed phase ordering approved (Foundation → Chanda → Kāraka → Vivakṣā → Penrose → Anuvṛtti → Paribhāṣā → Lakṣaṇa-lakṣya → Verification → API → Validation).
- Q7.3 — Single-track execution through Phase 14; parallelize from Phase 15 onwards.

### Phase Summary Table

| Phase | Focus                                              | Estimated effort | Stop-gate criterion                                          |
|-------|----------------------------------------------------|-----------------:|--------------------------------------------------------------|
| 12    | Perception Foundation                              |       2–3 weeks  | `phoenix/perception/` skeleton; smoke test passes            |
| 13    | Chanda meter monitor                               |       3–4 weeks  | Detects ≥95% of injected sensor degradations                 |
| 14    | Kāraka scene graph                                 |       4–6 weeks  | nuScenes annotations re-typified at ≥90% role assignment     |
| 15    | Vivakṣā multi-modal fusion                         |       3–4 weeks  | All six per-sensor scene graphs maintained in parallel       |
| 16    | Penrose pulse-train simulator (with Q5.2 scalability)|     6–10 weeks | Numerical demo; Protocol interfaces hardware-ready           |
| 17    | Anuvṛtti frame inheritance                         |       3–4 weeks  | Frame-stream compression ≥10× in steady-state                |
| 18    | Paribhāṣā mode selector + precedence               |       2–3 weeks  | Mode classification ≥95% accurate vs ground-truth weather    |
| 19    | Lakṣaṇa-lakṣya canonical library                   |       4–6 weeks  | ≥50 canonical examples per mode, all modes covered           |
| 20    | Three-axis perception wobble verification           |       3–4 weeks  | Cross-modality, cross-frame, cross-canonical axes integrated |
| 21    | API + CLI + MCP perception endpoints               |       3–4 weeks  | All four protocols expose perception capability              |
| 22    | Validation battery + commercial brief              |       3–4 weeks  | Performance benchmarks documented; customer brief draft      |

Total estimated effort: 36–52 weeks single-track for first three phases (Phases 12-14 serial), parallelizable from Phase 15. Roughly 8–12 months of dedicated build effort.

---

<a id="section-8"></a>

## 8. Phase-by-Phase Detail With Explicit Stop Gates

For each phase: scope, deliverables, on-disk paths, stop-gate criterion, ⚡ PERF callout, 🛡️ SAFETY callout, README updates, and per-phase recorded decisions. Format mirrors `BUILDGUIDE_phoenix_v1_phase0_skeleton.md`.

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
- **Vendor manifest specification for `vendor/perception_substrate/`** per Q6.3 disposition

**Stop-gate criterion.** `python -c "import phoenix.perception"` succeeds. `phoenix perception --version` prints version. `GET /v1/perception/health` returns 200. `pytest tests/perception/unit/test_perception_smoke.py` passes. Per-section READMEs exist for `phoenix/perception/`, `phoenix/sensors/`. Vendor manifest landed.

**⚡ PERF.** Foundation phase has minimal perf concerns. Smoke test should complete in <1s.

**🛡️ SAFETY.** Foundation phase introduces no new attack surface. The new endpoint `/v1/perception/health` is unauthenticated (matches `/v1/health` precedent) and exposes no sensitive data.

**Launcher updates.** None — foundation phase doesn't change startup behavior.

### Phase 13 — Chanda Meter Monitor

**Scope.** Build the polyrhythm integrity FSM. Independent of other perception techniques — smallest standalone deliverable that demonstrates value.

**Deliverables.**
- `phoenix/perception/chanda/meter_monitor.py`
- `phoenix/perception/chanda/meter_definitions.yaml` — expected meter per mode
- `phoenix/perception/chanda/degradation_policies.py`
- `phoenix/perception/chanda/README.md`
- `tests/perception/unit/test_chanda_meter_monitor.py` with synthetic degradation injection

**Stop-gate criterion.** Meter monitor detects ≥95% of injected sensor degradations on a synthetic stream derived from a real nuScenes scene. False positive rate ≤1% on healthy streams.

**⚡ PERF.** Meter monitor must run O(1) per sensor sample. CPU budget: ≤0.5% of a single core.

**🛡️ SAFETY.** Meter monitor failures must not propagate to the planner. Wrap in watchdog that escalates to "advisory unavailable" if the monitor itself is unhealthy. Audit-log every meter-break event.

**Decision recorded (Q13.1):** Meter monitor telemetry is query-on-demand in production; emit-always in dev mode via env var.

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

**⚡ PERF.** Query engine indexed by `(action_type, role_type)` for O(1) common-pattern lookup.

**🛡️ SAFETY.** Role inference is the riskiest piece. Wrong role assignments produce wrong queries. Need explicit confidence reporting; refuse to assign low-confidence roles rather than guessing.

**Decision recorded (Q14.1):** Annotation re-mapping uses rules + spot-check on 100 scenes for v0; full re-annotation deferred until evidence justifies investment.

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

### Phase 16 — Penrose Pulse-Train Simulator (with Q5.2 Scalability Constraint)

**Scope.** Numerical lidar simulator. Three pulse-coding strategies: M-sequence baseline, true-random Geiger-mode baseline, Penrose-substitution novel piece. Inject rain interference at varying densities. Measure detection-and-reconstruction performance. **THE IP-DEFENSIBLE NOVEL CONTRIBUTION.**

**Critical scalability constraint (Q5.2).** The simulator must be architected so that hardware integration in Phoenix v2 lands as a new driver implementing the same Protocol interfaces, not as a rewrite of the simulator. This binds the design from day one of Phase 16.

**Deliverables.**
- `phoenix/perception/penrose/temporal/interfaces.py` — **Protocol contracts: `LidarTransmitter`, `LidarReceiver`, `InterferenceModel`** (the abstract contracts that both simulator and future hardware drivers implement)
- `phoenix/perception/penrose/temporal/pulse_train.py` — substitution-rule pulse generator implementing `LidarTransmitter` Protocol
- `phoenix/perception/penrose/temporal/decoder.py` — substitution-rule decoder implementing `LidarReceiver` Protocol
- `phoenix/perception/penrose/temporal/interference_model.py` — pluggable rain/snow/fog interference (mathematical models implementing `InterferenceModel` Protocol; future hardware integration replaces this with physical-world observation)
- `phoenix/perception/penrose/temporal/simulator.py` — numerical lidar simulator that composes the three Protocols into a runnable simulation harness
- `phoenix/perception/penrose/spatial/tiling.py` — Penrose-tiled spatial sampling
- `phoenix/perception/penrose/spatial/feature_extraction.py` — aperiodic-grid features
- `phoenix/perception/penrose/README.md`
- Research note `docs/perception/penrose_pulse_research_note.md` — paper-grade documentation

**Scalability design constraints (binding from Phase 16 day one):**
1. **Protocol-based interfaces.** All transmitter/receiver/interference behavior lives behind `typing.Protocol` definitions. The simulator implements them; a future hardware driver implements them differently. No simulator-specific assumptions leak into the decoder or interference model.
2. **Hardware-realistic signal formats.** Pulse train outputs use timing precision and amplitude representation that real lidar transmitters could consume — picosecond-resolution timestamps, calibrated power levels, not arbitrary simulator units.
3. **Pluggable interference models.** Rain modeled mathematically in the simulator's `InterferenceModel` impl; real hardware sees rain physically and the interference happens before the receiver Protocol is even invoked. The hardware-integration path replaces the interference model implementation, not the receiver.
4. **Decoder neutrality.** The receiver's reconstruction logic operates on the signal interface only and never assumes simulator-specific noise characteristics, sampling regularity, or amplitude normalization.

**Stop-gate criterion.** Penrose pulse train demonstrates ≥20% reduction in residual-error point cloud reconstruction error vs M-sequence baseline at 20% rain-induced corruption rate (Q16.1 disposition). **Plus:** integration test suite in `tests/perception/integration/test_penrose_protocols.py` confirms a mock hardware driver (a stub class implementing the same Protocols with intentionally-different internal behavior) can be swapped for the simulator implementations and produce structurally compatible outputs without modifying decoder or interference-model code. This is the scalability gate per Q5.2.

**⚡ PERF.** Pulse train generation O(n) — substitution rule expansion is linear.

**🛡️ SAFETY.** Phase produces a *simulator*, not real lidar. Simulator findings inform future hardware partnership; nothing here deployed in real vehicles.

**Decision recorded (Q16.1):** Stop-gate threshold confirmed at 20% reconstruction-error reduction. Provisional — adjustable if simulation reveals different realistic gain.

**Decision recorded (Q16.2):** IP strategy — file provisional patent, then publish defensively. Apache 2.0 patent grant precedent (Decision 34) makes this consistent with v1 licensing posture.

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

### Phase 19 — Lakṣaṇa-Lakṣya Canonical Library

**Scope.** Curate canonical examples per weather mode. Build matcher.

**Deliverables.**
- `phoenix/perception/laksana_laksya/canonical_library.py`
- `phoenix/perception/laksana_laksya/canonical_examples/{mode}/` — file tree (git-LFS per Q6.2)
- `phoenix/perception/laksana_laksya/matcher.py`
- `phoenix/perception/laksana_laksya/README.md`

**Stop-gate criterion.** ≥50 canonical examples per weather mode, all modes covered. Matcher correctly classifies held-out frames at ≥95%.

**⚡ PERF.** Matcher uses fast feature embedding for nearest-neighbor lookup, not pixel-level comparison.

**🛡️ SAFETY.** Canonical library curated against ground-truth weather labels by human. Auto-curation produces feedback loops where system learns its own biases.

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

### Phase 21 — API + CLI + MCP Perception Endpoints

**Scope.** Extend Phoenix's existing front door (Section 5 of v1 architecture) with perception capability across all four transports: REST, WebSocket, CLI, MCP. Reuse Actor authentication.

**Deliverables.**
- Extensions to `phoenix/api/routes.py` — `/v1/perception/*` endpoints
- Extensions to `phoenix/api/ws_handlers.py` — streaming scene-graph updates
- Extensions to `phoenix/cli/commands/` — `phoenix perception <action>` commands
- Extensions to `phoenix/mcp/tools.py` — perception MCP tools
- Updates to `phoenix/api/openapi.yaml` — perception endpoint specs
- Tests across all four protocols

**Stop-gate criterion.** All four protocols expose perception capability. Cross-protocol audit-log correlation works (single `request_id` traces across REST → audit-log → ledger → MCP).

**⚡ PERF.** API endpoints add minimal overhead. WebSocket streaming budget ≤10ms per scene-graph update.

**🛡️ SAFETY.** All perception endpoints require Actor authentication, mirroring v1 (Decision 12). Rate limiting via existing `phoenix/safety/rate_limiter.py`. Audit-log every perception request.

### Phase 22 — Validation Battery + Commercial Brief

**Scope.** Performance benchmarks on real adverse-weather datasets. Customer-facing technical brief. Phoenix Perception positioning document.

**Deliverables.**
- `docs/perception/validation_report_v0.md` — quantitative benchmark results
- `docs/perception/commercial_brief_v0.md` — customer-facing positioning
- `docs/perception/ip_inventory_v0.md` — patentable contributions catalog

**Stop-gate criterion.** Validation report shows specific quantitative gains (operational-domain extension hours, false-positive rate reduction, scene graph query latency) on adverse-weather datasets. Commercial brief review-ready for target customer.

**⚡ PERF.** Validation benchmarks include latency profiles, not just accuracy.

**🛡️ SAFETY.** Validation report explicitly addresses failure modes catalogued in phase 12's literature review.

---

<a id="section-9"></a>

## 9. New Open Tensions Added to Section 11.14 of v1.1 Architecture

**Decision recorded (Q9.1, Q9.2, 2026-05-07):** Both architecture revisions approved.

The following entries land at Section 11.14 of `PHOENIX_ARCHITECTURE_v1.md` as part of the v1.0 → v1.1 architecture revision (documentation-only revision; no v1 implementation impact). Section 10.8 (v1.1 acceptance criteria) gains the perception harness criterion.

### 11.14.1 — Perception extension placement (RESOLVED in v1.1)
Resolved Option I (v1.x extension) per Q4.1.

### 11.14.2 — Perception substrate vendoring scope
Build-guide territory; specified during Phase 12 drafting per Q6.3.

### 11.14.3 — Sensor ingest layer placement (RESOLVED in v1.1)
Resolved top-level `phoenix/sensors/` per Q6.1.

### 11.14.4 — Canonical example library storage (RESOLVED in v1.1)
Resolved git-LFS per Q6.2.

### 11.14.5 — Penrose temporal pulse coding hardware integration (RESOLVED in v1.1)
Resolved simulator-only for v1.x with binding scalability constraint per Q5.2.

### 11.14.6 — Perception verification axes count
Three axes for v1.x; revisit at v1.x perception milestone per recommended disposition.

### 11.14.7 — Perception real-time latency tier
Add to v1.1 as documented latency tier alongside batch real-time (Decision 26) and streaming real-time (Decision 28).

The full text of each entry lives in `PHOENIX_ARCHITECTURE_v1.md` Section 11.14 after the v1.1 revision lands.

---

<a id="section-10"></a>

## 10. Honest Limits and Risks

**Phoenix Perception does not create photons.** When rain is severe enough that no useful sensor return arrives, no amount of grammar reconstructs information that wasn't there.

**The grammar is only as good as its training corpus.** Substantial annotation/curation effort required across the weather range and geographies of intended deployment.

**Penrose pulse-train work requires lidar hardware partnership for production deployment.** Phase 16's simulator establishes the IP and the Protocol contracts; real-vehicle deployment requires a hardware-vendor partnership, multi-year horizon. Q5.2's scalability constraint ensures this transition is a driver-swap rather than a rewrite.

**Safety certification is real even for placement B.** Aviation advisory-system precedent (DO-178C software level C/D) applies. Bar lower than ASIL-D; not zero.

**Snow is not just rain.** Different failure modes; each needs own grammar mode and canonical examples.

**Validation dataset bias.** nuScenes, Waymo Open, Argoverse over-represent California, Pittsburgh, Phoenix conditions. Phase 22 validation must include explicit bias discussion.

**Technique probation.** Some of the six techniques may not earn their keep on the perception domain. Phase stop gates explicitly allow technique deprecation if empirical case doesn't hold.

**Risk of over-claiming.** The product is the math. The Sanskrit framing is the inspiration. We must avoid the failure mode where we sell "Pāṇinian-inspired" as if inspiration is itself the product.

**Phoenix v1 attention dilution.** Build-guide drafting for perception phases happens only after v1 reaches a defined milestone (Phase 5 verification gate complete) so v1 implementation is not diluted.

---

<a id="section-11"></a>

## 11. Commercial Framing — Phoenix Perception as a Product

**Decisions recorded (Q11.1, Q11.2, Q11.3, 2026-05-07):**
- Q11.1 — Industrial/defense first, then automotive.
- Q11.2 — Apache 2.0 mirroring v1; monetize via Phoenix Cloud commercial bundle.
- Q11.3 — File provisional patent on Penrose temporal pulse coding, publish defensively.

### Two product positions

**Phoenix Perception for Adverse Weather AV Operations.** Sold to AV programs as parallel enhancement layer. Value proposition: extend safe operating domain by 30–60% more weather-hours per year. Target customers: Waymo, Cruise (Vulcan), Aurora, Mobileye, Pony.AI, Motional, Apollo. Pricing: per-vehicle license, possibly outcome-based on operational-domain-extension metrics.

**Phoenix Perception for Industrial and Defense (FIRST GTM).** Lower safety bar than automotive, broader sensor diversity, different threat models. Target customers: agricultural autonomy (John Deere, AGCO), mining (Caterpillar, Komatsu), maritime (Saildrone, Ocean Aero), defense (drone navigation, autonomous ground vehicles). Faster prove-out, more permissive regulatory environment.

### Recommended go-to-market sequence

Industrial/defense first per Q11.1. Lower regulatory friction means faster prove-out, more customer tolerance for novel approaches, and customers who specifically need to operate in degraded conditions. Accumulate field data and validation evidence in industrial/defense, then carry into automotive. NVIDIA's playbook (gaming and industrial first, then automotive). Path to revenue on 12-18 month timeline rather than 4-7 year automotive-direct timeline.

### Phoenix Perception relative to Phoenix v1 Core

Phoenix v1 Core (the existing locked architecture) is the substrate. Phoenix Perception is a domain extension that ships separately under the same Apache 2.0 (mirroring v1's posture per Q11.2 and Decision 34) and monetizes via Phoenix Cloud's commercial bundle (Decision 35) extended to include perception-specific contracts: enterprise SSO, audit-log retention SLA, white-glove drift recalibration on canonical example libraries, dedicated provider rate contracts for cloud-perception-compute.

---

<a id="section-12"></a>

## 12. Decisions Recorded (v1, 2026-05-07)

All 21 decisions approved by Adam on 2026-05-07. Recommendations from v0 accepted as-is, with one expansion on Q5.2 capturing the scalability-on-top constraint.

| #    | Question                                                                       | Decision                                              |
|------|--------------------------------------------------------------------------------|-------------------------------------------------------|
| 3.1  | Accept placement B (parallel enhancement)?                                     | **ACCEPTED** — placement B is the first-deployment commitment |
| 3.2  | Placement A as long-term ambition?                                             | **PERMANENT B** until 5+ years of field data          |
| 4.1  | Accept Option I (v1.x extension)?                                              | **ACCEPTED** — perception is v1.x extension           |
| 4.2  | Earliest Phase number for perception work?                                     | **PHASE 12**, after v1 release at Phase 11            |
| 5.1  | Approve six-techniques-plus-Penrose scope?                                     | **APPROVED**                                           |
| 5.2  | Penrose temporal pulse coding: simulator-only or hardware?                     | **SIMULATOR ONLY for v1.x; hardware in v2 contingent.** Adam's expansion: simulator must scale to hardware via Protocol-based interfaces, not rewrite. Phase 16 deliverables explicitly include `LidarTransmitter`, `LidarReceiver`, `InterferenceModel` Protocols binding from day one. |
| 6.1  | Sensor ingest top-level or nested?                                             | **TOP-LEVEL** `phoenix/sensors/`                      |
| 6.2  | Canonical example library storage?                                             | **GIT-LFS**                                            |
| 6.3  | What gets vendored at `vendor/perception_substrate/`?                          | **TBD during Phase 12 build-guide drafting**          |
| 7.1  | Approve 11-phase structure (Phases 12-22)?                                     | **APPROVED**                                           |
| 7.2  | Approve proposed phase ordering?                                               | **APPROVED**                                           |
| 7.3  | Single-track or parallel-track execution?                                      | **SINGLE-TRACK through Phase 14; parallelize from Phase 15** |
| 13.1 | Meter monitor telemetry?                                                       | **QUERY-ON-DEMAND** in production; emit-always in dev mode |
| 14.1 | nuScenes annotation re-mapping strategy?                                       | **RULES + SPOT-CHECK on 100 scenes for v0**           |
| 16.1 | Phase 16 stop-gate threshold?                                                  | **20%** confirmed (provisional, adjustable if simulation reveals different realistic gain) |
| 16.2 | Phase 16 IP strategy?                                                          | **FILE PROVISIONAL, publish defensively**             |
| 9.1  | Add perception-extension tensions (11.14.1-11.14.7) to v1 architecture Section 11? | **ADD** as part of v1.0 → v1.1 architecture revision |
| 9.2  | Update Section 10.8 v1.1 acceptance with perception harness criterion?         | **ADD** as part of v1.0 → v1.1 architecture revision |
| 11.1 | GTM sequence?                                                                  | **INDUSTRIAL/DEFENSE FIRST**, then automotive         |
| 11.2 | Perception licensing?                                                          | **APACHE 2.0** mirroring v1; monetize via Phoenix Cloud commercial bundle |
| 11.3 | Penrose pulse coding IP strategy?                                              | **FILE PROVISIONAL, publish defensively**             |

These dispositions bind the plan. Future revisions to specific decisions require explicit Adam-led architectural reconsideration, not silent drift in build-guide work.

---

<a id="section-13"></a>

## 13. Next Steps After v1 Lock

The plan is locked at v1 as of 2026-05-07. The immediate next steps:

1. **Architecture revision (v1.0 → v1.1)** — Section 11.14 added with 7 tensions; Section 10.8 extended with perception harness acceptance criterion; document header updated; CHANGELOG entry recorded. **Lands today (2026-05-07) as part of this same work cycle.**
2. **First perception build guide** — `BUILDGUIDE_phoenix_v1_phase12_perception_foundation.md` lands at `C:\Phoenix\` once Phoenix v1 reaches a defined milestone (recommendation: v1 Phase 5 verification gate complete) so v1 implementation is not diluted. **Not landing today; sequenced after v1 implementation reaches the milestone.**
3. **Phase 12 work begins** under Claude Code at the appropriate time, with the standard standing rules: phase gate review, stop-and-ask on ambiguity, PERF/SAFETY callouts, per-section READMEs, no OneDrive paths, launcher updates if relevant.

**Tomorrow's work** (2026-05-08): continues v1 implementation per the existing locked Phase 0 → Phase 11 pipeline. Perception planning is locked and waits its turn.

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
| §9 New tensions           | v1.1 Section 11.14                                   |
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

**End of Phoenix Perception Harness Extension Plan v1**

**Status:** LOCKED v1 — 2026-05-07. All 21 decisions recorded. Plan is the authoritative basis for v1.1 architecture revision and eventual Phase 12+ build guides.

— Adam & Claude
*Phoenix / Dr. Frank & Eddy / Third Space*
*May 7, 2026*
