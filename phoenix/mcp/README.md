# phoenix/mcp

## Purpose
The Phoenix MCP server. Used by agentic IDEs (Claude Code, Cursor, Cline) and by the reference admin client (architecture Section 9). Per Section 5.5, MCP tools are thin wrappers that call the REST API — the same pattern dr-frank-and-eddy v6.6 established with `frankenstein/mcp_server/server.py`. Phase 0 ships an empty stub; Phase 9 implements the v1 tool surface.

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 5.5 (MCP surface), Section 5.6 (auth + rate limit), Section 9 (reference admin client as MCP consumer), Section 1 Decision 25 (5 co-authors as MCP consumers, not v1 core).

## Key files and their roles
| File | Role |
|---|---|
| `__init__.py` | Empty Phase 0 stub. |
| `server.py` | (Phase 9) FastMCP server. |
| `tools.py` | (Phase 9) Tool registrations; each tool calls REST internally per Section 5.5. |
| `transport.py` | (Phase 9) stdio + HTTP+SSE transport handlers. |

## Vendored substrate
The seven Sanskrit memory tools (`phoenix_memory_compress`, `phoenix_memory_decompress`, `phoenix_memory_recall`, `phoenix_memory_codec_status`, `phoenix_memory_grammar_generate`, `phoenix_memory_grammar_parse`, `phoenix_memory_propose_rule`) are vendored from `frankenstein/mcp_server/server.py` v6.6 with the `phoenix_` prefix to avoid collision when both servers are configured in the same MCP client. See `vendor/synthesis/` once Phase 1 lands.

## Common failure modes
None yet — Phase 0 skeleton stub.

## Troubleshooting
Module is empty in Phase 0.

## Tests
- `tests/unit/test_smoke.py` — asserts `phoenix.mcp` imports.

## Recent changes
- 2026-05-06 — Phase 0: module created as empty stub.
