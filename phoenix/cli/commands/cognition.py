"""``phoenix cognition ...`` command group (Phase 13 Step 5c operator console).

One discoverable front door over the cognition-corpus harness — the same
capabilities the ``scripts/`` tools expose, unified under ``phoenix cognition``:

- ``adapt``            -- FELM/SAC3 source dataset -> corpus JSONL pairs.
- ``generate``         -- run providers -> UNLABELED candidate pairs (needs keys).
- ``label``            -- LLM-judge label generated pairs (needs a judge key).
- ``audit``            -- corpus class-balance report (per-class vs the floor).
- ``train``            -- train the GBM classifier (needs the [ml-classifier] extra).
- ``evaluate``         -- evaluate a model/stub against a corpus + the gate.
- ``vendor-embeddings``-- vendor the all-MiniLM-L6-v2 model for the semantic feature.

These are **offline, file-based** operations (no daemon round-trip), so the
handlers ignore the REST ``client`` and call the shipped ``cognition_wobble``
library directly, lazy-importing it so the CLI starts fast when cognition isn't
in use. Output is a structured payload rendered per ``--format``
(``json`` | ``text`` | ``table``).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from phoenix.cli.commands._shared import print_payload
from phoenix.cli.config_loader import CLIConfig
from phoenix.cli.http_client import CLIHTTPClient

_EXIT_OK = 0
_EXIT_GATE_FAIL = 1
_EXIT_USAGE = 2
_EXIT_MISSING_EXTRA = 4


# ---------------------------------------------------------------------------
# adapt


def _cmd_adapt(
    args: argparse.Namespace, _config: CLIConfig, _client: CLIHTTPClient, fmt: str
) -> int:
    from cognition_wobble.corpus import count_by_class, write_corpus
    from cognition_wobble.datasets import from_felm, from_sac3
    from cognition_wobble.disagreement_types import GRADED_CLASSES
    from cognition_wobble.jsonl import read_jsonl_records

    if not args.inp.exists():
        print(f"error: input not found: {args.inp}")
        return _EXIT_USAGE
    try:
        records = read_jsonl_records(args.inp)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}")
        return _EXIT_USAGE
    if not records:
        print(f"error: no records in {args.inp}")
        return _EXIT_USAGE

    if args.dataset == "felm":
        report = from_felm(records, source_tag=args.source_tag or "FELM")
    else:
        report = from_sac3(
            records,
            source_tag=args.source_tag or "SAC3",
            prelabel_from_votes=not args.no_prelabel,
        )
    if not report.examples:
        print(f"error: adapted 0 pairs from {report.n_input} records ({report.n_skipped} skipped)")
        return _EXIT_USAGE

    try:
        write_corpus(args.out, report.examples, header_lines=(f"{args.dataset.upper()}-adapted",))
    except OSError as exc:
        print(f"error: could not write {args.out}: {exc}")
        return _EXIT_USAGE

    counts = count_by_class(report.examples)
    print_payload(
        {
            "out": str(args.out),
            "emitted": report.n_emitted,
            "input": report.n_input,
            "skipped": report.n_skipped,
            "per_class": {c.value: counts[c] for c in GRADED_CLASSES if counts[c]},
        },
        fmt,
    )
    return _EXIT_OK


# ---------------------------------------------------------------------------
# audit


def _cmd_audit(
    args: argparse.Namespace, _config: CLIConfig, _client: CLIHTTPClient, fmt: str
) -> int:
    from cognition_wobble.audit import audit_corpus
    from cognition_wobble.corpus import load_corpus

    try:
        examples = load_corpus(args.corpus)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}")
        return _EXIT_USAGE

    report = audit_corpus(examples, min_per_class=args.min)
    print_payload(report, fmt)
    return _EXIT_USAGE if (args.strict and not report["ready"]) else _EXIT_OK


# ---------------------------------------------------------------------------
# train


def _cmd_train(
    args: argparse.Namespace, _config: CLIConfig, _client: CLIHTTPClient, fmt: str
) -> int:
    from cognition_wobble.corpus import load_corpus
    from phoenix.providers.cognition.errors import MissingOptionalDependency

    try:
        examples = load_corpus(args.corpus)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}")
        return _EXIT_USAGE

    try:
        from cognition_wobble.training import train_gbm

        result = train_gbm(examples, model_path=args.out, version=args.version)
    except MissingOptionalDependency as exc:
        print(f"error: {exc}")
        return _EXIT_MISSING_EXTRA
    except ValueError as exc:
        print(f"error: {exc}")
        return _EXIT_USAGE

    print_payload(
        {
            "model": str(result.model_path),
            "meta": str(result.meta_path),
            "trained_examples": result.n_train,
            "version": args.version,
        },
        fmt,
    )
    return _EXIT_OK


# ---------------------------------------------------------------------------
# evaluate


def _cmd_evaluate(
    args: argparse.Namespace, _config: CLIConfig, _client: CLIHTTPClient, fmt: str
) -> int:
    from cognition_wobble.acceptance import check_gate
    from cognition_wobble.corpus import load_corpus
    from cognition_wobble.eval import evaluate
    from phoenix.providers.cognition.errors import MissingOptionalDependency

    try:
        examples = load_corpus(args.corpus)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}")
        return _EXIT_USAGE

    if args.stub:
        from cognition_wobble.classifier import AlwaysUnclassifiedClassifier

        classifier: Any = AlwaysUnclassifiedClassifier()
    else:
        from cognition_wobble.classifier_gbm import GBMClassifier

        try:
            classifier = GBMClassifier(model_path=args.model)
        except MissingOptionalDependency as exc:
            print(f"error: {exc}")
            return _EXIT_MISSING_EXTRA
        except FileNotFoundError as exc:
            print(f"error: {exc}")
            return _EXIT_USAGE

    report = evaluate(classifier, examples)
    gate = check_gate(report, threshold=args.threshold)
    payload: dict[str, Any] = {
        "macro_f1": report.macro_f1,
        "gate_passed": gate.passed,
        "threshold": args.threshold,
        "n_graded": report.n_graded_examples,
        "per_class": {
            cls.value: {
                "precision": m.precision,
                "recall": m.recall,
                "f1": m.f1,
                "support": m.support,
            }
            for cls, m in report.per_class.items()
        },
    }
    if args.confusion:
        payload["confusion"] = {
            f"{gold.value}->{pred.value}": n for (gold, pred), n in report.confusion_matrix.items()
        }
    print_payload(payload, fmt)
    return _EXIT_OK if gate.passed else _EXIT_GATE_FAIL


# ---------------------------------------------------------------------------
# generate  (needs provider API keys)


def _seed_dir() -> Path:
    import cognition_wobble.calibration as _cal

    return Path(_cal.__file__).resolve().parent / "prompt_seeds"


def _load_seeds(path: Path) -> list[tuple[str, str]]:
    from cognition_wobble.jsonl import read_jsonl_records

    seeds: list[tuple[str, str]] = []
    for rec in read_jsonl_records(path):
        if "prompt" not in rec:
            raise ValueError(f"{path}: seed record missing 'prompt'")
        seeds.append((str(rec["prompt"]), str(rec.get("notes", ""))))
    return seeds


def _cmd_generate(
    args: argparse.Namespace, _config: CLIConfig, _client: CLIHTTPClient, fmt: str
) -> int:
    from cognition_wobble.corpus import count_by_class, write_corpus
    from cognition_wobble.disagreement_types import GRADED_CLASSES
    from cognition_wobble.generation import (
        generate_pairs,
        interpretive_specs,
        refusal_specs,
        stylistic_specs,
        tool_choice_specs,
    )
    from cognition_wobble.provider_factory import build_provider
    from phoenix.providers.cognition.errors import CognitionError, MissingOptionalDependency
    from phoenix.providers.cognition.types import Tool

    prompts_path = args.prompts or (_seed_dir() / f"{args.cog_class}.jsonl")
    if not prompts_path.exists():
        print(f"error: seed prompts not found: {prompts_path}")
        return _EXIT_USAGE
    try:
        seeds = _load_seeds(prompts_path)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}")
        return _EXIT_USAGE
    if not seeds:
        print(f"error: no seed prompts in {prompts_path}")
        return _EXIT_USAGE

    specs_in = [s.strip() for s in args.providers.split(",") if s.strip()]
    if args.cog_class in ("refusal", "interpretive") and len(specs_in) < 2:
        print(f"error: --class {args.cog_class} needs >= 2 providers (got {len(specs_in)})")
        return _EXIT_USAGE
    try:
        providers = [build_provider(s) for s in specs_in]
    except MissingOptionalDependency as exc:
        print(f"error: {exc}")
        return _EXIT_MISSING_EXTRA
    except (CognitionError, ValueError) as exc:
        print(f"error: {exc}")
        return _EXIT_USAGE

    tools = [
        Tool(
            name="web_search",
            description="Search the web.",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        ),
        Tool(
            name="get_weather",
            description="Get current weather.",
            input_schema={"type": "object", "properties": {"location": {"type": "string"}}},
        ),
    ]
    if args.cog_class == "refusal":
        specs = refusal_specs(seeds)
    elif args.cog_class == "interpretive":
        specs = interpretive_specs(seeds)
    elif args.cog_class == "tool_choice":
        specs = tool_choice_specs(seeds, tools=tools)
    else:
        specs = stylistic_specs(seeds, register_a=args.register_a, register_b=args.register_b)

    report = generate_pairs(
        providers, specs, max_tokens=args.max_tokens, temperature=args.temperature
    )
    if not report.pairs:
        print(f"error: all {report.n_attempted} specs skipped; no pairs generated")
        return _EXIT_USAGE
    try:
        write_corpus(args.out, report.pairs, header_lines=(f"UNLABELED target={args.cog_class}",))
    except OSError as exc:
        print(f"error: could not write {args.out}: {exc}")
        return _EXIT_USAGE

    counts = count_by_class(report.pairs)
    print_payload(
        {
            "out": str(args.out),
            "generated": len(report.pairs),
            "skipped": report.n_skipped,
            "target_class": args.cog_class,
            "per_class": {c.value: counts[c] for c in GRADED_CLASSES if counts[c]},
        },
        fmt,
    )
    return _EXIT_OK


# ---------------------------------------------------------------------------
# label  (needs a judge provider key)


def _cmd_label(
    args: argparse.Namespace, _config: CLIConfig, _client: CLIHTTPClient, fmt: str
) -> int:
    from cognition_wobble.annotation import label_pairs, labeled_examples, verify_summary
    from cognition_wobble.corpus import CorpusValidationError, load_corpus, write_corpus
    from cognition_wobble.provider_factory import build_provider
    from phoenix.providers.cognition.errors import CognitionError, MissingOptionalDependency

    try:
        examples = load_corpus(args.pairs)
    except (FileNotFoundError, CorpusValidationError, ValueError) as exc:
        print(f"error: {exc}")
        return _EXIT_USAGE

    try:
        from cognition_wobble.classifier_llm_judge import LLMJudgeClassifier

        judge = LLMJudgeClassifier(provider=build_provider(args.judge_provider))
    except MissingOptionalDependency as exc:
        print(f"error: {exc}")
        return _EXIT_MISSING_EXTRA
    except (CognitionError, ValueError) as exc:
        print(f"error: {exc}")
        return _EXIT_USAGE

    labeled = label_pairs(examples, judge, min_confidence=args.min_confidence)
    try:
        write_corpus(
            args.out,
            labeled_examples(labeled),
            header_lines=("judge-labeled; verify NEEDS-VERIFY",),
        )
    except OSError as exc:
        print(f"error: could not write {args.out}: {exc}")
        return _EXIT_USAGE

    summary = verify_summary(labeled)
    print_payload({"out": str(args.out), **summary}, fmt)
    return _EXIT_OK


# ---------------------------------------------------------------------------
# vendor-embeddings


def _cmd_vendor_embeddings(
    _args: argparse.Namespace, _config: CLIConfig, _client: CLIHTTPClient, fmt: str
) -> int:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print(
            "error: sentence-transformers not installed; "
            "install via `pip install phoenix-middleware[ml-classifier]`."
        )
        return _EXIT_MISSING_EXTRA

    from cognition_wobble.embeddings.loader import _VENDORED_MODEL_PATH, EMBEDDING_MODEL_NAME

    target = _VENDORED_MODEL_PATH
    if target.is_dir():
        print_payload({"status": "already-vendored", "path": str(target)}, fmt)
        return _EXIT_OK
    try:
        model = SentenceTransformer(EMBEDDING_MODEL_NAME)
        target.parent.mkdir(parents=True, exist_ok=True)
        model.save(str(target))
    except OSError as exc:
        print(f"error: could not write embedding model to {target}: {exc}")
        return _EXIT_USAGE
    print_payload({"status": "vendored", "path": str(target), "model": EMBEDDING_MODEL_NAME}, fmt)
    return _EXIT_OK


HANDLERS = {
    "adapt": _cmd_adapt,
    "generate": _cmd_generate,
    "label": _cmd_label,
    "audit": _cmd_audit,
    "train": _cmd_train,
    "evaluate": _cmd_evaluate,
    "vendor-embeddings": _cmd_vendor_embeddings,
}


__all__ = ["HANDLERS"]
