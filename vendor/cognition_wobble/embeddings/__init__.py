"""Sentence-embedding model loader for the cognition classifier.

Step 5b ships the loader; Step 5c commits the pinned 22 MB
``all-MiniLM-L6-v2`` model file under this package and updates the
``[sentence-transformers]`` pip extra pin.

The loader is lazy and tolerant: when ``sentence-transformers`` is not
installed or the model file isn't present, the
:func:`compute_semantic_distance` function falls back to exact-string
distance so the rest of the classifier pipeline keeps working.
"""

from cognition_wobble.embeddings.loader import (
    EMBEDDING_MODEL_NAME,
    compute_semantic_distance,
    is_embedding_model_available,
)

__all__ = [
    "EMBEDDING_MODEL_NAME",
    "compute_semantic_distance",
    "is_embedding_model_available",
]
