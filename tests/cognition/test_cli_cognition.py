"""Tests for the ``phoenix cognition`` operator console (offline verbs)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cognition_wobble.corpus import load_corpus
from cognition_wobble.disagreement_types import CognitionDisagreementType as C
from phoenix.cli.entry import main

_REPO = Path(__file__).resolve().parents[2]
_FELM = _REPO / "samples" / "step5c" / "felm_sample.jsonl"
_SAC3 = _REPO / "samples" / "step5c" / "sac3_sample.jsonl"
_FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_corpus.jsonl"


def test_cognition_no_subcommand_is_usage_error() -> None:
    assert main(["cognition"]) == 2


def test_adapt_felm_end_to_end(tmp_path: Path) -> None:
    out = tmp_path / "felm.jsonl"
    rc = main(["cognition", "adapt", "--dataset", "felm", "--in", str(_FELM), "--out", str(out)])
    assert rc == 0
    examples = load_corpus(out)
    assert len(examples) == 6
    assert all(e.gold_class is C.FACTUAL_DISAGREEMENT for e in examples)


def test_adapt_sac3_end_to_end(tmp_path: Path) -> None:
    out = tmp_path / "sac3.jsonl"
    assert (
        main(["cognition", "adapt", "--dataset", "sac3", "--in", str(_SAC3), "--out", str(out)])
        == 0
    )
    assert len(load_corpus(out)) == 7


def test_adapt_missing_input_exits_2(tmp_path: Path) -> None:
    rc = main(
        [
            "cognition",
            "adapt",
            "--dataset",
            "felm",
            "--in",
            str(tmp_path / "nope.jsonl"),
            "--out",
            str(tmp_path / "o.jsonl"),
        ]
    )
    assert rc == 2


def test_audit_synthetic_is_ready(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--format", "json", "cognition", "audit", "--corpus", str(_FIXTURE), "--strict"])
    assert rc == 0
    assert '"ready": true' in capsys.readouterr().out


def test_audit_under_floor_strict_exits_2(tmp_path: Path) -> None:
    p = tmp_path / "tiny.jsonl"
    p.write_text(
        '{"prompt": "q", "response_a": "a", "response_b": "b", "gold_class": "factual_agreement"}\n',
        encoding="utf-8",
    )
    assert main(["cognition", "audit", "--corpus", str(p), "--strict"]) == 2


def test_evaluate_stub_fails_gate(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--format", "json", "cognition", "evaluate", "--stub", "--corpus", str(_FIXTURE)])
    assert rc == 1
    assert '"gate_passed": false' in capsys.readouterr().out


def test_train_then_evaluate_model_clears_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest.importorskip("lightgbm")
    model = tmp_path / "gbm.txt"
    assert main(["cognition", "train", "--corpus", str(_FIXTURE), "--out", str(model)]) == 0
    assert model.exists()
    rc = main(
        [
            "--format",
            "json",
            "cognition",
            "evaluate",
            "--model",
            str(model),
            "--corpus",
            str(_FIXTURE),
            "--confusion",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert '"gate_passed": true' in out
    assert "confusion" in out
