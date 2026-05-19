# `phoenix/providers/cognition/`

Cognition substrate — native adapters for cloud LLM providers, per architecture v1 Section 4.2 + Decision 24 (v1.1 cognition extension).

This directory was a Phase-0 placeholder reserved for v1.x extension; Phase 13 fills it.

## Phase 13 status

**Step 1 (shipped):** `CognitionProvider` Protocol + payload dataclasses (`Prompt`, `Tool`, `CognitionResult`, `TokenUsage`, `ToolCall`, `ToolResult`) + `CognitionCapabilities`.

**Step 2 (current):** three concrete adapters (Anthropic, OpenAI, Google Gemini) + typed-error hierarchy + shared `_CognitionAdapterBase` (P13-2 default) + minimal `ProviderRegistry` widening.

**Step 3 (next):** optional `LiteLLMPassthroughProvider` behind the `[litellm]` pip extra.

## Files

- [`protocol.py`](protocol.py) — `CognitionProvider` Protocol (PEP 544, `@runtime_checkable`) + `CognitionError` base.
- [`capabilities.py`](capabilities.py) — `CognitionCapabilities` advertised feature dataclass.
- [`types.py`](types.py) — payload dataclasses (`Prompt`, `Tool`, `TokenUsage`, `ToolCall`, `ToolResult`, `CognitionResult`).
- [`errors.py`](errors.py) — typed-error hierarchy (`CognitionAuthError`, `CognitionRateLimitError`, `CognitionContextLengthError`, `CognitionContentPolicyError`, `CognitionTimeoutError`, `CognitionUnavailable`).
- [`_base.py`](_base.py) — `_CognitionAdapterBase` abstract base. Shared retry-with-backoff, API-key env-var SAFETY contract, exception-mapping hook.
- [`anthropic.py`](anthropic.py) — `AnthropicProvider` (Anthropic Claude API).
- [`openai.py`](openai.py) — `OpenAIProvider` (OpenAI Chat Completions API).
- [`google.py`](google.py) — `GoogleGeminiProvider` (Google Gemini API).

Step 3 will add `litellm_passthrough.py`.

## Installing the SDKs

Each adapter's underlying SDK ships as an optional extra (heavy deps; opt-in only):

```bash
pip install phoenix-middleware[anthropic]   # AnthropicProvider
pip install phoenix-middleware[openai]      # OpenAIProvider
pip install phoenix-middleware[google]      # GoogleGeminiProvider
pip install phoenix-middleware[cognition]   # umbrella: all three
```

Constructing an adapter without the SDK installed raises `CognitionError` with the install hint. Tests inject mocks via the `client=` constructor kwarg, so the test suite runs cleanly without any of the SDKs installed.

## SAFETY

API keys are read from environment variables only (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`); never from config files; never logged; never serialized into the ledger or audit. Missing-key situations raise `CognitionAuthError` at adapter construction time.

## Open items carried forward

- **`[OPEN: P13-1]`** — Raw provider response body storage. Step 1 default: `CognitionResult.raw_provider_body` reserves the field as `Optional[dict]`; populated only when `task.options.preserve_raw_provider_body=True` and the actor has `can_store_raw_provider_body`. Step 2 does not yet wire the gating into the adapters (that lands when `task.options` and the permission registry plumbing concretize in Step 9). The field is reserved as `None` in all Step 2 adapter returns.
- **`[OPEN: P13-2]`** — Shared base class for cognition adapters. Step 2 ships the default: `_CognitionAdapterBase` carries retry-backoff + API-key SAFETY + exception-mapping hook; each provider's adapter inherits and implements `_do_complete`, `capabilities`, `fingerprint`, `_map_sdk_exception`. Reversible to independent adapters if the inheritance cost rises.
- **Router cognition branch deferred** — the build guide mentions `Router.decide` gaining a `task.kind == "cognition"` branch in Step 2. Step 2 ships the registry helper (`ProviderRegistry.cognition_entries()`) but does NOT touch `Router.decide`. The full cognition dispatch path concretizes in Step 4+ when `WobbleAxis` impls need it.
- **Live model discovery deferred** — adapters ship with static fallback capability lists. Live discovery from each provider's models endpoint is a Step 2 follow-up.

See [`BUILDGUIDE_phoenix_v1_phase13_cognition_mcp_client.md`](../../../BUILDGUIDE_phoenix_v1_phase13_cognition_mcp_client.md) for full Phase 13 scope and [`DESIGN_DECISIONS_PHASE13_LOCKED_2026-05-18.md`](../../../DESIGN_DECISIONS_PHASE13_LOCKED_2026-05-18.md) for the five locked decisions.

See [`BUILDGUIDE_phoenix_v1_phase13_cognition_mcp_client.md`](../../../BUILDGUIDE_phoenix_v1_phase13_cognition_mcp_client.md) for full Phase 13 scope and [`DESIGN_DECISIONS_PHASE13_LOCKED_2026-05-18.md`](../../../DESIGN_DECISIONS_PHASE13_LOCKED_2026-05-18.md) for the five locked decisions.
