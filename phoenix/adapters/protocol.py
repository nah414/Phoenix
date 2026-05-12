"""LoRA adapter Protocol (Phase 9 Step 1).

Per architecture v1 Section 2.7: the v1 LoRA hot-swap interface ships
the **Protocol contract**, not a specific adapter. Users bring their
own LoRA weights; Phoenix's task-grammar layer (Section 3) queries
the adapter registry and routes natural-language input through any
loaded adapter that declares the required capability.

The interface is deliberately minimal: a name, version, base-model
fingerprint, declared capabilities, and three methods
(encode/decode/fingerprint). Adapters implementing this Protocol
are loadable via :func:`phoenix.adapters.loader.load_adapter`.

**The Protocol does not constrain how an adapter is implemented**:
the implementation can be a tiny in-process function (the identity
adapter), a wrapped LoRA-fine-tuned model loaded via PEFT, or a
subprocess that shells out to an inference server. The
:class:`~phoenix.adapters.sandbox.AdapterSandbox` provides the
subprocess execution path for adapters that need isolation.

**Fingerprint contract:** the fingerprint must be stable across
process lifetimes for the same adapter (Section 1 Decision 19's
replay verification depends on this). A typical implementation
SHA-256s the adapter's serialized weights + the base-model
fingerprint; the identity adapter returns a constant.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LoRAAdapter(Protocol):
    """Structural Protocol for loaded LoRA adapters.

    Per Section 2.7's interface. Implementers can be any class that
    structurally satisfies this Protocol (no inheritance required).

    Fields:

    - ``name`` (str) -- unique identifier within the registry.
      Lowercase ASCII per Section 7.3's naming convention.
    - ``version`` (str) -- semver string. Phoenix doesn't enforce
      semver; the field is informational + audit-recorded.
    - ``base_model_fingerprint`` (str) -- which LLM this adapter
      was trained on top of. Useful for replay-mode validation
      (a v6.7-Qwen3 adapter loaded against a different base would
      have a different fingerprint than the ledger entry recorded).
    - ``capabilities`` (list[str]) -- capability tokens the
      task-grammar layer routes against. E.g.
      ``["sanskrit-glyph-bidirectional"]`` for the v6.7-style
      reference, or ``["physics-domain-extension"]`` for a
      hypothetical physics-jargon translator.

    Methods:

    - ``encode_to_grammar(natural_language: str) -> str``: convert
      free-form text into a grammar token string the parser
      (Section 3.2's vendored grammar substrate) can consume.
    - ``decode_from_grammar(tokens: str) -> str``: inverse operation
      for surfaces that present grammar tokens back to humans.
    - ``fingerprint() -> str``: stable identifier for ledger
      provenance. Phoenix records this hex string in every
      :class:`~phoenix.trinity.data_model.ProvenanceTrace` that
      used the adapter, so strict + replay modes can refuse to
      execute against a different adapter version.
    """

    name: str
    version: str
    base_model_fingerprint: str
    capabilities: list[str]

    def encode_to_grammar(self, natural_language: str) -> str: ...

    def decode_from_grammar(self, tokens: str) -> str: ...

    def fingerprint(self) -> str: ...


__all__ = ["LoRAAdapter"]
