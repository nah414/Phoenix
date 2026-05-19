"""Structural-shape tests for the CognitionProvider Protocol."""

from __future__ import annotations

from phoenix.providers.cognition.capabilities import CognitionCapabilities
from phoenix.providers.cognition.protocol import CognitionProvider
from phoenix.providers.cognition.types import (
    CognitionResult,
    Prompt,
    TokenUsage,
    Tool,
)


class _StubCognitionProvider:
    """Minimal stub implementing the structural Protocol for tests."""

    provider_id = "stub.fake"
    model = "stub-model-v0"

    def complete(
        self,
        prompt: Prompt,
        *,
        max_tokens: int,
        temperature: float,
        tools: list[Tool] | None = None,
        stream: bool = False,
    ) -> CognitionResult:
        return CognitionResult(
            text="ok",
            tool_calls=[],
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            latency_ms=10.0,
            provider_fingerprint=f"{self.provider_id}|{self.model}|fixed",
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
        return f"{self.provider_id}|{self.model}|fixed"


def test_stub_satisfies_protocol_runtime_checkable() -> None:
    """``@runtime_checkable`` isinstance check passes for a conforming stub."""
    provider = _StubCognitionProvider()
    assert isinstance(provider, CognitionProvider)


def test_protocol_attributes_accessible() -> None:
    """``provider_id`` + ``model`` are reachable through the Protocol type."""
    provider: CognitionProvider = _StubCognitionProvider()
    assert provider.provider_id == "stub.fake"
    assert provider.model == "stub-model-v0"


def test_complete_returns_cognition_result() -> None:
    """``complete()`` returns a properly-shaped CognitionResult."""
    p = _StubCognitionProvider()
    prompt = Prompt(
        system="be brief",
        messages=[{"role": "user", "content": "hi"}],
    )
    result = p.complete(prompt, max_tokens=10, temperature=0.0)
    assert isinstance(result, CognitionResult)
    assert result.text == "ok"
    assert result.usage.input_tokens == 1
    assert result.tool_calls == []
    assert result.raw_provider_body is None  # P13-1 default


def test_capabilities_returns_capabilities() -> None:
    """``capabilities()`` returns a CognitionCapabilities."""
    p = _StubCognitionProvider()
    caps = p.capabilities()
    assert isinstance(caps, CognitionCapabilities)
    assert caps.max_context_tokens == 4096


def test_fingerprint_is_stable_str() -> None:
    """``fingerprint()`` is a str and idempotent for fixed-config providers."""
    p = _StubCognitionProvider()
    fp1 = p.fingerprint()
    fp2 = p.fingerprint()
    assert isinstance(fp1, str)
    assert fp1 == fp2
