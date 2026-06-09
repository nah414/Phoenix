# Phoenix v1 documentation

This directory is the documentation hub for Phoenix. The canonical
architecture spec ([`PHOENIX_ARCHITECTURE_v1.md`](../PHOENIX_ARCHITECTURE_v1.md))
remains at the repo root; everything else lives here.

## User-facing guides

- **[`distribution/`](distribution/README.md)** — install + run guides for
  each of the three v1 release artifacts (pip wheel, Docker image, Nuitka
  standalone binary). Per architecture v1 Section 1 Decision 29.
- **[`reproducibility/`](reproducibility/README.md)** — the reproducibility
  surface, including the cloud-shots-recorded asterisk on strict-mode replay
  (Section 1 Decision 20 + Section 11 RESOLVED disposition).

## Planning & extension records

- **[`planning/`](planning/)** — forward-looking extension plans and locked
  decision records:
  - `PHOENIX_PERCEPTION_HARNESS_PLAN_v1.md` — locked perception-harness
    extension plan (v0 archived under `planning/archive/`).
  - `ROADMAP_post_step5b_2026-05-20.md` — post-Step-5b roadmap snapshot.
  - `DESIGN_DECISIONS_PHASE13_LOCKED_2026-05-18.md` — Phase 13 locked
    design decisions.

## Internal development records

- **[`build-guides/`](build-guides/README.md)** — the per-phase build guides
  used to execute the v1 build against the architecture spec. These are
  internal development records, not user-facing documentation.
- **`superpowers/`** — implementation plans (`plans/`) and design specs
  (`specs/`) for individual development phases.

For the canonical architecture reference, see
[`PHOENIX_ARCHITECTURE_v1.md`](../PHOENIX_ARCHITECTURE_v1.md) at the repo root.
