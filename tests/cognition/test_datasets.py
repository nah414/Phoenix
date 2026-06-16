"""Tests for the FELM / SAC3 corpus adapters (fixtures in the real schemas)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from cognition_wobble.corpus import load_corpus, write_corpus
from cognition_wobble.datasets import from_felm, from_sac3
from cognition_wobble.disagreement_types import CognitionDisagreementType as C

# --- representative source records (match the documented dataset schemas) ---

_FELM_ERROR = {
    "prompt": "Show me the answer: 143287*534156=?",
    "response": "Multiplying gives 76529372.",
    "segmented_response": ["Multiplying gives 76529372."],
    "labels": [False],
    "comment": ["76537610772"],
    "type": [""],
    "ref": [""],
    "source": "authors",
}
_FELM_MIXED = {
    "prompt": "Tell me about X.",
    "response": "X was founded in 1900. It is in Paris.",
    "segmented_response": ["X was founded in 1900.", "It is in Paris."],
    "labels": [True, False],
    "comment": ["", "It is in London."],
    "source": "wk",
}
_FELM_FACTUAL = {
    "prompt": "What is 2+2?",
    "response": "It is 4.",
    "segmented_response": ["It is 4."],
    "labels": [True],
    "comment": [""],
}
_FELM_NO_CORRECTION = {
    "prompt": "q",
    "response": "wrong claim.",
    "segmented_response": ["wrong claim."],
    "labels": [False],
    "comment": [""],  # false segment, no correction -> unusable
}

_SAC3 = {
    "question": "is 3691 a prime number?",
    "target_answer": "No, it is not prime",
    "self_responses": ["Yes, 3691 is prime.", "Yes, prime.", "No, 3691 is not prime."],
    "sc2_vote": [1, 1, 0],
}


# ---------------------------------------------------------------------------
# FELM


def test_felm_error_record_becomes_disagreement_pair() -> None:
    report = from_felm([_FELM_ERROR])
    assert report.n_emitted == 1
    ex = report.examples[0]
    assert ex.gold_class is C.FACTUAL_DISAGREEMENT
    assert ex.response_a.text == "Multiplying gives 76529372."  # original error
    assert "76537610772" in ex.response_b.text  # corrected from comment
    assert ex.source_dataset == "FELM"


def test_felm_corrected_response_keeps_true_segments_swaps_false() -> None:
    ex = from_felm([_FELM_MIXED]).examples[0]
    # true segment preserved, false segment swapped for its correction
    assert "founded in 1900" in ex.response_b.text
    assert "It is in London." in ex.response_b.text
    assert "It is in Paris." not in ex.response_b.text


def test_felm_fully_factual_skipped() -> None:
    report = from_felm([_FELM_FACTUAL])
    assert report.n_emitted == 0
    assert report.n_skipped == 1
    assert "fully factual" in report.skip_reasons[0]


def test_felm_false_without_correction_skipped() -> None:
    report = from_felm([_FELM_NO_CORRECTION])
    assert report.n_emitted == 0
    assert "without a usable correction" in report.skip_reasons[0]


# ---------------------------------------------------------------------------
# SAC3


def test_sac3_votes_prelabel_agreement_and_disagreement() -> None:
    report = from_sac3([_SAC3])
    # response[0] paired with [1] (vote 1 -> agreement) and [2] (vote 0 -> disagreement)
    assert report.n_emitted == 2
    classes = {e.gold_class for e in report.examples}
    assert classes == {C.FACTUAL_AGREEMENT, C.FACTUAL_DISAGREEMENT}
    assert all(e.source_dataset == "SAC3" for e in report.examples)


def test_sac3_no_prelabel_emits_unclassified() -> None:
    report = from_sac3([_SAC3], prelabel_from_votes=False)
    assert report.n_emitted == 2
    assert all(e.gold_class is C.UNCLASSIFIED for e in report.examples)


def test_sac3_too_few_responses_skipped() -> None:
    report = from_sac3([{"question": "q", "self_responses": ["only one"]}])
    assert report.n_emitted == 0
    assert "self_responses" in report.skip_reasons[0]


# ---------------------------------------------------------------------------
# round-trip through the corpus schema


def test_adapted_examples_round_trip_through_corpus(tmp_path: Path) -> None:
    examples = from_felm([_FELM_ERROR, _FELM_MIXED]).examples + from_sac3([_SAC3]).examples
    out = tmp_path / "adapted.jsonl"
    write_corpus(out, examples, header_lines=("adapted",))
    restored = load_corpus(out)
    assert len(restored) == len(examples)
    assert [e.gold_class for e in restored] == [e.gold_class for e in examples]


# ---------------------------------------------------------------------------
# CLI (loaded from the script file)

_CLI = Path(__file__).resolve().parents[2] / "scripts" / "adapt_dataset.py"


def _load_cli():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("adapt_cli", _CLI)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_jsonl(path: Path, records: list[dict]) -> Path:
    import json

    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def test_adapt_cli_felm_end_to_end(tmp_path: Path) -> None:
    cli = _load_cli()
    inp = _write_jsonl(tmp_path / "felm.jsonl", [_FELM_ERROR, _FELM_MIXED, _FELM_FACTUAL])
    out = tmp_path / "pairs.jsonl"
    rc = cli.main(["--dataset", "felm", "--in", str(inp), "--out", str(out)])
    assert rc == 0
    restored = load_corpus(out)
    assert len(restored) == 2  # 2 error records -> 2 pairs; factual one skipped
    assert all(e.gold_class is C.FACTUAL_DISAGREEMENT for e in restored)


def test_adapt_cli_rejects_malformed_json(tmp_path: Path) -> None:
    cli = _load_cli()
    p = tmp_path / "bad.jsonl"
    p.write_text('{"prompt": "ok"}\n{bad}\n', encoding="utf-8")
    out = tmp_path / "o.jsonl"
    assert cli.main(["--dataset", "felm", "--in", str(p), "--out", str(out)]) == 2


def test_adapt_cli_all_skipped_exits_2(tmp_path: Path) -> None:
    cli = _load_cli()
    inp = _write_jsonl(tmp_path / "felm.jsonl", [_FELM_FACTUAL])  # nothing emittable
    out = tmp_path / "o.jsonl"
    assert cli.main(["--dataset", "felm", "--in", str(inp), "--out", str(out)]) == 2
    assert not out.exists()
