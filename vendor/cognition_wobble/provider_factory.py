"""Construct a cognition provider from a ``"kind:model"`` spec string.

Shared by the Step 5c generation + labeling CLIs. The concrete adapters are
imported lazily *inside* :func:`build_provider`, so importing this module pulls
in no provider SDKs (anthropic/openai/google/litellm); the SDK + API-key checks
happen at construction time (raising ``CognitionAuthError`` / the typed
``CognitionError`` if the env var or SDK is missing).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phoenix.providers.cognition.protocol import CognitionProvider

__all__ = ["build_provider"]


def build_provider(spec: str) -> CognitionProvider:
    """Build a provider from ``"kind:model"`` (e.g. ``"anthropic:claude-..."``).

    Supported kinds: ``anthropic``, ``openai``, ``google`` (alias ``gemini``),
    ``litellm``. Reads the provider's API key from the environment. Raises
    :class:`ValueError` for a malformed spec or unknown kind.
    """
    kind, _, model = spec.partition(":")
    kind = kind.strip().lower()
    model = model.strip()
    if not model:
        raise ValueError(f"provider spec '{spec}' must be 'kind:model'")
    if kind == "anthropic":
        from phoenix.providers.cognition.anthropic import AnthropicProvider

        return AnthropicProvider(model=model)
    if kind == "openai":
        from phoenix.providers.cognition.openai import OpenAIProvider

        return OpenAIProvider(model=model)
    if kind in ("google", "gemini"):
        from phoenix.providers.cognition.google import GoogleGeminiProvider

        return GoogleGeminiProvider(model=model)
    if kind == "litellm":
        from phoenix.providers.cognition.litellm_passthrough import LiteLLMPassthroughProvider

        return LiteLLMPassthroughProvider(litellm_model=model)
    raise ValueError(f"unknown provider kind '{kind}' (anthropic|openai|google|litellm)")
