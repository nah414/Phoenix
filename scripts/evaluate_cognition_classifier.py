#!/usr/bin/env python
"""Evaluate a cognition classifier against the macro-F1 gate (Step 5c).

Loads a labeled JSONL corpus, runs the chosen classifier over it, prints
the per-class + macro report, and exits non-zero when the macro-F1
acceptance gate (``>= 0.70`` across the six graded classes) is not met.
This is the harness that decides whether a trained artifact is allowed
to replace the shipped ``AlwaysUnclassifiedClassifier`` default.

Classifier selection:

- ``--model PATH``  -> load a :class:`GBMClassifier` from the artifact at
  PATH (requires the ``[ml-classifier]`` extra).
- ``--stub``        -> evaluate the ``AlwaysUnclassifiedClassifier``
  (always fails the gate; useful as a baseline / wiring check).

Example::

    python scripts/evaluate_cognition_classifier.py \
        --corpus path/to/eval_corpus.jsonl \
        --model vendor/cognition_wobble/models/gbm_classifier_v1.txt

Exit codes: 0 = gate PASS, 1 = gate FAIL, 2 = usage/load error,
3 = missing optional dependency.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cognition_wobble.acceptance import (  # noqa: E402
    ACCEPTANCE_MACRO_F1,
    check_gate,
    format_report,
)
from cognition_wobble.corpus import load_corpus  # noqa: E402
from cognition_wobble.eval import evaluate  # noqa: E402
from phoenix.providers.cognition.errors import MissingOptionalDependency  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus", type=Path, required=True, help="path to the labeled JSONL corpus"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--model", type=Path, help="path to a trained lightgbm .txt artifact")
    group.add_argument(
        "--stub", action="store_true", help="evaluate the AlwaysUnclassifiedClassifier baseline"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=ACCEPTANCE_MACRO_F1,
        help=f"macro-F1 gate (default: {ACCEPTANCE_MACRO_F1})",
    )
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=None,
        help="override the classifier's UNCLASSIFIED confidence cutoff",
    )
    args = parser.parse_args(argv)

    try:
        examples = load_corpus(args.corpus)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.stub:
        from cognition_wobble.classifier import AlwaysUnclassifiedClassifier

        classifier = AlwaysUnclassifiedClassifier()
    else:
        from cognition_wobble.classifier_gbm import GBMClassifier

        try:
            classifier = GBMClassifier(model_path=args.model)
        except MissingOptionalDependency as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 3
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    eval_kwargs = {}
    if args.confidence_threshold is not None:
        eval_kwargs["confidence_threshold"] = args.confidence_threshold
    report = evaluate(classifier, examples, **eval_kwargs)

    print(format_report(report, threshold=args.threshold))
    gate = check_gate(report, threshold=args.threshold)
    return 0 if gate.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
