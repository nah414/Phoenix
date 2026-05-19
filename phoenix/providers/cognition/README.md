# `phoenix/providers/cognition/`

Cognition substrate — native adapters for cloud LLM providers, per architecture v1 Section 4.2 + Decision 24 (v1.1 cognition extension).

This directory was a Phase-0 placeholder reserved for v1.x extension; Phase 13 fills it.

## Phase 13 status

**Step 1 (current):** `CognitionProvider` Protocol + payload dataclasses (`Prompt`, `Tool`, `CognitionResult`, `TokenUsage`, `ToolCall`, `ToolResult`) + `CognitionCapabilities`.

**Step 2 (next):** concrete adapters for Anthropic, OpenAI, Google Gemini, plus typed-error hierarchy.

**Step 3:** optional `LiteLLMPassthroughProvider` behind the `[litellm]` pip extra.

## Files

- [`protocol.py`](protocol.py) — `CognitionProvider` Protocol (PEP 544, `@runtime_checkable`) + `CognitionError` base.
- [`capabilities.py`](capabilities.py) — `CognitionCapabilities` advertised feature dataclass.
- [`types.py`](types.py) — payload dataclasses (`Prompt`, `Tool`, `TokenUsage`, `ToolCall`, `ToolResult`, `CognitionResult`).

Step 2 will add `anthropic.py`, `openai.py`, `google.py`, `errors.py`.
Step 3 will add `litellm_passthrough.py`.

## Open items carried forward

- **`[OPEN: P13-1]`** — Raw provider response body storage. Step 1 default: `CognitionResult.raw_provider_body` reserves the field as `Optional[dict]`; it is populated only when `task.options.preserve_raw_provider_body=True` and the actor has the `can_store_raw_provider_body` permission. Gating logic lands in Step 2's adapters.

See [`BUILDGUIDE_phoenix_v1_phase13_cognition_mcp_client.md`](../../../BUILDGUIDE_phoenix_v1_phase13_cognition_mcp_client.md) for full Phase 13 scope and [`DESIGN_DECISIONS_PHASE13_LOCKED_2026-05-18.md`](../../../DESIGN_DECISIONS_PHASE13_LOCKED_2026-05-18.md) for the five locked decisions.
