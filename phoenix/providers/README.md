# phoenix/providers

## Purpose
**Provider adapters** per architecture v1 Section 4.2. Phoenix v1 ships local hardware adapters (NPU, GPU, CPU) plus three cloud quantum providers (IBM Quantum via Qiskit Runtime, AWS Braket, IonQ direct). Phoenix v1.1 adds cloud GPU (Lambda Cloud, RunPod) and cloud cognition (Anthropic, OpenAI, Google). Two layered abstractions: Frankenstein 1.0's `ProviderAdapter` ABC for raw provider operations, and SynQc TDS's `BaseProviderClient` Protocol for experiment-preset interface used by Trinity Core's Orchestrate subsystem.

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 1 Decision 23 (v1 provider scope), Decision 24 (v1.1 expansion), Section 4.2 (vendored ProviderAdapter + BaseProviderClient), Section 2.5 (how Orchestrate calls into provider clients).

## Key files and their roles
| File | Role |
|---|---|
| `__init__.py` | Empty Phase 0 stub. |
| `base.py` | (Phase 4) Vendored Frankenstein 1.0 `ProviderAdapter` ABC. |
| `quantum/` | (Phase 4) IBM, Braket, IonQ adapters. |
| `classical/` | (Phase 4) Local NPU/GPU/CPU. |
| `cognition/` | (Phase v1.1 placeholder) Anthropic/OpenAI/Google/Grok/Perplexity adapters. |
| `cloud_gpu/` | (Phase v1.1 placeholder) Lambda Cloud, RunPod adapters. |

## Vendored substrate
- **Frankenstein 1.0 `ProviderAdapter` ABC** from `integration/providers/base.py` — universal interface across 19 quantum providers + 12 classical hardware types. Vendored verbatim into `vendor/`.
- **SynQc TDS `BaseProviderClient` Protocol** + `ProviderLiveResult` dataclass — vendored verbatim from SynQc TDS Core.

The two layers compose: a `BaseProviderClient` implementation typically wraps one or more `ProviderAdapter` instances. The Router picks the `BaseProviderClient`; that client internally uses `ProviderAdapter` for raw operations.

## Common failure modes
- Provider SDK breaking change between releases — quantum SDKs are the most volatile space; Phoenix's "single adapter interface" discipline is the defense, but each release validates against the current upstream SDK in CI.
- Provider authentication failure (e.g., expired API key) — surfaced as `ProviderAuthError`; Section 4.5 failover treats this as `DEGRADED` provider state.
- Provider quota exhaustion — surfaced via `ProviderQuotaExceeded`; Phoenix records the provider's reset window.

## Troubleshooting
- Provider health: `GET /v1/providers` (any authenticated actor); `GET /v1/admin/providers/health-history` (admin, includes failover history).
- Manual quarantine: `POST /v1/admin/providers/{id}/manual-quarantine` for ops out-of-band knowledge (e.g., maintenance announcements).

## Tests
- `tests/unit/test_smoke.py` — asserts `phoenix.providers` and the four sub-categories import.
- Per-provider integration tests land in Phase 4.

## Recent changes
- 2026-05-06 — Phase 0: module created as empty stub.
