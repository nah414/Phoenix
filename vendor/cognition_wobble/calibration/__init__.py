"""Calibration eval sets for the cognition disagreement classifier.

Step 5a (current) ships :mod:`cognition_wobble.calibration.bootstrap`
with ~14 hand-crafted examples (~2 per graded class plus 2
gold-UNCLASSIFIED rows). The bootstrap set is sufficient to exercise
the eval framework end-to-end; it is NOT sufficient to satisfy the
build-guide ``macro-F1 >= 0.70`` acceptance gate. Step 5b expands
this to >= 200 paired examples spanning all seven taxonomy classes
(~28+ per class).

Step 5b dataset sources (per build guide §4.5):

- **SAC3** — semantic agreement evaluation across LLMs.
- **FELM** — fact-checking benchmark for LLM outputs.
- **FINCH-ZK** — zero-shot fact-checking.
- **Phoenix-generated** — physics-prompt LLM responses, tool-use
  scenarios, Phoenix-specific edge cases.
"""

from cognition_wobble.calibration.bootstrap import BOOTSTRAP_EXAMPLES

__all__ = ["BOOTSTRAP_EXAMPLES"]
