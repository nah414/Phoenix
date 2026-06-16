#!/usr/bin/env python
"""LLM-judge labeling of generated cognition pairs (Step 5c, annotation path B).

Reads UNLABELED pairs (from ``scripts/generate_cognition_pairs.py``), runs each
through the existing LLM-as-judge classifier to propose a ``gold_class``, and
writes a labeled corpus JSONL plus a verify report. Rows the judge is least
reliable on (the four hard classes, abstentions, low confidence) are flagged
``NEEDS-VERIFY`` in ``annotation_notes`` for your human pass.

**Needs an API key** for the judge provider (a cheap, fast model is ideal, e.g.
Haiku / GPT-4o-mini). Set the matching env var.

Example::

    python scripts/label_cognition_pairs.py \
        --pairs pairs_refusal.jsonl \
        --judge-provider anthropic:claude-haiku-4-5-20251001 \
        --out labeled_refusal.jsonl

Exit codes: 0 = ok; 2 = usage/load error; 3 = missing optional dependency.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cognition_wobble.annotation import (  # noqa: E402
    label_pairs,
    labeled_examples,
    verify_summary,
)
from cognition_wobble.corpus import CorpusValidationError, load_corpus, write_corpus  # noqa: E402
from cognition_wobble.provider_factory import build_provider  # noqa: E402
from phoenix.providers.cognition.errors import (  # noqa: E402
    CognitionError,
    MissingOptionalDependency,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pairs",
        type=Path,
        required=True,
        help="input UNLABELED pairs JSONL (from generate_cognition_pairs.py)",
    )
    parser.add_argument(
        "--judge-provider",
        required=True,
        help="'kind:model' for the judge LLM (e.g. anthropic:claude-haiku-...)",
    )
    parser.add_argument("--out", type=Path, required=True, help="output labeled corpus JSONL")
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=0.5,
        help="judge abstains to UNCLASSIFIED below this (default 0.5)",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.5,
        help="flag NEEDS-VERIFY below this judge confidence (default 0.5)",
    )
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--temperature", type=float, default=0.0)
    args = parser.parse_args(argv)

    try:
        examples = load_corpus(args.pairs)
    except (FileNotFoundError, CorpusValidationError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # Import the judge lazily so corpus errors above don't require the SDK; keep
    # the import inside the try so an import-time failure shares the exit-3 path.
    try:
        from cognition_wobble.classifier_llm_judge import LLMJudgeClassifier

        provider = build_provider(args.judge_provider)
        judge = LLMJudgeClassifier(
            provider=provider, max_tokens=args.max_tokens, temperature=args.temperature
        )
    except MissingOptionalDependency as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except (CognitionError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    labeled = label_pairs(
        examples,
        judge,
        confidence_threshold=args.confidence_threshold,
        min_confidence=args.min_confidence,
    )

    header = (
        "Judge-labeled cognition pairs. Human-verify rows marked "
        "NEEDS-VERIFY before committing to the corpus."
    )
    try:
        write_corpus(args.out, labeled_examples(labeled), header_lines=(header,))
    except OSError as exc:
        print(f"error: could not write {args.out}: {exc}", file=sys.stderr)
        return 2

    summary = verify_summary(labeled)
    print(f"labeled {summary['total']} pairs -> {args.out}")
    print(f"  needs human verify: {summary['needs_verify']}")
    for key, val in sorted(summary.items()):
        if key not in ("total", "needs_verify"):
            print(f"  {key:<26} {val}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
