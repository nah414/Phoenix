"""Tests for the LiteLLMPassthroughProvider.

Per Phase 13 build guide Step 3 verification:

- Import of the module succeeds without ``litellm`` installed.
- Construction without the extra raises ``MissingOptionalDependency``.
- Mocked ``litellm.completion`` for the integration path.
- Pricing fallback chain: catalogue hit, Phoenix v2 hit, unavailable.

**License note (Step 3 verification):** LiteLLM is MIT-licensed,
Apache-2.0 compatible. Vendoring policy: LiteLLM ships in the
optional ``[litellm]`` extra; Phoenix's distribution does not bundle
LiteLLM source.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from phoenix.providers.cognition.errors import (
    CognitionAuthError,
    CognitionContextLengthError,
    CognitionRateLimitError,
    CognitionTimeoutError,
    CognitionUnavailable,
    MissingOptionalDependency,
    PricingUnavailable,
)
from phoenix.providers.cognition.types import Prompt, TokenUsage


def test_module_import_succeeds_without_litellm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing ``litellm_passthrough`` must NOT require litellm.

    The module imports litellm lazily inside ``__init__``; the module
    body itself doesn't reference litellm.
    """
    # Even with litellm absent from sys.modules, the import works.
    monkeypatch.delitem(sys.modules, "litellm", raising=False)
    monkeypatch.delitem(
        sys.modules,
        "phoenix.providers.cognition.litellm_passthrough",
        raising=False,
    )
    import phoenix.providers.cognition.litellm_passthrough  # noqa: F401


def test_construction_without_extra_raises_missing_optional() -> None:
    """Constructing without litellm installed raises MissingOptionalDependency."""
    from phoenix.providers.cognition.litellm_passthrough import (
        LiteLLMPassthroughProvider,
    )

    with patch.dict(sys.modules, {"litellm": None}):
        with pytest.raises(MissingOptionalDependency) as exc_info:
            LiteLLMPassthroughProvider(litellm_model="ollama/llama3:8b")
    msg = str(exc_info.value)
    assert "pip install" in msg
    assert "[litellm]" in msg


@pytest.fixture
def fake_litellm_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Install a fake ``litellm`` module + ``litellm.exceptions`` with
    the classes our adapter's ``_map_sdk_exception`` imports."""
    fake = types.ModuleType("litellm")
    exc_mod = types.ModuleType("litellm.exceptions")

    class AuthenticationError(Exception):
        pass

    class RateLimitError(Exception):
        pass

    class Timeout(Exception):
        pass

    class ServiceUnavailableError(Exception):
        pass

    class ContextWindowExceededError(Exception):
        pass

    class ContentPolicyViolationError(Exception):
        pass

    exc_mod.AuthenticationError = AuthenticationError
    exc_mod.RateLimitError = RateLimitError
    exc_mod.Timeout = Timeout
    exc_mod.ServiceUnavailableError = ServiceUnavailableError
    exc_mod.ContextWindowExceededError = ContextWindowExceededError
    exc_mod.ContentPolicyViolationError = ContentPolicyViolationError

    fake.exceptions = exc_mod
    fake.completion = MagicMock()
    fake.get_model_info = MagicMock(return_value=None)
    fake.cost_per_token = MagicMock()

    monkeypatch.setitem(sys.modules, "litellm", fake)
    monkeypatch.setitem(sys.modules, "litellm.exceptions", exc_mod)
    return fake


def _make_litellm_response(
    *,
    text: str = "hello",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    cached_tokens: int = 0,
) -> Any:
    """Build an OpenAI-compatible response shape (what LiteLLM returns)."""
    message = SimpleNamespace(content=text, tool_calls=None)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    details = SimpleNamespace(cached_tokens=cached_tokens) if cached_tokens else None
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prompt_tokens_details=details,
    )
    return SimpleNamespace(choices=[choice], usage=usage)


def test_happy_path_routes_through_litellm_completion(fake_litellm_module: Any) -> None:
    """A mocked litellm.completion call returns a canonical CognitionResult."""
    from phoenix.providers.cognition.litellm_passthrough import (
        LiteLLMPassthroughProvider,
    )

    fake_litellm_module.completion = MagicMock(return_value=_make_litellm_response())
    adapter = LiteLLMPassthroughProvider(
        litellm_model="ollama/llama3:8b", client=fake_litellm_module
    )
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])

    result = adapter.complete(prompt, max_tokens=100, temperature=0.0)

    assert result.text == "hello"
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 5
    assert result.provider_fingerprint.startswith("litellm/ollama/llama3:8b")
    fake_litellm_module.completion.assert_called_once()
    sent_kwargs = fake_litellm_module.completion.call_args.kwargs
    assert sent_kwargs["model"] == "ollama/llama3:8b"


def test_provider_id_is_per_instance(fake_litellm_module: Any) -> None:
    """Each LiteLLM passthrough gets a distinct provider_id."""
    from phoenix.providers.cognition.litellm_passthrough import (
        LiteLLMPassthroughProvider,
    )

    fake_litellm_module.completion = MagicMock(return_value=_make_litellm_response())
    a = LiteLLMPassthroughProvider(litellm_model="ollama/llama3:8b", client=fake_litellm_module)
    b = LiteLLMPassthroughProvider(
        litellm_model="bedrock/anthropic.claude-3-sonnet", client=fake_litellm_module
    )
    assert a.provider_id == "litellm/ollama/llama3:8b"
    assert b.provider_id == "litellm/bedrock/anthropic.claude-3-sonnet"
    assert a.provider_id != b.provider_id


def test_capabilities_from_get_model_info(fake_litellm_module: Any) -> None:
    """capabilities() reflects litellm.get_model_info when populated."""
    from phoenix.providers.cognition.litellm_passthrough import (
        LiteLLMPassthroughProvider,
    )

    fake_litellm_module.get_model_info = MagicMock(
        return_value={
            "supports_streaming": True,
            "supports_function_calling": True,
            "supports_vision": False,
            "max_input_tokens": 32768,
            "supports_prompt_caching": False,
        }
    )
    adapter = LiteLLMPassthroughProvider(
        litellm_model="cohere/command-r-plus", client=fake_litellm_module
    )
    caps = adapter.capabilities()
    assert caps.streaming is True
    assert caps.tool_use is True
    assert caps.max_context_tokens == 32768


def test_capabilities_fallback_when_info_missing(fake_litellm_module: Any) -> None:
    """capabilities() returns conservative defaults when get_model_info errors."""
    from phoenix.providers.cognition.litellm_passthrough import (
        LiteLLMPassthroughProvider,
    )

    fake_litellm_module.get_model_info = MagicMock(side_effect=RuntimeError("unknown model"))
    adapter = LiteLLMPassthroughProvider(
        litellm_model="custom/unknown-model", client=fake_litellm_module
    )
    caps = adapter.capabilities()
    assert caps.streaming is False
    assert caps.tool_use is False
    assert caps.max_context_tokens == 8192  # default fallback


def test_pricing_uses_litellm_catalog_when_available(fake_litellm_module: Any) -> None:
    """estimated_cost_usd uses litellm.cost_per_token when present."""
    from phoenix.providers.cognition.litellm_passthrough import (
        LiteLLMPassthroughProvider,
    )

    fake_litellm_module.cost_per_token = MagicMock(return_value=(0.001, 0.002))
    adapter = LiteLLMPassthroughProvider(
        litellm_model="ollama/llama3:8b", client=fake_litellm_module
    )
    cost = adapter.estimated_cost_usd(TokenUsage(input_tokens=100, output_tokens=50))
    assert cost == pytest.approx(0.003)


def test_pricing_falls_back_to_phoenix_v2(fake_litellm_module: Any) -> None:
    """When litellm.cost_per_token raises, Phoenix v2 pricing is consulted."""
    from phoenix.providers.cognition.litellm_passthrough import (
        LiteLLMPassthroughProvider,
    )
    from phoenix.pricing.v2.schema import CognitionPricingRecord

    fake_litellm_module.cost_per_token = MagicMock(side_effect=RuntimeError("not in catalog"))
    fake_pricing = {
        ("litellm/custom/local-model", "custom/local-model"): CognitionPricingRecord(
            provider="litellm/custom/local-model",
            model="custom/local-model",
            usd_per_1m_input_tokens=2.0,
            usd_per_1m_output_tokens=4.0,
            prompt_cache_discount_factor=1.0,
            batch_discount_factor=1.0,
            vision_token_multiplier=1.0,
        )
    }
    with patch(
        "phoenix.providers.cognition.litellm_passthrough.load_cognition_pricing",
        return_value=fake_pricing,
    ):
        adapter = LiteLLMPassthroughProvider(
            litellm_model="custom/local-model", client=fake_litellm_module
        )
        cost = adapter.estimated_cost_usd(TokenUsage(input_tokens=1_000_000, output_tokens=500_000))
    # 1M input * $2/M = $2; 500K output * $4/M = $2; total $4.
    assert cost == pytest.approx(4.0)


def test_pricing_unavailable_when_neither_source_has_rate(fake_litellm_module: Any) -> None:
    """PricingUnavailable raises when both LiteLLM and Phoenix v2 miss."""
    from phoenix.providers.cognition.litellm_passthrough import (
        LiteLLMPassthroughProvider,
    )

    fake_litellm_module.cost_per_token = MagicMock(side_effect=RuntimeError("not in catalog"))
    with patch(
        "phoenix.providers.cognition.litellm_passthrough.load_cognition_pricing",
        return_value={},
    ):
        adapter = LiteLLMPassthroughProvider(
            litellm_model="custom/totally-unknown", client=fake_litellm_module
        )
        with pytest.raises(PricingUnavailable):
            adapter.estimated_cost_usd(TokenUsage(input_tokens=100, output_tokens=50))


@pytest.mark.parametrize(
    "raise_exc_cls_attr,expected_phoenix",
    [
        ("AuthenticationError", CognitionAuthError),
        ("RateLimitError", CognitionRateLimitError),
        ("Timeout", CognitionTimeoutError),
        ("ServiceUnavailableError", CognitionUnavailable),
        ("ContextWindowExceededError", CognitionContextLengthError),
    ],
)
def test_error_mapping(
    fake_litellm_module: Any, raise_exc_cls_attr: str, expected_phoenix: type
) -> None:
    """Each LiteLLM exception class maps to the right Phoenix typed error."""
    from phoenix.providers.cognition.litellm_passthrough import (
        LiteLLMPassthroughProvider,
    )

    sdk_cls = getattr(fake_litellm_module.exceptions, raise_exc_cls_attr)
    fake_litellm_module.completion = MagicMock(side_effect=sdk_cls("boom"))
    adapter = LiteLLMPassthroughProvider(
        litellm_model="ollama/llama3:8b",
        client=fake_litellm_module,
        max_retries=0,
    )
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])

    with patch("phoenix.providers.cognition._base.time.sleep"):
        with pytest.raises(expected_phoenix):
            adapter.complete(prompt, max_tokens=10, temperature=0.0)


def test_litellm_provider_satisfies_protocol(fake_litellm_module: Any) -> None:
    """Structural Protocol conformance via runtime_checkable."""
    from phoenix.providers.cognition.litellm_passthrough import (
        LiteLLMPassthroughProvider,
    )
    from phoenix.providers.cognition.protocol import CognitionProvider

    adapter = LiteLLMPassthroughProvider(
        litellm_model="ollama/llama3:8b", client=fake_litellm_module
    )
    assert isinstance(adapter, CognitionProvider)


def test_litellm_does_not_require_api_key_env_var(
    monkeypatch: pytest.MonkeyPatch, fake_litellm_module: Any
) -> None:
    """LiteLLM passthrough construction works without LITELLM_API_KEY in env.

    Per the SAFETY contract: LiteLLM reads provider-specific keys
    (ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.) directly; Phoenix's
    base-class API-key check is bypassed via _requires_api_key=False.
    """
    from phoenix.providers.cognition.litellm_passthrough import (
        LiteLLMPassthroughProvider,
    )

    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    # Should construct without raising.
    adapter = LiteLLMPassthroughProvider(
        litellm_model="ollama/llama3:8b", client=fake_litellm_module
    )
    assert adapter.provider_id == "litellm/ollama/llama3:8b"
