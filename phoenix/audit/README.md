# phoenix/audit

## Purpose
**Structured event log** per architecture v1 Section 1 Decisions 16 and 22. Every Trinity Core layer transition, every router decision, every authentication check, every drift alert, every config change emits a structured event with timestamp, actor identity, layer, parameters, and result hash. Native Phoenix event format internally; OpenTelemetry export adapter on top so users get standards compliance without a hard dependency. Default destination is local JSONL file; OTel adapter is opt-in via config and exports to any OTLP-compatible backend.

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 1 Decision 16 (audit-grade structured logging), Decision 22 (OpenTelemetry as export standard, not internal format), Section 5.6 (cross-protocol correlation via `request_id`), Section 8.6 (audit-log streams + drift telemetry + per-provider telemetry).

## Key files and their roles
| File | Role |
|---|---|
| `__init__.py` | Empty Phase 0 stub. |
| `event_format.py` | (Phase 7) Native Phoenix event format — typed dataclasses, JSON-serializable, schema-versioned. |
| `emitter.py` | (Phase 7) Fire-and-forget async event writer; sub-50µs overhead per emit. |
| `otel_adapter.py` | (Phase 7) OpenTelemetry export adapter; opt-in via config; OTLP-compatible. |
| `jsonl_writer.py` | (Phase 7) Default local-file destination; the audit log of last resort. |

## Vendored substrate
None. `phoenix/audit/` is greenfield Phoenix code on top of OpenTelemetry's published spec.

## Common failure modes
- Audit emitter buffer full — events dropped with a counter exposed in `/v1/admin/health/detailed`. Fail-open on the audit path because blocking user-facing solves on a logging backend is worse than missed events; the dropped-event counter is itself an alert.
- OTel exporter unreachable — Phoenix continues writing to local JSONL; the adapter retries with exponential backoff.

## Troubleshooting
- The `request_id` (UUID v7) propagates across REST → audit log → ledger → MCP → WebSocket; correlate across protocols with one search.
- Audit log streaming: `GET /v1/audit/events` (filterable by actor, time window, event type) for any authenticated actor; `GET /v1/admin/audit/replay` for admin-only deeper history including denied requests.
- The OpenTelemetry adapter exports to any OTLP-compatible backend (Datadog, Splunk, Honeycomb, etc.) when enabled in `~/.phoenix/config.yaml`.

## Tests
- `tests/unit/test_smoke.py` — asserts `phoenix.audit` imports.
- `evals/audit/` (Phase 7+) — every required event type reaches the audit log under load.

## Recent changes
- 2026-05-06 — Phase 0: module created as empty stub.
