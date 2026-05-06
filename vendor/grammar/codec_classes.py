#!/usr/bin/env python3
"""
Dr. Frank & Eddy v6.4.1 -- Pratyāhāra Class Definitions (Phase 2.1)

Pāṇini's pratyāhāras are two-character abbreviations that refer to
entire *classes* of sounds. `aC` = all vowels. `haL` = all consonants.
One short name stands in for a set, so grammatical rules can quantify
over members instead of enumerating them.

In our codec, a "class" is a named set of morpheme sources (keys from
_MORPHEMES in sanskrit_codec). The class itself has:
  - name        -- human identifier (e.g. "Psi-family")
  - marker      -- one glyph that labels the class (e.g. Ψ)
  - members     -- tuple of morpheme source strings
  - description -- prose comment, NOT counted in MDL rule_bytes

The class-glyph wire form is three chars:
      CLASS_OPEN_MARKER + marker + CLASS_CLOSE_MARKER
so `⦗Ψ⦘` stands for "any morpheme in the Psi-family". The full
three-char sequence is distinct from the bare `Ψ` glyph (which is a
_KEYWORDS target for the word "quantum") -- those don't collide.

Phase 2.1 contract:
  - Classes are defined and exposed via an API / CLI / MDL surface.
  - NOTHING in encode() or decode() uses class-glyphs yet.
  - Phase 2.2 (sandhi) will introduce rules of the form
    "⦗∂⦘ of ⦗Ψ⦘" -> compound glyph, quantified over class members.

This file is the read-only source of truth for class definitions.
Any new class must be added by code review (not runtime), consistent
with how _PHRASES / _MORPHEMES are declared.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# Class-boundary glyphs. Picked from Unicode bracket block; verified in
# tests not to collide with any target in _PHRASES / _OPERATORS /
# _KEYWORDS / _MORPHEMES / COMPOUND_SEP / ESCAPE_MARKER.
CLASS_OPEN_MARKER = "\u2997"   # ⦗ LEFT BLACK TORTOISE SHELL BRACKET
CLASS_CLOSE_MARKER = "\u2998"  # ⦘ RIGHT BLACK TORTOISE SHELL BRACKET


@dataclass(frozen=True)
class MorphemeClass:
    """A named set of morphemes sharing a physical / linguistic role.
    Frozen so instances can be hashed and used in sets."""
    name: str                          # e.g. "Psi-family"
    marker: str                        # one-glyph class identifier, e.g. "Ψ"
    members: Tuple[str, ...]           # morpheme source words in this class
    description: str = ""              # prose; not counted in MDL

    @property
    def class_glyph(self) -> str:
        """Wire form: CLASS_OPEN_MARKER + marker + CLASS_CLOSE_MARKER."""
        return CLASS_OPEN_MARKER + self.marker + CLASS_CLOSE_MARKER

    def to_canonical(self) -> Dict[str, object]:
        """Stable dict form for MDL serialization. Excludes description
        (which is documentation, not rule). Members are sorted for
        determinism -- order in a class doesn't affect semantics."""
        return {
            "name": self.name,
            "marker": self.marker,
            "members": sorted(self.members),
        }


# -----------------------------------------------------------------------
# Class definitions. Ordering is alphabetical-by-name for stability.
# Each morpheme member MUST be a source key that exists in
# sanskrit_codec._MORPHEMES. Tests enforce this.
# -----------------------------------------------------------------------

PHYSICS_CLASSES: Tuple[MorphemeClass, ...] = (
    MorphemeClass(
        name="A-family",
        marker="\U0001D538",  # 𝔸 (double-struck A)
        members=("tensor", "matrix", "vector", "spin"),
        description="Algebraic structures: tensors, matrices, vectors, and "
                    "spin objects. Quantities whose compositional behaviour "
                    "(tensor product, matrix mult, direct sum) is the focus.",
    ),
    MorphemeClass(
        name="E-family",
        marker="E",   # plain capital E -- readability > strictness here;
                      # full class-glyph is ⦗E⦘ which is unambiguous
        members=("energy", "potential", "kinetic", "amplitude",
                 "phase", "frequency", "wavelength"),
        description="Energy-like scalars and wave descriptors.",
    ),
    MorphemeClass(
        name="Mu-family",
        marker="\u03BC",  # μ
        members=("density", "expected", "variance", "commutator"),
        description="Measurement / statistical operations and their outputs.",
    ),
    MorphemeClass(
        name="Partial-family",
        marker="\u2202",  # ∂
        members=("gradient", "partial", "derivative",
                 "laplacian", "divergence", "curl"),
        description="Differential operators acting on fields and functions.",
    ),
    MorphemeClass(
        name="Psi-family",
        marker="\u03A8",  # Ψ
        members=("eigen", "wave", "state", "function",
                 "bra", "ket", "hermitian", "unitary",
                 "observable", "hamiltonian"),
        description="Quantum concepts: states, functions, bra-ket objects, "
                    "and operators whose eigenstructure is physically "
                    "meaningful.",
    ),
    MorphemeClass(
        name="Star-family",
        marker="\u2733",  # ✳ (EIGHT SPOKED ASTERISK) -- distinct from ✱
                           # (HEAVY ASTERISK) which is U+2731, both rare
        members=("hbar", "speedoflight", "boltzmann",
                 "electroncharge", "finestructure"),
        description="Fundamental physical constants. Dimensional anchors "
                    "rather than variable quantities.",
    ),
    MorphemeClass(
        name="Theta-family",
        marker="\u03D1",  # ϑ (GREEK THETA SYMBOL) -- distinct from Θ
                           # U+0398 which _KEYWORDS uses for "theorem"
        members=("entropy", "temperature", "pressure", "volume", "mass"),
        description="Thermodynamic state variables.",
    ),
    MorphemeClass(
        name="V-family",
        marker="V",   # plain capital V -- class-glyph ⦗V⦘ is unambiguous
        members=("position", "velocity", "acceleration",
                 "momentum", "displacement"),
        description="Vector kinematic quantities (one-particle mechanics).",
    ),
)


# -----------------------------------------------------------------------
# Public lookup helpers
# -----------------------------------------------------------------------

def list_classes() -> Tuple[MorphemeClass, ...]:
    """Return all defined classes in stable order."""
    return PHYSICS_CLASSES


def class_by_name(name: str) -> MorphemeClass:
    """Lookup a class by name. Raises KeyError on unknown name."""
    for c in PHYSICS_CLASSES:
        if c.name == name:
            return c
    raise KeyError(f"unknown class: {name!r}")


def classes_for_morpheme(morpheme_source: str) -> Tuple[MorphemeClass, ...]:
    """Return the classes a given morpheme source belongs to. Most
    morphemes belong to exactly one class; this returns a tuple so
    future multi-class members (if we ever add them) aren't a schema
    change."""
    return tuple(c for c in PHYSICS_CLASSES if morpheme_source in c.members)


def is_in_class(morpheme_source: str, class_name: str) -> bool:
    """True iff `morpheme_source` is a member of the named class."""
    try:
        return morpheme_source in class_by_name(class_name).members
    except KeyError:
        return False


def class_marker_glyphs() -> Tuple[str, ...]:
    """All distinct marker glyphs (not the full ⦗marker⦘ wire form)."""
    return tuple(sorted({c.marker for c in PHYSICS_CLASSES}))


def class_glyphs() -> Tuple[str, ...]:
    """All full class-glyph wire forms (⦗marker⦘), sorted for stability."""
    return tuple(sorted({c.class_glyph for c in PHYSICS_CLASSES}))


def canonical_json() -> str:
    """Stable JSON representation of all classes. Used by MDL rule-bytes
    accounting. Sorted by class name, members sorted within each."""
    payload = [c.to_canonical() for c in PHYSICS_CLASSES]
    return json.dumps(
        payload, ensure_ascii=True, allow_nan=False, sort_keys=True,
    )


__all__ = [
    "CLASS_OPEN_MARKER",
    "CLASS_CLOSE_MARKER",
    "MorphemeClass",
    "PHYSICS_CLASSES",
    "canonical_json",
    "class_by_name",
    "class_glyphs",
    "class_marker_glyphs",
    "classes_for_morpheme",
    "is_in_class",
    "list_classes",
]
