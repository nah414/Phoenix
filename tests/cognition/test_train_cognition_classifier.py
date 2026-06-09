"""Training + end-to-end (load -> train -> eval) tests for Step 5c.

These require the ``[ml-classifier]`` extra (lightgbm); the whole module
is skipped when it is not installed. The dependency-free parts of the
pipeline (corpus loader, training-matrix builder, gate, report) are
covered in ``test_corpus_loader.py`` and ``test_classifier_acceptance.py``.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest

pytest.importorskip(
    "lightgbm", reason="cognition classifier training needs the [ml-classifier] extra"
)

from cognition_wobble.acceptance import check_gate
from cognition_wobble.calibration.synthetic import generate_synthetic_corpus
from cognition_wobble.classifier_gbm import _GBM_CLASS_ORDER, GBMClassifier
from cognition_wobble.corpus import load_corpus
from cognition_wobble.disagreement_types import GRADED_CLASSES, CognitionDisagreementType
from cognition_wobble.eval import CalibrationExample, evaluate
from cognition_wobble.features import FEATURE_NAMES
from cognition_wobble.training import train_gbm

_FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_corpus.jsonl"


def _stratified_split(
    examples: list[CalibrationExample], *, frac_train: float = 0.7
) -> tuple[list[CalibrationExample], list[CalibrationExample]]:
    """Deterministic per-class split (first ``frac_train`` -> train)."""
    by_class: dict[CognitionDisagreementType, list[CalibrationExample]] = defaultdict(list)
    for ex in examples:
        by_class[ex.gold_class].append(ex)
    train: list[CalibrationExample] = []
    test: list[CalibrationExample] = []
    for rows in by_class.values():
        cut = max(1, int(len(rows) * frac_train))
        train.extend(rows[:cut])
        test.extend(rows[cut:])
    return train, test


def test_train_gbm_writes_artifact_and_metadata(tmp_path: Path) -> None:
    examples = generate_synthetic_corpus(n_per_class=10)
    out = tmp_path / "gbm.txt"
    result = train_gbm(
        examples, model_path=out, version="gbm-test-v1", trained_at="2026-06-09T00:00:00Z"
    )

    assert out.exists()
    assert result.model_path == out
    assert result.meta_path == out.with_suffix(".txt.meta.json")
    assert result.n_train == 60  # 6 graded * 10

    meta = json.loads(result.meta_path.read_text(encoding="utf-8"))
    assert meta["classifier_version"] == "gbm-test-v1"
    assert meta["feature_names"] == list(FEATURE_NAMES)
    assert meta["class_order"] == [c.value for c in _GBM_CLASS_ORDER]
    assert meta["num_class"] == len(_GBM_CLASS_ORDER)
    assert meta["trained_at"] == "2026-06-09T00:00:00Z"


def test_train_gbm_no_write_returns_booster_only() -> None:
    examples = generate_synthetic_corpus(n_per_class=6)
    result = train_gbm(examples, write=False)
    assert result.booster is not None
    assert result.model_path is None
    assert result.meta_path is None


def test_saved_model_loads_via_gbmclassifier(tmp_path: Path) -> None:
    """The artifact round-trips through the shipped GBMClassifier loader."""
    examples = generate_synthetic_corpus(n_per_class=10)
    out = tmp_path / "gbm.txt"
    train_gbm(examples, model_path=out)

    # Load purely from disk via the production loader path.
    clf = GBMClassifier(model_path=out)
    ex = examples[0]
    res = clf.classify(ex.prompt, [ex.response_a, ex.response_b])
    assert res.disagreement_type in set(_GBM_CLASS_ORDER) | {CognitionDisagreementType.UNCLASSIFIED}
    assert set(FEATURE_NAMES) <= set(res.feature_values)


def test_end_to_end_load_train_eval_clears_gate(tmp_path: Path) -> None:
    """Full pipeline on the committed fixture clears the macro-F1 gate.

    The synthetic classes are designed separable, so a held-out split
    should sit well above 0.70 — this proves the harness wiring, not
    real-world accuracy.
    """
    examples = load_corpus(_FIXTURE)
    train, test = _stratified_split(examples)

    out = tmp_path / "gbm_e2e.txt"
    train_gbm(train, model_path=out, version="gbm-e2e-v1")

    clf = GBMClassifier(model_path=out)
    report = evaluate(clf, test)
    gate = check_gate(report)

    assert gate.passed, f"held-out macro-F1 {report.macro_f1:.3f} should clear 0.70"
    # every graded class has held-out support in this split
    assert all(report.per_class[c].support > 0 for c in GRADED_CLASSES)


def test_in_sample_fit_is_high(tmp_path: Path) -> None:
    examples = generate_synthetic_corpus(n_per_class=20)
    result = train_gbm(examples, write=False)
    clf = GBMClassifier(booster=result.booster)
    report = evaluate(clf, examples)
    # Separable synthetic data -> near-perfect in-sample fit.
    assert report.macro_f1 >= 0.70
