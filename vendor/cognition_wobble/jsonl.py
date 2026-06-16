"""Shared JSONL reader for Step 5c corpus/dataset source files.

One loader used by every adapt entry point — the ``phoenix cognition adapt`` CLI
(``phoenix.cli.commands.cognition``), the ``scripts/adapt_dataset.py`` script,
and the ``POST /v1/cognition/adapt`` endpoint (``phoenix.api.cognition_ui``) —
so all three skip the same blank/comment lines, enforce object records, and
report errors identically. Deliberately dependency-light (stdlib only) so the
CLI can import it without pulling in the heavier adapter modules.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["read_jsonl_records"]


def read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    """Load JSONL object records, skipping blank and ``#``-comment lines.

    Raises :class:`ValueError` with the 1-based line number on malformed JSON or
    a non-object record; propagates :class:`OSError` if the file can't be read.
    """
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path} line {line_no}: invalid JSON: {exc.msg}") from exc
            if not isinstance(obj, dict):
                raise ValueError(f"{path} line {line_no}: each record must be a JSON object")
            records.append(obj)
    return records
