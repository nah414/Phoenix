#!/usr/bin/env python
"""Generate candidate cognition pairs for the under-represented classes (Step 5c).

Runs >= 2 cognition providers (or one provider under two configs) on seed prompts
to produce candidate ``(prompt, response_a, response_b)`` pairs for the four
classes the source datasets don't cover. Emits UNLABELED pairs (placeholder
``gold_class``) in the corpus JSONL schema; label them next with
``scripts/label_cognition_pairs.py``.

**Needs API keys.** Set the env vars for the providers you use
(``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` / ``GOOGLE_API_KEY``).

Examples::

    # REFUSAL (needs 2 providers; disagreement on refusal is the signal):
    python scripts/generate_cognition_pairs.py --class refusal \
        --providers anthropic:claude-sonnet-4-7-20260418,openai:gpt-4o \
        --out pairs_refusal.jsonl

    # STYLISTIC (one provider, two registers):
    python scripts/generate_cognition_pairs.py --class stylistic \
        --providers anthropic:claude-haiku-4-5-20251001 \
        --register-a "Answer in one casual sentence, like texting a friend." \
        --register-b "Answer as a formal, detailed encyclopedia entry." \
        --out pairs_stylistic.jsonl

Default ``--prompts`` is the committed seed set for the chosen class under
``vendor/cognition_wobble/calibration/prompt_seeds/``.

Exit codes: 0 = ok; 2 = usage/load error; 3 = missing optional dependency.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cognition_wobble.corpus import write_corpus  # noqa: E402
from cognition_wobble.generation import (  # noqa: E402
    generate_pairs,
    interpretive_specs,
    refusal_specs,
    stylistic_specs,
    tool_choice_specs,
)
from cognition_wobble.provider_factory import build_provider  # noqa: E402
from phoenix.providers.cognition.errors import (  # noqa: E402
    CognitionError,
    MissingOptionalDependency,
)
from phoenix.providers.cognition.types import Tool  # noqa: E402

if TYPE_CHECKING:
    from cognition_wobble.generation import PairSpec

_SEED_DIR = _REPO_ROOT / "vendor" / "cognition_wobble" / "calibration" / "prompt_seeds"

# Generic tools exposed to the tool-eligible variant for TOOL_CHOICE generation.
_DEFAULT_TOOLS: list[Tool] = [
    Tool(
        name="web_search",
        description="Search the web for current information.",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    ),
    Tool(
        name="get_weather",
        description="Get the current weather for a location.",
        input_schema={
            "type": "object",
            "properties": {"location": {"type": "string"}},
            "required": ["location"],
        },
    ),
    Tool(
        name="calculator",
        description="Evaluate an arithmetic expression.",
        input_schema={
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
    ),
]

_DEFAULT_REGISTER_A = "Answer in one casual sentence, like texting a friend."
_DEFAULT_REGISTER_B = "Answer as a formal, detailed encyclopedia entry."


def _load_seeds(path: Path, limit: int | None) -> list[tuple[str, str]]:
    """Load ``{"prompt", "notes"}`` seed lines, skipping blanks/comments.

    Raises :class:`ValueError` (with the 1-based line number) on malformed JSON
    or a missing ``prompt`` field, so the CLI can report it cleanly instead of
    crashing with a raw traceback.
    """
    seeds: list[tuple[str, str]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path} line {line_no}: invalid JSON: {exc.msg}") from exc
            if not isinstance(obj, dict) or "prompt" not in obj:
                raise ValueError(f"{path} line {line_no}: each seed needs a 'prompt' field")
            seeds.append((str(obj["prompt"]), str(obj.get("notes", ""))))
    return seeds[:limit] if limit is not None else seeds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--class",
        dest="cls",
        required=True,
        choices=["refusal", "tool_choice", "interpretive", "stylistic"],
        help="which under-represented class to target",
    )
    parser.add_argument(
        "--providers",
        required=True,
        help="comma-separated 'kind:model' specs (e.g. anthropic:claude-...,openai:gpt-4o)",
    )
    parser.add_argument(
        "--prompts",
        type=Path,
        default=None,
        help="seed prompts JSONL (default: the committed seed set for --class)",
    )
    parser.add_argument("--out", type=Path, required=True, help="output unlabeled pairs JSONL")
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--limit", type=int, default=None, help="cap number of seed prompts")
    parser.add_argument("--register-a", default=_DEFAULT_REGISTER_A, help="stylistic register A")
    parser.add_argument("--register-b", default=_DEFAULT_REGISTER_B, help="stylistic register B")
    args = parser.parse_args(argv)

    prompts_path = args.prompts or (_SEED_DIR / f"{args.cls}.jsonl")
    if not prompts_path.exists():
        print(f"error: seed prompts not found: {prompts_path}", file=sys.stderr)
        return 2
    try:
        seeds = _load_seeds(prompts_path, args.limit)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not seeds:
        print(f"error: no seed prompts in {prompts_path}", file=sys.stderr)
        return 2

    provider_specs = [s for s in args.providers.split(",") if s.strip()]
    needs_two = args.cls in ("refusal", "interpretive")
    if needs_two and len(provider_specs) < 2:
        print(
            f"error: --class {args.cls} needs >= 2 providers (got {len(provider_specs)})",
            file=sys.stderr,
        )
        return 2

    try:
        providers = [build_provider(s) for s in provider_specs]
    except MissingOptionalDependency as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except (CognitionError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    specs: list[PairSpec]
    if args.cls == "refusal":
        specs = refusal_specs(seeds)
    elif args.cls == "interpretive":
        specs = interpretive_specs(seeds)
    elif args.cls == "tool_choice":
        specs = tool_choice_specs(seeds, tools=_DEFAULT_TOOLS)
    else:  # stylistic
        specs = stylistic_specs(seeds, register_a=args.register_a, register_b=args.register_b)

    report = generate_pairs(
        providers, specs, max_tokens=args.max_tokens, temperature=args.temperature
    )

    # All specs failed -> produce nothing and signal failure (don't write an
    # empty file and exit 0, which automation can't distinguish from success).
    if not report.pairs and report.n_attempted > 0:
        print(
            f"error: all {report.n_attempted} specs skipped; no pairs generated.", file=sys.stderr
        )
        for reason in report.skip_reasons[:10]:
            print(f"  {reason}", file=sys.stderr)
        return 2

    header = (
        f"UNLABELED cognition pairs (target={args.cls}). "
        f"Label with scripts/label_cognition_pairs.py."
    )
    try:
        write_corpus(args.out, report.pairs, header_lines=(header,))
    except OSError as exc:
        print(f"error: could not write {args.out}: {exc}", file=sys.stderr)
        return 2

    print(f"generated {len(report.pairs)} pairs (target={args.cls}) -> {args.out}")
    if report.skip_reasons:
        print(f"skipped {report.n_skipped} (provider errors):")
        for reason in report.skip_reasons[:10]:
            print(f"  {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
