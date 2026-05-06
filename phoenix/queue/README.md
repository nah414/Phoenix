# phoenix/queue

## Purpose
**NATS JetStream client** per architecture v1 Section 1 Decisions 32–33. NATS JetStream regardless of deployment size: single Go binary, embeddable, file-backed durable queues. No Redis dependency. A solo Phoenix install boots two processes — Phoenix itself + NATS — both managed by the same launcher script. No Docker required for solo use.

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 1 Decision 32 (NATS JetStream backend), Decision 33 (solo install boots Phoenix + NATS), Section 10.5 (launcher coordinates the boot order — NATS lands in launch.bat in Phase 6).

## Key files and their roles
| File | Role |
|---|---|
| `__init__.py` | Empty Phase 0 stub. Note: this directory shadows stdlib `queue` *within the phoenix.* namespace only*; the smoke test verifies they don't collide. |
| `nats_client.py` | (Phase 6) Connection management, subject layout, durable consumers. |
| `task_queue.py` | (Phase 6) Task submission and worker dispatch — Phoenix's job queue lives here. |
| `embedded_runner.py` | (Phase 6) Embedded NATS process launcher for solo deployments. |

## Vendored substrate
None. `phoenix/queue/` is greenfield Phoenix code on top of the published `nats-py` client (added as a runtime dep in Phase 6).

## Common failure modes
- `QueueUnavailable` — NATS connection lost; safety gate fails closed (no new task submission).
- `EmbeddedRunnerFailure` — solo-mode NATS subprocess crashed; the launcher's restart policy attempts re-spawn.
- `DurableConsumerLag` — workers can't keep up; surfaced via `/v1/admin/health/detailed` queue-depth metric.

## Troubleshooting
- Queue depth visibility: `GET /v1/admin/health/detailed` shows current depth across consumers.
- Solo install: the launcher boots NATS in the background before Phoenix; if Phoenix can't connect on startup, check the `.runtime/nats/` log for embedded-runner errors.
- Org install: Phoenix can either embed its own NATS or connect to an external NATS cluster (recommended for HA); set via `~/.phoenix/config.yaml`.

## Tests
- `tests/unit/test_smoke.py` — asserts `phoenix.queue` imports and is distinct from stdlib `queue`.
- `evals/audit/` (Phase 7+) — task lifecycle events (submit → solver.complete → control.complete → orchestrate.progress → complete) all land in the audit log via the queue.

## Recent changes
- 2026-05-06 — Phase 0: module created as empty stub.
