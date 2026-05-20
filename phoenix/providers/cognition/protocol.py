"""``CognitionProvider`` Protocol + base error type for cloud-LLM adapters.

Per architecture v1 Section 4.2 + Decision 24 (v1.1 cognition substrate
scope): every Phoenix cognition task dispatches through a
:class:`CognitionProvider` (Phoenix-native Protocol; parallels
:class:`BaseProviderClient` from
``phoenix/trinity/orchestrate/provider_client.py`` but with a chat-API
contract instead of a physics-submission contract). Phase 13 Step 1
ships the Protocol + payload dataclasses; Step 2 adds concrete adapters
(Anthropic, OpenAI, Google Gemini); Step 3 adds the optional LiteLLM
passthrough.

The Protocol is structural (PEP 544): any class exposing the
attributes + methods satisfies it; concrete adapters need not inherit.

Per [OPEN: P13-1] default (Phase 13 build guide Section 5): the raw
provider response body is available behind a
``task.options.preserve_raw_provider_body=True`` flag with a
``can_store_raw_provider_body`` permission. Step 1 reserves the
:attr:`CognitionResult.raw_provider_body` field (default ``None``); the
gating logic lands in Step 2's adapters.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from phoenix.providers.cognition.capabilities import CognitionCapabilities
from phoenix.providers.cognition.types import CognitionResult, Prompt, Tool


class CognitionError(Exception):
    """Base for cognition-provider failures.

    Step 2's concrete adapters raise typed subclasses
    (``CognitionAuthError``, ``CognitionRateLimitError``,
    ``CognitionContextLengthError``, ``CognitionContentPolicyError``,
    ``CognitionTimeoutError``, ``CognitionUnavailable``) defined in
    ``phoenix.providers.cognition.errors``.
    """


@runtime_checkable
class CognitionProvider(Protocol):
    """The structural contract every cloud-LLM adapter satisfies.

    Parallels :class:`BaseProviderClient` (Phase 3) for the physics-task
    dispatch surface. Step 2's concrete adapters (Anthropic, OpenAI,
    Google) implement this; Step 3 adds ``LiteLLMPassthroughProvider``
    behind the optional ``[litellm]`` extra. Marked ``@runtime_checkable``
    so the router (Step 2) can ``isinstance`` against the Protocol when
    discriminating registered providers.
    """

    provider_id: str
    """Stable provider identifier (e.g. ``"anthropic"``, ``"openai"``,
    ``"google"``, ``"litellm/<litellm_model>"``)."""

    model: str
    """Provider-specific model identifier (e.g.
    ``"claude-sonnet-4-7-20260418"``, ``"gpt-5"``,
    ``"gemini-2.5-pro"``)."""

    def complete(
        self,
        prompt: Prompt,
        *,
        max_tokens: int,
        temperature: float,
        tools: list[Tool] | None = None,
        stream: bool = False,
    ) -> CognitionResult:
        """Execute a chat completion against the provider's backend.

        Step 1 contract: synchronous; raises :class:`CognitionError`
        (or a Step 2 typed subclass) on failure. Step 7 extends with
        streaming-token support; when ``stream=True``, Step 2 adapters
        yield deltas via an internal async generator that Phoenix's
        event broker fans out, while the synchronous return value still
        carries the aggregate :class:`CognitionResult`.
        """
        ...

    def capabilities(self) -> CognitionCapabilities:
        """Advertised provider capabilities.

        Step 2's adapters live-read from the provider's models endpoint
        when possible (e.g., Anthropic ``/v1/models``, Google
        ``generativeai.list_models``); a static fallback list ships for
        offline bootstrapping. The router (Step 2) uses this to filter
        candidate providers for a task.
        """
        ...

    def fingerprint(self) -> str:
        """Stable fingerprint for ledger provenance.

        Includes provider name + model version + canonicalized request
        shape (temperature bucket, tool list hash, response-format
        hash). Phase 13 Step 8's Omega Ledger entries record this in
        ``cognition_provenance_json`` for bit-exact replay
        verification.
        """
        ...
