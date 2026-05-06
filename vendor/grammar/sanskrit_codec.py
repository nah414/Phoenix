#!/usr/bin/env python3
"""
Dr. Frank & Eddy v6.4.1 -- Sanskrit Codec (Phase E2 + Phase SEC.1)

Rule-based symbolic compressor + keyed permutation + AES-GCM-256 sealing.
The goal is not literal Sanskrit but the principle behind it: compact,
unambiguous, structurally regular notation. Three rule layers + a
security wrap:

  1. Phrase layer   -- multi-word idioms -> single-symbol tokens
                       (e.g. "for all" -> U+2200, "if and only if" -> U+21D4)
  2. Operator layer -- arithmetic / logic / set / quantum operators
                       -> unicode math symbols (>=, <=, !=, ->, etc.)
  3. Keyword layer  -- common technical keywords collapse to 1-2 char glyphs
                       (function -> f, return -> r, however -> H, because -> B)
  4. SEC.1 wrap     -- keyed glyph permutation + AES-GCM-256 seal over the
                       canonical compressed text. Output wire format:
                       "sks1:" + base64url(nonce || ciphertext || tag).
                       Keys derived per call from the install's BB84
                       Ed25519 identity via HKDF-SHA256. Not cached.

Rule substitutions are **reversible**: each canonical glyph maps to
exactly one source phrase. Under the sealed pipeline, decode detects the
"sks1:" prefix and unwraps; legacy v0 plaintext input (no prefix) still
decodes through the v0 inverse-rules path for backward compatibility.

Metrics (EncodeResult):
  - compression_ratio   = source bytes / wire bytes (post-seal, what you ship)
  - canonical_ratio     = source bytes / canonical_compressed bytes (pre-seal)
  - compressed_length   = wire length
  - canonical_length    = pre-seal length
  - encryption_mode     = "sealed" (default) or "plaintext" (debug)

Version strings in the output identify the codec revision so old content
remains decodable when a future revision lands.
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .codec_crypto import (
    IdentityNotInitialisedError,
    InstallIdentity,
    SealError,
    build_aad,
    derive_keys,
    plaintext_mode_enabled,
    seal as _aead_seal,
    unseal as _aead_unseal,
    zeroize,
)
from .codec_permutation import (
    PERMUTATION_VERSION,
    GlyphPermutation,
    build_permutation,
)

log = logging.getLogger("frankenstein.evolution.knowledge.sanskrit_codec")

# Bumped in SEC.1 to "sanskrit-v1-sealed", again in Phase 1.2 to
# "sanskrit-v2-morphemes", in Phase 2.1 to "sanskrit-v3-classes", and
# in Phase 2.2 to "sanskrit-v4-sandhi". Decoders detect the wire-format
# prefix (see SEALED_PREFIX) to tell legacy v0 plaintext from v1+
# sealed; the morpheme / class / sandhi layers are transparent to the
# sealing logic because they run inside the canonical rule chain
# before encryption.
#
# Phase 2.2: sandhi layer consumes the class vocabulary defined in
# Phase 2.1 to collapse multi-word physics phrases into single
# compound glyphs. Runs FIRST in forward (before phrase/operator/
# keyword/morpheme) and FIRST in inverse (before any glyph-level
# substitution), so its compound prefixes (which reuse chars from
# other rule tables) are consumed before downstream inverses can
# fragment them. Wire format unchanged from v3; rule-table grew.
CODEC_VERSION = "sanskrit-v4-sandhi"
LEGACY_CODEC_VERSION_V0 = "sanskrit-v0-rule"
LEGACY_CODEC_VERSION_V1 = "sanskrit-v1-sealed"
LEGACY_CODEC_VERSION_V2 = "sanskrit-v2-morphemes"
LEGACY_CODEC_VERSION_V3 = "sanskrit-v3-classes"

# Compound separator for morpheme layer. Must NOT collide with any target
# glyph in _PHRASES / _OPERATORS / _KEYWORDS / _MORPHEMES. Diamond
# operator (U+22C4) chosen because it is rare in English prose and not
# in any existing rule table.
COMPOUND_SEP = "\u22C4"  # ⋄

# Escape marker prefixed to any target glyph that ALREADY APPEARS in
# source text before encoding (see _escape_source_glyphs). Without this,
# a source text containing "≥" would round-trip through decode's
# _PHRASES inverse pass and come back as "greater than or equal to" --
# there would be no way to tell "source ≥" apart from "substituted ≥".
#
# SOH (U+0001) is the choice because it is a C0 control character that
# is never used in prose, code, markdown, or any text we would ingest.
# If a future corpus file somehow contains SOH, we will need to pick
# another marker; an invariant test flags its absence from the seed
# corpus at load time.
ESCAPE_MARKER = "\u0001"

# Wire-format prefix for sealed blobs. Readers use this to route between
# the sealed path and the legacy v0 plaintext path.
SEALED_PREFIX = "sks1:"


# -----------------------------------------------------------------------
# Rule tables. Keep each table ordered by longest-source-first so greedy
# replacement doesn't mis-bind shorter substrings embedded in longer ones.
# -----------------------------------------------------------------------

# Rule layer 1: multi-word phrases -> single symbol.
# Pairs are (source_phrase, target_glyph). Whitespace is normalised first.
_PHRASES: List[Tuple[str, str]] = [
    ("if and only if",               "\u21D4"),  # <=>
    ("for all",                      "\u2200"),  # ?
    ("there exists",                 "\u2203"),  # ?
    ("such that",                    "\u220D"),  # ?
    ("element of",                   "\u2208"),  # ?
    ("subset of",                    "\u2286"),  # ?
    ("in other words",               "\u2261"),  # =
    ("greater than or equal to",     "\u2265"),  # =
    ("less than or equal to",        "\u2264"),  # =
    ("not equal to",                 "\u2260"),  # ?
    ("much greater than",            "\u226B"),  # ?
    ("much less than",               "\u226A"),  # ?
    ("approximately equal to",       "\u2248"),  # ~
    ("proportional to",              "\u221D"),  # ?
    ("tensor product",               "\u2297"),  # ?
    ("direct sum",                   "\u2295"),  # ?
    ("partial derivative",           "\u2202"),  # ?
    ("square root",                  "\u221A"),  # v
    ("summation",                    "\u2211"),  # S
    ("product",                      "\u220F"),  # ?
    ("integral",                     "\u222B"),  # ?
    ("infinity",                     "\u221E"),  # 8
    ("therefore",                    "\u2234"),  # ?
    ("because",                      "\u2235"),  # ?
]


# Rule layer 2: short operators -> unicode math symbols.
# Glyphs MUST NOT overlap with any target in _PHRASES above, otherwise
# decode becomes ambiguous. Colliding operators (>=, <=, !=, <=>) are
# intentionally omitted here because the phrase layer already covers
# their long forms via unique glyphs.
_OPERATORS: List[Tuple[str, str]] = [
    ("==",    "\u225D"),  # defined equal
    ("->",    "\u2192"),  # rightwards arrow
    ("<-",    "\u2190"),  # leftwards arrow
    ("<->",   "\u2194"),  # left-right arrow
    ("=>",    "\u21D2"),  # rightwards double arrow
    ("&&",    "\u2227"),  # logical and
    ("||",    "\u2228"),  # logical or
    ("+/-",   "\u00B1"),  # plus-minus
]


# Rule layer 3: frequent technical keywords -> short glyphs.
# Wrapped with word boundaries at replace time so we don't eat partial words.
# Rule layer 3: frequent technical keywords -> short glyphs.
# Every target glyph MUST be unique across this table (otherwise decode
# maps ambiguously to whichever key is iterated first) and MUST NOT
# overlap any target used in _PHRASES above. The tests enforce both
# invariants.
_KEYWORDS: Dict[str, str] = {
    "function":    "\u0192",   # f with hook
    "return":      "\u21B5",   # downwards arrow with corner
    "however":     "\u29B5",   # circle with horizontal bar
    # "therefore" is covered by the phrase layer -- keep only there.
    "example":     "\u2EB9",   # kangxi-variant glyph
    "theorem":     "\u0398",   # capital theta
    "proof":       "\u03C0",   # lower-case pi
    "lemma":       "\u039B",   # capital lambda
    "definition":  "\u0394",   # capital delta
    "corollary":   "\u0393",   # capital gamma
    "note":        "\u00B6",   # pilcrow
    "quantum":     "\u03A8",   # capital psi
    "classical":   "\u03C7",   # lower-case chi
    "entangled":   "\u0246",   # latin capital E with stroke
    "operator":    "\u00F4",   # o-circumflex
    "eigenvalue":  "\u03BB",   # lower-case lambda
    "eigenvector": "\u03D1",   # greek theta symbol (variant)
    "probability": "\u2119",   # double-struck P
}

# Defensive filter (drop empty glyphs) happens inside
# `_rebuild_module_indices` along with all other derived tables.


# Rule layer 4 (Phase 1.2): physics morphemes -> short glyphs, composable.
#
# This layer decomposes WORDS (not whole phrases) into morphemes.
# "eigenstate" -> [eigen][state] -> "𝜆⋄ψ" where ⋄ is the compound
# separator. If a word covers exactly one morpheme, it is NOT encoded
# here (single-morpheme words either collide with _KEYWORDS entries
# that get first-pass priority, or are too short to benefit).
#
# Design constraints (see BUILDGUIDE_SANSKRIT_TIER1.md Phase 1.2):
#   - No target glyph may collide with any target in _PHRASES /
#     _OPERATORS / _KEYWORDS, or with another morpheme target. Tests
#     enforce this.
#   - No single-Latin-letter targets -- they round-trip incorrectly on
#     any plain prose containing that letter. We use Unicode math-alphabet
#     variants (script, italic, double-struck) which share *no* code
#     points with regular Latin letters.
#   - Composites (e.g. "laplacian" -> "∇²") are permitted as long as
#     they are ordered LONGER-FIRST so a decode of "∇²" is unambiguous
#     and not consumed by "∇" (gradient) before being matched as a
#     laplacian.
#
# Families (for pratyāhāra work in Phase 2.1, which will group morphemes
# into classes for sandhi rules):
#   Ψ-family   -- quantum concepts
#   ∂-family   -- differential operators
#   E-family   -- energy-like scalars
#   V-family   -- vector kinematics
#   ✱-family   -- fundamental constants
#   θ-family   -- thermodynamics
#   μ-family   -- measurement / statistics
#   𝔸-family   -- algebraic structures

# Ordered dict: composite (multi-char) targets are listed FIRST so the
# longest-match-first encode loop and decode regex both see them before
# any single-char prefix could bind. The decoder uses a regex built
# from `_MORPHEMES_INVERSE.keys()` in longest-first order.
_MORPHEMES: List[Tuple[str, str]] = [
    # --- Composites first (longest match) ------------------------------
    ("laplacian",      "\u2207\u00B2"),    # ∇²  (∂-family)
    ("divergence",     "\u2207\u00B7"),    # ∇·  (∂-family)
    ("curl",           "\u2207\u00D7"),    # ∇×  (∂-family)
    ("displacement",   "\u0394\U0001D4CD"),# Δ𝓍  (V-family)

    # --- Ψ-family (quantum concepts) -----------------------------------
    ("eigen",          "\U0001D706"),  # 𝜆  italic lambda -- distinct from _KEYWORDS eigenvalue -> λ
    ("wave",           "\u223F"),      # ∿  sine wave
    ("state",          "\u03C8"),      # ψ  Greek psi
    ("function",       "\U0001D4BB"),  # 𝒻  math script f -- distinct from _KEYWORDS function -> ƒ
    ("bra",            "\u27E8"),      # ⟨  mathematical left angle bracket
    ("ket",            "\u27E9"),      # ⟩  mathematical right angle bracket
    ("hermitian",      "\u210B"),      # ℋ  script H
    ("unitary",        "\U0001D4B0"),  # 𝒰  math script U
    ("observable",     "\U0001D4AA"),  # 𝒪  math script O
    ("hamiltonian",    "\u0124"),      # Ĥ  H with circumflex

    # --- ∂-family atomic (composites above) ----------------------------
    ("gradient",       "\u2207"),      # ∇  nabla
    ("partial",        "\U0001D715"),  # 𝜕  math italic partial differential (distinct from _PHRASES "partial derivative" -> ∂)
    ("derivative",     "\U0001D4B9"),  # 𝒹  math script d

    # --- E-family (energy-like scalars) --------------------------------
    ("energy",         "\u2130"),      # ℰ  script E
    ("potential",      "\U0001D4B1"),  # 𝒱  script V
    ("kinetic",        "\U0001D4A6"),  # 𝒦  script K
    ("amplitude",      "\U0001D49C"),  # 𝒜  script A
    ("phase",          "\u03C6"),      # φ  Greek phi
    ("frequency",      "\u03BD"),      # ν  Greek nu
    ("wavelength",     "\U0001D714"),  # 𝜔  math italic omega

    # --- V-family atomic (composite above) -----------------------------
    ("position",       "\U0001D4CD"),  # 𝓍  math script x
    ("velocity",       "\U0001D4CB"),  # 𝓋  math script v
    ("acceleration",   "\U0001D4B6"),  # 𝒶  math script a
    ("momentum",       "\U0001D4C5"),  # 𝓅  math script p

    # --- ✱-family (fundamental constants) ------------------------------
    ("hbar",           "\u210F"),      # ℏ  h with stroke
    ("speedoflight",   "\U0001D450"),  # 𝑐  math italic c
    ("boltzmann",      "\U0001D458"),  # 𝑘  math italic k
    ("electroncharge", "\U0001D452"),  # 𝑒  math italic e
    ("finestructure",  "\u03B1"),      # α  alpha

    # --- θ-family (thermodynamics) -------------------------------------
    ("entropy",        "\U0001D4AE"),  # 𝒮  math script S
    ("temperature",    "\u03F4"),      # ϴ  Greek capital theta symbol (distinct from _KEYWORDS theorem -> Θ U+0398)
    ("pressure",       "\U0001D4AB"),  # 𝒫  math script P
    ("volume",         "\U0001D54D"),  # 𝕍  double-struck V
    ("mass",           "\U0001D4C2"),  # 𝓂  math script m

    # --- μ-family (statistics / measurement) ---------------------------
    ("density",        "\u03C1"),      # ρ  Greek rho
    ("expected",       "\U0001D53C"),  # 𝔼  double-struck E
    ("variance",       "\u03C3\u00B2"),# σ²  sigma squared (composite but atomic within morphemes)
    ("commutator",     "\u229E"),      # ⊞  squared plus

    # --- 𝔸-family (algebra) --------------------------------------------
    ("tensor",         "\U0001D54B"),  # 𝕋  double-struck T
    ("matrix",         "\U0001D544"),  # 𝕄  double-struck M
    ("vector",         "\u21C0"),      # ⇀  rightwards harpoon with barb up (distinct from _OPERATORS -> which is U+2192)
    ("spin",           "\u2191"),      # ↑  upwards arrow
]

# Derived tables (inverse map, regex alternation, compound pattern, sorted
# sources) and the escape-machinery alternation are built by
# `_rebuild_module_indices()` below. Declared here as empty placeholders
# so every downstream reference to them is bindable at import time; the
# rebuild call at the END of the module fills them with the real values.
#
# Phase 3.1: these derivations are in a callable so mdl_sandbox can swap
# in trial rule tables and regenerate the indices consistently, then
# restore originals after scoring.
_MORPHEMES_INVERSE: Dict[str, str] = {}
_MORPHEME_TARGETS_ORDERED: List[str] = []
_MORPHEME_TARGET_ALT: str = ""
_COMPOUND_PATTERN: "re.Pattern[str]" = re.compile(r"(?!)")  # never-match placeholder
_MORPHEME_SOURCES_ORDERED: List[Tuple[str, str]] = []


# -----------------------------------------------------------------------
# Escape-marker machinery (Phase 1.2.1): preserve pre-existing target
# glyphs in source text so round-trip stays lossless even when the
# source contains characters like ≥, ×, ∂ that our rule targets use.
# -----------------------------------------------------------------------

def _all_target_glyphs() -> List[str]:
    """Collect every target glyph across all rule layers. Deduplicated,
    sorted by decreasing length so composite targets (∇², σ², Δ𝓍,
    sandhi compound prefixes) are matched before their substrings in
    the escape pre-pass regex.

    Phase 2.1: class-glyphs (⦗marker⦘) and the bare class markers are
    also included so source text containing them survives round-trip.

    Phase 2.2: sandhi brackets (⟦ ⟧) and every compound_prefix (∂Ψ,
    ∇E, etc.) are added so source containing ASCII-art sandhi-like
    substrings round-trips losslessly."""
    from .codec_classes import (
        CLASS_OPEN_MARKER, CLASS_CLOSE_MARKER, class_glyphs,
    )
    from .codec_sandhi import all_sandhi_target_glyphs
    targets: set[str] = set()
    for _, g in _PHRASES:
        targets.add(g)
    for _, g in _OPERATORS:
        targets.add(g)
    targets.update(_KEYWORDS.values())
    for _, g in _MORPHEMES:
        targets.add(g)
    targets.add(COMPOUND_SEP)         # escape literal ⋄ in source
    targets.add(CLASS_OPEN_MARKER)    # escape ⦗
    targets.add(CLASS_CLOSE_MARKER)   # escape ⦘
    for cg in class_glyphs():
        targets.add(cg)               # and the full ⦗Ψ⦘ etc. (longest-first)
    for sg in all_sandhi_target_glyphs():
        targets.add(sg)               # ⟦, ⟧, and every compound_prefix
    return sorted(targets, key=len, reverse=True)


_ESCAPE_ALTERNATION: str = ""
_ESCAPE_SOURCE_PATTERN: "re.Pattern[str]" = re.compile(r"(?!)")  # placeholder


def _rebuild_module_indices() -> None:
    """Regenerate all derived module-level tables from the current
    _PHRASES / _OPERATORS / _KEYWORDS / _MORPHEMES (plus the class +
    sandhi module tables). Called once at module import and by
    `mdl_sandbox` when swapping trial rule tables in and out.

    The function is intentionally idempotent: calling it twice in a row
    produces the same state. All mutation happens through module globals
    so existing references (e.g. the encode/decode path) pick up the new
    values on the next call.

    Note: this also clears the sandhi regex caches so new classes /
    sandhi rules compile freshly under the new state.
    """
    global _KEYWORDS, _MORPHEMES
    global _MORPHEMES_INVERSE, _MORPHEME_TARGETS_ORDERED
    global _MORPHEME_TARGET_ALT, _COMPOUND_PATTERN, _MORPHEME_SOURCES_ORDERED
    global _ESCAPE_ALTERNATION, _ESCAPE_SOURCE_PATTERN

    # Defensive filters (drop empty glyphs). Same invariants as the
    # previous import-time list/dict comprehensions.
    _KEYWORDS = {k: v for k, v in _KEYWORDS.items() if v}
    _MORPHEMES = [(s, g) for s, g in _MORPHEMES if g]

    # Morpheme-layer derived tables.
    _MORPHEMES_INVERSE = {g: s for s, g in _MORPHEMES}
    _MORPHEME_TARGETS_ORDERED = sorted(
        {g for _, g in _MORPHEMES}, key=len, reverse=True,
    )
    if _MORPHEME_TARGETS_ORDERED:
        _MORPHEME_TARGET_ALT = (
            "(?:" + "|".join(re.escape(g) for g in _MORPHEME_TARGETS_ORDERED) + ")"
        )
        _COMPOUND_PATTERN = re.compile(
            rf"{_MORPHEME_TARGET_ALT}(?:{re.escape(COMPOUND_SEP)}{_MORPHEME_TARGET_ALT})+"
        )
    else:
        _MORPHEME_TARGET_ALT = ""
        _COMPOUND_PATTERN = re.compile(r"(?!)")
    _MORPHEME_SOURCES_ORDERED = sorted(
        _MORPHEMES, key=lambda p: -len(p[0]),
    )

    # Escape-layer alternation spans every rule table's targets.
    _ESCAPE_ALTERNATION = "|".join(re.escape(g) for g in _all_target_glyphs())
    if _ESCAPE_ALTERNATION:
        _ESCAPE_SOURCE_PATTERN = re.compile(_ESCAPE_ALTERNATION)
    else:
        _ESCAPE_SOURCE_PATTERN = re.compile(r"(?!)")

    # Sandhi caches depend on the class + sandhi rule tables that live
    # in codec_sandhi. Clear them so the next encode/decode recompiles
    # against the new state.
    from . import codec_sandhi as _cs
    _cs._FORWARD_RE_CACHE.clear()
    _cs._INVERSE_RE_CACHE.clear()


# Build the indices once at import time. Downstream module globals
# (_MORPHEMES_INVERSE, _ESCAPE_SOURCE_PATTERN, ...) go from empty
# placeholders to their real values after this call.
_rebuild_module_indices()


def _escape_source_glyphs(text: str) -> str:
    """Prepend ESCAPE_MARKER to every character of every target glyph
    already present in source text. Runs at the START of encode, BEFORE
    any rule pass, so that rule substitutions produce clean (unescaped)
    output glyphs and we can distinguish those from pre-existing ones
    on decode.

    Multi-char targets (∇², ⦗Ψ⦘, Δ𝓍) have EACH character marked: a
    single prefix would leave interior characters exposed to an inverse
    pass whose lookbehind only sees the preceding character. Per-char
    marking guarantees every byte of a pre-existing target survives.

    Longest-first matching in the alternation prevents ∇ (gradient)
    from grabbing ∇² (laplacian)'s prefix."""
    def _per_char_escape(m: "re.Match[str]") -> str:
        glyph = m.group(0)
        return "".join(ESCAPE_MARKER + ch for ch in glyph)
    return _ESCAPE_SOURCE_PATTERN.sub(_per_char_escape, text)


def _build_inverse_sub(dst: str) -> "re.Pattern[str]":
    """Regex for decode pass: match `dst` only when it is NOT preceded
    by ESCAPE_MARKER. Used in every inverse rule pass so escaped glyphs
    survive the substitution."""
    return re.compile(r"(?<!" + re.escape(ESCAPE_MARKER) + r")" + re.escape(dst))


# -----------------------------------------------------------------------
# Internal helpers
# -----------------------------------------------------------------------

def _normalise(text: str) -> str:
    """Collapse runs of whitespace to single spaces but preserve line breaks.
    Keeps the phrase matcher stable without mangling paragraph structure."""
    # Replace any run of horizontal whitespace with a single space. Leave
    # newlines alone so downstream tooling can still break on them.
    return re.sub(r"[ \t\r\f\v]+", " ", text)


def _word_boundary_replace(text: str, source: str, target: str) -> str:
    """Replace `source` with `target` only when it occurs as a whole word."""
    pattern = r"\b" + re.escape(source) + r"\b"
    return re.sub(pattern, target, text, flags=re.IGNORECASE)


# -----------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------

@dataclass
class EncodeResult:
    compressed: str              # wire-format output (sealed or legacy plaintext)
    compression_ratio: float     # source_length / compressed_length (post-seal)
    codec_version: str
    source_length: int
    compressed_length: int       # wire length
    canonical_length: int = 0    # pre-seal canonical compressed length
    canonical_ratio: float = 0.0 # source_length / canonical_length
    encryption_mode: str = "plaintext"  # "sealed" | "plaintext"


_WORD_TOKEN_RE = re.compile(r"[A-Za-z]+")


def _try_morpheme_decompose(word: str) -> Optional[List[str]]:
    """Greedy left-to-right morpheme decomposition of an alphabetical
    word. Returns a list of morpheme glyphs if the whole word is
    covered, or None if it is not.

    Single-morpheme matches ARE accepted (since Phase 1.2 corpus
    expansion): a word that fully corresponds to one morpheme emits
    the bare glyph (no COMPOUND_SEP). This is safe because morpheme
    target glyphs are globally unique across all rule layers -- a bare
    morpheme glyph in output always decodes unambiguously to its
    source. The test_morpheme_glyph_uniqueness case enforces this.

    Case-insensitive matching (we already lowercase-normalise on encode).
    """
    lower = word.lower()
    if len(lower) < 3:
        return None   # no morpheme source is < 3 chars; skip cheap cases
    parts: List[str] = []
    i = 0
    n = len(lower)
    while i < n:
        matched = False
        for src, glyph in _MORPHEME_SOURCES_ORDERED:
            L = len(src)
            if lower.startswith(src, i) and i + L <= n:
                parts.append(glyph)
                i += L
                matched = True
                break
        if not matched:
            return None
    return parts if len(parts) >= 1 else None


def _apply_forward_rules(text: str) -> str:
    """Apply the rule layers in order. Pure function; no crypto.

    Layer order (encode):
      0. Normalise whitespace
      1. Escape pre-existing target glyphs (Phase 1.2.1: so source text
         containing ≥, ×, ∂ etc. round-trips intact)
      2. Sandhi     (Phase 2.2, longest-match-wins multi-word compounds;
                     runs FIRST so its compound prefixes reuse chars from
                     downstream tables safely)
      3. _PHRASES   (word-boundary, longest-first)
      4. _OPERATORS (literal substring)
      5. _KEYWORDS  (word-boundary)
      6. _MORPHEMES (whole alphabetical word, greedy decomposition)
    """
    from . import codec_sandhi
    out = _normalise(text)
    out = _escape_source_glyphs(out)
    out = codec_sandhi.apply_forward(out)
    for src, dst in _PHRASES:
        out = _word_boundary_replace(out, src, dst)
    for src, dst in _OPERATORS:
        out = out.replace(src, dst)
    for src, dst in _KEYWORDS.items():
        out = _word_boundary_replace(out, src, dst)
    # Morpheme layer: only fires on whole-word alphabetical tokens that
    # the earlier layers did not replace. Single-char glyphs from those
    # layers are not alphabetical and don't match _WORD_TOKEN_RE, so
    # this pass won't re-process them.
    def _morph_sub(m: "re.Match[str]") -> str:
        word = m.group(0)
        parts = _try_morpheme_decompose(word)
        if parts is None:
            return word
        return COMPOUND_SEP.join(parts)
    out = _WORD_TOKEN_RE.sub(_morph_sub, out)
    return out


def _apply_inverse_rules(compressed: str) -> str:
    """Reverse the rule layers in reverse order. Pure function; no crypto.

    Every inverse substitution uses a regex with a negative lookbehind
    for ESCAPE_MARKER so that pre-existing source glyphs (marked during
    encode) are NOT reverted. The final pass strips the escape markers.

    Phase 2.2: sandhi inverse runs FIRST. Its compound prefixes reuse
    characters from the phrase / keyword / morpheme tables (e.g. ∂Ψ,
    ρΨ, Ψ⊗Ψ). If the other inverses fired first they would fragment
    those prefixes; running sandhi first consumes the full compound in
    one match and emits canonical source text that downstream inverses
    correctly leave alone."""
    from . import codec_sandhi
    out = compressed

    # Sandhi reverse: consumes {prefix}⟦payload⟧ compounds, emits
    # canonical "partial derivative of state" / "commutator of X and
    # Y" templates. Downstream inverses see only canonical source or
    # non-sandhi glyphs from here on.
    out = codec_sandhi.apply_inverse(out)

    # Morpheme reverse, pass 1: compound sequences via _COMPOUND_PATTERN.
    # Compound glyphs only come from rule substitution (never from
    # source), so they don't carry escape markers. Pattern matches them
    # directly with no lookbehind concerns.
    def _morph_undo(m: "re.Match[str]") -> str:
        segments = m.group(0).split(COMPOUND_SEP)
        return "".join(_MORPHEMES_INVERSE.get(seg, seg) for seg in segments)
    out = _COMPOUND_PATTERN.sub(_morph_undo, out)

    # Morpheme reverse, pass 2: bare single-morpheme glyphs. Use
    # lookbehind to skip escaped (source-origin) glyphs.
    for glyph in _MORPHEME_TARGETS_ORDERED:
        src = _MORPHEMES_INVERSE[glyph]
        out = _build_inverse_sub(glyph).sub(src, out)

    for src, dst in _KEYWORDS.items():
        out = _build_inverse_sub(dst).sub(src, out)
    for src, dst in _OPERATORS:
        out = _build_inverse_sub(dst).sub(src, out)
    for src, dst in _PHRASES:
        out = _build_inverse_sub(dst).sub(src, out)

    # Final pass: strip ALL escape markers. Any ESCAPE_MARKER remaining
    # in `out` at this point precedes a source-origin glyph that the
    # inverse passes correctly skipped; we just need to drop the marker.
    out = out.replace(ESCAPE_MARKER, "")
    return out


def _canonical_glyph_pool() -> List[str]:
    """Collect every target glyph across the three rule tables. Stable
    ordering (sorted at permutation build time) so permutations are
    deterministic given the seed."""
    pool: List[str] = []
    pool.extend(g for _, g in _PHRASES)
    pool.extend(g for _, g in _OPERATORS)
    pool.extend(_KEYWORDS.values())
    return pool


def _wire_encode(sealed_bytes: bytes) -> str:
    """Format: SEALED_PREFIX + base64url(sealed_bytes), no padding."""
    b64 = base64.urlsafe_b64encode(sealed_bytes).rstrip(b"=").decode("ascii")
    return f"{SEALED_PREFIX}{b64}"


def _wire_decode(wire: str) -> bytes:
    """Parse SEALED_PREFIX + base64url back to raw sealed bytes."""
    if not wire.startswith(SEALED_PREFIX):
        raise ValueError(f"wire format missing prefix {SEALED_PREFIX!r}")
    b64 = wire[len(SEALED_PREFIX):]
    pad_len = (-len(b64)) % 4
    return base64.urlsafe_b64decode(b64 + "=" * pad_len)


def encode(text: str, *, seal: bool = True) -> EncodeResult:
    """Compress `text` through the rule layers and (by default) seal with
    AES-GCM-256 keyed by the install's Ed25519 identity.

    If `seal=False` OR the FRANK_CODEC_ALLOW_PLAINTEXT env flag is set,
    the canonical compressed text is returned without the sealing wrap
    (debug / dev path only). Production callers MUST leave seal=True.

    Raises IdentityNotInitialisedError when sealing is requested but the
    install Ed25519 identity has not been created yet -- callers should
    either initialise the identity first or fall back to seal=False for
    non-sensitive development use. (The same Ed25519 keypair is also
    consumed by the messaging subsystem as its BB84 peer identity, but
    no quantum entropy or peer exchange enters this code path.)
    """
    if not text:
        return EncodeResult(
            compressed="", compression_ratio=1.0,
            codec_version=CODEC_VERSION,
            source_length=0, compressed_length=0,
            canonical_length=0, canonical_ratio=1.0,
            encryption_mode="plaintext",
        )

    canonical = _apply_forward_rules(text)
    source_len = len(text)
    canonical_len = len(canonical)
    canonical_ratio = (source_len / canonical_len) if canonical_len else 1.0

    # Debug bypass: seal=False or env flag set -> return unsealed.
    if not seal or plaintext_mode_enabled():
        return EncodeResult(
            compressed=canonical,
            compression_ratio=canonical_ratio,
            codec_version=CODEC_VERSION,
            source_length=source_len,
            compressed_length=canonical_len,
            canonical_length=canonical_len,
            canonical_ratio=canonical_ratio,
            encryption_mode="plaintext",
        )

    # Sealed path: derive ephemeral keys, permute, seal, zeroize.
    perm_seed, aead_key, identity = derive_keys()
    try:
        permutation = build_permutation(_canonical_glyph_pool(), bytes(perm_seed))
        keyed_text = permutation.apply_forward(canonical)
        aad = build_aad(CODEC_VERSION, identity.fingerprint, PERMUTATION_VERSION)
        sealed_bytes = _aead_seal(
            keyed_text.encode("utf-8"), bytes(aead_key), aad
        )
        wire = _wire_encode(sealed_bytes)
    finally:
        zeroize(perm_seed)
        zeroize(aead_key)

    wire_len = len(wire)
    return EncodeResult(
        compressed=wire,
        compression_ratio=(source_len / wire_len) if wire_len else 1.0,
        codec_version=CODEC_VERSION,
        source_length=source_len,
        compressed_length=wire_len,
        canonical_length=canonical_len,
        canonical_ratio=canonical_ratio,
        encryption_mode="sealed",
    )


def decode(compressed: str) -> str:
    """Reverse `encode`. Automatically detects wire format:

      - "sks1:" prefix -> sealed path: unwrap AES-GCM, inverse-permute,
        then apply inverse rule layers.
      - Otherwise     -> legacy v0 plaintext path: apply inverse rule
        layers directly. Lets pre-SEC.1 corpus rows stay readable until
        bulk re-encode migrates them.

    Raises SealError on tampered / wrong-key / wrong-AAD sealed blobs.
    Raises IdentityNotInitialisedError on sealed input when the install
    Ed25519 identity is missing.
    """
    if not compressed:
        return ""

    if compressed.startswith(SEALED_PREFIX):
        sealed_bytes = _wire_decode(compressed)
        perm_seed, aead_key, identity = derive_keys()
        try:
            aad = build_aad(CODEC_VERSION, identity.fingerprint, PERMUTATION_VERSION)
            keyed_bytes = _aead_unseal(sealed_bytes, bytes(aead_key), aad)
            keyed_text = keyed_bytes.decode("utf-8")
            permutation = build_permutation(_canonical_glyph_pool(), bytes(perm_seed))
            canonical = permutation.apply_inverse(keyed_text)
        finally:
            zeroize(perm_seed)
            zeroize(aead_key)
        return _apply_inverse_rules(canonical)

    # Legacy v0 path: bare compressed text, no sealing.
    return _apply_inverse_rules(compressed)


def decode_to_canonical_glyphs(compressed: str) -> str:
    """v6.7.5 chunk 1 -- partial decode: stop after AES-GCM unseal +
    inverse-permute, BEFORE applying the inverse rule layers.

    Output is the canonical-glyph form (e.g. ``λ Ψ ⟦ψ⟧ = ϑ ✳ ...``)
    rather than the full English plaintext that ``decode()`` returns.
    Used by the v68-D2 retriever when ``actor_origin="external"``
    (governance Decision 1: external-agent recall is canonical-glyph-only).

    Per the v6.6 PLUGIN_CONTRACT Option A, external agents transport
    plaintext-canonical-glyphs across the install boundary; this
    function produces exactly that form on the way out.  External
    agents that want full plaintext call ``memory_decompress`` next,
    which is install-bound (the master key only exists on this install).

    Raises ``SealError`` on tampered / wrong-key / wrong-AAD sealed
    blobs.  Raises ``IdentityNotInitialisedError`` on sealed input when
    the install Ed25519 identity is missing.

    For pre-SEC.1 legacy v0 plaintext input (no ``sks1:`` prefix), the
    input IS already canonical-glyph form, so this returns it unchanged.
    This matches ``decode()``'s legacy fallback shape.
    """
    if not compressed:
        return ""

    if compressed.startswith(SEALED_PREFIX):
        sealed_bytes = _wire_decode(compressed)
        perm_seed, aead_key, identity = derive_keys()
        try:
            aad = build_aad(CODEC_VERSION, identity.fingerprint, PERMUTATION_VERSION)
            keyed_bytes = _aead_unseal(sealed_bytes, bytes(aead_key), aad)
            keyed_text = keyed_bytes.decode("utf-8")
            permutation = build_permutation(_canonical_glyph_pool(), bytes(perm_seed))
            canonical = permutation.apply_inverse(keyed_text)
        finally:
            zeroize(perm_seed)
            zeroize(aead_key)
        return canonical

    # Legacy v0 path: input is already canonical-glyph form.
    return compressed


def compression_ratio(text: str) -> float:
    """Convenience: encode and return only the ratio (post-seal if sealed)."""
    return encode(text).compression_ratio


def lossless_roundtrip(text: str, *, seal: bool = True) -> bool:
    """True iff decode(encode(text, seal=seal).compressed) == _normalise(text.lower()).
    Useful for QA on specific inputs -- caller asserts lowercase-canonical.

    By default runs through the full sealed pipeline. Pass seal=False to
    test just the rule layers (useful if the install Ed25519 identity is
    not available in the current env)."""
    normalised = _normalise(text.lower())
    roundtrip = decode(encode(normalised, seal=seal).compressed)
    return roundtrip == normalised


__all__ = [
    "CODEC_VERSION",
    "LEGACY_CODEC_VERSION_V0",
    "LEGACY_CODEC_VERSION_V1",
    "LEGACY_CODEC_VERSION_V2",
    "LEGACY_CODEC_VERSION_V3",
    "SEALED_PREFIX",
    "EncodeResult",
    "encode",
    "decode",
    "decode_to_canonical_glyphs",
    "compression_ratio",
    "lossless_roundtrip",
]
