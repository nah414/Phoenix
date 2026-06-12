"""Labeled cognition-corpus schema + JSONL loader (Phase 13 Step 5c).

Step 5b ships the classifier scaffolding (Protocol, GBM wrapper,
feature extractors, eval framework) plus a 14-example
:mod:`cognition_wobble.calibration.bootstrap` set. Step 5c trains the
real GBM on a >= 200-example labeled corpus and clears the
``macro-F1 >= 0.70`` acceptance gate.

This module is the **on-disk contract** between the (Adam-side) labeled
corpus and the training/eval pipeline. It is deliberately inert with
respect to production: loading a corpus neither trains nor registers a
classifier; it only materializes :class:`CalibrationExample` rows that
:func:`cognition_wobble.training.train_gbm` and
:func:`cognition_wobble.eval.evaluate` consume.

Corpus format
-------------
**JSONL** — one JSON object per line. Blank lines and lines whose first
non-whitespace character is ``#`` are skipped (so a corpus file may
carry comment headers). Each record:

.. code-block:: json

    {
      "prompt": "What is the capital of France?",
      "response_a": "Paris.",
      "response_b": "The capital of France is Paris.",
      "gold_class": "factual_agreement",
      "source_dataset": "phoenix-generated",
      "annotation_notes": "Same load-bearing claim; surface phrasing differs."
    }

Field reference
~~~~~~~~~~~~~~~
``prompt`` (required)
    Either a bare string (becomes a single ``user`` turn) **or** a full
    object ``{"system": str|null, "messages": [...], "metadata": {...}}``
    mirroring :class:`phoenix.providers.cognition.types.Prompt`.

``response_a`` / ``response_b`` (required)
    Either a bare string (the model's text) **or** an object
    ``{"text": str, "tool_calls": [{"call_id", "name", "arguments"}]}``.
    Only ``text`` and ``tool_calls`` feed the feature extractors
    (:func:`cognition_wobble.features.extract_features`); token usage,
    latency, and fingerprint are not label-bearing, so the loader fills
    neutral placeholders. ``tool_calls`` entries require ``name``;
    ``call_id`` and ``arguments`` default to ``""`` / ``{}``.

``gold_class`` (required)
    One of the :class:`CognitionDisagreementType` string values
    (``factual_agreement``, ``stylistic_divergence``,
    ``factual_disagreement``, ``interpretive_divergence``,
    ``refusal_divergence``, ``tool_choice_divergence``,
    ``unclassified``). ``unclassified`` rows are allowed (they test that
    the classifier abstains) but are filtered out of the graded F1 by
    :func:`cognition_wobble.eval.evaluate`.

``source_dataset`` (optional, default ``"unknown"``)
    Provenance tag (``"SAC3"``, ``"FELM"``, ``"FINCH-ZK"``,
    ``"phoenix-generated"``, ``"synthetic-fixture"``, ...).

``annotation_notes`` (optional, default ``""``)
    Free-form annotator notes; never consumed by training/eval, only
    surfaced in debugging.

Any unrecognized top-level key raises :class:`CorpusValidationError`
(fail-closed: a typo'd field name should not be silently ignored).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from cognition_wobble.disagreement_types import CognitionDisagreementType
from cognition_wobble.eval import CalibrationExample
from phoenix.providers.cognition.types import (
    CognitionResult,
    Prompt,
    TokenUsage,
    ToolCall,
)

__all__ = [
    "CorpusValidationError",
    "REQUIRED_FIELDS",
    "OPTIONAL_FIELDS",
    "load_corpus",
    "parse_corpus_record",
    "example_to_record",
    "write_corpus",
    "count_by_class",
]


# Top-level record keys. Used for fail-closed validation (an unknown key
# is a likely typo in a hand-authored corpus and should be surfaced).
REQUIRED_FIELDS: frozenset[str] = frozenset(
    {"prompt", "response_a", "response_b", "gold_class"}
)
OPTIONAL_FIELDS: frozenset[str] = frozenset(
    {"source_dataset", "annotation_notes"}
)
_ALL_FIELDS: frozenset[str] = REQUIRED_FIELDS | OPTIONAL_FIELDS

# Neutral placeholders for the non-label-bearing CognitionResult fields.
# The feature extractors read only ``.text`` and ``.tool_calls``; usage /
# latency / fingerprint exist to satisfy the frozen dataclass contract.
_PLACEHOLDER_USAGE: TokenUsage = TokenUsage(input_tokens=0, output_tokens=0)

# Valid gold-class string values, precomputed for fast membership + a
# stable, sorted error message.
_VALID_CLASS_VALUES: tuple[str, ...] = tuple(
    sorted(c.value for c in CognitionDisagreementType)
)


class CorpusValidationError(ValueError):
    """A corpus record (or file) failed schema validation.

    Carries the 1-based line number when raised during file loading so
    annotators can jump straight to the offending row. Raised
    fail-closed: an unknown field, missing required field, malformed
    tool-call entry, or unrecognized ``gold_class`` all stop the load
    rather than silently dropping the row.
    """

    def __init__(self, message: str, *, line_no: int | None = None) -> None:
        self.line_no = line_no
        prefix = f"corpus line {line_no}: " if line_no is not None else ""
        super().__init__(f"{prefix}{message}")


def _parse_prompt(raw: Any) -> Prompt:
    """Materialize a :class:`Prompt` from a bare string or full object."""
    if isinstance(raw, str):
        return Prompt(system=None, messages=[{"role": "user", "content": raw}])
    if isinstance(raw, dict):
        messages = raw.get("messages")
        if not isinstance(messages, list) or not messages:
            raise CorpusValidationError(
                "prompt object requires a non-empty 'messages' list"
            )
        system = raw.get("system")
        if system is not None and not isinstance(system, str):
            raise CorpusValidationError("prompt 'system' must be a string or null")
        metadata = raw.get("metadata", {})
        if not isinstance(metadata, dict):
            raise CorpusValidationError("prompt 'metadata' must be an object")
        return Prompt(system=system, messages=messages, metadata=metadata)
    raise CorpusValidationError(
        f"'prompt' must be a string or object; got {type(raw).__name__}"
    )


def _parse_tool_call(raw: Any) -> ToolCall:
    if not isinstance(raw, dict):
        raise CorpusValidationError(
            f"tool_call must be an object; got {type(raw).__name__}"
        )
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise CorpusValidationError("tool_call requires a non-empty 'name'")
    call_id = raw.get("call_id", "")
    arguments = raw.get("arguments", {})
    if not isinstance(arguments, dict):
        raise CorpusValidationError("tool_call 'arguments' must be an object")
    return ToolCall(call_id=str(call_id), name=name, arguments=arguments)


def _parse_response(raw: Any, *, source_dataset: str) -> CognitionResult:
    """Materialize a :class:`CognitionResult` from a string or object.

    Only ``text`` and ``tool_calls`` are label-bearing; the remaining
    fields are filled with neutral placeholders.
    """
    fingerprint = f"corpus|{source_dataset}"
    if isinstance(raw, str):
        return CognitionResult(
            text=raw,
            tool_calls=[],
            usage=_PLACEHOLDER_USAGE,
            latency_ms=0.0,
            provider_fingerprint=fingerprint,
        )
    if isinstance(raw, dict):
        text = raw.get("text", "")
        if not isinstance(text, str):
            raise CorpusValidationError("response 'text' must be a string")
        tool_calls_raw = raw.get("tool_calls", [])
        if not isinstance(tool_calls_raw, list):
            raise CorpusValidationError("response 'tool_calls' must be a list")
        tool_calls = [_parse_tool_call(tc) for tc in tool_calls_raw]
        return CognitionResult(
            text=text,
            tool_calls=tool_calls,
            usage=_PLACEHOLDER_USAGE,
            latency_ms=0.0,
            provider_fingerprint=fingerprint,
        )
    raise CorpusValidationError(
        f"response must be a string or object; got {type(raw).__name__}"
    )


def _parse_gold_class(raw: Any) -> CognitionDisagreementType:
    if not isinstance(raw, str):
        raise CorpusValidationError(
            f"'gold_class' must be a string; got {type(raw).__name__}"
        )
    try:
        return CognitionDisagreementType(raw)
    except ValueError:
        raise CorpusValidationError(
            f"unknown gold_class {raw!r}; "
            f"valid values: {', '.join(_VALID_CLASS_VALUES)}"
        ) from None


def parse_corpus_record(
    obj: dict[str, Any], *, line_no: int | None = None
) -> CalibrationExample:
    """Parse one decoded JSON object into a :class:`CalibrationExample`.

    Validates fail-closed: missing required fields, unknown fields, an
    unrecognized ``gold_class``, or malformed prompt/response/tool-call
    shapes all raise :class:`CorpusValidationError` (annotated with
    ``line_no`` when supplied).
    """
    if not isinstance(obj, dict):
        raise CorpusValidationError(
            f"record must be a JSON object; got {type(obj).__name__}",
            line_no=line_no,
        )

    unknown = set(obj) - _ALL_FIELDS
    if unknown:
        raise CorpusValidationError(
            f"unknown field(s): {', '.join(sorted(unknown))}; "
            f"allowed: {', '.join(sorted(_ALL_FIELDS))}",
            line_no=line_no,
        )
    missing = REQUIRED_FIELDS - set(obj)
    if missing:
        raise CorpusValidationError(
            f"missing required field(s): {', '.join(sorted(missing))}",
            line_no=line_no,
        )

    source_dataset = obj.get("source_dataset", "unknown")
    if not isinstance(source_dataset, str):
        raise CorpusValidationError(
            "'source_dataset' must be a string", line_no=line_no
        )
    annotation_notes = obj.get("annotation_notes", "")
    if not isinstance(annotation_notes, str):
        raise CorpusValidationError(
            "'annotation_notes' must be a string", line_no=line_no
        )

    try:
        prompt = _parse_prompt(obj["prompt"])
        response_a = _parse_response(obj["response_a"], source_dataset=source_dataset)
        response_b = _parse_response(obj["response_b"], source_dataset=source_dataset)
        gold_class = _parse_gold_class(obj["gold_class"])
    except CorpusValidationError as exc:
        # Re-stamp with the line number if the inner parser didn't have it.
        if exc.line_no is None and line_no is not None:
            raise CorpusValidationError(str(exc), line_no=line_no) from None
        raise

    return CalibrationExample(
        prompt=prompt,
        response_a=response_a,
        response_b=response_b,
        gold_class=gold_class,
        source_dataset=source_dataset,
        annotation_notes=annotation_notes,
    )


def load_corpus(path: str | Path) -> list[CalibrationExample]:
    """Load a JSONL corpus file into a list of :class:`CalibrationExample`.

    Skips blank lines and ``#``-comment lines. Raises
    :class:`CorpusValidationError` (with the 1-based line number) on the
    first malformed record, :class:`FileNotFoundError` when ``path``
    does not exist, and :class:`CorpusValidationError` when the file
    parses but contains zero examples (an empty corpus is almost always
    a mistake the caller wants surfaced).
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"corpus file not found: {p}")

    examples: list[CalibrationExample] = []
    with p.open("r", encoding="utf-8") as fh:
        for line_no, raw_line in enumerate(fh, start=1):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise CorpusValidationError(
                    f"invalid JSON: {exc.msg}", line_no=line_no
                ) from exc
            examples.append(parse_corpus_record(obj, line_no=line_no))

    if not examples:
        raise CorpusValidationError(f"corpus file {p} contains no examples")
    return examples


def example_to_record(example: CalibrationExample) -> dict[str, Any]:
    """Serialize a :class:`CalibrationExample` to a JSONL-ready dict.

    Inverse of :func:`parse_corpus_record` (round-trips through
    :func:`load_corpus`). Always writes the full object form for prompt
    and responses so the output is unambiguous; the generator scripts
    use this to emit fixture corpora in the canonical schema.
    """
    def _response_obj(r: CognitionResult) -> dict[str, Any]:
        obj: dict[str, Any] = {"text": r.text}
        if r.tool_calls:
            obj["tool_calls"] = [
                {"call_id": tc.call_id, "name": tc.name, "arguments": tc.arguments}
                for tc in r.tool_calls
            ]
        return obj

    record: dict[str, Any] = {
        "prompt": {
            "system": example.prompt.system,
            "messages": example.prompt.messages,
        },
        "response_a": _response_obj(example.response_a),
        "response_b": _response_obj(example.response_b),
        "gold_class": example.gold_class.value,
        "source_dataset": example.source_dataset,
    }
    if example.prompt.metadata:
        record["prompt"]["metadata"] = example.prompt.metadata
    if example.annotation_notes:
        record["annotation_notes"] = example.annotation_notes
    return record


def write_corpus(
    path: str | Path,
    examples: list[CalibrationExample],
    *,
    header_lines: tuple[str, ...] = (),
) -> Path:
    """Atomically write ``examples`` to a JSONL corpus file.

    Writes to a temp file in the destination directory and ``os.replace``s it
    into place, so a reader (or an interrupted run) never observes a partial
    corpus — a downstream load sees either the old file or the complete new one.
    ``header_lines`` are written first as ``#``-prefixed comments (a leading
    ``#`` and trailing newline are added if absent).

    Round-trips through :func:`load_corpus`. Propagates :class:`OSError` on write
    failure (the caller decides the exit code).
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            for line in header_lines:
                text = line if line.startswith("#") else f"# {line}"
                fh.write(text if text.endswith("\n") else text + "\n")
            for ex in examples:
                fh.write(json.dumps(example_to_record(ex), ensure_ascii=False, sort_keys=True) + "\n")
        os.replace(tmp, p)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    return p


def count_by_class(
    examples: list[CalibrationExample],
) -> dict[CognitionDisagreementType, int]:
    """Tally examples per gold class (all seven, including UNCLASSIFIED).

    Used by the training/eval CLIs to report class balance and warn on
    under-represented classes before training.
    """
    counts: dict[CognitionDisagreementType, int] = {
        c: 0 for c in CognitionDisagreementType
    }
    for ex in examples:
        counts[ex.gold_class] += 1
    return counts
