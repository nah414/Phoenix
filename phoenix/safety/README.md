# phoenix/safety

## Purpose
The **safety gate** between the task grammar layer and Trinity Core per architecture v1 Section 7. Validates `Actor` (vendored from dr-frank-and-eddy with HMAC-SHA256 + 5-minute window + constant-time compare), checks capabilities (`can_submit_tasks`, `frontier_physics`, `is_admin`, etc.), enforces rate limits (token-bucket per actor + per-org aggregation), and raises typed exceptions on every failure. Defense-in-depth: redoes the type check, freshness check, and signature verification that the front door already did, so a front-door bug is not a free pass into Trinity Core. Fail-closed on every failure mode.

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 7 (safety gate), Section 7.2 (vendored Actor pattern + honest threat model), Section 7.3 (`ActorPermissions` registry), Section 7.4 (nine-stage validation pipeline), Section 7.5 (rate limiting policy + cost-weighted token bucket), Section 7.6 (org enrollment ceremony with HKDF subkeys), Section 7.7 (operator override for HUMAN_REVIEW), Section 7.8 (failure modes + typed exceptions).

## Key files and their roles
| File | Role |
|---|---|
| `__init__.py` | Empty Phase 0 stub. |
| `gate.py` | (Phase 6) Nine-stage validation pipeline. |
| `permissions.py` | (Phase 6) `ActorPermissions` registry + lookup. Storage in state backend (append-only audit trail). |
| `rate_limiter.py` | (Phase 6) Token-bucket with cost weighting and per-org aggregation. |
| `enrollment.py` | (Phase 6) Org enrollment ceremony — HKDF-derived per-install subkey. |
| `override.py` | (Phase 6) Operator override flow for `HUMAN_REVIEW` per Section 7.7. |

## Vendored substrate
Vendors `evolution/knowledge/actor.py` from dr-frank-and-eddy v6.6 unchanged into `vendor/actor/`:
- Frozen `Actor` dataclass: `name`, `identity_fingerprint`, `issued_at`, `signature`.
- HMAC-SHA256 signing/verification with constant-time compare and a 5-minute (`±300s`) validity window.
- Type guard: passing a raw string or dict to engine boundaries raises `TypeError` *before* policy checks.

**Threat model (per Section 7.2): the Actor pattern is defense-in-depth, NOT airtight.** A malicious local process running as the same OS user can read the install Ed25519 master key out of DPAPI/Keychain/libsecret and sign Actors. True per-app isolation requires OS-level ACLs or per-app credential stores. Phoenix documents this honestly; users on multi-user machines need additional OS-level isolation.

## Common failure modes
- `TypeError` (HTTP 500) — wrong type for actor parameter (internal Phoenix bug).
- `PermissionError(ActorExpired)` (401) — wall-clock skew or stale request.
- `PermissionError(SignatureInvalid)` (401) — wrong key or tampered payload.
- `PermissionDenied(UnknownActor)` (403) — actor name signed with valid key but not in registry.
- `PermissionDenied(MissingCapability)` (403) — actor lacks the specific flag for the request.
- `FrontierPhysicsRefused` (403) — Wheeler-DeWitt or gravitational solver requested without `frontier_physics` capability.
- `RateLimitExceeded` (429) — token bucket empty; `Retry-After` header set.
- `PermissionRegistryUnavailable` (503) — state backend failure during lookup; fail-closed.

## Troubleshooting
- All safety-gate decisions emit structured audit events with actor fingerprint, capability checked, decision, request_id (Section 1 Decision 16). Inspect via `GET /v1/admin/audit/replay`.
- Permission cache TTL is 30 seconds; revocations propagate within that window.
- Org-level rate limits aggregate across all installs in the org; one misbehaving install can exhaust the org's budget. Inspect via `GET /v1/admin/budget`.

## Tests
- `tests/unit/test_smoke.py` — asserts `phoenix.safety` imports.
- `evals/frontier_physics/` (Phase 7+) — frontier-physics gating refuses correctly.
- `evals/cost_ceiling/` (Phase 4+) — rate-limit + cost-ceiling interaction.

## Recent changes
- 2026-05-06 — Phase 0: module created as empty stub.
