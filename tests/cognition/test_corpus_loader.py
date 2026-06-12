"""Tests for the Step 5c labeled-corpus schema + JSONL loader.

Dependency-free (no lightgbm / sentence-transformers); always runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cognition_wobble.corpus import (
    CorpusValidationError,
    count_by_class,
    example_to_record,
    load_corpus,
    parse_corpus_record,
    write_corpus,
)
from cognition_wobble.disagreement_types import CognitionDisagreementType

_FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_corpus.jsonl"


# ---------------------------------------------------------------------------
# parse_corpus_record — happy paths


def test_parse_string_form() -> None:
    ex = parse_corpus_record(
        {
            "prompt": "What is the capital of France?",
            "response_a": "Paris.",
            "response_b": "The capital of France is Paris.",
            "gold_class": "factual_agreement",
        }
    )
    assert ex.gold_class is CognitionDisagreementType.FACTUAL_AGREEMENT
    assert ex.response_a.text == "Paris."
    assert ex.prompt.system is None
    assert ex.prompt.messages == [{"role": "user", "content": "What is the capital of France?"}]
    # placeholder, non-label-bearing fields are filled
    assert ex.response_a.provider_fingerprint == "corpus|unknown"


def test_parse_object_form_with_tool_calls() -> None:
    ex = parse_corpus_record(
        {
            "prompt": {"system": "sys", "messages": [{"role": "user", "content": "weather?"}]},
            "response_a": {
                "text": "",
                "tool_calls": [{"name": "get_weather", "arguments": {"city": "Boston"}}],
            },
            "response_b": "It is sunny.",
            "gold_class": "tool_choice_divergence",
            "source_dataset": "phoenix-generated",
            "annotation_notes": "a called a tool; b did not",
        }
    )
    assert ex.prompt.system == "sys"
    assert ex.response_a.tool_calls[0].name == "get_weather"
    assert ex.response_a.tool_calls[0].call_id == ""  # defaulted
    assert ex.response_a.tool_calls[0].arguments == {"city": "Boston"}
    assert ex.source_dataset == "phoenix-generated"


def test_gold_unclassified_is_allowed() -> None:
    ex = parse_corpus_record(
        {"prompt": "hmm", "response_a": "ok", "response_b": "?", "gold_class": "unclassified"}
    )
    assert ex.gold_class is CognitionDisagreementType.UNCLASSIFIED


# ---------------------------------------------------------------------------
# parse_corpus_record — fail-closed validation


def test_unknown_gold_class_raises_with_line_no() -> None:
    with pytest.raises(CorpusValidationError) as ei:
        parse_corpus_record(
            {"prompt": "q", "response_a": "a", "response_b": "b", "gold_class": "bogus"},
            line_no=42,
        )
    assert ei.value.line_no == 42
    assert "bogus" in str(ei.value)
    # error lists the valid values so an annotator can fix it
    assert "factual_agreement" in str(ei.value)


def test_missing_required_field_raises() -> None:
    with pytest.raises(CorpusValidationError, match="missing required field"):
        parse_corpus_record({"prompt": "q", "response_a": "a", "gold_class": "factual_agreement"})


def test_unknown_field_raises() -> None:
    with pytest.raises(CorpusValidationError, match="unknown field"):
        parse_corpus_record(
            {
                "prompt": "q",
                "response_a": "a",
                "response_b": "b",
                "gold_class": "factual_agreement",
                "lable": "typo",
            }
        )


def test_prompt_object_without_messages_raises() -> None:
    with pytest.raises(CorpusValidationError, match="messages"):
        parse_corpus_record(
            {
                "prompt": {"system": None, "messages": []},
                "response_a": "a",
                "response_b": "b",
                "gold_class": "factual_agreement",
            }
        )


def test_tool_call_without_name_raises() -> None:
    with pytest.raises(CorpusValidationError, match="name"):
        parse_corpus_record(
            {
                "prompt": "q",
                "response_a": {"text": "", "tool_calls": [{"arguments": {}}]},
                "response_b": "b",
                "gold_class": "tool_choice_divergence",
            }
        )


def test_non_string_response_text_raises() -> None:
    with pytest.raises(CorpusValidationError, match="text"):
        parse_corpus_record(
            {
                "prompt": "q",
                "response_a": {"text": 123},
                "response_b": "b",
                "gold_class": "factual_agreement",
            }
        )


# ---------------------------------------------------------------------------
# load_corpus — file-level behavior


def _write(tmp_path: Path, lines: list[str]) -> Path:
    p = tmp_path / "corpus.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_load_skips_comments_and_blanks(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        [
            "# a comment header",
            "",
            '{"prompt": "q", "response_a": "a", "response_b": "b", "gold_class": "factual_agreement"}',
            "   ",
            '{"prompt": "q2", "response_a": "a2", "response_b": "b2", "gold_class": "refusal_divergence"}',
        ],
    )
    examples = load_corpus(p)
    assert len(examples) == 2
    assert examples[1].gold_class is CognitionDisagreementType.REFUSAL_DIVERGENCE


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_corpus(tmp_path / "nope.jsonl")


def test_load_empty_corpus_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, ["# only comments", ""])
    with pytest.raises(CorpusValidationError, match="no examples"):
        load_corpus(p)


def test_load_invalid_json_reports_line(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        [
            '{"prompt": "q", "response_a": "a", "response_b": "b", "gold_class": "factual_agreement"}',
            "{not json}",
        ],
    )
    with pytest.raises(CorpusValidationError) as ei:
        load_corpus(p)
    assert ei.value.line_no == 2


def test_bad_record_reports_line_number(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        [
            '{"prompt": "q", "response_a": "a", "response_b": "b", "gold_class": "factual_agreement"}',
            '{"prompt": "q", "response_a": "a", "response_b": "b", "gold_class": "WRONG"}',
        ],
    )
    with pytest.raises(CorpusValidationError) as ei:
        load_corpus(p)
    assert ei.value.line_no == 2


# ---------------------------------------------------------------------------
# round-trip + helpers


def test_round_trip_through_record() -> None:
    original = parse_corpus_record(
        {
            "prompt": {
                "system": "s",
                "messages": [{"role": "user", "content": "hi"}],
                "metadata": {"k": "v"},
            },
            "response_a": {
                "text": "",
                "tool_calls": [{"call_id": "c1", "name": "t", "arguments": {"x": 1}}],
            },
            "response_b": "plain",
            "gold_class": "tool_choice_divergence",
            "source_dataset": "rt",
            "annotation_notes": "note",
        }
    )
    record = example_to_record(original)
    restored = parse_corpus_record(record)
    assert restored.gold_class is original.gold_class
    assert restored.prompt.system == "s"
    assert restored.prompt.metadata == {"k": "v"}
    assert restored.response_a.tool_calls[0].name == "t"
    assert restored.response_b.text == "plain"
    assert restored.annotation_notes == "note"


def test_count_by_class_covers_all_seven() -> None:
    counts = count_by_class([])
    assert set(counts) == set(CognitionDisagreementType)
    assert all(v == 0 for v in counts.values())


# ---------------------------------------------------------------------------
# committed synthetic fixture


def test_write_corpus_round_trips_through_load(tmp_path: Path) -> None:
    original = load_corpus(_FIXTURE)[:5]
    out = tmp_path / "sub" / "written.jsonl"  # parent dir does not exist yet
    written = write_corpus(out, original, header_lines=("a header note",))
    assert written == out
    # header comment was added with a leading '#', and the round-trip preserves rows
    first_line = out.read_text(encoding="utf-8").splitlines()[0]
    assert first_line.startswith("# a header note")
    restored = load_corpus(out)
    assert len(restored) == len(original)
    assert [e.gold_class for e in restored] == [e.gold_class for e in original]


def test_write_corpus_leaves_no_temp_files(tmp_path: Path) -> None:
    examples = load_corpus(_FIXTURE)[:2]
    out = tmp_path / "c.jsonl"
    write_corpus(out, examples)
    # only the final file remains -- the temp file was os.replace'd away
    assert {p.name for p in tmp_path.iterdir()} == {"c.jsonl"}


def test_committed_fixture_loads() -> None:
    examples = load_corpus(_FIXTURE)
    counts = count_by_class(examples)
    # 6 graded * 40 + 12 unclassified
    assert counts[CognitionDisagreementType.FACTUAL_AGREEMENT] == 40
    assert counts[CognitionDisagreementType.TOOL_CHOICE_DIVERGENCE] == 40
    assert counts[CognitionDisagreementType.UNCLASSIFIED] == 12
    assert len(examples) == 252
    # everything is tagged synthetic so it can never be mistaken for real data
    assert all(e.source_dataset == "synthetic-fixture" for e in examples)
