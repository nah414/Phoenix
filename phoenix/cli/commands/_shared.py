"""Shared CLI command helpers (Phase 9 Steps 7 + 8).

Three responsibilities:

1. ``load_spec_json(text_or_at_path)`` -- accept either inline JSON
   or ``@/path/to/file.json``; raise :class:`SpecError` on either
   parse failure. Used by ``task submit`` + ``lora load`` +
   ``identity enroll``.
2. ``print_payload(payload, fmt)`` -- thin wrapper over
   :func:`phoenix.cli.output_formats.render` that handles ``None``
   payloads (DELETE responses with no body) gracefully.
3. ``task_cache_path()`` / ``read_task_cache`` / ``write_task_cache``
   -- per-user JSON cache of the most recent POST /v1/tasks response
   for the ``phoenix task get`` command (Phase 9 v1 has no GET
   /v1/tasks/{id} endpoint, so the CLI surfaces the cached value).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from phoenix.cli.output_formats import render


class SpecError(Exception):
    """Raised when a CLI spec argument can't be parsed."""


def load_spec_json(text_or_at_path: str) -> Any:
    """Resolve a CLI spec arg to a Python object.

    Supports two shapes:
      - ``@path/to/file.json`` -- read the file, parse as JSON.
      - inline JSON -- parse directly.

    Raises :class:`SpecError` on any failure with a helpful message
    naming the offending input.
    """
    if not text_or_at_path:
        raise SpecError("Spec argument is empty")
    if text_or_at_path.startswith("@"):
        path = Path(text_or_at_path[1:])
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SpecError(f"Cannot read spec file {path}: {exc}") from exc
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise SpecError(f"Malformed JSON in {path}: {exc}") from exc
    try:
        return json.loads(text_or_at_path)
    except json.JSONDecodeError as exc:
        raise SpecError(f"Malformed inline JSON: {exc}") from exc


def print_payload(payload: Any, fmt: str) -> None:
    """Render ``payload`` to stdout. ``None`` -> ``(no content)``."""
    if payload is None:
        print("(no content)")
        return
    print(render(payload, format_name=fmt))


def task_cache_path() -> Path:
    """Per-user JSON cache for the `phoenix task get` command.

    Lives at ``~/.phoenix/task_cache.json``. The directory is
    created on first write.
    """
    return Path.home() / ".phoenix" / "task_cache.json"


def read_task_cache(*, path: Path | None = None) -> dict[str, Any]:
    """Return the per-user task cache (empty dict if missing/broken).

    Defensive: a corrupted cache file does not crash the CLI.
    """
    target = path if path is not None else task_cache_path()
    if not target.exists():
        return {}
    try:
        parsed: Any = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def write_task_cache(cache: dict[str, Any], *, path: Path | None = None) -> None:
    """Atomically write the task cache to disk."""
    target = path if path is not None else task_cache_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(target)


__all__ = [
    "SpecError",
    "load_spec_json",
    "print_payload",
    "read_task_cache",
    "task_cache_path",
    "write_task_cache",
]
