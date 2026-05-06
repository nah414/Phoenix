# phoenix/api

## Purpose
Front-door REST and WebSocket surface for Phoenix per architecture v1 Section 5. The REST API is canonical; CLI, MCP, and WebSocket all delegate to it via thin adapters. Phase 0 ships only `GET /v1/health`; the full `/v1/...` endpoint surface lands across later phases.

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 5 (front door), Section 5.2 (REST endpoint surface), Section 5.3 (WebSocket events), Section 5.6 (cross-protocol auth + rate limiting + audit), Section 5.8 (per-protocol latency budget).

## Key files and their roles
| File | Role |
|---|---|
| `__init__.py` | Empty Phase 0 stub. |
| `routes.py` | (Phase 0 Step 7) FastAPI app exposing `GET /v1/health`. Full endpoint surface lands in Phases 5+. |
| `__main__.py` | (Phase 0 Step 7) Module entry point: `python -m phoenix.api --port 8003`. |
| `error_envelope.py` | (Phase 0 Step 7) Typed error envelope dataclass per Section 5.2; wired into real exception handlers in Phase 5. |
| `admin_routes.py` | (Phase 8) `/v1/admin/...` endpoints — dev-ops backdoor. |
| `ws_handlers.py` | (Phase 5+) WebSocket task lifecycle, drift events, verification gate streaming. |
| `openapi.yaml` | (Phase 5) Committed OpenAPI 3.1 schema, served live at `GET /v1/openapi.json`. |

## Vendored substrate
None. `phoenix/api/` is greenfield Phoenix code.

## Common failure modes
None yet — Phase 0 skeleton stub.

## Troubleshooting
After Step 7: if `python -m phoenix.api` fails to bind port 8003, check for collisions with `netstat -ano | findstr :8003`. The launcher refuses to silently bump ports per Section 10.5.

## Tests
- `tests/unit/test_smoke.py` (Phase 0 Step 6) — asserts `phoenix.api` imports.
- `tests/integration/test_health.py` (Phase 0 Step 6) — asserts the `GET /v1/health` contract.

## Recent changes
- 2026-05-06 — Phase 0 (BUILDGUIDE_phoenix_v1_phase0_skeleton.md): module created as empty stub.
