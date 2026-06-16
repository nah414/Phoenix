"""Tests for the Step 5c vendored-embedding-model load preference.

Injects a fake ``sentence_transformers`` module so the loader's path-resolution
(vendored local dir -> by-name download -> None) can be exercised without the
real SDK or a 22 MB model download.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from cognition_wobble.embeddings import loader


class _FakeST:
    """Records the path/name it was constructed with."""

    instances: list[str] = []

    def __init__(self, name_or_path: str) -> None:
        type(self).instances.append(name_or_path)

    def encode(self, texts: Any, convert_to_numpy: bool = False) -> list[float]:
        return [0.0, 0.0, 0.0]


@pytest.fixture
def fake_sentence_transformers(monkeypatch: pytest.MonkeyPatch):
    mod = types.ModuleType("sentence_transformers")
    mod.SentenceTransformer = _FakeST  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", mod)
    _FakeST.instances.clear()
    loader.reset_loader_cache()
    yield
    loader.reset_loader_cache()


def test_prefers_vendored_path_when_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_sentence_transformers: None
) -> None:
    vendored = tmp_path / "all-MiniLM-L6-v2"
    vendored.mkdir()
    monkeypatch.setattr(loader, "_VENDORED_MODEL_PATH", vendored)
    loader.reset_loader_cache()

    assert loader.is_embedding_model_available() is True
    assert _FakeST.instances[-1] == str(vendored)


def test_falls_back_to_name_when_no_vendored_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_sentence_transformers: None
) -> None:
    monkeypatch.setattr(loader, "_VENDORED_MODEL_PATH", tmp_path / "does-not-exist")
    loader.reset_loader_cache()

    assert loader.is_embedding_model_available() is True
    assert _FakeST.instances[-1] == loader.EMBEDDING_MODEL_NAME
