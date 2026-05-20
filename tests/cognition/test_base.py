"""Tests for ``_CognitionAdapterBase`` retry/backoff behavior."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from phoenix.providers.cognition._base import _CognitionAdapterBase
from phoenix.providers.cognition.capabilities import CognitionCapabilities
from phoenix.providers.cognition.errors import (
    CognitionAuthError,
    CognitionError,
    CognitionRateLimitError,
    CognitionTimeoutError,
    CognitionUnavailable,
)
from phoenix.providers.cognition.types import CognitionResult, Prompt, TokenUsage


class _FakeAdapter(_CognitionAdapterBase):
    """Concrete subclass for exercising the base class's retry logic."""

    provider_id = "fake"
    _api_key_env_var = "FAKE_API_KEY"

    def __init__(self, *, side_effects: list[Any], **kwargs: Any) -> None:
        self._side_effects = list(side_effects)
        super().__init__(model="fake-model", api_key="test-key", **kwargs)
        self.calls = 0

    def _do_complete(
        self,
        prompt: Prompt,
        *,
        max_tokens: int,
        temperature: float,
        tools: Any,
        stream: bool,
    ) -> CognitionResult:
        del prompt, max_tokens, temperature, tools, stream
        self.calls += 1
        outcome = self._side_effects.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def capabilities(self) -> CognitionCapabilities:
        return CognitionCapabilities(
            streaming=False,
            tool_use=False,
            vision=False,
            max_context_tokens=1024,
            supports_prompt_cache=False,
            supports_batch=False,
        )

    def fingerprint(self) -> str:
        return "fake|fake-model|test"

    def _map_sdk_exception(self, exc: Exception) -> CognitionError | None:
        return None


def _stub_result() -> CognitionResult:
    return CognitionResult(
        text="ok",
        tool_calls=[],
        usage=TokenUsage(input_tokens=1, output_tokens=1),
        latency_ms=1.0,
        provider_fingerprint="fake|fake-model|test",
    )


def _make_prompt() -> Prompt:
    return Prompt(system=None, messages=[{"role": "user", "content": "hi"}])


def test_retry_succeeds_after_transient_error() -> None:
    """A transient RateLimitError on attempt 1 retries and succeeds."""
    adapter = _FakeAdapter(
        side_effects=[CognitionRateLimitError("rate-limited"), _stub_result()],
        backoff_base_sec=0.0,  # no real sleep
        backoff_max_sec=0.0,
    )
    with patch("phoenix.providers.cognition._base.time.sleep"):
        result = adapter.complete(_make_prompt(), max_tokens=10, temperature=0.0)
    assert result.text == "ok"
    assert adapter.calls == 2


def test_retry_exhausted_raises_last() -> None:
    """After max_retries+1 transient failures, raise the last exception."""
    adapter = _FakeAdapter(
        side_effects=[
            CognitionTimeoutError("t1"),
            CognitionTimeoutError("t2"),
            CognitionTimeoutError("t3"),
            CognitionTimeoutError("t4"),  # max_retries=3 means 4 total attempts
        ],
        backoff_base_sec=0.0,
        backoff_max_sec=0.0,
        max_retries=3,
    )
    with patch("phoenix.providers.cognition._base.time.sleep"):
        with pytest.raises(CognitionTimeoutError):
            adapter.complete(_make_prompt(), max_tokens=10, temperature=0.0)
    assert adapter.calls == 4


def test_no_retry_on_auth_error() -> None:
    """CognitionAuthError raises immediately — not transient."""
    adapter = _FakeAdapter(
        side_effects=[CognitionAuthError("bad key")],
        backoff_base_sec=0.0,
        backoff_max_sec=0.0,
    )
    with pytest.raises(CognitionAuthError):
        adapter.complete(_make_prompt(), max_tokens=10, temperature=0.0)
    assert adapter.calls == 1


def test_retry_after_seconds_honored() -> None:
    """RateLimitError.retry_after_seconds is respected by the backoff."""
    adapter = _FakeAdapter(
        side_effects=[
            CognitionRateLimitError("rl", retry_after_seconds=0.123),
            _stub_result(),
        ],
        backoff_base_sec=99.0,  # would dominate if Retry-After weren't honored
        backoff_max_sec=99.0,
    )
    sleeps: list[float] = []
    with patch("phoenix.providers.cognition._base.time.sleep", side_effect=sleeps.append):
        result = adapter.complete(_make_prompt(), max_tokens=10, temperature=0.0)
    assert result.text == "ok"
    # Retry-After of 0.123 should win over the 99.0 backoff base.
    assert sleeps == [pytest.approx(0.123)]


def test_unavailable_is_retried() -> None:
    """CognitionUnavailable counts as transient."""
    adapter = _FakeAdapter(
        side_effects=[CognitionUnavailable("503"), _stub_result()],
        backoff_base_sec=0.0,
        backoff_max_sec=0.0,
    )
    with patch("phoenix.providers.cognition._base.time.sleep"):
        result = adapter.complete(_make_prompt(), max_tokens=10, temperature=0.0)
    assert result.text == "ok"
    assert adapter.calls == 2


def test_explicit_api_key_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit ``api_key`` kwarg takes precedence over the env var."""
    monkeypatch.setenv("FAKE_API_KEY", "from-env")
    adapter = _FakeAdapter(side_effects=[_stub_result()])
    assert adapter._api_key == "test-key"


class _EnvOnlyAdapter(_FakeAdapter):
    """Variant that always reads the key from the env."""

    def __init__(self, *, side_effects: Any) -> None:
        self._side_effects = list(side_effects)
        # Skip _FakeAdapter.__init__ to avoid the explicit api_key kwarg.
        _CognitionAdapterBase.__init__(self, model="fake-model")
        self.calls = 0


def test_env_only_adapter_picks_up_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_API_KEY", "from-env")
    adapter = _EnvOnlyAdapter(side_effects=[_stub_result()])
    assert adapter._api_key == "from-env"


def test_env_only_adapter_raises_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FAKE_API_KEY", raising=False)
    with pytest.raises(CognitionAuthError):
        _EnvOnlyAdapter(side_effects=[_stub_result()])
