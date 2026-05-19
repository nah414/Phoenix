"""``_CognitionAdapterBase`` — shared retry + error-mapping for cognition adapters.

Per Phase 13 build guide Step 2 ``[OPEN: P13-2]`` default: the three
concrete adapters share a base class for retry + error mapping;
provider-specific code (prompt translation, tool schema, response
shape) lives in subclasses. The alternative was full independence per
adapter — the default trades a small amount of coupling for one
audit-target on the retry/error-mapping discipline.

The base class is intentionally NOT a Protocol — Phase 13's adapters
inherit (not just structurally satisfy) for retry/error sharing. The
public surface still conforms to :class:`CognitionProvider` Protocol
structurally because the adapter classes carry the
:attr:`provider_id`/:attr:`model` attributes and the three methods.
"""

from __future__ import annotations

import logging
import os
import random
import time
from abc import ABC, abstractmethod
from typing import Callable, TypeVar

from phoenix.providers.cognition.capabilities import CognitionCapabilities
from phoenix.providers.cognition.errors import (
    CognitionAuthError,
    CognitionContentPolicyError,
    CognitionContextLengthError,
    CognitionError,
    CognitionRateLimitError,
    CognitionTimeoutError,
    CognitionUnavailable,
)
from phoenix.providers.cognition.types import CognitionResult, Prompt, Tool

log = logging.getLogger(__name__)

T = TypeVar("T")

_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF_BASE_SEC = 1.0
_DEFAULT_BACKOFF_MAX_SEC = 30.0


class _CognitionAdapterBase(ABC):
    """Abstract base for cognition adapters.

    Subclasses implement:

    - :meth:`_do_complete`: the provider-specific SDK call. The base
      class wraps it with retry/backoff in :meth:`complete`.
    - :meth:`capabilities`: return the static :class:`CognitionCapabilities`
      for this ``(provider, model)``.
    - :meth:`fingerprint`: return the canonical fingerprint string.
    - :meth:`_map_sdk_exception`: classify a provider-SDK exception
      into a Phoenix-typed exception (or ``None`` to bubble unchanged).

    The base class enforces the API-key env-var SAFETY contract: keys
    read only from :func:`os.environ`; never logged.
    """

    provider_id: str
    """Stable provider identifier (e.g. ``"anthropic"``)."""

    model: str
    """Provider-specific model identifier (e.g.
    ``"claude-sonnet-4-7-20260418"``)."""

    _api_key_env_var: str
    """Name of the environment variable that holds the API key."""

    _requires_api_key: bool = True
    """Class flag — when ``False``, the base skips the env-var auth
    check at ``__init__``. Phase 13 Step 3's LiteLLM passthrough sets
    this to ``False`` because LiteLLM reads provider-specific keys
    from the environment internally (no single ``LITELLM_API_KEY``)."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_base_sec: float = _DEFAULT_BACKOFF_BASE_SEC,
        backoff_max_sec: float = _DEFAULT_BACKOFF_MAX_SEC,
    ) -> None:
        """Construct the adapter.

        Args:
            model: Provider-specific model identifier.
            api_key: Optional explicit API key. If ``None``, the
                adapter reads from :attr:`_api_key_env_var`. Phoenix
                callers should NOT pass this; ops set the environment
                variable and Phoenix never touches the value.
                The kwarg exists only so tests can inject a
                placeholder.
            max_retries: Maximum retry attempts on transient errors
                (429s / 504s / 503s). Default 3.
            backoff_base_sec: Base delay for exponential backoff.
            backoff_max_sec: Cap on backoff delay.
        """
        self.model = model
        self._max_retries = max_retries
        self._backoff_base_sec = backoff_base_sec
        self._backoff_max_sec = backoff_max_sec
        if self._requires_api_key:
            self._api_key = (
                api_key if api_key is not None else os.environ.get(self._api_key_env_var)
            )
            if not self._api_key:
                raise CognitionAuthError(
                    f"{self.provider_id}: no API key found. Set the "
                    f"{self._api_key_env_var} environment variable."
                )
        else:
            # LiteLLM-style providers manage their own credential resolution.
            self._api_key = api_key

    def complete(
        self,
        prompt: Prompt,
        *,
        max_tokens: int,
        temperature: float,
        tools: list[Tool] | None = None,
        stream: bool = False,
    ) -> CognitionResult:
        """Execute a chat completion with retry on transient errors.

        Wraps :meth:`_do_complete` with exponential-backoff retry on
        :class:`CognitionRateLimitError`,
        :class:`CognitionTimeoutError`, and
        :class:`CognitionUnavailable`. Non-transient errors
        (:class:`CognitionAuthError`,
        :class:`CognitionContextLengthError`,
        :class:`CognitionContentPolicyError`) raise immediately.
        """
        return self._retry_with_backoff(
            lambda: self._do_complete(
                prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                tools=tools,
                stream=stream,
            )
        )

    def _retry_with_backoff(self, fn: Callable[[], T]) -> T:
        """Run ``fn`` with exponential backoff on transient errors.

        Per Phase 13 build guide Step 2: exponential backoff with
        jitter on 429s and 5xx; immediate raise on 4xx non-429; max 3
        retries before failover.
        """
        last_exc: CognitionError | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return fn()
            except (
                CognitionRateLimitError,
                CognitionTimeoutError,
                CognitionUnavailable,
            ) as exc:
                last_exc = exc
                if attempt >= self._max_retries:
                    break
                delay = self._compute_backoff(attempt, exc)
                log.warning(
                    "Cognition adapter %s retry %d/%d after %.2fs: %s",
                    self.provider_id,
                    attempt + 1,
                    self._max_retries,
                    delay,
                    type(exc).__name__,
                )
                time.sleep(delay)
        assert last_exc is not None
        raise last_exc

    def _compute_backoff(self, attempt: int, exc: CognitionError) -> float:
        """Compute the next-retry delay.

        If the exception is :class:`CognitionRateLimitError` and the
        provider supplied a ``Retry-After`` value, honor it (clamped
        to :attr:`_backoff_max_sec`). Otherwise: exponential-with-
        jitter ``base * 2^attempt`` capped at the max.
        """
        if isinstance(exc, CognitionRateLimitError) and exc.retry_after_seconds is not None:
            return min(exc.retry_after_seconds, self._backoff_max_sec)
        base: float = self._backoff_base_sec * float(2**attempt)
        jitter: float = random.uniform(0, base * 0.25)
        return float(min(base + jitter, self._backoff_max_sec))

    # ------------------------------------------------------------------
    # Subclass contract

    @abstractmethod
    def _do_complete(
        self,
        prompt: Prompt,
        *,
        max_tokens: int,
        temperature: float,
        tools: list[Tool] | None,
        stream: bool,
    ) -> CognitionResult:
        """Provider-specific SDK call. Map SDK exceptions to typed
        Phoenix exceptions before returning."""
        ...

    @abstractmethod
    def capabilities(self) -> CognitionCapabilities:
        """Static capability advertisement for this ``(provider, model)``."""
        ...

    @abstractmethod
    def fingerprint(self) -> str:
        """Stable fingerprint for ledger provenance."""
        ...

    @abstractmethod
    def _map_sdk_exception(self, exc: Exception) -> CognitionError | None:
        """Classify a provider-SDK exception.

        Return a typed Phoenix exception to raise; return ``None`` to
        let the original exception propagate (used for genuinely
        unexpected errors that shouldn't be silently swallowed).

        The implementations live in each concrete adapter so the
        provider-SDK-specific exception classes don't leak into this
        base module.
        """
        ...

    # ------------------------------------------------------------------
    # Helpers for subclasses

    def _wrap_sdk_call(self, fn: Callable[[], T]) -> T:
        """Run ``fn`` and translate any provider-SDK exception via
        :meth:`_map_sdk_exception`.

        Wraps the bare SDK call site in each adapter's
        :meth:`_do_complete`. Re-raises the mapped Phoenix exception
        (or, if mapping returns ``None``, re-raises the original
        wrapped in :class:`CognitionError` to preserve typing).
        """
        try:
            return fn()
        except CognitionError:
            raise  # already typed
        except Exception as exc:
            mapped = self._map_sdk_exception(exc)
            if mapped is not None:
                raise mapped from exc
            raise CognitionError(f"{self.provider_id}: unexpected SDK error: {exc}") from exc


__all__ = [
    "_CognitionAdapterBase",
    "CognitionAuthError",
    "CognitionContentPolicyError",
    "CognitionContextLengthError",
    "CognitionRateLimitError",
    "CognitionTimeoutError",
    "CognitionUnavailable",
]
