# `phoenix/`

Phoenix v1 package — production-grade quantum-accuracy middleware.

Top-level layout follows architecture v1 Section 10.3's file plan: one
subdirectory per architectural subsystem, each shipping its own README
that documents the subsystem's role, key files, and architectural
references.

## Subsystem map

| Subdirectory | Role | Architecture reference |
|---|---|---|
| [`_internal/`](_internal/README.md) | Cross-cutting utilities: version, latency tier enum, config loader, structured logging. | Section 10.3 |
| [`adapters/`](adapters/README.md) | LoRA adapter sandbox (Phase 9). | Section 9 |
| [`admin/`](admin/README.md) | Dev-ops admin endpoints + kill switch (Phase 8). | Section 8 |
| [`api/`](api/README.md) | Front-door REST + WebSocket surface. | Section 5 |
| [`audit/`](audit/README.md) | Structured audit-log emitter and consumers (Phase 7). | Section 7.8 + Decision 16 |
| [`cli/`](cli/README.md) | `phoenix` console-entry CLI (Phase 9). | Section 9 |
| [`grammar/`](grammar/README.md) | Task-grammar dispatch — vendored grammar wrapper. | Section 2.2 |
| [`identity/`](identity/README.md) | Install identity (Ed25519 keystore) + Actor parsing. | Sections 7.2 + 7.3 |
| [`ledger/`](ledger/README.md) | Hashchained Omega Ledger writes + replay (Phase 7). | Decision 15 + Section 6.7 |
| [`mcp/`](mcp/README.md) | MCP server surface (Phase 9). | Section 9 |
| [`providers/`](providers/README.md) | Provider adapters: classical, quantum, cognition, cloud GPU. | Section 4.2 |
| [`queue/`](queue/README.md) | NATS JetStream queue (Phase 6b). | Section 1 Decision 32 |
| [`router/`](router/README.md) | Routing decision algorithm + provider registry + failover. | Section 4 |
| [`safety/`](safety/README.md) | 9-stage safety gate, permissions, kill switch, rate limit. | Section 7.4 |
| [`state/`](state/README.md) | State backend (SQLite default + Postgres opt-in) + migrations. | Decision 31 |
| [`trinity/`](trinity/README.md) | Trinity Core — Solver / Control / Orchestrate. | Section 2 |
| [`verification/`](verification/README.md) | Verification gate orchestrator + 3 wobble axes + rung table. | Section 6 |

## How a request flows

```
POST /v1/tasks (api/routes.py)
  ↓ Actor parsing (identity/bootstrap.py)
  ↓ 9-stage safety gate (safety/gate.py)
  ↓ pipeline.solve(task)
  ↓ VerificationGate.verify (verification/gate.py)
      ↓ CrossPrecisionAxis  — Solver subsystem (trinity/solver/)
      ↓ CrossControlAxis    — Control subsystem (trinity/control/)
      ↓ Router.decide       — Section 4 seven-stage algorithm (router/)
      ↓ orchestrate(...)    — Orchestrate subsystem (trinity/orchestrate/)
      ↓ CrossProviderAxis   — Section 6.3 R4+ rung (verification/wobble_axis.py)
      ↓ PhoenixDisagreementType classification (verification/agreement_classifier.py)
  ↓ Result envelope (trinity/data_model.py)
  ↓ Audit emit (audit/emitter.py) + ledger write (ledger/omega_ledger.py)
  ↓ HTTP 200 response with full ProvenanceTrace
```

Each subsystem's README explains its piece in detail. Architecture spec
[`PHOENIX_ARCHITECTURE_v1.md`](../PHOENIX_ARCHITECTURE_v1.md) at the
repo root has the canonical descriptions.

## Vendoring boundary

Phoenix never lives in `vendor/`; `vendor/` is the frozen frank-data
substrate Phoenix consumes via the sys.path injection in
[`phoenix/__init__.py`](__init__.py). See [`vendor/README.md`](../vendor/README.md)
for the vendoring contract (Section 10.2 + 11.7.1).
