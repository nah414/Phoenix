"""Lazy loader + semantic-distance computation.

Per Phase 13 build guide Step 5: sentence-embedding cosine similarity
via ``sentence-transformers`` (pinned model: ``all-MiniLM-L6-v2``,
22 MB). The model is shipped at this package's directory in Step 5c;
Step 5b ships only the loader.

When ``sentence-transformers`` is not installed OR the model file
isn't found, :func:`compute_semantic_distance` falls back to the
exact-string match from :func:`phoenix.verification.axes._distance`.
This preserves the Step 4 → Step 5 evolution: the metric upgrade is
opt-in via installation of the ``[ml-classifier]`` extra.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Any

EMBEDDING_MODEL_NAME: str = "all-MiniLM-L6-v2"
"""The pinned sentence-transformers model. 22 MB; commits in Step 5c
under ``vendor/cognition_wobble/embeddings/all-MiniLM-L6-v2/``."""

_VENDORED_MODEL_PATH: Path = Path(__file__).parent / EMBEDDING_MODEL_NAME
"""Vendored on-disk copy of the model (Step 5c, decision #3 vendor-first).
When this directory exists it is loaded offline in preference to the
network-based by-name download; ``scripts/vendor_embedding_model.py``
populates it."""


@lru_cache(maxsize=1)
def _try_load_model() -> Any | None:
    """Lazy-load the sentence-transformers model.

    Resolution order: the vendored local copy at :data:`_VENDORED_MODEL_PATH`
    (offline, no network) → the by-name HuggingFace download
    (:data:`EMBEDDING_MODEL_NAME`) → ``None``. Returns ``None`` on any failure
    (SDK missing, model files missing, runtime error). Cached so the loader runs
    at most once per process.
    """
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return None
    try:
        # Step 5c: prefer the vendored local copy (offline) when present.
        if _VENDORED_MODEL_PATH.is_dir():
            return SentenceTransformer(str(_VENDORED_MODEL_PATH))
        # Fallback: by-name load (downloads from HF Hub on first use).
        return SentenceTransformer(EMBEDDING_MODEL_NAME)
    except Exception:
        # The model may not be downloaded/vendored yet. Return None and let
        # callers fall back to exact-string distance.
        return None


def is_embedding_model_available() -> bool:
    """Return ``True`` when the embedding model loads successfully.

    Useful for tests + the verification gate's depth-decision logic:
    when embeddings aren't available, the gate may want to skip the
    cognition axes' semantic-distance promotion and use raw exact-
    string distance.
    """
    return _try_load_model() is not None


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors, in ``[-1, 1]``."""
    dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=True))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def compute_semantic_distance(text_a: str, text_b: str) -> float:
    """Sentence-embedding cosine distance in ``[0, 1]``.

    Returns ``0.0`` for identical texts; ``1.0`` for maximally
    dissimilar texts. Computed as ``(1 - cosine_similarity) / 2``
    so the codomain is ``[0, 1]`` even when cosine similarity is
    negative.

    **Fallback:** when the embedding model isn't available (SDK
    missing or model file not vendored yet), returns the exact-string
    distance: ``0.0`` if canonicalized texts match, ``1.0`` otherwise.
    """
    model = _try_load_model()
    if model is None:
        return 0.0 if text_a.strip() == text_b.strip() else 1.0

    # Both branches handle empty texts identically — exact match.
    if not text_a.strip() and not text_b.strip():
        return 0.0

    try:
        embeddings = model.encode([text_a, text_b], convert_to_numpy=False)
        # Coerce to plain lists of floats for the cosine math (the
        # sentence-transformers API returns torch tensors by default).
        vec_a = [float(x) for x in embeddings[0]]
        vec_b = [float(x) for x in embeddings[1]]
    except Exception:
        # Model loaded but inference failed — degrade gracefully.
        return 0.0 if text_a.strip() == text_b.strip() else 1.0

    cos = _cosine_similarity(vec_a, vec_b)
    # Map cosine [-1, 1] → distance [0, 1]: identical → 0; opposite → 1.
    return max(0.0, min(1.0, (1.0 - cos) / 2.0))


def reset_loader_cache() -> None:
    """Test helper: clear the cached loader.

    Tests that monkeypatch ``sentence_transformers`` use this to force
    a re-load with the patched module.
    """
    _try_load_model.cache_clear()
