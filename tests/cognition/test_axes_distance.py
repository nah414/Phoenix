"""Tests for the Step 4 cognition-axes distance helpers."""

from __future__ import annotations

import pytest

from phoenix.verification.axes._distance import (
    mean_off_diagonal,
    pairwise_distance_matrix,
    text_distance,
)


def test_exact_match_is_zero() -> None:
    assert text_distance("hello", "hello") == 0.0


def test_different_is_one() -> None:
    assert text_distance("hello", "world") == 1.0


def test_whitespace_canonicalization() -> None:
    """Surrounding whitespace doesn't count."""
    assert text_distance("hello", "  hello  ") == 0.0
    assert text_distance("hello\n", "hello") == 0.0


def test_case_preserved() -> None:
    """Casing matters (LLM outputs are case-meaningful)."""
    assert text_distance("Hello", "hello") == 1.0


def test_pairwise_matrix_is_symmetric() -> None:
    texts = ["a", "b", "a"]
    m = pairwise_distance_matrix(texts)
    assert m[0][1] == m[1][0]
    assert m[0][2] == m[2][0]
    assert m[1][2] == m[2][1]


def test_pairwise_matrix_diagonal_zero() -> None:
    texts = ["a", "b", "c"]
    m = pairwise_distance_matrix(texts)
    for i in range(3):
        assert m[i][i] == 0.0


def test_pairwise_matrix_values() -> None:
    texts = ["a", "b", "a"]
    m = pairwise_distance_matrix(texts)
    # texts[0] == texts[2], so m[0][2] == 0.0
    assert m[0][2] == 0.0
    # texts[0] != texts[1], so m[0][1] == 1.0
    assert m[0][1] == 1.0


def test_mean_off_diagonal_single_text() -> None:
    """N=1 yields 0.0 — no pairwise comparison possible."""
    m = pairwise_distance_matrix(["only"])
    assert mean_off_diagonal(m) == 0.0


def test_mean_off_diagonal_empty() -> None:
    """Empty matrix yields 0.0."""
    assert mean_off_diagonal([]) == 0.0


def test_mean_off_diagonal_all_match() -> None:
    """All identical texts → 0.0."""
    m = pairwise_distance_matrix(["x", "x", "x"])
    assert mean_off_diagonal(m) == 0.0


def test_mean_off_diagonal_all_differ() -> None:
    """All texts differ → 1.0."""
    m = pairwise_distance_matrix(["x", "y", "z"])
    assert mean_off_diagonal(m) == pytest.approx(1.0)


def test_mean_off_diagonal_partial() -> None:
    """Two match, one differs → 2/3 (4 of 6 off-diagonals are 1.0)."""
    m = pairwise_distance_matrix(["x", "x", "y"])
    # off-diagonals: (0,1)=0, (0,2)=1, (1,0)=0, (1,2)=1, (2,0)=1, (2,1)=1
    # mean = 4/6 = 0.6667
    assert mean_off_diagonal(m) == pytest.approx(4 / 6)
