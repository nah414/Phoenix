"""Reference identity adapter (Phase 9 Step 1, locked OPEN-1).

A weights-free echo adapter that satisfies the :class:`LoRAAdapter`
Protocol. Two purposes:

1. **Test vehicle** -- the adapter loader / validator / registry
   tests exercise their happy path against this adapter without
   requiring real LoRA weights. The validator's round-trip test
   passes trivially because ``decode(encode(x)) == x`` by
   construction.

2. **Client template** -- developers bringing real LoRA weights
   can copy this file as a starting point. The shape is right;
   only the four method bodies need to change.

The identity adapter is **always loadable** -- it has no external
dependencies, no weight files, no network. Tests rely on this
property: a fresh registry can be populated with this adapter
without any setup.

**Fingerprint:** the identity adapter's fingerprint is a constant
SHA-256 of the literal string ``"phoenix.adapters.identity_adapter:v1"``
so it's stable across processes + platforms.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


_IDENTITY_FINGERPRINT_SEED = "phoenix.adapters.identity_adapter:v1"


def _compute_identity_fingerprint() -> str:
    """SHA-256 of a constant seed -- stable across processes."""
    return hashlib.sha256(_IDENTITY_FINGERPRINT_SEED.encode("utf-8")).hexdigest()


@dataclass
class IdentityAdapter:
    """Reference echo adapter -- encode/decode are pass-through.

    Satisfies :class:`~phoenix.adapters.protocol.LoRAAdapter`
    structurally. ``capabilities`` defaults to ``["identity"]`` so
    the task-grammar layer can opt in explicitly via that
    capability token.
    """

    name: str = "identity"
    version: str = "1.0.0"
    base_model_fingerprint: str = "phoenix.identity_adapter.no_base_model"
    capabilities: list[str] = field(default_factory=lambda: ["identity"])

    def encode_to_grammar(self, natural_language: str) -> str:
        """Pass-through: the input IS the grammar string.

        A real LoRA adapter would translate from natural language
        into one of the grammar productions Section 3.2's vendored
        grammar parser accepts. The identity adapter trusts the
        caller passed a grammar string already.
        """
        return natural_language

    def decode_from_grammar(self, tokens: str) -> str:
        """Pass-through: grammar tokens go back unchanged."""
        return tokens

    def fingerprint(self) -> str:
        """Constant SHA-256 of the identity seed.

        Stable across processes + platforms -- the replay engine
        can compare against a ledger entry's recorded fingerprint
        without ambiguity.
        """
        return _compute_identity_fingerprint()


def make_identity_adapter(
    *,
    name: str = "identity",
    capabilities: list[str] | None = None,
) -> IdentityAdapter:
    """Construct an :class:`IdentityAdapter` with optional name override.

    Tests use this to register multiple identity adapters under
    different names ("identity-a", "identity-b") for registry
    behavior tests.
    """
    return IdentityAdapter(
        name=name,
        capabilities=list(capabilities) if capabilities is not None else ["identity"],
    )


__all__ = [
    "IdentityAdapter",
    "make_identity_adapter",
]
