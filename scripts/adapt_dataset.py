#!/usr/bin/env python
"""Adapt a FELM / SAC3 source dataset into Step 5c corpus JSONL.

Reads a JSONL file of source records (one record per line, in the dataset's
native schema), runs the matching adapter, and writes corpus pairs for the two
factual classes. See ``cognition_wobble.datasets`` for the per-dataset mapping
and ``docs/planning/STEP5C_CORPUS_PLAN.md`` for the sourcing plan.

- ``--dataset felm``: FELM records with a verified error -> deterministic,
  pre-labeled FACTUAL_DISAGREEMENT pairs (original vs corrected). No model calls.
- ``--dataset sac3``: SAC3 method output -> candidate FACTUAL_AGREEMENT /
  FACTUAL_DISAGREEMENT pairs from the consistency votes (re-judge / verify).

Examples::

    python scripts/adapt_dataset.py --dataset felm \
        --in felm_wk.jsonl --out felm_pairs.jsonl
    python scripts/adapt_dataset.py --dataset sac3 \
        --in sac3_out.jsonl --out sac3_pairs.jsonl --no-prelabel

Exit codes: 0 = ok; 2 = usage/load error / nothing emitted.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cognition_wobble.corpus import count_by_class, write_corpus  # noqa: E402
from cognition_wobble.datasets import AdapterReport, from_felm, from_sac3  # noqa: E402
from cognition_wobble.disagreement_types import GRADED_CLASSES  # noqa: E402


def _load_records(path: Path) -> list[dict[str, Any]]:
    """Load JSONL source records, skipping blank/comment lines.

    Raises :class:`ValueError` (with the 1-based line number) on malformed JSON
    or a non-object line so the CLI can report it cleanly.
    """
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path} line {line_no}: invalid JSON: {exc.msg}") from exc
            if not isinstance(obj, dict):
                raise ValueError(f"{path} line {line_no}: each record must be a JSON object")
            records.append(obj)
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", required=True, choices=["felm", "sac3"], help="source dataset format"
    )
    parser.add_argument(
        "--in", dest="inp", type=Path, required=True, help="input JSONL of source records"
    )
    parser.add_argument("--out", type=Path, required=True, help="output corpus JSONL")
    parser.add_argument(
        "--source-tag", default=None, help="source_dataset tag (default: FELM/SAC3)"
    )
    parser.add_argument(
        "--no-prelabel",
        action="store_true",
        help="sac3 only: emit UNLABELED pairs instead of vote-labeled",
    )
    args = parser.parse_args(argv)

    if not args.inp.exists():
        print(f"error: input not found: {args.inp}", file=sys.stderr)
        return 2
    try:
        records = _load_records(args.inp)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not records:
        print(f"error: no records in {args.inp}", file=sys.stderr)
        return 2

    if args.dataset == "felm":
        report: AdapterReport = from_felm(records, source_tag=args.source_tag or "FELM")
    else:
        report = from_sac3(
            records,
            source_tag=args.source_tag or "SAC3",
            prelabel_from_votes=not args.no_prelabel,
        )

    if not report.examples:
        print(
            f"error: adapted 0 pairs from {report.n_input} records ({report.n_skipped} skipped).",
            file=sys.stderr,
        )
        for reason in report.skip_reasons[:10]:
            print(f"  {reason}", file=sys.stderr)
        return 2

    header = (
        f"{args.dataset.upper()}-adapted corpus pairs "
        f"({report.n_emitted} from {report.n_input} records)."
    )
    try:
        write_corpus(args.out, report.examples, header_lines=(header,))
    except OSError as exc:
        print(f"error: could not write {args.out}: {exc}", file=sys.stderr)
        return 2

    counts = count_by_class(report.examples)
    print(f"adapted {report.n_emitted} pairs from {report.n_input} records -> {args.out}")
    for cls in GRADED_CLASSES:
        if counts[cls]:
            print(f"  {cls.value:<26} {counts[cls]}")
    if report.skip_reasons:
        print(f"skipped {report.n_skipped} records (first few):")
        for reason in report.skip_reasons[:5]:
            print(f"  {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
