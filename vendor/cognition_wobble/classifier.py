"""``CognitionClassifier`` Protocol + result type + stub impl.

Per Phase 13 build guide Step 5 + ``[OPEN: P13-4]`` default: the
classifier is pluggable. Step 5a ships the Protocol contract and a
stub (:class:`AlwaysUnclassifiedClassifier`); Step 5b ships the real
GBM-based hybrid classifier (GBM primary, LLM-as-judge for
``UNCLASSIFIED`` escalation).

The Protocol is structural (PEP 544): any class exposing
:attr:`version` + the :meth:`classify` method satisfies it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from cognition_wobble.disagreement_types import CognitionDisagreementType

if TYPE_CHECKING:
    from phoenix.providers.cognition.types import CognitionResult, Prompt


# Default confidence threshold below which the classifier returns
# UNCLASSIFIED. Step 5b's hybrid impl may escalate to LLM-as-judge
# instead of returning UNCLASSIFIED at this boundary; the threshold
# stays as the GBM-primary-vs-LLM-judge decision point.
DEFAULT_CONFIDENCE_THRESHOLD: float = 0.5


@dataclass(frozen=True)
class ClassificationResult:
    """One classifier invocation's structured output.

    The Step 8 Omega Ledger entry's
    ``cognition_classifier_provenance_json`` field carries a
    serialized projection of this (per the privacy-aware ledger
    schema from 13-D2).

    Fields:
        disagreement_type: The classifier's output class. May be
            :attr:`CognitionDisagreementType.UNCLASSIFIED` when
            confidence is below threshold.
        confidence: Classifier confidence in ``[0, 1]``. Step 5b's
            GBM impl produces calibrated probabilities; the stub
            always returns ``0.0``.
        classifier_version: Stable version string (e.g.
            ``"gbm-v0.1.0"``). Replay against a recorded
            :class:`ClassificationResult` checks this for stability;
            a version mismatch flags the row as
            ``classifier-version-mismatch`` rather than failing
            replay outright.
        feature_values: Per-feature values used by the classifier
            (e.g. ``{"semantic_distance": 0.42, "length_ratio": 1.1,
            ...}``). Step 5b populates with real features; the stub
            returns an empty dict. Step 8's ledger stores a hashed
            summary, not the full dict, when ``prompt_disposition``
            is ``HASH_ONLY``.
        escalated_to_judge: ``True`` when the hybrid classifier
            escalated to LLM-as-judge for this call. The GBM-only
            path leaves this ``False``. Step 5a's stub always
            returns ``False``.
    """

    disagreement_type: CognitionDisagreementType
    confidence: float
    classifier_version: str
    feature_values: dict[str, float] = field(default_factory=dict)
    escalated_to_judge: bool = False


@runtime_checkable
class CognitionClassifier(Protocol):
    """The structural contract every cognition classifier satisfies.

    Step 5b's hybrid impl + the LLM-as-judge escalation path both
    satisfy this Protocol. Tests inject stub classifiers via the
    Protocol to exercise the verification gate's cognition branch
    without requiring the real GBM model.

    The Protocol is intentionally minimal:

    - :attr:`version`: stable string for ledger provenance.
    - :meth:`classify`: classify one (prompt, responses) tuple.

    Step 5b may add optional methods (e.g. ``feature_extract`` for
    debugging) as non-required additions to the Protocol.
    """

    version: str
    """Stable classifier version string (e.g. ``"gbm-v0.1.0"``,
    ``"stub-always-unclassified-v1"``). Phase 13 Step 8's Omega
    Ledger records this so replay can verify classification
    stability across classifier upgrades."""

    def classify(
        self,
        prompt: Prompt,
        responses: list[CognitionResult],
        *,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> ClassificationResult:
        """Classify the disagreement (if any) across the given responses.

        Args:
            prompt: The original prompt the responses were generated
                from. The classifier may inspect prompt structure
                (e.g. detect tool-eligible prompts for
                TOOL_CHOICE_DIVERGENCE detection).
            responses: Two or more :class:`CognitionResult` instances
                to classify. The classifier MAY require N >= 2 and
                raise :class:`ValueError` otherwise; the stub allows
                any N.
            confidence_threshold: When classifier confidence falls
                below this, return
                :attr:`CognitionDisagreementType.UNCLASSIFIED` rather
                than the best-guess class. Defaults to
                :data:`DEFAULT_CONFIDENCE_THRESHOLD`. Step 5b's
                hybrid impl uses this as the GBM-vs-LLM-judge
                decision boundary.
        """
        ...


class AlwaysUnclassifiedClassifier:
    """Stub classifier — always returns ``UNCLASSIFIED`` with confidence 0.0.

    Useful for:

    1. Exercising the verification-gate cognition branch end-to-end
       without requiring the Step 5b real classifier.
    2. Unit-testing the eval framework's macro-F1 + per-class metric
       computation against a known-bad classifier (every prediction
       UNCLASSIFIED → all per-class precision/recall are 0).
    3. Bootstrapping new cognition deployments where the operator
       hasn't yet trained a calibrated classifier.

    The Step 5b hybrid GBM/LLM-judge classifier replaces this in
    production deployments. The stub remains in the package for
    testing and bootstrapping.
    """

    version: str = "stub-always-unclassified-v1"

    def classify(
        self,
        prompt: Prompt,
        responses: list[CognitionResult],
        *,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> ClassificationResult:
        del prompt, responses, confidence_threshold
        return ClassificationResult(
            disagreement_type=CognitionDisagreementType.UNCLASSIFIED,
            confidence=0.0,
            classifier_version=self.version,
            feature_values={},
            escalated_to_judge=False,
        )
