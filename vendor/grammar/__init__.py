"""Phase 3.2: Generative grammar layer for the Sanskrit codec.

Public surface:
  - Grammar, Production, Symbol, ParseTree, ParseError, GrammarLoadError
  - load_grammar / load_default_grammar
  - generate
  - parse
"""

from __future__ import annotations

from .grammar_loader import (
    DEFAULT_GRAMMAR_PATH,
    Grammar,
    GrammarLoadError,
    Production,
    Symbol,
    load_default_grammar,
    load_grammar,
    load_grammar_from_dict,
)
from .generator import GenerationError, generate
from .parser import ParseError, ParseTree, parse

__all__ = [
    "DEFAULT_GRAMMAR_PATH",
    "GenerationError",
    "Grammar",
    "GrammarLoadError",
    "ParseError",
    "ParseTree",
    "Production",
    "Symbol",
    "generate",
    "load_default_grammar",
    "load_grammar",
    "load_grammar_from_dict",
    "parse",
]
