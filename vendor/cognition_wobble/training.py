"""GBM training pipeline for the cognition disagreement classifier (Step 5c).

Consumes a labeled corpus (loaded via :mod:`cognition_wobble.corpus`)
and produces a serialized **lightgbm native-text** model artifact that
:class:`cognition_wobble.classifier_gbm.GBMClassifier` loads verbatim
(``lgb.Booster(model_file=...)``). A sidecar ``*.meta.json`` records the
class order, feature names, version, and class balance so a trained
artifact is self-describing for replay/provenance.

**Why native-text, not joblib:** the shipped ``GBMClassifier`` loads via
``lgb.Booster(model_file=str(target))``. Matching that exact contract is
what makes the Step 5c swap plug-and-play — train here, drop the
``.txt`` at ``vendor/cognition_wobble/models/gbm_classifier_v1.txt``,
register ``GBMClassifier`` at daemon startup, done.

**Inert by default:** importing or calling this module trains nothing
until invoked with a real corpus and an explicit output path. It does
not touch the registry default and does not write to the package's
``models/`` directory unless the caller points it there.

``lightgbm`` is the opt-in ``[ml-classifier]`` extra; constructing a
trainer without it raises :class:`MissingOptionalDependency` with the
install hint (same contract as :class:`GBMClassifier`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cognition_wobble.classifier_gbm import _GBM_CLASS_ORDER
from cognition_wobble.corpus import count_by_class
from cognition_wobble.disagreement_types import GRADED_CLASSES, CognitionDisagreementType
from cognition_wobble.features import FEATURE_NAMES, extract_features
from phoenix.providers.cognition.errors import MissingOptionalDependency

if TYPE_CHECKING:
    from cognition_wobble.eval import CalibrationExample

__all__ = [
    "DEFAULT_GBM_PARAMS",
    "DEFAULT_NUM_BOOST_ROUND",
    "TrainingData",
    "TrainResult",
    "build_training_matrix",
    "train_gbm",
]


# ---------------------------------------------------------------------------
# Hyperparameters
#
# [ADAM-TUNE] These defaults are deliberately conservative for a *small*
# tabular multiclass problem (~200-1000 rows, 6 features). They are the
# one place in this harness where your real corpus's shape should drive
# the choice once it exists:
#   - num_leaves / min_data_in_leaf: raise as the corpus grows past a few
#     hundred rows per class to let the trees capture more structure;
#     lower them if you see train/eval F1 diverge (overfit).
#   - class_weight: pass "balanced" to train_gbm() if the corpus is
#     skewed (e.g. far more FACTUAL_AGREEMENT than TOOL_CHOICE_DIVERGENCE);
#     the default None trains on the raw distribution.
# Reproducibility: seed + single-thread + deterministic make the emitted
# .txt artifact byte-stable run-to-run, which matters for a *committed*
# model and for Step 8's classifier-version replay story.
DEFAULT_GBM_PARAMS: dict[str, Any] = {
    "objective": "multiclass",
    "metric": "multi_logloss",
    "boosting_type": "gbdt",
    "num_leaves": 15,
    "learning_rate": 0.05,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.9,
    "bagging_freq": 1,
    "min_data_in_leaf": 5,
    "seed": 42,
    "num_threads": 1,
    "deterministic": True,
    "force_col_wise": True,
    "verbose": -1,
}

DEFAULT_NUM_BOOST_ROUND: int = 200


@dataclass(frozen=True)
class TrainingData:
    """Feature matrix + integer labels ready for lightgbm.

    Pure-Python (no numpy/lightgbm) so the data-prep path is testable
    without the ``[ml-classifier]`` extra installed.

    Fields:
        x: One feature row per training example, each a list of floats
            in :data:`cognition_wobble.features.FEATURE_NAMES` order.
        y: Integer class label per row; the index into
            :data:`cognition_wobble.classifier_gbm._GBM_CLASS_ORDER`.
        feature_names: The pinned feature order (echoed for provenance).
        class_order: The class-index → :class:`CognitionDisagreementType`
            mapping (string values) the labels in ``y`` reference.
        n_dropped_unclassified: Gold-UNCLASSIFIED rows excluded from
            training (the GBM never learns UNCLASSIFIED — it is the
            inference-time threshold escape valve).
    """

    x: list[list[float]]
    y: list[int]
    feature_names: tuple[str, ...]
    class_order: tuple[str, ...]
    n_dropped_unclassified: int


@dataclass(frozen=True)
class TrainResult:
    """Outcome of :func:`train_gbm`.

    Fields:
        booster: The trained lightgbm ``Booster`` (in-memory).
        model_path: Where the native-text artifact was written (or
            ``None`` when ``write=False``).
        meta_path: Where the sidecar metadata JSON was written (or
            ``None``).
        metadata: The metadata dict that was (or would be) serialized.
        n_train: Number of graded examples actually trained on.
        class_counts: Per-class training-row counts (graded classes).
    """

    booster: Any
    model_path: Path | None
    meta_path: Path | None
    metadata: dict[str, Any]
    n_train: int
    class_counts: dict[CognitionDisagreementType, int] = field(default_factory=dict)


def build_training_matrix(examples: list[CalibrationExample]) -> TrainingData:
    """Turn calibration examples into a numeric (X, y) training matrix.

    Drops gold-UNCLASSIFIED rows (UNCLASSIFIED is not a trained class —
    see module docstring). Raises :class:`ValueError` when no graded
    examples remain, since lightgbm cannot train on an empty matrix.

    This function is dependency-free (no lightgbm); it is the unit the
    contract tests exercise to prove feature ordering + label mapping
    stay aligned with :data:`_GBM_CLASS_ORDER` and :data:`FEATURE_NAMES`.
    """
    class_to_index = {cls: i for i, cls in enumerate(_GBM_CLASS_ORDER)}

    x: list[list[float]] = []
    y: list[int] = []
    n_dropped = 0
    for ex in examples:
        if ex.gold_class not in GRADED_CLASSES:
            # UNCLASSIFIED (or any non-graded) gold row: not a training target.
            n_dropped += 1
            continue
        feats = extract_features(ex.prompt, ex.response_a, ex.response_b)
        x.append([float(feats[name]) for name in FEATURE_NAMES])
        y.append(class_to_index[ex.gold_class])

    if not x:
        raise ValueError(
            "no graded training examples after dropping UNCLASSIFIED rows; "
            "a corpus needs examples in the six graded classes to train."
        )

    return TrainingData(
        x=x,
        y=y,
        feature_names=FEATURE_NAMES,
        class_order=tuple(c.value for c in _GBM_CLASS_ORDER),
        n_dropped_unclassified=n_dropped,
    )


def _balanced_sample_weights(y: list[int], num_class: int) -> list[float]:
    """Inverse-frequency sample weights (sklearn 'balanced' convention).

    ``weight_c = n_samples / (n_classes_present * count_c)``. Classes
    absent from ``y`` contribute no rows, so only present classes are
    counted in the denominator's ``n_classes_present``.
    """
    counts = [0] * num_class
    for label in y:
        counts[label] += 1
    n_present = sum(1 for c in counts if c > 0)
    n_samples = len(y)
    per_class_weight = [
        (n_samples / (n_present * counts[c])) if counts[c] > 0 else 0.0
        for c in range(num_class)
    ]
    return [per_class_weight[label] for label in y]


def train_gbm(
    examples: list[CalibrationExample],
    *,
    model_path: str | Path | None = None,
    write: bool = True,
    params: dict[str, Any] | None = None,
    num_boost_round: int = DEFAULT_NUM_BOOST_ROUND,
    class_weight: str | None = None,
    version: str = "gbm-v1.0.0",
    trained_at: str | None = None,
) -> TrainResult:
    """Train a lightgbm multiclass GBM on ``examples`` and (optionally) save it.

    Args:
        examples: Labeled calibration examples. Gold-UNCLASSIFIED rows
            are dropped (UNCLASSIFIED is the inference threshold escape
            valve, never a trained class).
        model_path: Output path for the native-text ``.txt`` artifact.
            Required when ``write=True``. The sidecar metadata is written
            beside it as ``<model_path>.meta.json``.
        write: When ``False``, train in-memory only (tests / dry runs);
            ``model_path`` may be ``None``.
        params: lightgbm params overriding :data:`DEFAULT_GBM_PARAMS`.
            ``objective`` and ``num_class`` are always forced so the
            output vector index-aligns with :data:`_GBM_CLASS_ORDER`.
        num_boost_round: Boosting iterations.
        class_weight: ``"balanced"`` to apply inverse-frequency sample
            weights; ``None`` (default) trains on the raw distribution.
        version: Classifier version string stamped into the metadata
            (the value :attr:`GBMClassifier.version` should report once
            this artifact ships).
        trained_at: Optional ISO-8601 timestamp recorded in metadata.
            Passed in (not generated) so callers control provenance and
            so training stays deterministic/testable.

    Returns:
        A :class:`TrainResult` carrying the in-memory booster, the paths
        written, and the metadata.

    Raises:
        MissingOptionalDependency: ``lightgbm`` is not installed.
        ValueError: No graded examples to train on, or ``write=True``
            without a ``model_path``.
    """
    try:
        import lightgbm as lgb
        import numpy as np
    except ImportError as exc:
        raise MissingOptionalDependency(
            "lightgbm not installed; install via "
            "`pip install phoenix-middleware[ml-classifier]` to train the "
            "cognition classifier."
        ) from exc

    if write and model_path is None:
        raise ValueError("model_path is required when write=True")

    num_class = len(_GBM_CLASS_ORDER)
    data = build_training_matrix(examples)

    merged_params = dict(DEFAULT_GBM_PARAMS)
    if params:
        merged_params.update(params)
    # Force the contract-critical params regardless of caller overrides.
    merged_params["objective"] = "multiclass"
    merged_params["num_class"] = num_class

    weights = (
        _balanced_sample_weights(data.y, num_class)
        if class_weight == "balanced"
        else None
    )

    # lightgbm's Dataset requires an ndarray (a plain list[list[float]] is
    # rejected in lightgbm >= 4); build_training_matrix stays dependency-free,
    # so we coerce here where numpy is guaranteed present.
    dataset = lgb.Dataset(
        np.asarray(data.x, dtype=np.float64),
        label=np.asarray(data.y, dtype=np.int32),
        weight=np.asarray(weights, dtype=np.float64) if weights is not None else None,
        feature_name=list(FEATURE_NAMES),
        free_raw_data=False,
    )
    booster = lgb.train(
        merged_params,
        dataset,
        num_boost_round=num_boost_round,
    )

    class_counts = {
        cls: cnt
        for cls, cnt in count_by_class(examples).items()
        if cls in GRADED_CLASSES
    }

    metadata: dict[str, Any] = {
        "classifier_version": version,
        "feature_names": list(FEATURE_NAMES),
        "class_order": list(data.class_order),
        "num_class": num_class,
        "n_train_examples": len(data.y),
        "n_dropped_unclassified": data.n_dropped_unclassified,
        "class_counts": {cls.value: cnt for cls, cnt in class_counts.items()},
        "lightgbm_version": getattr(lgb, "__version__", "unknown"),
        "num_boost_round": num_boost_round,
        "class_weight": class_weight,
        "params": merged_params,
        "trained_at": trained_at,
    }

    written_model_path: Path | None = None
    written_meta_path: Path | None = None
    if write:
        assert model_path is not None  # guarded above
        out = Path(model_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        booster.save_model(str(out))
        meta_out = out.with_suffix(out.suffix + ".meta.json")
        meta_out.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
        written_model_path = out
        written_meta_path = meta_out

    return TrainResult(
        booster=booster,
        model_path=written_model_path,
        meta_path=written_meta_path,
        metadata=metadata,
        n_train=len(data.y),
        class_counts=class_counts,
    )
