#!/usr/bin/env python3
"""
Dr. Frank & Eddy v6.4.1 -- Sanskrit Codec Glyph Permutation (Phase SEC.1)

Deterministic Fisher-Yates shuffle of the target-glyph pool keyed by the
HKDF-derived permutation seed.

After encode's rule layers emit canonical glyphs (drawn from the fixed
_PHRASES / _OPERATORS / _KEYWORDS tables), those glyphs are remapped
through a per-install bijection: canonical -> keyed. Decode reverses.

Properties:
  - Bijection: each canonical glyph maps to exactly one keyed glyph; no
    collisions; inverse table is complete.
  - Determinism: same seed twice -> same forward/inverse tables.
  - No glyph escapes the pool: the keyed-glyph space equals the canonical
    pool, just shuffled. This keeps round-trip lossless under any
    composition of encode/decode.

The permutation pool is computed once per derive call by the caller that
passes in the canonical glyph list. We don't cache across calls (keys are
ephemeral per the SEC.1 key-handling policy).
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from typing import Iterable


PERMUTATION_VERSION = "v1"


@dataclass(frozen=True)
class GlyphPermutation:
    """Forward and inverse glyph remappings for a single key + pool.

    Both dicts cover exactly the canonical pool. Glyphs not in the pool
    (arbitrary text characters) pass through encode/decode untouched.
    """
    forward: dict[str, str]    # canonical_glyph -> keyed_glyph
    inverse: dict[str, str]    # keyed_glyph -> canonical_glyph
    version: str = PERMUTATION_VERSION

    def __post_init__(self):
        # Invariants: bijection + same pool on both sides.
        assert len(self.forward) == len(self.inverse), (
            "permutation is not a bijection: forward/inverse sizes differ"
        )
        assert set(self.forward.values()) == set(self.inverse.keys()), (
            "permutation is not a bijection: forward values != inverse keys"
        )

    def apply_forward(self, text: str) -> str:
        """Replace each canonical glyph in text with its keyed glyph.
        Non-pool characters pass through unchanged."""
        if not self.forward:
            return text
        # Build a translation table for single-char keys (fast path).
        # For multi-char glyphs we fall back to a loop.
        single_char = {k: v for k, v in self.forward.items() if len(k) == 1 and len(v) == 1}
        multi_char = {k: v for k, v in self.forward.items()
                      if k not in single_char}
        out = text
        if single_char:
            trans = str.maketrans(single_char)
            out = out.translate(trans)
        for src, dst in multi_char.items():
            out = out.replace(src, dst)
        return out

    def apply_inverse(self, text: str) -> str:
        """Reverse of apply_forward."""
        if not self.inverse:
            return text
        single_char = {k: v for k, v in self.inverse.items() if len(k) == 1 and len(v) == 1}
        multi_char = {k: v for k, v in self.inverse.items()
                      if k not in single_char}
        out = text
        if single_char:
            trans = str.maketrans(single_char)
            out = out.translate(trans)
        for src, dst in multi_char.items():
            out = out.replace(src, dst)
        return out


def _seeded_rng(seed: bytes):
    """Deterministic PRNG for Fisher-Yates. We use a simple SHA-256-based
    stream so we don't depend on `random.Random(seed)` semantics across
    Python versions. Returns a generator yielding unsigned 64-bit ints."""
    counter = 0
    buffer = b""
    while True:
        if not buffer:
            counter_bytes = counter.to_bytes(8, "big")
            buffer = hashlib.sha256(seed + counter_bytes).digest()
            counter += 1
        word = struct.unpack_from(">Q", buffer, 0)[0]
        buffer = buffer[8:]
        yield word


def fisher_yates_shuffle(items: list, seed: bytes) -> list:
    """Deterministic Fisher-Yates shuffle. Returns a NEW list; input not
    modified. Same (items, seed) always produces the same ordering."""
    rng = _seeded_rng(seed)
    out = list(items)
    n = len(out)
    for i in range(n - 1, 0, -1):
        # Draw a word, mod (i+1) for unbiased-ish index selection.
        # With 64-bit draws and small n, rejection bias is negligible.
        j = next(rng) % (i + 1)
        out[i], out[j] = out[j], out[i]
    return out


def build_permutation(canonical_glyphs: Iterable[str], seed: bytes) -> GlyphPermutation:
    """Build a bijective permutation over the canonical glyph pool.

    The pool is deduplicated (so tables containing the same glyph under
    multiple source phrases still produce a sane permutation). The
    shuffled ordering is derived deterministically from seed; we then map
    canonical[i] -> shuffled[i].

    Edge case: empty pool -> empty permutation, no-op on apply.
    """
    pool = sorted(set(canonical_glyphs))   # sorted for stability
    if not pool:
        return GlyphPermutation(forward={}, inverse={})

    shuffled = fisher_yates_shuffle(pool, seed)

    forward: dict[str, str] = {}
    inverse: dict[str, str] = {}
    for canonical, keyed in zip(pool, shuffled):
        forward[canonical] = keyed
        inverse[keyed] = canonical

    return GlyphPermutation(forward=forward, inverse=inverse)


__all__ = [
    "PERMUTATION_VERSION",
    "GlyphPermutation",
    "fisher_yates_shuffle",
    "build_permutation",
]
