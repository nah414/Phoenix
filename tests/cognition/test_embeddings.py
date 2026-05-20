"""Tests for the cognition_wobble.embeddings loader."""

from __future__ import annotations

import pytest

from cognition_wobble.embeddings.loader import (
    compute_semantic_distance,
    is_embedding_model_available,
    reset_loader_cache,
)


def test_fallback_when_model_unavailable() -> None:
    """When the embedding model isn't available, distance uses exact-string."""
    # In test envs without sentence-transformers installed, the loader
    # returns None and the function falls back to exact-string match.
    reset_loader_cache()
    if is_embedding_model_available():
        pytest.skip("sentence-transformers is actually available; this test exercises the fallback")

    assert compute_semantic_distance("hello", "hello") == 0.0
    assert compute_semantic_distance("hello", "world") == 1.0
    # Whitespace canonicalization.
    assert compute_semantic_distance("hello  ", "hello") == 0.0


def test_fallback_with_empty_text() -> None:
    """Empty texts compared via the fallback yield 0.0."""
    reset_loader_cache()
    if is_embedding_model_available():
        pytest.skip("real embedding model is available; not testing fallback")
    assert compute_semantic_distance("", "") == 0.0


def test_reset_loader_cache_idempotent() -> None:
    """reset_loader_cache() can be called multiple times safely."""
    reset_loader_cache()
    reset_loader_cache()
    # No exception raised.
