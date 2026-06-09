#!/usr/bin/env python
"""Train the cognition disagreement classifier (Phase 13 Step 5c).

This is the script the ``GBMClassifier`` docstring promises. It loads a
labeled JSONL corpus (see ``cognition_wobble.corpus`` for the schema),
trains a lightgbm multiclass GBM over the six graded classes, writes the
native-text model artifact + a ``*.meta.json`` sidecar, and (unless
``--no-eval``) prints an in-sample calibration report so you can see the
training-set fit before running a held-out evaluation with
``scripts/evaluate_cognition_classifier.py``.

Requires the ``[ml-classifier]`` extra (lightgbm); without it the script
exits with a clear install hint.

Example::

    # Train on the synthetic fixture (smoke test of the pipeline):
    python scripts/train_cognition_classifier.py \
        --corpus tests/cognition/fixtures/synthetic_corpus.jsonl \
        --out /tmp/gbm_synth.txt

    # Train the real model for the swap (once Adam's corpus exists):
    python scripts/train_cognition_classifier.py \
        --corpus path/to/real_corpus.jsonl \
        --out vendor/cognition_wobble/models/gbm_classifier_v1.txt \
        --version gbm-v1.0.0 --class-weight balanced

The script never registers the classifier or mutates the shipped
default; it only produces an artifact. Wiring the artifact in is a
separate, deliberate step (see
``docs/.../STEP5C_classifier_training_harness.md``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cognition_wobble.corpus import count_by_class, load_corpus  # noqa: E402
from cognition_wobble.disagreement_types import GRADED_CLASSES  # noqa: E402
from phoenix.providers.cognition.errors import MissingOptionalDependency  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus", type=Path, required=True, help="path to the labeled JSONL corpus"
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="output path for the lightgbm .txt artifact"
    )
    parser.add_argument(
        "--version", default="gbm-v1.0.0", help="classifier version stamped into metadata"
    )
    parser.add_argument(
        "--class-weight",
        choices=["balanced"],
        default=None,
        help="apply inverse-frequency sample weights",
    )
    parser.add_argument("--num-boost-round", type=int, default=200)
    parser.add_argument(
        "--trained-at", default=None, help="ISO-8601 timestamp recorded in metadata"
    )
    parser.add_argument(
        "--no-eval", action="store_true", help="skip the in-sample calibration report"
    )
    args = parser.parse_args(argv)

    try:
        examples = load_corpus(args.corpus)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    counts = count_by_class(examples)
    graded_total = sum(n for c, n in counts.items() if c in GRADED_CLASSES)
    print(f"loaded {len(examples)} examples ({graded_total} graded) from {args.corpus}")
    for cls, n in counts.items():
        if cls in GRADED_CLASSES:
            flag = "  <-- under-represented" if 0 < n < 20 else ""
            empty = "  <-- EMPTY" if n == 0 else ""
            print(f"  {cls.value:<26} {n}{flag}{empty}")

    # Import the trainer lazily so the corpus-loading errors above don't
    # require lightgbm to be installed.
    from cognition_wobble.training import train_gbm

    try:
        result = train_gbm(
            examples,
            model_path=args.out,
            version=args.version,
            class_weight=args.class_weight,
            num_boost_round=args.num_boost_round,
            trained_at=args.trained_at,
        )
    except MissingOptionalDependency as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"\ntrained on {result.n_train} graded examples")
    print(f"model  -> {result.model_path}")
    print(f"meta   -> {result.meta_path}")

    if not args.no_eval:
        from cognition_wobble.acceptance import format_report
        from cognition_wobble.classifier_gbm import GBMClassifier
        from cognition_wobble.eval import evaluate

        clf = GBMClassifier(booster=result.booster)
        report = evaluate(clf, examples)
        print()
        print(format_report(report))
        print(
            "\nNOTE: the report above is IN-SAMPLE (evaluated on the training "
            "corpus). Run scripts/evaluate_cognition_classifier.py against a "
            "held-out split for the real acceptance decision."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
