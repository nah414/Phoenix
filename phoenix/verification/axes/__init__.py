"""Cognition wobble axes (Phase 13 Step 4).

Three :class:`CognitionAxis` implementations for cross-model
verification of cognition outputs:

- :class:`CrossModelAxis` — same prompt to two or more providers.
- :class:`SelfConsistencyAxis` — same prompt to the same provider
  multiple times with varied temperatures.
- :class:`PromptPerturbationAxis` — semantically-equivalent rewrites
  dispatched to the same primary provider.

All three emit :class:`CognitionDisagreementMetric` with
``disagreement_type = PhoenixDisagreementType.COGNITION_UNCLASSIFIED``
per Step 4 spec; the cognition disagreement classifier (Step 5)
replaces ``UNCLASSIFIED`` with a real class.

**Protocol relationship:** The existing
:class:`phoenix.verification.wobble_axis.WobbleAxis` Protocol takes a
:class:`PhysicsTask`; cognition axes take a :class:`Prompt`. Step 4
ships the cognition axes as their own classes with a parallel result
type (:class:`CognitionDisagreementMetric`); full Protocol
unification (or a sibling Protocol) lands when the verification gate
gains its cognition branch in Step 9+.
"""

from phoenix.verification.axes._result import (
    CognitionAxisProvenance,
    CognitionDisagreementMetric,
)
from phoenix.verification.axes.cross_model import CrossModelAxis
from phoenix.verification.axes.prompt_perturbation import PromptPerturbationAxis
from phoenix.verification.axes.self_consistency import SelfConsistencyAxis

__all__ = [
    "CognitionAxisProvenance",
    "CognitionDisagreementMetric",
    "CrossModelAxis",
    "PromptPerturbationAxis",
    "SelfConsistencyAxis",
]
