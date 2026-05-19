"""Tests for the typed-error hierarchy."""

from __future__ import annotations

import pytest

from phoenix.providers.cognition.errors import (
    CognitionAuthError,
    CognitionContentPolicyError,
    CognitionContextLengthError,
    CognitionError,
    CognitionRateLimitError,
    CognitionTimeoutError,
    CognitionUnavailable,
)


def test_all_typed_errors_subclass_cognition_error() -> None:
    """All Step 2 typed errors descend from CognitionError."""
    for cls in (
        CognitionAuthError,
        CognitionRateLimitError,
        CognitionContextLengthError,
        CognitionContentPolicyError,
        CognitionTimeoutError,
        CognitionUnavailable,
    ):
        assert issubclass(cls, CognitionError)


def test_rate_limit_carries_retry_after() -> None:
    """CognitionRateLimitError preserves provider-supplied Retry-After."""
    e = CognitionRateLimitError("rate limited", retry_after_seconds=2.5)
    assert e.retry_after_seconds == 2.5


def test_rate_limit_retry_after_optional() -> None:
    """CognitionRateLimitError works without Retry-After."""
    e = CognitionRateLimitError("rate limited")
    assert e.retry_after_seconds is None


def test_content_policy_carries_reason_code() -> None:
    """CognitionContentPolicyError preserves the provider's reason code."""
    e = CognitionContentPolicyError("refused", reason_code="content_policy_violation")
    assert e.reason_code == "content_policy_violation"


def test_content_policy_reason_optional() -> None:
    """CognitionContentPolicyError works without a reason code."""
    e = CognitionContentPolicyError("refused")
    assert e.reason_code is None


def test_typed_errors_are_raiseable() -> None:
    """Every typed error can be raised and caught as CognitionError."""
    for cls in (
        CognitionAuthError,
        CognitionRateLimitError,
        CognitionContextLengthError,
        CognitionContentPolicyError,
        CognitionTimeoutError,
        CognitionUnavailable,
    ):
        with pytest.raises(CognitionError):
            raise cls("test")
