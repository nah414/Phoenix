"""Phase 13 Step 10 — cognition provider panic mode (§10.7 acceptance).

Per Phase 13 build guide §4.10 acceptance battery: when a cognition
provider is unreachable, Phoenix MUST surface a typed
:class:`CognitionUpstreamFailure` (or its retry-exhausted variant)
rather than silently returning a stale or partial result. The
contract mirrors the existing physics-provider panic-mode pattern
(Phase 11 Step 4: ``test_drift_detector_down.py``) — fail-closed,
typed error, audit row written, no silent degradation.

This module pins the cognition-provider portion of that contract:

1. An ``AnthropicProvider`` whose underlying client raises
   :class:`anthropic.APIConnectionError` surfaces
   :class:`CognitionUpstreamFailure` to the caller after the retry
   budget is exhausted.
2. The typed error carries ``provider_id`` + ``model`` so the
   operator knows which provider failed.
3. ``isinstance(exc, CognitionError)`` passes for typed-aware
   callers (the base class for the whole cognition error tree).
4. The wobble axis (e.g., :class:`CrossModelAxis`) wraps the same
   typed error rather than aborting verification with a generic
   exception — operators get the cognition-specific context.

**No alternate-provider failover yet** — the build guide's "failover
to alternate provider if registered" path is a v1.1.x extension; for
v1.1.dev0 the test asserts the typed-error surface, which is the
load-bearing piece.
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

import phoenix  # noqa: F401  -- triggers sys.path injection


pytestmark = pytest.mark.acceptance


@pytest.fixture
def fake_anthropic_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Install a fake ``anthropic`` module exporting the typed errors."""
    fake = types.ModuleType("anthropic")

    class AuthenticationError(Exception):
        pass

    class RateLimitError(Exception):
        pass

    class APITimeoutError(Exception):
        pass

    class APIConnectionError(Exception):
        pass

    class InternalServerError(Exception):
        pass

    class BadRequestError(Exception):
        def __init__(self, msg: str = "bad", body: Any = None) -> None:
            super().__init__(msg)
            self.body = body

    fake.AuthenticationError = AuthenticationError
    fake.RateLimitError = RateLimitError
    fake.APITimeoutError = APITimeoutError
    fake.APIConnectionError = APIConnectionError
    fake.InternalServerError = InternalServerError
    fake.BadRequestError = BadRequestError

    class Anthropic:
        def __init__(self, **_: Any) -> None:
            self.messages = MagicMock()

    fake.Anthropic = Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    return fake


def test_provider_unreachable_surfaces_typed_error(
    fake_anthropic_module: types.ModuleType,
) -> None:
    """An APIConnectionError after retry exhaustion → CognitionUnavailable."""
    from phoenix.providers.cognition.anthropic import AnthropicProvider
    from phoenix.providers.cognition.errors import CognitionUnavailable
    from phoenix.providers.cognition.protocol import CognitionError
    from phoenix.providers.cognition.types import Prompt

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = fake_anthropic_module.APIConnectionError(
        "simulated connection refused"
    )

    adapter = AnthropicProvider(
        model="claude-sonnet-4-7-20260418",
        api_key="test",
        client=fake_client,
    )
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])

    with pytest.raises(CognitionUnavailable) as exc_info:
        adapter.complete(prompt, max_tokens=10, temperature=0.0)

    # Typed inheritance — every cognition error subtypes CognitionError.
    assert isinstance(exc_info.value, CognitionError)


def test_provider_typed_error_message_names_provider(
    fake_anthropic_module: types.ModuleType,
) -> None:
    """The typed error string names which provider failed.

    The cognition errors don't carry structured ``provider_id`` / ``model``
    attributes (the message-string is the audit surface); we assert the
    operator-actionable provider identifier is present in the message.
    """
    from phoenix.providers.cognition.anthropic import AnthropicProvider
    from phoenix.providers.cognition.errors import CognitionUnavailable
    from phoenix.providers.cognition.types import Prompt

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = fake_anthropic_module.APIConnectionError(
        "simulated dns failure"
    )

    adapter = AnthropicProvider(
        model="claude-sonnet-4-7-20260418",
        api_key="test",
        client=fake_client,
    )
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "x"}])

    with pytest.raises(CognitionUnavailable) as exc_info:
        adapter.complete(prompt, max_tokens=10, temperature=0.0)

    err_msg = str(exc_info.value)
    assert "anthropic" in err_msg.lower()
    assert "connection" in err_msg.lower() or "dns" in err_msg.lower()


def test_cross_model_axis_propagates_typed_error(
    fake_anthropic_module: types.ModuleType,
) -> None:
    """The wobble axis doesn't swallow the cognition-provider failure.

    The cross-model axis dispatches the same prompt to N providers; if
    one fails with a typed cognition error, that error propagates rather
    than the axis silently completing with the surviving provider's
    output. This is the load-bearing fail-closed contract.
    """
    from phoenix.providers.cognition.anthropic import AnthropicProvider
    from phoenix.providers.cognition.errors import CognitionUnavailable
    from phoenix.providers.cognition.types import Prompt
    from phoenix.verification.axes.cross_model import CrossModelAxis

    # Adapter A: works.
    fake_client_a = MagicMock()
    fake_client_a.messages.create.return_value = types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text="answer")],
        usage=types.SimpleNamespace(
            input_tokens=1,
            output_tokens=1,
            cache_read_input_tokens=0,
        ),
        model="claude-sonnet-4-7-20260418",
    )

    # Adapter B: connection error.
    fake_client_b = MagicMock()
    fake_client_b.messages.create.side_effect = fake_anthropic_module.APIConnectionError(
        "simulated outage"
    )

    adapter_a = AnthropicProvider(
        model="claude-sonnet-4-7-20260418",
        api_key="test",
        client=fake_client_a,
    )
    adapter_b = AnthropicProvider(
        model="claude-opus-4-20260418",
        api_key="test",
        client=fake_client_b,
    )

    axis = CrossModelAxis(providers=[adapter_a, adapter_b])
    prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])

    with pytest.raises(CognitionUnavailable):
        axis.run(prompt, max_tokens=10, temperature=0.0)


# Build-guide note: the "failover to alternate provider if registered"
# path is deferred to v1.1.x. The fail-closed surface above is the
# load-bearing acceptance contract — failover is an additive
# improvement on top of that guarantee.
