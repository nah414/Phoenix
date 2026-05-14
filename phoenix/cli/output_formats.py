"""CLI output formatting (Phase 9 Step 6).

Three render modes:

- ``json`` -- machine-friendly; ``json.dumps`` with ``indent=2``.
  The CLI's default when stdout is piped.
- ``text`` -- human-friendly; a simple key-per-line format with
  nested dicts indented. Default when stdout is a TTY.
- ``table`` -- tabular; only meaningful when the payload is a list
  of dicts. Falls back to ``text`` for scalar/dict payloads.

The ``auto`` mode (the CLI's default) inspects
``sys.stdout.isatty()`` and dispatches to ``text`` or ``json``.
Tests can force a mode by passing it explicitly.
"""

from __future__ import annotations

import json
import sys
from typing import Any

_VALID_FORMATS = frozenset({"auto", "json", "text", "table"})


class OutputFormatError(Exception):
    """Raised when ``format_name`` isn't recognised."""


def render(
    payload: Any,
    *,
    format_name: str = "auto",
    is_tty: bool | None = None,
) -> str:
    """Render ``payload`` for terminal output.

    Parameters:
      - ``payload``: JSON-serialisable value (dict, list, scalar).
      - ``format_name``: one of ``auto`` | ``json`` | ``text`` |
        ``table``. ``auto`` resolves to ``text`` on a TTY, ``json``
        when piped (per Section 5.4 UX expectations).
      - ``is_tty``: explicit override for tests; defaults to
        :func:`sys.stdout.isatty`.

    Returns the rendered string (without a trailing newline; the
    caller decides whether to ``print()`` or
    ``sys.stdout.write()``).
    """
    if format_name not in _VALID_FORMATS:
        raise OutputFormatError(
            f"Unknown output format {format_name!r}; valid: {sorted(_VALID_FORMATS)}"
        )

    if format_name == "auto":
        if is_tty is None:
            is_tty = sys.stdout.isatty()
        format_name = "text" if is_tty else "json"

    if format_name == "json":
        return _render_json(payload)
    if format_name == "text":
        return _render_text(payload)
    if format_name == "table":
        return _render_table(payload)
    # Defensive: _VALID_FORMATS would've raised already
    raise OutputFormatError(f"Unknown format {format_name!r}")  # pragma: no cover


def _render_json(payload: Any) -> str:
    """Two-space-indent JSON. Stable ordering for snapshot tests."""
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


def _render_text(payload: Any, indent: int = 0) -> str:
    """Human-readable text format. Recursive on nested dicts/lists."""
    pad = "  " * indent
    if isinstance(payload, dict):
        if not payload:
            return f"{pad}(empty)"
        lines = []
        for key in sorted(payload.keys()):
            value = payload[key]
            if isinstance(value, (dict, list)) and value:
                lines.append(f"{pad}{key}:")
                lines.append(_render_text(value, indent + 1))
            else:
                lines.append(f"{pad}{key}: {_format_scalar(value)}")
        return "\n".join(lines)
    if isinstance(payload, list):
        if not payload:
            return f"{pad}(empty list)"
        lines = []
        for idx, item in enumerate(payload):
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}[{idx}]")
                lines.append(_render_text(item, indent + 1))
            else:
                lines.append(f"{pad}[{idx}] {_format_scalar(item)}")
        return "\n".join(lines)
    return f"{pad}{_format_scalar(payload)}"


def _render_table(payload: Any) -> str:
    """Tabular: list-of-dicts -> ASCII columns.

    Non-list payloads (or lists of scalars) fall back to text format.
    """
    if not isinstance(payload, list) or not payload:
        return _render_text(payload)
    if not all(isinstance(row, dict) for row in payload):
        return _render_text(payload)

    # Union of keys preserves order across rows
    seen: dict[str, None] = {}
    for row in payload:
        for key in row:
            seen[key] = None
    columns = list(seen.keys())

    # Column widths
    widths = {col: len(col) for col in columns}
    for row in payload:
        for col in columns:
            widths[col] = max(widths[col], len(_format_scalar(row.get(col, ""))))

    header = " | ".join(col.ljust(widths[col]) for col in columns)
    sep = "-+-".join("-" * widths[col] for col in columns)
    body = "\n".join(
        " | ".join(_format_scalar(row.get(col, "")).ljust(widths[col]) for col in columns)
        for row in payload
    )
    return f"{header}\n{sep}\n{body}"


def _format_scalar(value: Any) -> str:
    """Stable string representation for table/text rendering."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        # Avoid scientific notation for "nice" small numbers
        if 1e-4 <= abs(value) < 1e16 or value == 0.0:
            return repr(value)
        return f"{value:.6g}"
    return str(value)


__all__ = ["OutputFormatError", "render"]
