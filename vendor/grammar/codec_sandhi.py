#!/usr/bin/env python3
"""
Dr. Frank & Eddy v6.4.1 -- Sandhi (context-sensitive compound rules, Phase 2.2)

Pāṇini's second compression mechanism: sandhi rules apply based on
surrounding context. In Sanskrit: sound changes at word boundaries. In
our codec: multi-word physics phrases whose compression depends on the
WHOLE phrase, not the constituent words.

A sandhi rule is a sequence of literal phrases interleaved with
class-member slots. The match regex is built from the rule's literals
and the member alternation of each slot's class. On match, the captured
member source words are looked up in _MORPHEMES to produce payload
glyphs, and the compound is emitted as:

    {compound_prefix} SANDHI_OPEN {glyph}(, {glyph})* SANDHI_CLOSE

Example:
    source "partial derivative of state"
    -> rule 1 ("partial derivative of", Psi-family slot)
    -> compound prefix "∂Ψ", class member "state" -> morpheme glyph ψ
    -> "∂Ψ⟦ψ⟧"

Sandhi runs BEFORE the phrase / operator / keyword / morpheme forward
passes in encode. It runs BEFORE all other inverse passes in decode so
that its compound glyphs (which reuse chars from other rule targets
like ∂, Ψ, ⊗) are consumed before downstream inverses can fragment
them.

Round-trip invariant: sandhi rules MATCH ONLY EXACT PHRASES WITHOUT
INTERVENING ARTICLES ("the", "a", "an"). Phrases containing articles
fall through to the regular rule pipeline, where each morpheme /
keyword / phrase substitutes independently. This keeps round-trip
strictly lossless without needing the compound glyph to carry
article-state bits.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union


# Sandhi-payload boundary markers. Picked from math bracket block; tests
# verify they do not collide with any target in _PHRASES / _OPERATORS /
# _KEYWORDS / _MORPHEMES / COMPOUND_SEP / ESCAPE_MARKER / class
# markers (⦗ ⦘). These are added to the codec's escape target list so
# source text containing them round-trips losslessly.
SANDHI_OPEN = "\u27E6"   # ⟦ MATHEMATICAL LEFT WHITE SQUARE BRACKET
SANDHI_CLOSE = "\u27E7"  # ⟧ MATHEMATICAL RIGHT WHITE SQUARE BRACKET

# Separator between payload glyphs when a sandhi rule has multiple class
# slots. ASCII comma, never a target glyph, never needs escaping.
SANDHI_SEP = ","


# -----------------------------------------------------------------------
# Rule structure
# -----------------------------------------------------------------------

@dataclass(frozen=True)
class SandhiSlot:
    """A class-member slot in a sandhi pattern. On match, the slot
    captures one source word that must be a member of `class_name`."""
    class_name: str


# A sandhi pattern is an alternating tuple of literal phrases (str)
# and class slots (SandhiSlot). Literals come first (or between slots),
# never two literals in a row (collapse them), and the pattern must
# contain at least one slot (otherwise it is a phrase rule, not sandhi).
SandhiToken = Union[str, SandhiSlot]


@dataclass(frozen=True)
class SandhiRule:
    """A context-sensitive compound rule.

    Forward direction: the pattern is matched against source text. On
    match, class-slot captures are mapped to morpheme glyphs and emitted
    as {compound_prefix}{SANDHI_OPEN}{glyph,glyph,...}{SANDHI_CLOSE}.

    Inverse direction: the compound in encoded text is matched, its
    payload morpheme glyphs are mapped back to source words, and the
    canonical template is filled with {0}, {1}, ... placeholders.
    """
    name: str
    priority: int
    pattern: Tuple[SandhiToken, ...]
    compound_prefix: str
    canonical_template: str
    example: str
    description: str = ""

    @property
    def slot_count(self) -> int:
        return sum(1 for t in self.pattern if isinstance(t, SandhiSlot))

    def to_canonical(self) -> List[object]:
        """Stable serialisation for MDL rule-bytes accounting.

        Compact tuple-of-values layout (no field names, no padding) so
        MDL does not overpay for JSON framing. Order is fixed as
        [name, priority, pattern_tokens, compound_prefix,
        canonical_template]. Pattern tokens are 2-element lists of
        [kind, value] where kind is "l" (literal) or "k" (class).

        Description is documentation, not a rule -- excluded for the
        same reason codec_classes.MorphemeClass excludes it."""
        pattern_repr: List[List[str]] = []
        for tok in self.pattern:
            if isinstance(tok, SandhiSlot):
                pattern_repr.append(["k", tok.class_name])
            else:
                pattern_repr.append(["l", tok])
        return [
            self.name,
            self.priority,
            pattern_repr,
            self.compound_prefix,
            self.canonical_template,
        ]


# -----------------------------------------------------------------------
# Initial sandhi rule set (10 rules, priority ordered)
#
# Priority determines forward match order (higher first). For tiebreaks,
# rules declared earlier in the tuple win. Within a priority bucket,
# patterns with distinct literal prefixes cannot overlap, so order is
# cosmetic. The value 100 is the default; raise for rules that should
# preempt lower-priority overlapping patterns in future expansions.
# -----------------------------------------------------------------------

PHYSICS_SANDHI_RULES: Tuple[SandhiRule, ...] = (
    SandhiRule(
        name="partial_derivative_of_psi",
        priority=100,
        pattern=("partial derivative of", SandhiSlot("Psi-family")),
        compound_prefix="\u2202\u03A8",   # ∂Ψ
        canonical_template="partial derivative of {0}",
        example="partial derivative of state",
        description="Differential operator acting on a quantum object.",
    ),
    SandhiRule(
        name="gradient_of_energy",
        priority=100,
        pattern=("gradient of", SandhiSlot("E-family")),
        compound_prefix="\u2207E",        # ∇E
        canonical_template="gradient of {0}",
        example="gradient of energy",
        description="Gradient of an energy-like scalar field.",
    ),
    SandhiRule(
        name="eigenvalue_of_psi",
        priority=100,
        pattern=("eigenvalue of", SandhiSlot("Psi-family")),
        compound_prefix="\u03BB\u03A8",   # λΨ
        canonical_template="eigenvalue of {0}",
        example="eigenvalue of hamiltonian",
        description="Eigenvalue of a quantum operator.",
    ),
    SandhiRule(
        name="expectation_value_of_psi",
        priority=100,
        pattern=("expectation value of", SandhiSlot("Psi-family")),
        compound_prefix="\u27E8\u03A8\u27E9",   # ⟨Ψ⟩
        canonical_template="expectation value of {0}",
        example="expectation value of observable",
        description="Expectation value of a quantum observable.",
    ),
    SandhiRule(
        name="commutator_of_psi_and_psi",
        priority=110,  # binary-slot rules preempt single-slot prefixes
        pattern=(
            "commutator of", SandhiSlot("Psi-family"),
            "and", SandhiSlot("Psi-family"),
        ),
        compound_prefix="[\u03A8,\u03A8]", # [Ψ,Ψ]
        canonical_template="commutator of {0} and {1}",
        example="commutator of hermitian and unitary",
        description="Commutator of two quantum operators.",
    ),
    SandhiRule(
        name="probability_of_measuring_psi",
        priority=100,
        pattern=("probability of measuring", SandhiSlot("Psi-family")),
        compound_prefix="\u2119\u03A8",   # ℙΨ
        canonical_template="probability of measuring {0}",
        example="probability of measuring state",
        description="Born-rule probability of a measurement outcome.",
    ),
    SandhiRule(
        name="time_evolution_of_psi",
        priority=100,
        pattern=("time evolution of", SandhiSlot("Psi-family")),
        compound_prefix="\u2202\u209C\u03A8",   # ∂ₜΨ
        canonical_template="time evolution of {0}",
        example="time evolution of state",
        description="Schrödinger / Heisenberg time evolution.",
    ),
    SandhiRule(
        name="density_matrix_of_psi",
        priority=100,
        pattern=("density matrix of", SandhiSlot("Psi-family")),
        compound_prefix="\u03C1\u03A8",   # ρΨ
        canonical_template="density matrix of {0}",
        example="density matrix of state",
        description="Density operator representing a quantum state.",
    ),
    SandhiRule(
        name="inner_product_of_psi_and_psi",
        priority=110,
        pattern=(
            "inner product of", SandhiSlot("Psi-family"),
            "and", SandhiSlot("Psi-family"),
        ),
        compound_prefix="\u27E8\u03A8|\u03A8\u27E9",  # ⟨Ψ|Ψ⟩
        canonical_template="inner product of {0} and {1}",
        example="inner product of bra and ket",
        description="Inner product of two quantum states.",
    ),
    SandhiRule(
        name="tensor_product_of_psi_and_psi",
        priority=110,
        pattern=(
            "tensor product of", SandhiSlot("Psi-family"),
            "and", SandhiSlot("Psi-family"),
        ),
        compound_prefix="\u03A8\u2297\u03A8", # Ψ⊗Ψ
        canonical_template="tensor product of {0} and {1}",
        example="tensor product of hermitian and unitary",
        description="Tensor product of two quantum objects.",
    ),
)


# -----------------------------------------------------------------------
# Public lookup helpers
# -----------------------------------------------------------------------

def list_rules() -> Tuple[SandhiRule, ...]:
    """All sandhi rules in declaration order."""
    return PHYSICS_SANDHI_RULES


def rule_by_name(name: str) -> SandhiRule:
    """Find a rule by name. Raises KeyError on unknown name."""
    for r in PHYSICS_SANDHI_RULES:
        if r.name == name:
            return r
    raise KeyError(f"unknown sandhi rule: {name!r}")


def canonical_json() -> str:
    """Stable JSON of all sandhi rules, used by MDL rule-bytes.

    Uses compact separators `(",", ":")` so rule_bytes counts semantic
    content only, not JSON whitespace. Each rule is a tuple-of-values
    (see SandhiRule.to_canonical)."""
    payload = [r.to_canonical() for r in PHYSICS_SANDHI_RULES]
    return json.dumps(
        payload, ensure_ascii=True, allow_nan=False,
        separators=(",", ":"),
    )


# -----------------------------------------------------------------------
# Forward / inverse matchers
#
# Built lazily on first use so this module can import before
# sanskrit_codec._MORPHEMES is fully populated (matters only if someone
# eventually circular-imports, which we do not today). The compile is
# cached in module-level dicts keyed by rule name.
# -----------------------------------------------------------------------

_FORWARD_RE_CACHE: Dict[str, "re.Pattern[str]"] = {}
_INVERSE_RE_CACHE: Dict[str, "re.Pattern[str]"] = {}


def _morpheme_source_to_glyph() -> Dict[str, str]:
    """Lazy accessor: source word -> morpheme glyph.

    Imported inside the function to avoid a top-level circular import
    with sanskrit_codec (which imports this module via _all_target_glyphs).
    """
    from .sanskrit_codec import _MORPHEMES
    return {src: glyph for src, glyph in _MORPHEMES}


def _morpheme_glyph_to_source() -> Dict[str, str]:
    """Lazy accessor: morpheme glyph -> source word. See above."""
    from .sanskrit_codec import _MORPHEMES
    return {glyph: src for src, glyph in _MORPHEMES}


def _class_member_alternation(class_name: str) -> str:
    """Regex alternation of class members, longest-source-first so
    greedy engines bind "hamiltonian" before "wave" if both happened
    to be prefixes of each other (they aren't today)."""
    from .codec_classes import class_by_name
    members = sorted(class_by_name(class_name).members, key=len, reverse=True)
    return "(?:" + "|".join(re.escape(m) for m in members) + ")"


def _class_member_glyph_alternation(class_name: str) -> str:
    """Regex alternation of morpheme GLYPHS for members of a class.
    Used in inverse regex to match payload."""
    from .codec_classes import class_by_name
    src_to_glyph = _morpheme_source_to_glyph()
    members = sorted(class_by_name(class_name).members, key=len, reverse=True)
    glyphs = [src_to_glyph[m] for m in members if m in src_to_glyph]
    # Sort glyphs by length too, in case of multi-codepoint morpheme
    # glyphs like σ² (variance) or ∇² (laplacian) -- ensures longest
    # match wins inside the alternation.
    glyphs = sorted(set(glyphs), key=len, reverse=True)
    return "(?:" + "|".join(re.escape(g) for g in glyphs) + ")"


def _compile_forward(rule: SandhiRule) -> "re.Pattern[str]":
    """Build the source-side regex for forward matching of this rule.

    Literals are re.escape'd. Between-token whitespace is `[ ]+`
    (horizontal-space only). Sandhi deliberately does NOT cross line
    breaks -- `_normalise` has already collapsed horizontal runs to
    single spaces, so sandhi-matchable phrases always live on a single
    line. Allowing `\\s+` would let a newline be absorbed into the
    match and the canonical template's decode would emit a space in
    its place, breaking round-trip.

    Each SandhiSlot becomes a capturing group over its class's member
    alternation. Word boundaries bracket the whole match to avoid
    binding inside longer words.
    """
    if rule.name in _FORWARD_RE_CACHE:
        return _FORWARD_RE_CACHE[rule.name]

    parts: List[str] = [r"\b"]
    for i, tok in enumerate(rule.pattern):
        if i > 0:
            parts.append(r"[ ]+")
        if isinstance(tok, SandhiSlot):
            parts.append("(" + _class_member_alternation(tok.class_name) + ")")
        else:
            parts.append(re.escape(tok))
    parts.append(r"\b")
    pat = re.compile("".join(parts), flags=re.IGNORECASE)
    _FORWARD_RE_CACHE[rule.name] = pat
    return pat


def _compile_inverse(rule: SandhiRule) -> "re.Pattern[str]":
    """Build the encoded-side regex for inverse matching of this rule.

    Matches `{prefix}{SANDHI_OPEN}{glyph}(,{glyph}){n-1}{SANDHI_CLOSE}`
    where n = rule.slot_count. Each payload glyph is a capture over the
    relevant class's morpheme-glyph alternation.

    A negative-lookbehind on ESCAPE_MARKER guards the compound prefix
    so that sandhi-like substrings appearing in SOURCE (protected by
    escape markers during encode) are not mistakenly consumed.
    """
    if rule.name in _INVERSE_RE_CACHE:
        return _INVERSE_RE_CACHE[rule.name]

    from .sanskrit_codec import ESCAPE_MARKER

    # Build payload portion. Each slot emits (CLASS_GLYPH_ALT) separated
    # by SANDHI_SEP.
    slot_classes = [t.class_name for t in rule.pattern if isinstance(t, SandhiSlot)]
    payload_parts = [
        "(" + _class_member_glyph_alternation(cn) + ")"
        for cn in slot_classes
    ]
    payload = re.escape(SANDHI_SEP).join(payload_parts)

    pat = re.compile(
        r"(?<!" + re.escape(ESCAPE_MARKER) + r")"
        + re.escape(rule.compound_prefix)
        + re.escape(SANDHI_OPEN)
        + payload
        + re.escape(SANDHI_CLOSE)
    )
    _INVERSE_RE_CACHE[rule.name] = pat
    return pat


def _sort_rules_for_forward() -> Tuple[SandhiRule, ...]:
    """Priority-descending, then declaration-order. Stable across runs."""
    return tuple(
        sorted(
            PHYSICS_SANDHI_RULES,
            key=lambda r: (-r.priority, PHYSICS_SANDHI_RULES.index(r)),
        )
    )


def apply_forward(text: str) -> str:
    """Apply all sandhi rules in priority order to source text.

    Each rule's match produces a compound glyph that replaces the
    matched substring. Matches are non-overlapping (re.sub semantics);
    a longer rule that has already consumed text cannot be revisited
    by a shorter overlapping rule.
    """
    src_to_glyph = _morpheme_source_to_glyph()
    out = text
    for rule in _sort_rules_for_forward():
        pat = _compile_forward(rule)

        def _emit(m: "re.Match[str]", _r: SandhiRule = rule) -> str:
            captures = [g.lower() for g in m.groups() if g is not None]
            payload_glyphs = [src_to_glyph[c] for c in captures]
            return (
                _r.compound_prefix
                + SANDHI_OPEN
                + SANDHI_SEP.join(payload_glyphs)
                + SANDHI_CLOSE
            )

        out = pat.sub(_emit, out)
    return out


def apply_inverse(text: str) -> str:
    """Apply all sandhi inverse patterns. Matches in encoded text are
    replaced with the rule's canonical_template filled in using the
    source words looked up from payload morpheme glyphs.

    Intended to run as the FIRST inverse pass in decode, before any
    other glyph-to-source substitution. That way the compound prefix
    characters (which reuse glyphs from other rule tables like ∂, Ψ,
    ⊗) are still intact when this regex runs."""
    glyph_to_src = _morpheme_glyph_to_source()
    out = text
    for rule in _sort_rules_for_forward():
        pat = _compile_inverse(rule)

        def _reconstruct(m: "re.Match[str]", _r: SandhiRule = rule) -> str:
            sources = [glyph_to_src[g] for g in m.groups() if g is not None]
            return _r.canonical_template.format(*sources)

        out = pat.sub(_reconstruct, out)
    return out


def all_sandhi_target_glyphs() -> Tuple[str, ...]:
    """Every glyph or multi-char sequence introduced by the sandhi layer
    that the codec's escape mechanism must protect if it appears
    literally in source text. Includes:

      - SANDHI_OPEN / SANDHI_CLOSE brackets
      - Every rule's compound_prefix (so source text containing e.g.
        "Ψ⊗Ψ" as ASCII art round-trips losslessly)

    Sandhi payloads themselves are morpheme glyphs already registered
    in the escape pool by _MORPHEMES, so we don't re-add them.
    """
    targets: set[str] = {SANDHI_OPEN, SANDHI_CLOSE}
    for r in PHYSICS_SANDHI_RULES:
        targets.add(r.compound_prefix)
    return tuple(sorted(targets, key=len, reverse=True))


__all__ = [
    "SANDHI_OPEN",
    "SANDHI_CLOSE",
    "SANDHI_SEP",
    "SandhiSlot",
    "SandhiRule",
    "PHYSICS_SANDHI_RULES",
    "list_rules",
    "rule_by_name",
    "canonical_json",
    "apply_forward",
    "apply_inverse",
    "all_sandhi_target_glyphs",
]
