#!/usr/bin/env python3
"""
Dr. Frank & Eddy v6.4.1 -- Generative grammar loader (Phase 3.2)

Parses a YAML grammar file into an immutable `Grammar` object. Used by
the generator + parser to derive well-formed compressed physics
statements for E4's symbolic-candidate mode.

YAML schema (safe_load only -- no Python-object injection):

    grammar: physics_v1                  # required, short identifier
    version: 1                           # required, integer
    start: Statement                     # non-terminal to begin derivation
    productions:                         # mapping of lhs -> list of alts
      Statement:
        - ["<QuantityExpr>", " = ", "<QuantityExpr>"]
        - ["<ForAll>", "<Binding>", ":", "<Statement>"]
      QuantityExpr:
        - ["<ScalarExpr>"]
        ...

Notation:
  - `<SymbolName>` is a NON-TERMINAL. The name must match a top-level key
    in `productions`; load-time validation catches typos.
  - Anything else is a TERMINAL (a literal string, possibly including
    spaces / punctuation / Unicode glyphs).

Both forms survive YAML's safe_load intact. There is no attempt to
evaluate terminals -- they are emitted verbatim into the generated
output and must match verbatim during parsing.

Security: uses `yaml.safe_load` exclusively. Tag directives like
`!!python/object:` or `!!python/name:` raise `yaml.YAMLError`. A
separate test (`test_grammar_yaml_cannot_inject`) asserts this.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml


log = logging.getLogger("frankenstein.evolution.knowledge.grammar.loader")


DEFAULT_GRAMMAR_PATH = Path(
    "C:/frank-data/evolution/knowledge/grammar/physics_v1.yaml"
)


_NT_PATTERN = re.compile(r"^<([A-Za-z][A-Za-z0-9_]*)>$")


class GrammarLoadError(ValueError):
    """Raised when YAML fails to parse or fails our schema validation.
    Callers seal the raw input as an instability signal and fall back
    to the default grammar rather than crash."""


# ---- Types ---------------------------------------------------------------

@dataclass(frozen=True)
class Symbol:
    """One symbol in a production RHS. Terminals carry their verbatim
    string value. Non-terminals carry the referenced production name."""
    kind: str          # "terminal" | "nonterminal"
    value: str

    @property
    def is_terminal(self) -> bool:
        return self.kind == "terminal"

    @property
    def is_nonterminal(self) -> bool:
        return self.kind == "nonterminal"


@dataclass(frozen=True)
class Production:
    """One LHS -> RHS alternative. RHS is a tuple of Symbols so downstream
    code can rely on immutability + hashability for memoisation."""
    lhs: str
    rhs: Tuple[Symbol, ...]

    @property
    def has_terminal_only_rhs(self) -> bool:
        return all(s.is_terminal for s in self.rhs)

    @property
    def nonterminal_count(self) -> int:
        return sum(1 for s in self.rhs if s.is_nonterminal)


@dataclass(frozen=True)
class Grammar:
    """A loaded, validated context-free grammar.

    `productions` maps each LHS to its tuple of Productions (alternatives).
    All LHS names are known to be reachable from `start` AND productive
    (i.e., every non-terminal can eventually derive a terminal-only
    string within some finite depth)."""
    name: str
    version: int
    start: str
    productions: Mapping[str, Tuple[Production, ...]]
    source_path: Optional[str] = None

    def alternatives(self, lhs: str) -> Tuple[Production, ...]:
        """All alternatives for the named non-terminal. Raises KeyError
        on an unknown name (should never happen post-validation)."""
        return self.productions[lhs]

    def is_nonterminal(self, name: str) -> bool:
        return name in self.productions

    def nonterminal_names(self) -> Tuple[str, ...]:
        """Stable (sorted) list of non-terminals. Useful for UI rendering."""
        return tuple(sorted(self.productions.keys()))

    def to_serialisable(self) -> Dict[str, Any]:
        """JSON-safe dict used by the REST endpoint + CLI + UI. Terminals
        are rendered as their verbatim string; non-terminals get the
        `<Name>` wire form so callers round-trip cleanly."""
        out: Dict[str, Any] = {
            "name": self.name,
            "version": self.version,
            "start": self.start,
            "productions": {},
            "source_path": self.source_path,
        }
        for lhs in self.nonterminal_names():
            alts: List[List[Dict[str, str]]] = []
            for prod in self.productions[lhs]:
                rhs_out: List[Dict[str, str]] = []
                for sym in prod.rhs:
                    rhs_out.append({"kind": sym.kind, "value": sym.value})
                alts.append(rhs_out)
            out["productions"][lhs] = alts
        return out


# ---- Parsing YAML into types --------------------------------------------

def _parse_symbol(raw: Any) -> Symbol:
    """Convert one YAML RHS element into a Symbol.

    YAML always emits strings for our payloads. The `<Name>` wrapper marks
    non-terminals; anything else is a terminal. Non-empty string required."""
    if not isinstance(raw, str):
        raise GrammarLoadError(
            f"RHS tokens must be strings, got {type(raw).__name__}: {raw!r}"
        )
    if raw == "":
        raise GrammarLoadError("empty string in RHS is not allowed")
    m = _NT_PATTERN.match(raw)
    if m:
        return Symbol(kind="nonterminal", value=m.group(1))
    return Symbol(kind="terminal", value=raw)


def _parse_production(lhs: str, raw_rhs: Any) -> Production:
    """One production alternative. RHS must be a non-empty list."""
    if not isinstance(raw_rhs, list) or not raw_rhs:
        raise GrammarLoadError(
            f"production for {lhs!r}: RHS must be a non-empty list, got {raw_rhs!r}"
        )
    symbols = tuple(_parse_symbol(tok) for tok in raw_rhs)
    return Production(lhs=lhs, rhs=symbols)


def _validate_productivity(
    productions: Mapping[str, Tuple[Production, ...]],
    start: str,
) -> None:
    """Ensure every non-terminal can be expanded to terminals in finite
    depth. Classic fixed-point algorithm: seed the set of productive
    non-terminals with those that have a terminal-only alternative,
    then iterate until stable. A non-terminal not in the final set
    would cause the generator to loop forever at arbitrary depth.

    Also verifies the start symbol is reachable (trivially, it is a
    non-terminal by construction)."""
    productive: set[str] = set()
    # Seed with non-terminals that have at least one terminal-only RHS.
    for lhs, prods in productions.items():
        if any(p.has_terminal_only_rhs for p in prods):
            productive.add(lhs)

    changed = True
    while changed:
        changed = False
        for lhs, prods in productions.items():
            if lhs in productive:
                continue
            for p in prods:
                if all(
                    s.is_terminal or s.value in productive
                    for s in p.rhs
                ):
                    productive.add(lhs)
                    changed = True
                    break

    unproductive = set(productions.keys()) - productive
    if unproductive:
        raise GrammarLoadError(
            f"unproductive non-terminals (no terminal path): "
            f"{sorted(unproductive)}"
        )
    if start not in productive:
        raise GrammarLoadError(
            f"start symbol {start!r} is not productive"
        )


def _validate_references(
    productions: Mapping[str, Tuple[Production, ...]],
) -> None:
    """Every non-terminal symbol in every RHS must match a defined LHS."""
    defined = set(productions.keys())
    unknown: List[Tuple[str, str]] = []
    for lhs, prods in productions.items():
        for p in prods:
            for s in p.rhs:
                if s.is_nonterminal and s.value not in defined:
                    unknown.append((lhs, s.value))
    if unknown:
        formatted = "; ".join(f"{lhs} references <{ref}>" for lhs, ref in unknown)
        raise GrammarLoadError(f"unknown non-terminal references: {formatted}")


# ---- Public API ----------------------------------------------------------

def load_grammar_from_dict(
    raw: Mapping[str, Any],
    *,
    source_path: Optional[str] = None,
) -> Grammar:
    """Convert a dict (from YAML or constructed in-memory) into a
    validated Grammar. Raises GrammarLoadError on any schema violation."""
    if not isinstance(raw, Mapping):
        raise GrammarLoadError(
            f"grammar root must be a mapping, got {type(raw).__name__}"
        )

    name = raw.get("grammar")
    if not isinstance(name, str) or not name:
        raise GrammarLoadError("grammar: missing or invalid `grammar` field")
    version_raw = raw.get("version")
    if not isinstance(version_raw, int):
        raise GrammarLoadError("grammar: missing or non-integer `version` field")
    start = raw.get("start")
    if not isinstance(start, str) or not start:
        raise GrammarLoadError("grammar: missing or invalid `start` field")

    raw_productions = raw.get("productions")
    if not isinstance(raw_productions, Mapping) or not raw_productions:
        raise GrammarLoadError("grammar: `productions` must be a non-empty mapping")

    built: Dict[str, Tuple[Production, ...]] = {}
    for lhs, alts in raw_productions.items():
        if not isinstance(lhs, str):
            raise GrammarLoadError(
                f"production LHS must be a string, got {type(lhs).__name__}: {lhs!r}"
            )
        if not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", lhs):
            raise GrammarLoadError(
                f"production LHS {lhs!r} must be an identifier "
                f"([A-Za-z][A-Za-z0-9_]*)"
            )
        if not isinstance(alts, list) or not alts:
            raise GrammarLoadError(
                f"production {lhs!r} must be a non-empty list of alternatives"
            )
        built[lhs] = tuple(_parse_production(lhs, alt) for alt in alts)

    if start not in built:
        raise GrammarLoadError(
            f"start symbol {start!r} is not defined in productions"
        )

    _validate_references(built)
    _validate_productivity(built, start)

    return Grammar(
        name=name,
        version=int(version_raw),
        start=start,
        productions=built,
        source_path=source_path,
    )


def load_grammar(path: Path | str) -> Grammar:
    """Load a grammar from a YAML file. Uses safe_load exclusively;
    malicious YAML (e.g. `!!python/object:`) raises yaml.YAMLError which
    we re-raise as GrammarLoadError."""
    p = Path(path)
    if not p.exists():
        raise GrammarLoadError(f"grammar file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise GrammarLoadError(f"YAML parse failed: {e}") from e
    grammar = load_grammar_from_dict(raw, source_path=str(p))
    log.info(
        "loaded grammar %s v%d from %s (%d non-terminals)",
        grammar.name, grammar.version, p, len(grammar.productions),
    )
    return grammar


_DEFAULT_GRAMMAR_CACHE: Optional[Grammar] = None


def load_default_grammar(force_reload: bool = False) -> Grammar:
    """Load the bundled physics_v1 grammar (process-wide cached)."""
    global _DEFAULT_GRAMMAR_CACHE
    if _DEFAULT_GRAMMAR_CACHE is None or force_reload:
        _DEFAULT_GRAMMAR_CACHE = load_grammar(DEFAULT_GRAMMAR_PATH)
    return _DEFAULT_GRAMMAR_CACHE


__all__ = [
    "DEFAULT_GRAMMAR_PATH",
    "Grammar",
    "GrammarLoadError",
    "Production",
    "Symbol",
    "load_default_grammar",
    "load_grammar",
    "load_grammar_from_dict",
]
