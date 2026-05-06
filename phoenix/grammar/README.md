# phoenix/grammar

## Purpose
The **task grammar layer** — between Phoenix's external front door (REST/WS/CLI/MCP) and Trinity Core. Takes user input in three forms (structured JSON, grammar tokens, natural language via LoRA adapter), validates and translates to a typed `PhysicsTask` that Trinity Core can consume. By the time a request leaves this layer, it is well-typed, schema-validated, actor-authenticated, and frontier-physics-gated.

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 3 (task grammar layer), Section 3.2 (vendored Pāṇinian grammar substrate), Section 3.3 (structured-JSON entry point), Section 3.4 (grammar-tokens entry point), Section 3.5 (LoRA hot-swap interface), Section 3.6 (translator contract), Section 3.7 (failure modes + typed exceptions).

## Key files and their roles
| File | Role |
|---|---|
| `__init__.py` | Empty Phase 0 stub. |
| `schema_validator.py` | (Phase 5) JSON-Schema validation for structured-JSON entry point. |
| `translator.py` | (Phase 5) Walks the parse tree and maps non-terminals to `PhysicsContext` fields. Versioned `translator_v1`, `translator_v1.1`, etc. |
| `lora_runtime.py` | (Phase 9) LoRA adapter runtime invocation + subprocess sandbox per Section 3.5. |

## Vendored substrate
Vendors `evolution/knowledge/grammar/` from dr-frank-and-eddy v6.6 verbatim into `vendor/grammar/`:
- `Grammar`, `Production`, `Symbol`, `ParseTree` types.
- `load_default_grammar()`, `parse()`, `generate()` functions.
- `physics_v1.yaml` — 13 non-terminals, 51 productions, the eight invariants (productivity, determinism, bounded generation, parser round-trip, codec round-trip, E4 backend compatibility, security, performance).
- 31-test invariant suite vendored alongside the code; runs as a smoke test on every release.

## Common failure modes
- `UnsupportedSchemaError` — schema_version not in supported list.
- `SchemaValidationError` — JSON shape doesn't match schema.
- `ParseError` — grammar parser rejected input.
- `UnexecutableStatementError` — well-formed grammar but no Trinity Core mapping.
- `AmbiguousStatementError` — multiple valid Trinity Core interpretations; user must supply `regime_hint`.
- `AdapterTimeoutError`, `AdapterValidationError` — LoRA subprocess sandbox issues.

## Troubleshooting
- `ParseError` includes position info; the failing token is logged in the audit event with the offending path.
- `AmbiguousStatementError` enumerates the candidate interpretations — pass `regime_hint` to disambiguate.
- LoRA adapter subprocess timeouts are configurable via `~/.phoenix/config.yaml`; default 5 seconds.

## Tests
- `tests/unit/test_smoke.py` — asserts `phoenix.grammar` imports.
- `tests/invariants/` (Phase 1+) — vendored 31-test grammar invariant suite plus Phoenix-side translation coverage.

## Recent changes
- 2026-05-06 — Phase 0: module created as empty stub.
