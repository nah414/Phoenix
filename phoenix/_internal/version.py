"""Phoenix version constant and vendor-version reader.

Phase 0 ships the version constant. The vendor-version reader returns the
parsed contents of `vendor/VENDOR_VERSION.txt` once that file exists (Phase 0
Step 3 creates it as a placeholder; Phase 1 populates it with real hashes).
The reader returns None when the file is absent.
"""

from __future__ import annotations

from pathlib import Path

__version__ = "1.0.0.dev9"

VENDOR_VERSION_FILE = Path(__file__).parent.parent.parent / "vendor" / "VENDOR_VERSION.txt"


def read_vendor_version() -> dict[str, str] | None:
    """Return the parsed contents of `vendor/VENDOR_VERSION.txt`, or None if absent.

    Each `key: value` line in the file becomes a dict entry (whitespace-trimmed,
    blanks and `#`-comments ignored). Phase 0's placeholder ships with all
    values empty; Phase 1's vendor sync populates them with real hashes.
    """
    if not VENDOR_VERSION_FILE.exists():
        return None
    text = VENDOR_VERSION_FILE.read_text(encoding="utf-8")
    parsed: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        parsed[key.strip()] = value.strip()
    return parsed
