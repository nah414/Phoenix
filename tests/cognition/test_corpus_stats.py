"""Tests for the Step 5c corpus audit CLI (scripts/corpus_stats.py).

Dependency-free; always runs. The script isn't a package module, so it's
loaded from its file path via importlib and its ``main(argv)`` is exercised
directly with captured stdout.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "corpus_stats.py"
_FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_corpus.jsonl"


def _load_cli():
    spec = importlib.util.spec_from_file_location("corpus_stats_cli", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


corpus_stats = _load_cli()


def _write(tmp_path: Path, lines: list[str]) -> Path:
    p = tmp_path / "corpus.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_balanced_fixture_is_ready(capsys: pytest.CaptureFixture[str]) -> None:
    rc = corpus_stats.main(["--corpus", str(_FIXTURE), "--no-features"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "VERDICT: READY" in out
    assert "240 graded" in out


def test_under_floor_strict(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = _write(
        tmp_path,
        [
            '{"prompt": "q", "response_a": "a", "response_b": "b", "gold_class": "factual_agreement"}'
        ],
    )
    rc = corpus_stats.main(["--corpus", str(p), "--strict", "--no-features"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "VERDICT: NOT READY" in out
    assert "UNDER" in out or "EMPTY" in out


def test_under_floor_without_strict_exits_0(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        [
            '{"prompt": "q", "response_a": "a", "response_b": "b", "gold_class": "factual_agreement"}'
        ],
    )
    # Advisory by default: not ready, but exit 0 unless --strict.
    assert corpus_stats.main(["--corpus", str(p), "--no-features"]) == 0


def test_duplicate_detection(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    row = '{"prompt": "dup", "response_a": "x", "response_b": "y", "gold_class": "refusal_divergence"}'
    p = _write(tmp_path, [row, row, row])
    corpus_stats.main(["--corpus", str(p), "--no-features"])
    out = capsys.readouterr().out
    assert "exact-duplicate" in out


def test_missing_corpus_exits_2(tmp_path: Path) -> None:
    assert corpus_stats.main(["--corpus", str(tmp_path / "nope.jsonl")]) == 2
