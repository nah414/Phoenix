"""Typed-error hierarchy for cognition-provider failures.

Per Phase 13 build guide Step 2: every cloud-LLM SDK error maps to one
of these typed Phoenix exceptions before reaching the rest of the
system. The Router's failover protocol (Phase 4 Section 4.5) and the
verification gate's panic-mode tests (Phase 11 Section 10.7) both
discriminate on these types.

**SAFETY (Phase 13 Step 2 SAFETY note):** API keys are read from
environment variables only (``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``,
``GOOGLE_API_KEY``); never from config files; never logged; never
serialized into the ledger or audit. Provider authentication failures
yield :class:`CognitionAuthError` with no key material in the message.

The base :class:`CognitionError` is defined in
:mod:`phoenix.providers.cognition.protocol` so Step 1's Protocol module
stays standalone; this module ships the typed subclasses Step 2's
adapters raise.
"""

from __future__ import annotations

from phoenix.providers.cognition.protocol import CognitionError


class CognitionAuthError(CognitionError):
    """Provider rejected the credentials (401 / equivalent).

    The adapter must NOT include the key (or any prefix) in the
    exception message. Phase 13 SAFETY contract: callers may safely
    log this exception's repr without leaking credentials. Recovery is
    operator-action (rotate the key in the environment); not
    failover-eligible.
    """


class CognitionRateLimitError(CognitionError):
    """Provider returned a rate-limit response (429 / equivalent).

    Carries :attr:`retry_after_seconds` when the provider's response
    surfaces a ``Retry-After`` header (Anthropic), an
    ``X-RateLimit-Reset`` header, or an equivalent error-body field.
    The shared retry backoff in
    :class:`_CognitionAdapterBase._retry_with_backoff` respects this
    when present; otherwise it uses exponential-with-jitter.
    """

    def __init__(self, msg: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(msg)
        self.retry_after_seconds = retry_after_seconds


class CognitionContextLengthError(CognitionError):
    """Prompt + expected output exceeds the model's context window.

    Mapped from 413 (Anthropic), ``context_length_exceeded`` error code
    (OpenAI), ``InvalidArgument`` with context-related details
    (Google). Not failover-eligible — Phoenix would just hit the same
    cap on another model. The caller should shrink the prompt or pick
    a longer-context model.
    """


class CognitionContentPolicyError(CognitionError):
    """Provider refused on safety / policy / capability grounds.

    Anthropic ``content_policy_violation``, OpenAI moderation block,
    Google ``BLOCK_*`` finish reasons. The adapter records the
    provider's surface reason in :attr:`reason_code` so the cognition
    disagreement classifier (Phase 13 Step 5) can register
    ``REFUSAL_DIVERGENCE`` consistently. Not failover-eligible.
    """

    def __init__(self, msg: str, *, reason_code: str | None = None) -> None:
        super().__init__(msg)
        self.reason_code = reason_code


class CognitionTimeoutError(CognitionError):
    """Provider call exceeded the configured timeout (504 / equivalent).

    Failover-eligible. The shared retry backoff retries up to 3 times
    before re-raising; the Router (later in Phase 13) then quarantines
    the provider and tries an alternate.
    """


class CognitionUnavailable(CognitionError):
    """Provider unreachable or 5xx other than 504 (503 / equivalent).

    Failover-eligible. Same recovery shape as
    :class:`CognitionTimeoutError`. Phase 13 Step 10's
    :func:`test_cognition_panic_mode` exercises this path
    end-to-end: cognition provider unreachable → failover to
    registered alternate, or refuse-to-complete if no alternatives.
    """


__all__ = [
    "CognitionError",
    "CognitionAuthError",
    "CognitionRateLimitError",
    "CognitionContextLengthError",
    "CognitionContentPolicyError",
    "CognitionTimeoutError",
    "CognitionUnavailable",
]
