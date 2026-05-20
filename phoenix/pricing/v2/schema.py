"""Pricing v2 schema for cognition (LLM) cost estimates.

Per Phase 13 build guide Step 1 + architecture v1 Section 4.7: pricing
v2 covers per-token LLM costs, prompt-cache discounts, and batch-API
discounts. Wires through the Phase 10 cost-ceiling engine.
:mod:`phoenix.router.pricing` (v1) covers physics; this module covers
cognition.

The v1 pricing surface at :mod:`phoenix.router.pricing` is unchanged;
pricing v2 lives at the top-level :mod:`phoenix.pricing` namespace to
reflect that cognition pricing has different consumers (verification
gate, cost ceiling) than physics pricing (router).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CognitionPricingRecord:
    """One row of the cognition pricing v2 table.

    Fields:
        provider: Provider identifier matching
            :attr:`CognitionProvider.provider_id` (e.g. ``"anthropic"``).
        model: Model identifier matching :attr:`CognitionProvider.model`
            (e.g. ``"claude-sonnet-4-7-20260418"``).
        usd_per_1m_input_tokens: USD cost per 1,000,000 input tokens.
        usd_per_1m_output_tokens: USD cost per 1,000,000 output tokens.
        prompt_cache_discount_factor: Multiplier applied to input-token
            cost for cached tokens. ``1.0`` = no discount; ``0.1`` = 90%
            discount (Anthropic's published rate as of 2026). Cost for
            cached tokens is ``cached_input_tokens / 1e6 *
            usd_per_1m_input_tokens * prompt_cache_discount_factor``.
        batch_discount_factor: Multiplier applied to total cost when the
            task ran via the provider's Batch API. ``0.5`` = 50% off
            (Anthropic, OpenAI both publish this rate).
        vision_token_multiplier: Multiplier applied to input-token cost
            for vision-content tokens. ``1.0`` = vision tokens count
            the same as text; higher for providers that bill vision
            more heavily.
    """

    provider: str
    model: str
    usd_per_1m_input_tokens: float
    usd_per_1m_output_tokens: float
    prompt_cache_discount_factor: float = 1.0
    batch_discount_factor: float = 1.0
    vision_token_multiplier: float = 1.0
