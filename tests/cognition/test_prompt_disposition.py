"""Tests for :mod:`phoenix.ledger.prompt_disposition` (Phase 13 Step 8).

Per 13-D2 (locked 2026-05-18): the canonicalization + hashing helpers
are the load-bearing privacy primitive for cognition prompt storage.
These tests verify the three invariants the build guide pins:

- Idempotency: ``canonicalize(canonicalize(p)) == canonicalize(p)``
  (operating on the equivalent re-parsed form).
- Whitespace invariance: prompts that differ only in line endings or
  internal whitespace produce equal canonical forms / hashes.
- Key-order invariance: dicts with the same keys in different orders
  produce equal canonical forms / hashes.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from phoenix.ledger.prompt_disposition import (
    DEFAULT_DISPOSITION,
    PromptDisposition,
    canonicalize_prompt,
    hash_canonical_form,
    hash_prompt,
    parse_disposition,
)
from phoenix.providers.cognition.types import Prompt


# ---------------------------------------------------------------------------
# Disposition enum


def test_default_disposition_is_hash_only() -> None:
    """13-D2: HASH_ONLY is the load-bearing default."""
    assert DEFAULT_DISPOSITION is PromptDisposition.HASH_ONLY


def test_disposition_string_values() -> None:
    """The Enum's string values are stable wire-format tokens."""
    assert PromptDisposition.HASH_ONLY.value == "HASH_ONLY"
    assert PromptDisposition.VERBATIM.value == "VERBATIM"
    assert PromptDisposition.ENCRYPTED_OPT_IN.value == "ENCRYPTED_OPT_IN"


def test_parse_disposition_none_returns_default() -> None:
    assert parse_disposition(None) is PromptDisposition.HASH_ONLY


def test_parse_disposition_known_values() -> None:
    assert parse_disposition("HASH_ONLY") is PromptDisposition.HASH_ONLY
    assert parse_disposition("VERBATIM") is PromptDisposition.VERBATIM
    assert parse_disposition("ENCRYPTED_OPT_IN") is PromptDisposition.ENCRYPTED_OPT_IN


def test_parse_disposition_unknown_value_raises() -> None:
    with pytest.raises(ValueError, match="Unknown prompt_disposition"):
        parse_disposition("PLAINTEXT_FOREVER")


# ---------------------------------------------------------------------------
# canonicalize_prompt


def test_canonicalize_basic_shape() -> None:
    """The canonical form is a JSON document with system + messages keys."""
    p = Prompt(
        system="You are helpful.",
        messages=[{"role": "user", "content": "Hello"}],
    )
    out = canonicalize_prompt(p)
    parsed = json.loads(out)
    assert set(parsed.keys()) == {"system", "messages"}
    assert parsed["system"] == "You are helpful."
    assert parsed["messages"][0]["role"] == "user"


def test_canonicalize_system_none_is_json_null() -> None:
    """A None system serializes to JSON null, not the string 'None'."""
    p = Prompt(system=None, messages=[{"role": "user", "content": "x"}])
    out = canonicalize_prompt(p)
    parsed = json.loads(out)
    assert parsed["system"] is None


def test_canonicalize_excludes_metadata() -> None:
    """Metadata (actor_id / task_id audit fields) MUST NOT enter the hash."""
    p = Prompt(
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        metadata={"actor_id": "adam", "task_id": "tsk_1"},
    )
    out = canonicalize_prompt(p)
    assert "actor_id" not in out
    assert "task_id" not in out
    assert "metadata" not in out


def test_canonicalize_key_order_invariance() -> None:
    """Dicts with same keys in different orders produce identical output."""
    p1 = Prompt(
        system="s",
        messages=[{"role": "user", "content": "x"}],
    )
    # Same content, different key order (constructed via dict literal).
    p2 = Prompt(
        system="s",
        messages=[{"content": "x", "role": "user"}],
    )
    assert canonicalize_prompt(p1) == canonicalize_prompt(p2)


def test_canonicalize_whitespace_invariance_crlf_vs_lf() -> None:
    """CRLF vs LF line endings normalize to the same canonical form."""
    p_lf = Prompt(system=None, messages=[{"role": "user", "content": "a\nb"}])
    p_crlf = Prompt(system=None, messages=[{"role": "user", "content": "a\r\nb"}])
    # Both collapse the run of newline chars to a single space.
    assert canonicalize_prompt(p_lf) == canonicalize_prompt(p_crlf)


def test_canonicalize_whitespace_invariance_internal_runs() -> None:
    """Runs of spaces/tabs collapse to a single space."""
    p_one_space = Prompt(system=None, messages=[{"role": "user", "content": "foo bar"}])
    p_many_spaces = Prompt(
        system=None,
        messages=[{"role": "user", "content": "foo   \t  bar"}],
    )
    assert canonicalize_prompt(p_one_space) == canonicalize_prompt(p_many_spaces)


def test_canonicalize_whitespace_invariance_leading_trailing() -> None:
    """Leading and trailing whitespace get stripped."""
    p_clean = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])
    p_padded = Prompt(system=None, messages=[{"role": "user", "content": "  hi  "}])
    assert canonicalize_prompt(p_clean) == canonicalize_prompt(p_padded)


def test_canonicalize_system_whitespace_normalized() -> None:
    """The system prompt also gets whitespace normalization."""
    p_clean = Prompt(system="be nice", messages=[])
    p_messy = Prompt(system="  be   nice  ", messages=[])
    assert canonicalize_prompt(p_clean) == canonicalize_prompt(p_messy)


def test_canonicalize_no_inter_token_whitespace() -> None:
    """The JSON output uses compact separators (no spaces around colons / commas)."""
    p = Prompt(system="s", messages=[{"role": "user", "content": "x"}])
    out = canonicalize_prompt(p)
    assert ": " not in out
    assert ", " not in out


def test_canonicalize_ensure_ascii_true() -> None:
    """Unicode chars escape to \\uXXXX form so encoding is Python-version-stable."""
    p = Prompt(system="café", messages=[])
    out = canonicalize_prompt(p)
    # The é (U+00E9) escapes to é with ensure_ascii=True.
    assert "\\u00e9" in out
    # And the raw byte is NOT present.
    assert "é" not in out


def test_canonicalize_idempotency_via_reparsed_form() -> None:
    """Canonicalizing the result via Prompt-reconstruction matches the original."""
    p = Prompt(
        system="sys",
        messages=[{"role": "user", "content": "hello\nworld"}],
    )
    canonical = canonicalize_prompt(p)
    parsed = json.loads(canonical)
    # Reconstruct a Prompt from the canonical JSON and re-canonicalize.
    p_round = Prompt(system=parsed["system"], messages=parsed["messages"])
    assert canonicalize_prompt(p_round) == canonical


def test_canonicalize_nested_dict_normalization() -> None:
    """Nested dicts inside message content (e.g. tool_use input) normalize too."""
    p1 = Prompt(
        system=None,
        messages=[
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "search", "input": {"q": "x"}}],
            }
        ],
    )
    # Same content but with whitespace artifacts in the query string.
    p2 = Prompt(
        system=None,
        messages=[
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "search", "input": {"q": "  x  "}}],
            }
        ],
    )
    assert canonicalize_prompt(p1) == canonicalize_prompt(p2)


# ---------------------------------------------------------------------------
# hash_prompt + hash_canonical_form


def test_hash_prompt_is_64_char_hex() -> None:
    p = Prompt(system=None, messages=[{"role": "user", "content": "x"}])
    h = hash_prompt(p)
    assert len(h) == 64
    # Lowercase hex.
    assert all(c in "0123456789abcdef" for c in h)


def test_hash_prompt_matches_sha256_of_canonical_form() -> None:
    """The hash is exactly SHA-256(canonical_form.encode("utf-8"))."""
    p = Prompt(system="sys", messages=[{"role": "user", "content": "hi"}])
    canonical = canonicalize_prompt(p)
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert hash_prompt(p) == expected


def test_hash_prompt_stable_across_equivalent_prompts() -> None:
    """Whitespace + key-order variants hash to the same value."""
    p1 = Prompt(system="sys", messages=[{"role": "user", "content": "hello world"}])
    p2 = Prompt(
        system="sys",
        messages=[{"content": "hello   world", "role": "user"}],
    )
    assert hash_prompt(p1) == hash_prompt(p2)


def test_hash_prompt_changes_for_different_content() -> None:
    """Semantically-different prompts produce different hashes."""
    p1 = Prompt(system=None, messages=[{"role": "user", "content": "alice"}])
    p2 = Prompt(system=None, messages=[{"role": "user", "content": "bob"}])
    assert hash_prompt(p1) != hash_prompt(p2)


def test_hash_prompt_metadata_doesnt_affect_hash() -> None:
    """Metadata changes do NOT change the hash."""
    p1 = Prompt(
        system=None,
        messages=[{"role": "user", "content": "x"}],
        metadata={"actor_id": "a"},
    )
    p2 = Prompt(
        system=None,
        messages=[{"role": "user", "content": "x"}],
        metadata={"actor_id": "b", "task_id": "t"},
    )
    assert hash_prompt(p1) == hash_prompt(p2)


def test_hash_canonical_form_matches_hash_prompt() -> None:
    """hash_canonical_form on a canonical string matches hash_prompt on the Prompt."""
    p = Prompt(system="s", messages=[{"role": "user", "content": "hi"}])
    canonical = canonicalize_prompt(p)
    assert hash_canonical_form(canonical) == hash_prompt(p)
