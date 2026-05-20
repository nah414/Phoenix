"""Round-trip + frozen-invariant tests for CognitionCapabilities."""

from __future__ import annotations

import dataclasses
from dataclasses import asdict

import pytest

from phoenix.providers.cognition.capabilities import CognitionCapabilities


def test_capabilities_asdict_roundtrip() -> None:
    """``CognitionCapabilities -> asdict -> CognitionCapabilities`` is identity."""
    caps = CognitionCapabilities(
        streaming=True,
        tool_use=True,
        vision=False,
        max_context_tokens=200_000,
        supports_prompt_cache=True,
        supports_batch=True,
    )
    d = asdict(caps)
    rebuilt = CognitionCapabilities(**d)
    assert rebuilt == caps


def test_capabilities_frozen() -> None:
    """Capabilities is frozen — direct field assignment raises."""
    caps = CognitionCapabilities(
        streaming=False,
        tool_use=False,
        vision=False,
        max_context_tokens=4096,
        supports_prompt_cache=False,
        supports_batch=False,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        caps.streaming = True  # type: ignore[misc]


def test_capabilities_equality_by_value() -> None:
    """Two CognitionCapabilities with the same fields are equal."""
    a = CognitionCapabilities(
        streaming=True,
        tool_use=False,
        vision=False,
        max_context_tokens=8192,
        supports_prompt_cache=False,
        supports_batch=False,
    )
    b = CognitionCapabilities(
        streaming=True,
        tool_use=False,
        vision=False,
        max_context_tokens=8192,
        supports_prompt_cache=False,
        supports_batch=False,
    )
    assert a == b
    assert hash(a) == hash(b)
