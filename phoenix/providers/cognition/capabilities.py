"""``CognitionCapabilities`` dataclass for advertised provider features.

Per architecture v1 Section 4.2 + Phase 13 build guide Step 1: each
:class:`CognitionProvider` advertises its capabilities via
:meth:`CognitionProvider.capabilities`. The router (Step 2) uses these
to filter candidate providers for a task; the verification gate (Step
4) uses them to select compatible cross-model agreement pairs.

Capabilities are advertised, not negotiated. A provider that says
``supports_prompt_cache=True`` claims the capability; if a vendor
removes prompt-caching the adapter's static fallback ships the updated
value.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CognitionCapabilities:
    """Advertised feature set of a cognition provider's model.

    Frozen because capabilities are static per ``(provider, model)``
    pair; a new model gets a new :class:`CognitionCapabilities` instance
    rather than mutating an existing one. Used by the router's
    Stage 1 (capability-filter) and Stage 6 (ranking) when
    ``task.kind == "cognition"``.
    """

    streaming: bool
    """Whether ``complete(..., stream=True)`` yields token deltas
    incrementally. ``False`` means ``stream=True`` still returns an
    aggregate response (the provider doesn't support partial
    responses)."""

    tool_use: bool
    """Whether the provider supports tool/function calling. ``False``
    means the ``tools=`` argument to ``complete`` is silently ignored
    (or raises, depending on the adapter)."""

    vision: bool
    """Whether image content in :class:`Prompt` messages is
    supported."""

    max_context_tokens: int
    """Total context window (input + output) the provider's model can
    handle in a single call. The router skips over-context tasks before
    submission."""

    supports_prompt_cache: bool
    """Whether the provider supports prompt-cache discounts (Anthropic
    cache control, OpenAI automatic prompt caching, Google context
    caching). When ``True``, :attr:`CognitionResult.prompt_cache_hit`
    is populated and the pricing v2
    ``prompt_cache_discount_factor`` applies during cost accounting."""

    supports_batch: bool
    """Whether the provider has a Batch API (Anthropic Message Batches,
    OpenAI Batch API) for large-volume async dispatch. Pricing v2's
    ``batch_discount_factor`` applies when used."""
