# wobble

## Purpose
Wobble detector -- measures agreement and divergence across multiple LLM co-author responses using embedding similarity and TF-IDF cosine distance. Used in the convergence loop to determine whether co-authors agree or need further rounds.

## Files
| File | Description |
|------|-------------|
| `detector.py` | Wobble detection engine (397 lines) -- computes pairwise divergence scores, identifies divergent pairs, classifies consensus |

## Key Classes/Functions
- `WobbleDetector` -- main detector; primary method uses sentence-transformers embeddings, falls back to sklearn TF-IDF cosine distance
- `WobbleResult` -- dataclass with score, consensus flag, divergent_pairs, pairwise_distances, n_responses, method used
- `WobbleConfig` -- configuration dataclass for thresholds and detector behavior

## Testing
```bash
python -m pytest tests/test_wobble.py -v
```

## v6.3 Updates (April 2026)

### Wobble Scores in Shared Query Results
- When Third Space query results are shared to a BB84 peer via messaging, the wobble scores are included in the shared payload
- The QueryResultCard in the messaging UI renders wobble score, consensus flag, and divergent pair info so the receiving peer can see the full agreement/disagreement analysis
- No changes to the wobble detector itself -- this is a data-flow addition at the messaging/UI layer

## Notes
- Primary backend: `sentence-transformers` (`SentenceTransformer`). If not installed, falls back to `sklearn` TF-IDF
- Both `sentence-transformers` and `sklearn` are optional dependencies -- at least one must be available
- Wobble score feeds into the sigma weight calculation: `SIGMA_WEIGHTS = {"agreement": 0.35, "wobble": 0.25, "validator": 0.25, "round": 0.15}`
- In v5.0 (single-brain), wobble detection is used less since Qwen3 is the sole local LLM, but the module remains for Third Space co-author divergence checks


## Known issues — v6.1.2 investigation (2026-04-10)

**Status: NO ISSUES IDENTIFIED in this directory.**

The v6.1.2 deep-read investigation (Opus, 2026-04-10) found no bugs or required changes affecting `wobble/`. The disagreement detector, classifier, and action handlers are stable.

See `TROUBLESHOOTING.md` at the project root for the project-wide reference.

### Important context for future Claude Code sessions touching wobble/

- **The wobble detector measures co-author disagreement** as a scalar between 0 (perfect consensus) and 1 (maximum disagreement). It feeds into `compute_sigma()` as the `wobble` factor.
- **`disagreement_classifier.py` (Phase 3C of the original wobble build, NOT to be confused with v6.1.2 Phase 3C)** classifies findings into 5 types: CONTRADICTION, TEMPORAL_DRIFT, FRAME_MISMATCH, HEDGED_CONSENSUS, UNKNOWN. The classifier output is rendered by `ui/src/components/DisagreementView.tsx`.
- **Borderline wobble scores** (within ±0.1 of the threshold) trigger bidirectional wobble analysis via `detect_bidirectional`. The bidirectional path uses sklearn TF-IDF and is gated behind `HAS_SKLEARN`. If sklearn is missing, the bidirectional path is skipped silently.
- **There is a separate `WOBBLE_REBUILD_NOTES.md`** at `archive/docs-pre-v6.5/WOBBLE_REBUILD_NOTES.md` (moved from the project root in v6.5 P8). It documents the v4.x rebuild of this subsystem; read it before doing any architectural changes here.
