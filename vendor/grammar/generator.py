#!/usr/bin/env python3
"""
Dr. Frank & Eddy v6.4.1 -- Generative grammar: derivation engine (Phase 3.2)

Deterministic (seed-driven) derivation from a `Grammar`. Given the same
grammar + seed + max_depth, `generate` always returns the same string.

Algorithm:
  - random.Random(seed) drives every choice.
  - At each non-terminal, we have a budget `depth_left`. If > 0 we pick
    any alternative uniformly at random. If <= 0 we pick ONLY among
    alternatives whose RHS is terminal-only (or, if none exists for this
    non-terminal, among the alternatives with the fewest non-terminals,
    and recurse with reset depth -- the productivity invariant from the
    grammar loader guarantees termination).
  - Terminal symbols emit their literal `value` verbatim.

Output is the concatenation of emitted terminals. The grammar author
controls spacing by placing whitespace inside terminals (e.g. ` = ` with
leading + trailing spaces).

`max_depth` is defensive: the productivity invariant guarantees we won't
actually recurse forever, but a misconfigured grammar + a hostile seed
could still try. The outer loop caps at `hard_cap` recursive calls and
raises `GenerationError` if exceeded.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List, Optional, Sequence

from .grammar_loader import Grammar, Production, Symbol


DEFAULT_MAX_DEPTH = 6
DEFAULT_HARD_CAP = 10_000   # absolute recursion-count ceiling


class GenerationError(RuntimeError):
    """Raised when the generator cannot terminate -- typically because
    the grammar's productivity invariant was violated at runtime or
    because `hard_cap` was reached."""


@dataclass
class _GenContext:
    rng: random.Random
    parts: List[str]
    calls: int
    hard_cap: int

    def emit(self, value: str) -> None:
        self.parts.append(value)

    def bump(self) -> None:
        self.calls += 1
        if self.calls > self.hard_cap:
            raise GenerationError(
                f"generator exceeded hard_cap={self.hard_cap} recursive calls"
            )


def _pick_alternative(
    alts: Sequence[Production],
    depth_left: int,
    rng: random.Random,
) -> Production:
    """Depth-aware choice:

      - depth_left > 0: uniform pick over ALL alternatives.
      - depth_left == 0: prefer terminal-only alternatives; if none
        exist, prefer the alternative with fewest non-terminals
        (productivity ensures at least one such path exists).
    """
    if depth_left > 0:
        return rng.choice(list(alts))

    terminal_only = [a for a in alts if a.has_terminal_only_rhs]
    if terminal_only:
        return rng.choice(terminal_only)

    min_nt = min(a.nonterminal_count for a in alts)
    shortest = [a for a in alts if a.nonterminal_count == min_nt]
    return rng.choice(shortest)


def _derive(
    grammar: Grammar,
    symbol: Symbol,
    depth_left: int,
    ctx: _GenContext,
) -> None:
    ctx.bump()
    if symbol.is_terminal:
        ctx.emit(symbol.value)
        return
    alts = grammar.alternatives(symbol.value)
    prod = _pick_alternative(alts, depth_left, ctx.rng)
    next_depth = max(depth_left - 1, 0) if depth_left > 0 else 0
    for sym in prod.rhs:
        _derive(grammar, sym, next_depth, ctx)


def generate(
    grammar: Grammar,
    *,
    start: Optional[str] = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
    seed: Optional[int] = None,
    hard_cap: int = DEFAULT_HARD_CAP,
) -> str:
    """Return a well-formed encoded string drawn from `grammar`.

    Parameters
    ----------
    grammar     : validated Grammar
    start       : override the grammar's declared start non-terminal
    max_depth   : non-terminal expansion budget (default 6). Once depth
                  is exhausted the generator picks base-case alternatives
                  -- see `_pick_alternative`.
    seed        : integer seed for `random.Random`. Same (grammar, seed,
                  max_depth) produces the same output.
    hard_cap    : absolute recursion-call ceiling (default 10_000).
                  Guards against pathological grammars at runtime.

    Raises
    ------
    GenerationError if the grammar fails to produce a terminal-only
    expansion within `hard_cap` recursive calls.
    """
    start_symbol = start or grammar.start
    if start_symbol not in grammar.productions:
        raise GenerationError(
            f"start symbol {start_symbol!r} is not defined in grammar"
        )
    if max_depth < 0:
        raise GenerationError(f"max_depth must be >= 0, got {max_depth}")

    rng = random.Random(seed if seed is not None else 0)
    ctx = _GenContext(rng=rng, parts=[], calls=0, hard_cap=int(hard_cap))
    _derive(grammar, Symbol("nonterminal", start_symbol), max_depth, ctx)
    return "".join(ctx.parts)


__all__ = [
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_HARD_CAP",
    "GenerationError",
    "generate",
]
