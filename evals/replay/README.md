# evals/replay/

## Purpose
Evaluations that verify Phoenix's strict and replay reproducibility modes produce bit-exact match for the deterministic portion of the pipeline (solver execution, control verification, post-shot processing). Cloud-quantum hardware shots are intrinsically nondeterministic and recorded once; replay reads from the recorded shots rather than re-running on hardware. Architecture v1 Section 1 Decision 19 names this as "the strongest reproducibility guarantee any quantum middleware on the market would make"; this directory's job is to prove it under realistic conditions.

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 1 Decisions 19 (three reproducibility modes), 20 (cloud-shot reproducibility limit + `cloud_shots_recorded` provenance flag), 21 (operational discipline: requirements.lock + RNG seeds + FP environment), Section 6.7 (replay verification of every RunRecord), Section 10.7 (long-window six-month replay test as a v1 acceptance criterion added in the 2026-05-06 tightening).

## Phase
This directory is populated in Phase 7 (audit + ledger + drift). Phase 0 ships only this README placeholder.

## What evals will land here
- A `default`-mode solve has no replay guarantee but writes full provenance.
- A `strict`-mode solve replays bit-exactly when the same `requirements.lock`, vendored substrate, RNG seeds, and FP environment are present.
- A `replay`-mode solve replays-and-verifies before returning, with the expected ~2x wall-clock cost.
- A solve invoking a cloud quantum provider has `provenance.cloud_shots_recorded=True`; replay reads from recorded shots and produces bit-exact post-shot pipeline output. The original cloud run is *not* re-run; this is documented as the asterisk on the reproducibility claim.
- An adapter-version mismatch on replay raises `AdapterVersionMismatch` with the recorded vs current fingerprints.
- A `requirements.lock` mismatch on replay raises clearly rather than silently producing different results.
- **Long-window six-month replay test**: take a v1.0 ledger entry whose solve completed cleanly, store it for 6+ simulated months, then run `/v1/tasks/{id}/replay` against v1.0 (or v1.0+patch) and confirm bit-exact match. Run on CI's typical hardware *plus* a clean Linux container *plus* a clean macOS runner so platform-specific drift is also caught.

## Recent changes
- 2026-05-06 — Phase 0: placeholder created.
