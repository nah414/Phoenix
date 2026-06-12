#!/usr/bin/env python
"""Vendor the all-MiniLM-L6-v2 sentence-transformers model (Step 5c, decision #3).

Downloads the pinned embedding model and saves an offline copy under
``vendor/cognition_wobble/embeddings/all-MiniLM-L6-v2/`` so
``semantic_distance`` stops falling back to binary. The loader
(``cognition_wobble/embeddings/loader.py``) already prefers this vendored path
when it exists.

Run once, with the ``[ml-classifier]`` extra installed, then commit the saved
directory::

    pip install -e ".[ml-classifier]"
    python scripts/vendor_embedding_model.py
    git add vendor/cognition_wobble/embeddings/all-MiniLM-L6-v2

Exit codes: 0 = vendored (or already present); 2 = write error; 3 =
sentence-transformers not installed.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cognition_wobble.embeddings.loader import EMBEDDING_MODEL_NAME  # noqa: E402

_TARGET = _REPO_ROOT / "vendor" / "cognition_wobble" / "embeddings" / EMBEDDING_MODEL_NAME


def main() -> int:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print(
            "error: sentence-transformers not installed; install via "
            "`pip install phoenix-middleware[ml-classifier]`.",
            file=sys.stderr,
        )
        return 3

    if _TARGET.is_dir():
        print(f"already vendored at {_TARGET}; nothing to do.")
        return 0

    print(f"downloading {EMBEDDING_MODEL_NAME} from HuggingFace ...")
    model = SentenceTransformer(EMBEDDING_MODEL_NAME)

    try:
        _TARGET.parent.mkdir(parents=True, exist_ok=True)
        print(f"saving offline copy to {_TARGET} ...")
        model.save(str(_TARGET))
    except OSError as exc:
        print(f"error: could not write embedding model to {_TARGET}: {exc}", file=sys.stderr)
        return 2

    # Verify it loads offline from the vendored path.
    offline = SentenceTransformer(str(_TARGET))
    dim = len(offline.encode("verification sentence", convert_to_numpy=False))
    print(f"OK: offline load works (embedding dim={dim}).")
    print(f"next: git add {_TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
