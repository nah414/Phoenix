"""Tests for the Step 2 ProviderRegistry extension for cognition providers."""

from __future__ import annotations

from phoenix.providers.cognition.capabilities import CognitionCapabilities
from phoenix.providers.cognition.protocol import CognitionProvider
from phoenix.providers.cognition.types import (
    CognitionResult,
    Prompt,
    TokenUsage,
    Tool,
)
from phoenix.router.provider_registry import ProviderEntry, ProviderRegistry


class _FakeCognitionProvider:
    """Minimal CognitionProvider conformant for registry registration."""

    provider_id = "fake.cognition"
    model = "fake-model"

    def complete(
        self,
        prompt: Prompt,
        *,
        max_tokens: int,
        temperature: float,
        tools: list[Tool] | None = None,
        stream: bool = False,
    ) -> CognitionResult:
        del prompt, max_tokens, temperature, tools, stream
        return CognitionResult(
            text="",
            tool_calls=[],
            usage=TokenUsage(input_tokens=0, output_tokens=0),
            latency_ms=0.0,
            provider_fingerprint="fake.cognition|fake-model",
        )

    def capabilities(self) -> CognitionCapabilities:
        return CognitionCapabilities(
            streaming=False,
            tool_use=False,
            vision=False,
            max_context_tokens=4096,
            supports_prompt_cache=False,
            supports_batch=False,
        )

    def fingerprint(self) -> str:
        return "fake.cognition|fake-model"


def test_cognition_provider_registers() -> None:
    """A CognitionProvider can be wrapped in a ProviderEntry and registered."""
    registry = ProviderRegistry()
    entry = ProviderEntry(client=_FakeCognitionProvider())
    registry.register(entry)
    assert registry.get("fake.cognition") is entry
    assert "fake.cognition" in registry


def test_cognition_entries_filters() -> None:
    """cognition_entries() returns only entries whose client is a CognitionProvider."""
    from phoenix.providers.classical.local_simulator import LocalClassicalSimulator

    registry = ProviderRegistry()
    registry.register(ProviderEntry(client=LocalClassicalSimulator()))
    registry.register(ProviderEntry(client=_FakeCognitionProvider()))

    cognition = registry.cognition_entries()
    all_entries = registry.all_entries()

    assert len(all_entries) == 2
    assert len(cognition) == 1
    assert isinstance(cognition[0].client, CognitionProvider)


def test_cognition_entries_empty_for_physics_only_registry() -> None:
    """cognition_entries() is [] when no cognition providers registered."""
    from phoenix.providers.classical.local_simulator import LocalClassicalSimulator

    registry = ProviderRegistry()
    registry.register(ProviderEntry(client=LocalClassicalSimulator()))

    assert registry.cognition_entries() == []
