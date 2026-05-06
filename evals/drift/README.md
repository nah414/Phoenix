# evals/drift/

## Purpose
Evaluations that verify Phoenix's three calibration drift detectors fire when expected, stay silent when expected, and compose correctly (e.g., multi-detector agreement triggers high-confidence drift escalation). Architecture v1 Section 1 Decision 17 commits to continuous drift monitoring via the Tier-1 analytical battery (HO-1, ISW-1, H1S-1, RABI-1, SCG-1), an ML-based statistical drift detector, and a cross-version comparison. This directory's job is to prove the three detectors do what they say they do.

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 1 Decision 17 (calibration drift monitoring — three detectors, 6-hour cadence, multi-detector escalation), Section 6.5 (drift-aware promotion in the verification gate), Section 8.2 (`/v1/admin/calibration/detail`, `/run`, `/history`).

## Phase
This directory is populated in Phase 7 (audit + ledger + drift). Phase 0 ships only this README placeholder.

## What evals will land here
- A simulated drift (deliberately injected miscalibration) triggers the Tier-1 detector within one drift cycle.
- A simulated drift on the same baseline triggers the ML statistical detector.
- A version-bump drift (current Tier-1 disagrees with prior release's recorded Tier-1) triggers the cross-version detector.
- Single-detector firings produce `drift_warning` provenance flags but do not block solves.
- Multi-detector agreement (≥2 detectors firing) escalates to "high confidence drift" and the dev-ops backdoor surfaces an alert.
- Drift-aware promotion: when any detector is in `drift_warning` state at task submission, the verification gate auto-promotes one rung beyond what `max_error_bar` would normally select (Section 6.5).
- Replay reads the *recorded* drift state from the ledger entry, not the current live drift state — so replays don't spuriously diverge if drift state changes between original and replay.

## Recent changes
- 2026-05-06 — Phase 0: placeholder created.
