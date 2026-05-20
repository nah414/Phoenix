"""Distance metric for cognition wobble axes (Phase 13 Step 4).

Per ``[OPEN: P13-3]`` default: Step 4 ships the crude exact-string
match; Step 5 swaps in sentence-embedding cosine similarity using the
vendored ``all-MiniLM-L6-v2`` model (shipped at
``vendor/cognition_wobble/embeddings/``).

The exact-string metric exists for two reasons:

1. **Step 4 has no Step 5 deps.** Sentence-transformers is a Step 5
   deliverable; shipping Step 4 with a stub-but-callable metric lets
   the Step 4 → Step 5 evolution be a metric swap rather than a
   structural rewrite.
2. **Reproducibility.** Exact-string distance is deterministic by
   construction; Step 4's verification gate uses fixed seeds and
   exact-string for the panic-mode acceptance tests.

The Step 5 swap-in surface is a single private function
:func:`_text_distance`; the axis classes call it through this module
so the metric upgrade is localized.
"""

from __future__ import annotations


def _text_distance(text_a: str, text_b: str) -> float:
    """Step 4 distance: exact-string match after whitespace canonicalization.

    Returns ``0.0`` when the canonicalized texts match, ``1.0``
    otherwise. The canonicalization strips leading + trailing
    whitespace; case is preserved (LLM outputs are case-meaningful).

    Step 5 swaps this implementation for sentence-embedding cosine
    similarity (model: ``all-MiniLM-L6-v2``, 22 MB, shipped in
    ``vendor/cognition_wobble/embeddings/``). The axis classes call
    through :func:`text_distance` so the swap is a one-line change.
    """
    return 0.0 if text_a.strip() == text_b.strip() else 1.0


def text_distance(text_a: str, text_b: str) -> float:
    """Public distance entry point used by the cognition axes.

    Step 5b upgrade: routes through
    :func:`cognition_wobble.embeddings.compute_semantic_distance`,
    which uses the pinned ``all-MiniLM-L6-v2`` sentence-transformers
    model when available. When the model isn't available (the
    ``[ml-classifier]`` extra not installed OR the model artifact
    not yet committed — Step 5c work), the cognition_wobble loader
    falls back to exact-string distance via
    :func:`_text_distance`. Net result: this function is
    drop-in-compatible with the Step 4 behavior, with a free
    upgrade once the embedding artifact lands.
    """
    try:
        from cognition_wobble.embeddings import compute_semantic_distance
    except ImportError:
        # cognition_wobble itself failed to import — fall back hard.
        return _text_distance(text_a, text_b)
    # Explicit float() cast: cognition_wobble is in mypy's
    # ignore_missing_imports list, so its returns are Any-typed at
    # this call site. The cast restores the strict float return type.
    return float(compute_semantic_distance(text_a, text_b))


def pairwise_distance_matrix(texts: list[str]) -> list[list[float]]:
    """Compute the symmetric N×N pairwise distance matrix.

    Diagonal entries are ``0.0`` by definition. The matrix is
    symmetric: ``matrix[i][j] == matrix[j][i]``. Computes the upper
    triangle and mirrors to skip the redundant work.
    """
    n = len(texts)
    matrix: list[list[float]] = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = text_distance(texts[i], texts[j])
            matrix[i][j] = d
            matrix[j][i] = d
    return matrix


def mean_off_diagonal(matrix: list[list[float]]) -> float:
    """Return the mean of the off-diagonal entries of ``matrix``.

    Returns ``0.0`` for N < 2 (no pairwise comparison possible);
    otherwise averages the ``N*(N-1)`` off-diagonal entries.
    """
    n = len(matrix)
    if n < 2:
        return 0.0
    total = 0.0
    count = 0
    for i in range(n):
        for j in range(n):
            if i != j:
                total += matrix[i][j]
                count += 1
    return total / count if count > 0 else 0.0


__all__ = [
    "text_distance",
    "pairwise_distance_matrix",
    "mean_off_diagonal",
]
