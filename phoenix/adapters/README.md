# phoenix/adapters

## Purpose
**LoRA adapter loading + sandbox** per architecture v1 Section 3.5. Phoenix v1 ships the *interface* for loading trained LoRA adapters (v6.7-style or otherwise) without vendoring a specific adapter — BYO-LoRA. Loaded adapters declare capabilities (e.g., `sanskrit-glyph-bidirectional`, `physics-domain-extension`); the task grammar layer queries the adapter registry and routes natural-language input through them. Per Section 3.5, adapter execution runs inside a subprocess with per-call timeout (default 5s), restricted filesystem (own scratch dir only), and no network access — a misbehaving adapter cannot wedge the Phoenix process.

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 2.7 (LoRA hot-swap interface — Protocol contract), Section 3.5 (runtime invocation + sandbox), Section 1 Decision 8 (LoRA hot-swap is a v1 capability, not v1 content).

## Key files and their roles
| File | Role |
|---|---|
| `__init__.py` | Empty Phase 0 stub. |
| `protocol.py` | (Phase 9) `LoRAAdapter` Protocol: `name`, `version`, `base_model_fingerprint`, `capabilities`; methods `encode_to_grammar`, `decode_from_grammar`, `fingerprint`. |
| `loader.py` | (Phase 9) Adapter discovery + load orchestration. |
| `validator.py` | (Phase 9) Inference-time round-trip validation; refuses to register an adapter that fails. |
| `sandbox.py` | (Phase 9) Subprocess isolation — filesystem ACLs + network deny + timeout enforcement. |

## Vendored substrate
None. The v6.7-style Sanskrit LoRA from dr-frank-and-eddy that teaches Qwen3 to read/emit Sanskrit glyphs natively is one possible loaded adapter, but Phoenix does NOT vendor it. The Protocol contract is satisfied by any v6.7-derived adapter declaring `sanskrit-glyph-bidirectional` capability.

## Common failure modes
- `AdapterValidationError` (503) — round-trip suite failed; adapter rejected.
- `AdapterTimeoutError` (504) — subprocess exceeded the configured budget.
- `AdapterVersionMismatch` — strict-mode replay sees a different adapter fingerprint than recorded in the ledger.
- `AdapterNotLoaded` (404) — operations on an unloaded adapter (e.g., `force-revalidate`).

## Troubleshooting
- Adapter fingerprints land in every Result's provenance (per Section 3.5). Strict and replay modes verify fingerprint equality.
- Force re-validation: `POST /v1/admin/adapters/{id}/force-revalidate` re-runs the inference-time validation suite. History at `GET /v1/admin/adapters/{id}/round-trip-history`.
- `RemoteLLMAdapter` Protocol (a *different* shape that requires network access) lands in Phase 9; the sandbox here is for local LoRA adapters only.

## Tests
- `tests/unit/test_smoke.py` — asserts `phoenix.adapters` imports.

## Recent changes
- 2026-05-06 — Phase 0: module created as empty stub.
