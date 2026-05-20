"""``GBMClassifier`` — gradient-boosted-machine cognition disagreement classifier.

Per Phase 13 build guide Step 5 + ``[OPEN: P13-4]`` default: a
calibrated GBM is the primary classifier in the hybrid architecture;
the LLM-as-judge escalation (:mod:`cognition_wobble.classifier_llm_judge`)
fires when GBM confidence is below threshold.

Step 5b ships the wrapper class + serialization contract. Step 5c
trains the model on 200+ calibration examples and commits the
serialized artifact.

The classifier uses ``lightgbm`` (pinned; lighter than xgboost on
Windows + comparable accuracy on tabular features). Step 5c's
training pipeline lives at ``scripts/train_cognition_classifier.py``
(future Step 5c deliverable).

**SAFETY:** the model artifact is shipped under
``vendor/cognition_wobble/`` so it travels with the wheel install.
No network round-trip at runtime; no PII in the feature vector
(features are computed from response text but never persisted in
their input form — Step 8's ledger respects 13-D2 prompt_disposition).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from cognition_wobble.classifier import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    ClassificationResult,
)
from cognition_wobble.disagreement_types import CognitionDisagreementType
from cognition_wobble.features import FEATURE_NAMES, extract_features
from phoenix.providers.cognition.errors import MissingOptionalDependency

if TYPE_CHECKING:
    from phoenix.providers.cognition.types import CognitionResult, Prompt


_DEFAULT_MODEL_PATH = (
    Path(__file__).parent / "models" / "gbm_classifier_v1.txt"
)
"""Default location of the serialized lightgbm model. Step 5c commits
the artifact here; Step 5b's tests inject mocks via the ``booster``
kwarg."""


# Order matters: the trained GBM's class indices map to this tuple in
# enumeration order. Step 5c's training script writes this mapping
# alongside the model artifact for cross-version verification.
_GBM_CLASS_ORDER: tuple[CognitionDisagreementType, ...] = (
    CognitionDisagreementType.FACTUAL_AGREEMENT,
    CognitionDisagreementType.STYLISTIC_DIVERGENCE,
    CognitionDisagreementType.FACTUAL_DISAGREEMENT,
    CognitionDisagreementType.INTERPRETIVE_DIVERGENCE,
    CognitionDisagreementType.REFUSAL_DIVERGENCE,
    CognitionDisagreementType.TOOL_CHOICE_DIVERGENCE,
)


class GBMClassifier:
    """Gradient-boosted cognition disagreement classifier.

    Loads a serialized lightgbm Booster at construction; predicts
    class probabilities at classification time. Returns
    :attr:`CognitionDisagreementType.UNCLASSIFIED` when the maximum
    class probability falls below ``confidence_threshold``.
    """

    version: str = "gbm-v1-step5b-scaffold"
    """Version string for ledger provenance. Step 5c bumps this when
    the trained model lands (likely ``"gbm-v1.0.0"``). The
    ``-step5b-scaffold`` suffix flags that Step 5b ships an
    untrained-or-stub-trained model; replay against a 5b-tagged
    classification carries this caveat."""

    def __init__(
        self,
        *,
        booster: Any = None,
        model_path: Path | None = None,
    ) -> None:
        """Construct.

        Args:
            booster: Optional pre-loaded lightgbm Booster (tests
                inject mocks here). When ``None``, the classifier
                attempts to load from ``model_path``.
            model_path: Path to the serialized lightgbm model. Falls
                back to :data:`_DEFAULT_MODEL_PATH`.

        Raises:
            MissingOptionalDependency: When ``lightgbm`` is not
                installed and no ``booster`` is injected.
            FileNotFoundError: When the model file is missing.
        """
        if booster is not None:
            self._booster = booster
        else:
            try:
                import lightgbm as lgb
            except ImportError as exc:
                raise MissingOptionalDependency(
                    "lightgbm SDK not installed; "
                    "install via `pip install phoenix-middleware[ml-classifier]`."
                ) from exc
            target = model_path if model_path is not None else _DEFAULT_MODEL_PATH
            if not target.exists():
                raise FileNotFoundError(
                    f"Cognition classifier model not found at {target}. "
                    "Step 5c commits the trained artifact; Step 5b ships "
                    "the loader scaffolding only. Train via "
                    "`python scripts/train_cognition_classifier.py` "
                    "(Step 5c deliverable)."
                )
            self._booster = lgb.Booster(model_file=str(target))

    def classify(
        self,
        prompt: Prompt,
        responses: list[CognitionResult],
        *,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> ClassificationResult:
        """Classify the disagreement.

        Step 5b contract:

        - Extract features from ``(prompt, responses[0], responses[1])``.
        - Call ``booster.predict(...)`` to get a probability vector
          over the six graded classes (in :data:`_GBM_CLASS_ORDER`).
        - Return the argmax class IFF its probability >=
          ``confidence_threshold``; otherwise UNCLASSIFIED.

        Raises ``ValueError`` if fewer than 2 responses are provided.
        """
        if len(responses) < 2:
            raise ValueError(
                f"GBMClassifier requires at least 2 responses; got {len(responses)}."
            )

        features = extract_features(prompt, responses[0], responses[1])
        feature_vector = [features[name] for name in FEATURE_NAMES]

        # Booster.predict returns a 2D array even for a single input row;
        # we want the first row's probabilities.
        raw = self._booster.predict([feature_vector])
        probs = list(raw[0])

        best_idx = max(range(len(probs)), key=lambda i: probs[i])
        best_prob = float(probs[best_idx])

        if best_prob < confidence_threshold:
            return ClassificationResult(
                disagreement_type=CognitionDisagreementType.UNCLASSIFIED,
                confidence=best_prob,
                classifier_version=self.version,
                feature_values=features,
                escalated_to_judge=False,
            )

        return ClassificationResult(
            disagreement_type=_GBM_CLASS_ORDER[best_idx],
            confidence=best_prob,
            classifier_version=self.version,
            feature_values=features,
            escalated_to_judge=False,
        )
