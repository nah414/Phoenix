#!/usr/bin/env python
"""Audit a labeled cognition corpus's class balance + feature separability (Step 5c).

Implements step 2 of the Step 5c iterate-to-gate loop
(`docs/planning/STEP5C_CORPUS_PLAN.md`): **audit the distribution before training.**
The roadmap is explicit that class imbalance (not features) is the first thing to fix, so
this tool reports per-class counts against the ~28-per-graded-class floor, flags
under-represented / empty classes, surfaces exact-duplicate pairs, and prints per-class
feature centroids (so you can eyeball whether the six features actually separate the
classes before spending a training run).

Dependency-free: uses only the corpus loader + feature extractors (no lightgbm). The
semantic-distance feature falls back to binary when the embedding model isn't vendored;
that's expected and noted in the output.

Usage::

    python scripts/corpus_stats.py --corpus path/to/corpus.jsonl
    python scripts/corpus_stats.py --corpus path/to/corpus.jsonl --min 28 --strict

Exit codes: 0 = every graded class meets the floor; 1 = at least one graded class is under
the floor (only when ``--strict``; otherwise always 0); 2 = load error.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cognition_wobble.corpus import count_by_class, load_corpus  # noqa: E402
from cognition_wobble.disagreement_types import (  # noqa: E402
    GRADED_CLASSES,
    CognitionDisagreementType,
)
from cognition_wobble.embeddings.loader import is_embedding_model_available  # noqa: E402
from cognition_wobble.eval import CalibrationExample  # noqa: E402
from cognition_wobble.features import FEATURE_NAMES, extract_features  # noqa: E402
from phoenix.providers.cognition.types import CognitionResult  # noqa: E402

# Build-guide floor: ">= 200 paired examples spanning all seven taxonomy classes
# (~28 per class minimum)".
DEFAULT_MIN_PER_GRADED_CLASS = 28

# Stable display order (matches the GBM class order).
_DISPLAY_ORDER: tuple[CognitionDisagreementType, ...] = (
    CognitionDisagreementType.FACTUAL_AGREEMENT,
    CognitionDisagreementType.STYLISTIC_DIVERGENCE,
    CognitionDisagreementType.FACTUAL_DISAGREEMENT,
    CognitionDisagreementType.INTERPRETIVE_DIVERGENCE,
    CognitionDisagreementType.REFUSAL_DIVERGENCE,
    CognitionDisagreementType.TOOL_CHOICE_DIVERGENCE,
)


def _dup_key(example: CalibrationExample) -> tuple[object, ...]:
    """Identity for exact-duplicate detection (prompt + both response texts + tools)."""

    def _tools(r: CognitionResult) -> tuple[str, ...]:
        return tuple(sorted(tc.name for tc in r.tool_calls))

    prompt_text = "".join(str(m.get("content", "")) for m in example.prompt.messages)
    return (
        prompt_text,
        example.response_a.text,
        _tools(example.response_a),
        example.response_b.text,
        _tools(example.response_b),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus", type=Path, required=True, help="path to the labeled JSONL corpus"
    )
    parser.add_argument(
        "--min",
        type=int,
        default=DEFAULT_MIN_PER_GRADED_CLASS,
        help=f"per-graded-class floor (default: {DEFAULT_MIN_PER_GRADED_CLASS})",
    )
    parser.add_argument(
        "--strict", action="store_true", help="exit 1 if any graded class is under the floor"
    )
    parser.add_argument(
        "--no-features",
        action="store_true",
        help="skip the per-class feature-centroid table (faster)",
    )
    args = parser.parse_args(argv)

    try:
        examples = load_corpus(args.corpus)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    counts = count_by_class(examples)
    graded_total = sum(counts[c] for c in GRADED_CLASSES)
    unclassified = counts[CognitionDisagreementType.UNCLASSIFIED]

    print(f"Corpus audit: {args.corpus}")
    print(
        f"  total examples : {len(examples)} ({graded_total} graded, {unclassified} unclassified)"
    )
    print(f"  graded floor   : {args.min} per class")
    print()

    print(f"{'class':<26}{'count':>7}  status")
    print("-" * 48)
    under = []
    for cls in _DISPLAY_ORDER:
        n = counts[cls]
        if n == 0:
            status = "EMPTY"
            under.append(cls)
        elif n < args.min:
            status = f"UNDER (need +{args.min - n})"
            under.append(cls)
        else:
            status = "ok"
        print(f"{cls.value:<26}{n:>7}  {status}")
    print(f"{'unclassified':<26}{unclassified:>7}  (not graded)")
    print()

    # Exact-duplicate detection.
    seen: dict[tuple[object, ...], int] = {}
    for ex in examples:
        k = _dup_key(ex)
        seen[k] = seen.get(k, 0) + 1
    dup_pairs = sum(v - 1 for v in seen.values() if v > 1)
    if dup_pairs:
        print(
            f"! exact-duplicate rows: {dup_pairs} "
            f"({sum(1 for v in seen.values() if v > 1)} distinct pairs repeated)"
        )
        print()

    if not args.no_features:
        avail = is_embedding_model_available()
        note = "" if avail else "  (semantic_distance is BINARY - embedding model not vendored)"
        print(f"per-class feature centroids{note}")
        print(f"  features: {', '.join(n[:10] for n in FEATURE_NAMES)}")
        print("-" * 48)
        by_class: dict[CognitionDisagreementType, list[list[float]]] = {
            c: [] for c in _DISPLAY_ORDER
        }
        for ex in examples:
            if ex.gold_class in by_class:
                feats = extract_features(ex.prompt, ex.response_a, ex.response_b)
                by_class[ex.gold_class].append([feats[name] for name in FEATURE_NAMES])
        for cls in _DISPLAY_ORDER:
            rows = by_class[cls]
            if not rows:
                print(f"{cls.value:<26} (no examples)")
                continue
            centroid = [
                round(statistics.fmean(r[i] for r in rows), 3) for i in range(len(FEATURE_NAMES))
            ]
            print(f"{cls.value:<26} {centroid}")
        print()

    ready = not under and graded_total >= 200
    if under:
        names = ", ".join(c.value for c in under)
        print(f"VERDICT: NOT READY - under the floor: {names}")
    elif graded_total < 200:
        print(f"VERDICT: NOT READY - {graded_total}/200 graded examples (classes balanced).")
    else:
        print(f"VERDICT: READY - {graded_total} graded, all classes >= {args.min}.")

    return 1 if (args.strict and not ready) else 0


if __name__ == "__main__":
    raise SystemExit(main())
