# `vendor/ml/`

Vendored ML primitives consumed by Phoenix's drift detector
ensemble (Section 1 Decision 17 + Phase 6b Step 7). Ships
`drift_ensemble.py` -- the three-detector ensemble (Tier-1
analytical, ML statistical, cross-version) that drives the
verification gate's drift-state read (Section 6.8).

Per Section 11.7.1's vendor-verbatim discipline, kept unchanged
from upstream. Phoenix's drift state module (`phoenix/verification/
drift_state.py`) imports `ml.drift_ensemble` via the sys.path
injection wired in `phoenix/__init__.py`.

Architectural reference: `PHOENIX_ARCHITECTURE_v1.md` Section 1
Decision 17 (three drift detectors), Section 6.8 (drift-state
fail-closed), Section 11.7.1 (vendor discipline).
