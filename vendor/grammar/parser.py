#!/usr/bin/env python3
"""
Dr. Frank & Eddy v6.4.1 -- Generative grammar: recursive-descent parser (Phase 3.2)

Given a `Grammar` and a candidate string, `parse` either returns a
`ParseTree` that reconstructs the derivation structure, or raises
`ParseError`.

Strategy: classic recursive-descent with memoisation (a Packrat-style
cache keyed on `(non_terminal, position)`) so overlapping alternatives
don't explode to O(2^n) on deep grammars. For unambiguous grammars the
cache is populated once per (NT, pos) and the parse is O(n * grammar).

Edge cases handled:
  - Left-recursive productions are protected by a `depth_left` budget
    and a "no-progress" check -- if a non-terminal attempts to expand
    itself at the same position without consuming any characters, we
    return a miss for that branch instead of looping.
  - Terminal matches are verbatim (re.escape'd during comparison via
    str.startswith on the remaining input).

Intended as the inverse of `generator.generate`: for any seed+depth
that produced a string `s`, `parse(grammar, s)` yields a tree whose
pre-order terminal sequence equals `s`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .grammar_loader import Grammar, Production, Symbol


DEFAULT_PARSE_DEPTH = 64


class ParseError(ValueError):
    """Raised when the parser cannot match the input against the grammar."""


@dataclass
class ParseTree:
    """Derivation tree node.

    `kind="nonterminal"` nodes carry their LHS name and the list of child
    trees produced by the matched production. `kind="terminal"` nodes
    carry the literal string consumed."""
    kind: str          # "nonterminal" | "terminal"
    value: str         # non-terminal name OR terminal literal
    children: List["ParseTree"] = field(default_factory=list)

    def to_serialisable(self) -> dict:
        return {
            "kind": self.kind,
            "value": self.value,
            "children": [c.to_serialisable() for c in self.children],
        }

    def collect_terminals(self) -> str:
        """Pre-order concatenation of terminal leaves. Equivalent to the
        original generated string for a clean round-trip."""
        out: List[str] = []
        def _walk(node: ParseTree) -> None:
            if node.kind == "terminal":
                out.append(node.value)
            else:
                for c in node.children:
                    _walk(c)
        _walk(self)
        return "".join(out)


@dataclass
class _ParseResult:
    tree: ParseTree
    consumed: int       # total characters consumed starting at `pos`


# ---- Memoisation cache --------------------------------------------------

# Cache key: (lhs_name, position, depth_left). Value: Optional[_ParseResult]
# where None means "no match attempted / known failure".
_CacheKey = Tuple[str, int, int]


@dataclass
class _ParseState:
    grammar: Grammar
    text: str
    cache: Dict[_CacheKey, Optional[_ParseResult]] = field(default_factory=dict)
    # Track in-flight (nonterminal, pos) pairs to break left-recursive
    # cycles without progress. Entry is popped when the call returns.
    active: Dict[Tuple[str, int], int] = field(default_factory=dict)


def _try_alt(
    state: _ParseState,
    prod: Production,
    pos: int,
    depth_left: int,
) -> Optional[_ParseResult]:
    """Attempt to match `prod`'s RHS starting at `pos`. Returns None
    if any symbol fails. Consumes the longest possible prefix per
    greedy recursive-descent."""
    children: List[ParseTree] = []
    cursor = pos
    for sym in prod.rhs:
        if sym.is_terminal:
            if state.text.startswith(sym.value, cursor):
                children.append(ParseTree(kind="terminal", value=sym.value))
                cursor += len(sym.value)
            else:
                return None
        else:
            sub = _parse_nonterminal(state, sym.value, cursor, depth_left)
            if sub is None:
                return None
            children.append(sub.tree)
            cursor = pos + sum(
                _len(c) for c in children
            )
    return _ParseResult(
        tree=ParseTree(
            kind="nonterminal", value=prod.lhs, children=children,
        ),
        consumed=cursor - pos,
    )


def _len(node: ParseTree) -> int:
    """Character length the tree consumed from the input."""
    if node.kind == "terminal":
        return len(node.value)
    return sum(_len(c) for c in node.children)


def _parse_nonterminal(
    state: _ParseState,
    lhs: str,
    pos: int,
    depth_left: int,
) -> Optional[_ParseResult]:
    """Try all alternatives for `lhs` starting at `pos`. Packrat-cached
    on (lhs, pos, depth_left). Left-recursion without progress returns
    None."""
    if depth_left <= 0:
        return None
    key: _CacheKey = (lhs, pos, depth_left)
    if key in state.cache:
        return state.cache[key]

    # Left-recursion break: if we are already expanding this NT at this
    # same position, bail out of the inner attempt.
    active_key = (lhs, pos)
    if state.active.get(active_key, 0) > 0:
        state.cache[key] = None
        return None
    state.active[active_key] = state.active.get(active_key, 0) + 1

    best: Optional[_ParseResult] = None
    try:
        for prod in state.grammar.alternatives(lhs):
            attempt = _try_alt(state, prod, pos, depth_left - 1)
            if attempt is None:
                continue
            if best is None or attempt.consumed > best.consumed:
                best = attempt
    finally:
        state.active[active_key] -= 1

    state.cache[key] = best
    return best


def parse(
    grammar: Grammar,
    text: str,
    *,
    start: Optional[str] = None,
    max_depth: int = DEFAULT_PARSE_DEPTH,
) -> ParseTree:
    """Parse `text` against `grammar`, returning the derivation tree.

    Parameters
    ----------
    grammar     : validated Grammar
    text        : candidate string. Must be EXACTLY consumed on success;
                  a partial match raises ParseError.
    start       : override the grammar's declared start non-terminal.
    max_depth   : recursion budget (default 64). Must comfortably exceed
                  the generator's max_depth so the parser can reconstruct
                  deeply-nested trees. Raise to handle tall grammars.

    Raises
    ------
    ParseError if no valid derivation is found or the match leaves
    trailing characters unconsumed.
    """
    start_symbol = start or grammar.start
    if start_symbol not in grammar.productions:
        raise ParseError(
            f"start symbol {start_symbol!r} is not defined in grammar"
        )
    if text == "":
        raise ParseError("cannot parse empty string")
    state = _ParseState(grammar=grammar, text=text)
    result = _parse_nonterminal(state, start_symbol, 0, max_depth)
    if result is None:
        raise ParseError(
            f"no derivation found for <{start_symbol}> at pos 0; "
            f"input starts with {text[:40]!r}"
        )
    if result.consumed != len(text):
        raise ParseError(
            f"partial match: consumed {result.consumed}/{len(text)} chars; "
            f"trailing: {text[result.consumed:result.consumed + 40]!r}"
        )
    return result.tree


__all__ = [
    "DEFAULT_PARSE_DEPTH",
    "ParseError",
    "ParseTree",
    "parse",
]
