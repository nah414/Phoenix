"""Prompt-disposition canonicalization + hashing (Phase 13 Step 8).

Per 13-D2 (locked 2026-05-18): the default ledger posture for
cognition prompts is ``HASH_ONLY`` — Phoenix stores only the
SHA-256 of the canonicalized prompt, never the prompt body itself.
``VERBATIM`` and ``ENCRYPTED_OPT_IN`` are explicit opt-ins that
require admin Actor permission (Step 9 wires
``can_store_prompt_verbatim`` and ``can_store_prompt_encrypted``).

This module provides the canonicalization + hashing helpers that the
cognition wobble axes (Step 4) and the Omega Ledger replay engine
(Phase 7) use to:

1. Compute the deterministic SHA-256 fingerprint stored in the
   ``prompt_hash`` column (added by the Step 8 migration).
2. Verify, on replay, that a reissued prompt matches the original.

**Canonical form** (load-bearing for replay verification):

The canonical encoding is a JSON document with::

    {"system": <system or null>, "messages": [<msg>, ...]}

where:

- ``sort_keys=True`` makes the encoding key-order-invariant.
- ``ensure_ascii=True`` makes the encoding Python-version-invariant
  (no Unicode surrogate variation).
- ``separators=(",", ":")`` strips inter-token whitespace.
- ``allow_nan=False`` rejects NaN/Infinity (which are not JSON).
- Each message dict is recursively normalized (sorted keys at every
  level; tool_use/tool_result inputs get the same treatment).
- Whitespace inside *text content* (the human-language string the
  model sees) is normalized: leading/trailing trim + collapse runs
  of internal whitespace to a single space. This is the load-bearing
  invariant for prompts that arrive with platform-different line
  endings (CRLF vs LF) or trailing-whitespace artifacts from
  copy-paste; without normalization, the same prompt hashes
  differently on Windows vs Linux.

**Deliberately excluded from the hash:**

- :attr:`Prompt.metadata` — audit fields (actor_id, task_id) that
  vary across runs but don't change the prompt's semantic content.
  Including metadata in the hash would make replay verification
  spuriously fail when, e.g., the same prompt is replayed by a
  different actor for forensic review.
- Tool definitions (a :class:`Prompt` doesn't carry them; the
  :func:`canonicalize_call` helper hashes the (prompt, tools, model,
  temperature) tuple separately for cases that need full call
  fingerprinting).

**SAFETY:** This module performs no I/O and no permission checks.
The caller (the wobble axis / ledger append path) is responsible for
checking the actor's ``can_store_prompt_verbatim`` permission BEFORE
serializing a prompt verbatim to the ledger. The hashing path is
permission-free because the SHA-256 of a prompt is not
prompt-revealing.
"""

from __future__ import annotations

import enum
import hashlib
import json
import re
from typing import Any, Final

from phoenix.providers.cognition.types import Prompt

# ---------------------------------------------------------------------------
# Disposition taxonomy


class PromptDisposition(str, enum.Enum):
    """How Phoenix may store a cognition prompt in the Omega Ledger.

    Per 13-D2, the three values map to escalating privacy postures:

    - :attr:`HASH_ONLY` (default): only the SHA-256 of the
      canonicalized prompt is stored. Replay cannot reconstruct the
      prompt; it can only verify a reissued prompt matches.
    - :attr:`VERBATIM`: the canonicalized prompt JSON is stored in
      cleartext in the ``prompt_verbatim`` column. Requires
      ``can_store_prompt_verbatim`` (Step 9). Replay can re-invoke the
      cognition provider with the stored prompt for bit-exact
      regeneration (deterministic providers) or
      ``non_deterministic_replay``-flagged regeneration (non-det).
    - :attr:`ENCRYPTED_OPT_IN`: the canonicalized prompt is encrypted
      and stored in the ``prompt_encrypted`` BLOB column. Phase 13
      ships the column + the Protocol; the customer-key-management
      ceremony lands later (deferred per [OPEN: P13-3] default).
      Until the ceremony lands, attempting this disposition raises
      :class:`~phoenix.ledger.encryption.EncryptedDispositionNotConfigured`.

    The enum's string-value pattern (``str, Enum``) makes JSON
    serialization a no-op (``PromptDisposition.HASH_ONLY ==
    "HASH_ONLY"``) and lets SQLite-typed comparison work directly.
    """

    HASH_ONLY = "HASH_ONLY"
    """SHA-256 only; the load-bearing default."""

    VERBATIM = "VERBATIM"
    """Cleartext canonicalized prompt; requires opt-in permission."""

    ENCRYPTED_OPT_IN = "ENCRYPTED_OPT_IN"
    """Encrypted-at-rest; requires opt-in permission + key-mgmt ceremony."""


DEFAULT_DISPOSITION: Final[PromptDisposition] = PromptDisposition.HASH_ONLY
"""The 13-D2-locked default for any code path that didn't choose."""


# ---------------------------------------------------------------------------
# Canonicalization


_WS_RUN: Final[re.Pattern[str]] = re.compile(r"\s+")


def _normalize_text(text: str) -> str:
    """Normalize whitespace inside a text content string.

    - Trim leading + trailing whitespace.
    - Collapse runs of internal whitespace (space, tab, newline, CR)
      to a single space.

    This is the load-bearing invariant for cross-OS prompt-hash
    equality: CRLF vs LF line endings, trailing copy-paste spaces,
    and stray tabs all normalize to the same form.
    """
    return _WS_RUN.sub(" ", text).strip()


def _normalize_value(value: Any) -> Any:
    """Recursively normalize a JSON-shaped value.

    - ``dict`` → new dict with the same keys; each value normalized
      recursively. (Key sorting is done at JSON-encoding time via
      ``sort_keys=True``; we don't reorder here so the function stays
      O(n) and lossless.)
    - ``list`` → new list of normalized values (order preserved).
    - ``str`` → whitespace-normalized via :func:`_normalize_text`.
    - Anything else (int/float/bool/None) → passed through.

    Recursion is bounded by the JSON spec's natural shape; Phoenix
    prompts are at most a few KB so stack depth is not a concern.
    """
    if isinstance(value, dict):
        return {k: _normalize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_value(v) for v in value]
    if isinstance(value, str):
        return _normalize_text(value)
    return value


def canonicalize_prompt(prompt: Prompt) -> str:
    """Encode ``prompt`` as the deterministic canonical JSON.

    Returns the canonical JSON string that :func:`hash_prompt` SHA-256s
    and that ``VERBATIM`` disposition stores in the
    ``prompt_verbatim`` column.

    The canonical form is::

        {"messages": [<msg>, ...], "system": <system or null>}

    The ``metadata`` field on :class:`Prompt` is **deliberately
    omitted** — see module docstring for the rationale.

    Idempotency: ``canonicalize_prompt(p) ==
    canonicalize_prompt(canonicalize_back(canonicalize_prompt(p)))``
    for any round-trippable ``p``.

    Whitespace invariance: prompts that differ only in line endings
    (CRLF vs LF) or in runs of internal whitespace produce the same
    canonical string.

    Key-order invariance: ``{"a": 1, "b": 2}`` and ``{"b": 2, "a": 1}``
    canonicalize identically.
    """
    payload: dict[str, Any] = {
        "system": _normalize_text(prompt.system) if prompt.system is not None else None,
        "messages": [_normalize_value(msg) for msg in prompt.messages],
    }
    return json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    )


def hash_prompt(prompt: Prompt) -> str:
    """SHA-256 the canonical encoding; return hex.

    Returns a 64-character lowercase hex string. This is what
    ``prompt_disposition=HASH_ONLY`` writes to the ``prompt_hash``
    column.

    On replay, :func:`hash_prompt` is recomputed for the reissued
    prompt and compared byte-by-byte against the stored hash. A match
    proves the same prompt was issued (up to canonicalization
    invariance); a mismatch means the chain has been tampered with or
    the caller used a different prompt.
    """
    canonical = canonicalize_prompt(prompt)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def hash_canonical_form(canonical_json: str) -> str:
    """Helper for callers that already have the canonical string.

    Used by the replay engine's strict-mode path: it stores the
    canonical JSON (when disposition=VERBATIM) and re-hashes that
    canonical form for cross-verification without re-running the full
    canonicalization pipeline.
    """
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Validation helpers


def parse_disposition(value: str | None) -> PromptDisposition:
    """Parse a string into a :class:`PromptDisposition`.

    Returns the :data:`DEFAULT_DISPOSITION` (``HASH_ONLY``) for
    ``None`` input. Raises :class:`ValueError` for an unrecognized
    string; the caller (typically the request-decoder or the ledger
    read path) maps this to an HTTP 400 or a corruption-detected
    audit entry depending on origin.
    """
    if value is None:
        return DEFAULT_DISPOSITION
    try:
        return PromptDisposition(value)
    except ValueError as exc:
        valid = ", ".join(d.value for d in PromptDisposition)
        raise ValueError(f"Unknown prompt_disposition {value!r}; valid values: {valid}") from exc


__all__ = [
    "PromptDisposition",
    "DEFAULT_DISPOSITION",
    "canonicalize_prompt",
    "hash_prompt",
    "hash_canonical_form",
    "parse_disposition",
]
