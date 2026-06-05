# Phase 13.x.8 — Per-actor key isolation — Design v2 (descoped to plumbing)

**Date:** 2026-06-05
**Author:** Adam (with Claude as design partner)
**Status:** REVISED — supersedes the 2026-05-28 v1 (`0d2d9ab`). Incorporates the adversarial design review (workflow `wf_432eec7f`, 6 reviewers, 50 findings). Scope decision (Option A — descope to plumbing) locked by Adam 2026-06-05.
**Type:** v1.1 sub-improvement design (Phase 13.x track)

**Supersedes:** `docs/superpowers/specs/2026-05-28-phase-13x8-per-actor-key-isolation-design.md` (v1). The v1 doc remains as the historical record of the original (write-path-assuming) design; this v2 is authoritative.

**Architectural reference:**
- `phoenix/ledger/encryption.py:153-188` — `_PROMPT_ENCRYPTOR` singleton + `get_/set_/reset_prompt_encryptor()`. **The proven pattern this design mirrors.**
- `phoenix/ledger/encryption_age.py` (PR #21) — `AgePromptEncryptor`, `default_keys_dir()`, `encryptor_from_default_layout()`, `AgeKeyLoadError`/`AgeKeyPermissionError`.
- `phoenix/ledger/keygen.py` (PR #23) — `generate_age_keypair(*, keys_dir, name, force)` + `_resolve_paths` (already supports nested `actors/<name>/` via the `keys_dir` parameter — verified, no keygen change needed).
- `phoenix/ledger/cognition_replay.py:988` — `_replay_encrypted` reads `payload.get("prompt_encrypted")` (payload-based, NOT a column).
- `phoenix/admin/encryption_admin.py` (PR #23) — rotate-key endpoint + auth chain (`_admin_authn` → `require_admin` → `can_rotate_encryption_key`).
- `vendor/actor/actor.py:81` — `Actor` has `name` (signature-bound), **no `id` field**.
- `phoenix/api/routes.py:1114` — `_ACTOR_NAME_RE = ^[a-z0-9_\-]{1,64}$` (the validation allowlist for actor names).

---

## 0 — What the review changed (read this first)

The v1 spec assumed an ENCRYPTED_OPT_IN **encrypt/write path** exists that 13.x.8 would modify in a one-line edit. **It does not exist.** Verified by 4 of 6 reviewers against the live tree:

- The only `.encrypt()` call site is internal to `AgePromptEncryptor`.
- `get_prompt_encryptor()` is called ONLY on the read/replay side (`cognition_replay.py:998`).
- `set_prompt_encryptor()` is never invoked at daemon startup (not in `main.py` / `api/__main__.py` / `routes.py` / `launcher.py`).
- No `CognitionEntry` dataclass, no `cognition_to_ledger_entry` converter, nothing writes `entry_kind="cognition"` rows with encrypted prompts.

This is the **deliberate** 13-D2 state: "ENCRYPTED_OPT_IN — column shipped, key-mgmt ceremony deferred to first commercial customer." Phase 13 shipped the decrypt/replay half + the encryptor + the permission gate; the encrypt-on-write half was intentionally never poured.

**Scope decision (Adam, 2026-06-05): Option A — descope 13.x.8 to per-actor *plumbing*.** Build the registry, keygen routing, CLI, admin endpoints, and decrypt-routing so per-actor isolation is wired, tested, and ready — but do NOT build the ENCRYPTED_OPT_IN write path or startup encryptor activation (those stay deferred per 13-D2). The plumbing is independently testable on hand-constructed payloads.

Two further verified blockers, now fixed in this v2:
- **`actor.id` → `actor.name`** everywhere (Actor has no `id`).
- **Payload-field storage, not a SQL column** (`prompt_encryption_actor_id` lives inside `payload_json`; a bare `ALTER TABLE` column would be written nowhere and read nowhere). **No migration file.**

---

## 1 — Context

Phase 13.x.6 + 13.x.7 ship a **single shared encryption substrate**: one daemon-level identity + recipients directory, one global `set_prompt_encryptor()`, one shared recipient set for all (future) ENCRYPTED_OPT_IN rows.

This works for single-tenant deployments. It does NOT serve multi-tenant / compliance scenarios where horizontal blast-radius, per-tenant cryptographic separation (SOC 2 / FedRAMP / HIPAA), and per-actor revocation matter. Phase 13.x.8 introduces per-actor key isolation: each Phoenix actor (keyed by `actor.name`) gets their own age identity + recipient pair, resolved at encrypt/decrypt time.

**Goals this v2 delivers** (plumbing):
- Per-actor encryptor registry with fail-closed isolation semantics.
- Per-actor key generation (CLI + admin endpoint) under `actors/<name>/`.
- Decrypt-routing that reads the per-actor identifier from the payload and resolves the correct encryptor.
- Enumeration of actors with per-actor keys configured.

**Goals deferred** (require the unbuilt write path; explicitly out of scope, see §3):
- **Forensic attribution** in the encrypt audit trail. The encrypt audit currently hard-codes `actor_id="system.encryptor"` (`encryption_age.py:250`) and `PromptEncryptor.encrypt(canonical_form)` has no actor param. Per-actor *keys* without per-actor *audit attribution* would over-claim forensic attribution, so this goal is deferred to the write-path activation phase, NOT claimed by 13.x.8.

## 2 — Goal

Ship per-actor cryptographic **plumbing** for `ENCRYPTED_OPT_IN` such that, when the ENCRYPTED_OPT_IN write path is later activated (deferred per 13-D2), per-actor isolation is already wired and only needs the writer to call `get_prompt_encryptor_for_actor(actor.name)` + stamp `payload["prompt_encryption_actor_id"]`.

Concretely:
1. Each actor (per `actor.name`) can have their own age identity + recipient files under `actors/<name>/`.
2. A registry resolves `actor.name` → that actor's encryptor, **fail-closed** when the actor is configured for isolation but the keys are broken.
3. The decrypt/replay path reads `payload.get("prompt_encryption_actor_id")` and routes to the per-actor encryptor (or the shared one when absent).
4. CLI `--actor` + admin endpoint `actor_name` provision per-actor keys; an enumeration endpoint lists configured actors.
5. **Backward-compatible:** absent/NULL `prompt_encryption_actor_id` → shared encryptor (existing behavior preserved). No new SQL column, no migration.

## 3 — Out of scope (deferred)

**Deferred-with-the-write-path (13-D2 activation, a separate future phase):**
- The ENCRYPTED_OPT_IN **encrypt/write path** itself: `CognitionEntry` type + `cognition_to_ledger_entry` converter + dispatch wiring + `set_prompt_encryptor()` activation at daemon startup. Without this, no production row is encrypted at all (per-actor or shared). 13.x.8 plumbing is correct and tested but not end-to-end-live until this lands.
- **Per-actor audit attribution** (threading actor identity into the encrypt audit event). Ties to the write path.
- **Key-regenerated-vs-tampered distinction** via encrypt-time recipient-fingerprint comparison. The fingerprint is recorded by the write path; 13.x.8's decrypt-routing surfaces a clear typed error on per-actor decrypt failure but cannot distinguish "keys regenerated" from "tampered" until the write path records the fingerprint. Best-effort typed error now; full distinction with the write path.

**Deferred-to-follow-up (independent of the write path):**
- **Batch migration** of existing shared-key rows to per-actor (same class as 13.x.7's deferred batch-rotate).
- **Cross-actor delegated decrypt** for compliance audits (v1.2.x).
- **Hardware-backed / KMS-backed per-actor keys** (v1.2.x).
- **Per-actor key escrow / recovery** (out of scope; regenerating an actor's keys loses their prior encrypted data).

## 4 — API surface

### 4.1 Directory layout

Extends `~/.phoenix/runtime/encryption_keys/`:

```
encryption_keys/
├── identity.txt                          # Shared/default (13.x.6; preserved)
├── recipients/
│   ├── primary.pub
│   └── rotation-*.pub
└── actors/                               # NEW (Phase 13.x.8)
    └── <actor_name>/                     # 0o700 on POSIX (Windows: NTFS ACL, WARN)
        ├── identity.txt                  # mode 0o600
        └── recipients/
            ├── primary.pub
            └── rotation-*.pub
```

**Convention (fail-closed by presence):** if `actors/<actor_name>/` **exists**, that actor is configured for isolation — a load failure MUST raise (never silently fall back to shared). If the directory is **absent**, the actor falls back to the shared encryptor (13.x.6 behavior).

`<actor_name>` MUST pass `_ACTOR_NAME_RE` (`^[a-z0-9_\-]{1,64}$`) before any path construction, and the resolved path MUST be asserted to stay under the `actors/` root (path-traversal guard).

### 4.2 New module `phoenix/ledger/encryption_actors.py`

Per-actor encryptor registry. Mirrors `encryption.py`'s singleton pattern, keyed by `actor_name`, **with a `threading.Lock`** (this cache mutates at request time, unlike the startup-only shared registry).

```python
import threading

_per_actor_encryptors: dict[str, PromptEncryptor] = {}
_lock = threading.Lock()


def get_prompt_encryptor_for_actor(actor_name: str) -> PromptEncryptor:
    """Resolve the encryptor for actor_name. FAIL-CLOSED by directory presence.

    Resolution:
    1. Cache hit (under lock).
    2. If actors/<actor_name>/ directory EXISTS:
         load via encryptor_from_actor_default_layout(actor_name);
         on AgeKeyLoadError OR AgeKeyPermissionError → RAISE (PerActorKeyError),
         never fall back to shared (no silent isolation downgrade).
       cache + return.
    3. If the directory is ABSENT: fall back to get_prompt_encryptor() (shared).
    """

def set_prompt_encryptor_for_actor(actor_name: str, encryptor: PromptEncryptor) -> None: ...

def reset_prompt_encryptor_for_actor(actor_name: str | None = None) -> None:
    """Evict one actor's cached encryptor (None → evict all). Under lock."""

def encryptor_from_actor_default_layout(actor_name: str) -> AgePromptEncryptor:
    """Build an AgePromptEncryptor rooted at actors/<validated name>/.
    Reuses keygen/encryption_age default-layout logic with keys_dir =
    default_keys_dir()/'actors'/<name>."""

def list_actors_with_keys() -> list[str]:
    """actor_names with a valid actors/<name>/identity.txt on disk."""
```

New typed error: `PerActorKeyError` (raised on directory-present-but-broken-keys, so callers can distinguish fail-closed from the shared fallback).

`actor_name` validation (`_ACTOR_NAME_RE` + path-under-root assertion) happens in every function that constructs an `actors/<name>/` path.

### 4.3 Storage of the per-actor identifier (payload, NOT a column)

`prompt_encryption_actor_id` lives **inside the cognition `payload_json`**, alongside `prompt_encrypted`. Rationale (verified): the SQLite + Postgres backends persist only 7 base columns (`entry_id`, `entry_kind`, `timestamp_unix`, `actor_id`, `parent_hash`, `entry_hash`, `payload_json`); the Phase 13 `prompt_*` columns are added by migration but never written or selected by the backends. Replay reads every prompt field from the parsed payload. A bare `ALTER TABLE ADD COLUMN` would be written nowhere and read nowhere — silently routing per-actor rows to the shared key.

**Consequence: NO migration file.** (The v1 spec's `phase13x8_per_actor_keys.py` is dropped entirely.)

Semantics:
- `payload["prompt_encryption_actor_id"]` absent or null → shared encryptor (`get_prompt_encryptor()`).
- present → per-actor encryptor (`get_prompt_encryptor_for_actor(<name>)`).

### 4.4 Write path — DEFERRED (documented for the future writer)

The ENCRYPTED_OPT_IN write path does not exist (§0). When it is built (deferred phase), it MUST:
1. Resolve the actor from auth context as `actor.name` (NOT `actor.id` — Actor has no `id`).
2. Call `get_prompt_encryptor_for_actor(actor.name)`.
3. Write ciphertext to `payload["prompt_encrypted"]`.
4. Stamp `payload["prompt_encryption_actor_id"] = actor.name`.
5. Emit an actor-tagged audit event (the deferred forensic-attribution work).

13.x.8 provides the registry + resolution this future writer calls. It does NOT build the writer.

### 4.5 Decrypt/replay routing (IN scope)

Modify `phoenix/ledger/cognition_replay.py::_replay_encrypted`:
1. Read `actor_name = payload.get("prompt_encryption_actor_id")`.
2. If absent/null → `get_prompt_encryptor()` (shared; back-compat — all current rows).
3. If present → `get_prompt_encryptor_for_actor(actor_name)`.
4. On per-actor decrypt failure, surface a clear typed error (best-effort; the keys-regenerated-vs-tampered distinction is deferred with the write-path fingerprint recording).

Tested with hand-constructed payloads carrying `prompt_encryption_actor_id` (no live writer produces them yet).

### 4.6 CLI + admin endpoints (IN scope)

**CLI:** `phoenix admin generate-encryption-key --actor <name>`. When set, validates `<name>` (`_ACTOR_NAME_RE`) and routes keys to `actors/<name>/` by passing `keys_dir = default_keys_dir()/'actors'/<name>` to `generate_age_keypair` (keygen already supports this; no keygen change). Without `--actor`, behavior unchanged (shared).

**Admin rotate endpoint:** `POST /v1/admin/encryption/rotate-key` body gains optional `actor_name`. The live handler hardcodes `keys_dir=None` (`encryption_admin.py:230`); the new path computes `keys_dir = default_keys_dir()/'actors'/<validated name>` and passes it. The `can_rotate_encryption_key` gate is checked against the **authenticated admin** (the target `actor_name` is just a parameter). On success, after audit emit, fire `reset_prompt_encryptor_for_actor(actor_name)` (cache eviction per [OPEN: cache-invalidation]).

**Enumeration endpoint:** `GET /v1/admin/encryption/actors` → `list_actors_with_keys()`. Gated on `is_admin` via `require_admin` (NOT the nonexistent `can_use_admin_endpoints`; the codebase uses `is_admin`).

### 4.7 No new permission flag

`can_rotate_encryption_key` (exists, `permissions.py:105`) gates the rotate-key endpoint regardless of which actor's keys are involved. The enumeration endpoint uses `is_admin`. Per [OPEN: self-serve-rotation] = NO, no actor-self-serve surface is added. **13.x.8 adds no `ActorPermissions` field.**

## 5 — Decision flow

### 5.1 Per-actor encryptor resolution (fail-closed)

```
get_prompt_encryptor_for_actor(actor_name):
  validate actor_name (_ACTOR_NAME_RE); assert path under actors/ root
  with _lock:
    if actor_name in cache: return cache[actor_name]
  dir = default_keys_dir()/'actors'/actor_name
  if dir.exists():                      # configured for isolation
    try:
      enc = encryptor_from_actor_default_layout(actor_name)
    except (AgeKeyLoadError, AgeKeyPermissionError) as exc:
      raise PerActorKeyError(actor_name) from exc   # FAIL CLOSED
    with _lock:
      cache[actor_name] = enc
    return enc
  return get_prompt_encryptor()         # not configured → shared fallback
```

### 5.2 Decrypt routing (replay)

```
_replay_encrypted(payload):
  actor_name = payload.get("prompt_encryption_actor_id")
  enc = get_prompt_encryptor() if not actor_name
        else get_prompt_encryptor_for_actor(actor_name)
  plaintext = enc.decrypt(payload["prompt_encrypted"])   # typed error on failure
```

### 5.3 Rotate-key with actor_name

```
POST /v1/admin/encryption/rotate-key {actor_name?, ...}:
  _admin_authn → require_admin → can_rotate_encryption_key   (authenticated admin)
  if actor_name:
    validate; keys_dir = default_keys_dir()/'actors'/<name>
  else:
    keys_dir = None   (shared, existing behavior)
  result = generate_age_keypair(keys_dir=keys_dir, ...)
  emit audit (admin.encryption.rotate.success, with target actor_name if set)
  if actor_name: reset_prompt_encryptor_for_actor(actor_name)   # evict stale cache
  return result
```

## 6 — Resolved open tensions (from the v1 [OPEN:] markers + review)

- **actor-bootstrap:** rec (b) opt-in — confirmed. Resolved jointly with §5.1 fail-closed-by-directory-presence: a present `actors/<name>/` directory IS the opt-in signal. Auto-generate-on-enroll is rejected (would couple the encryption-extra-optional enroll path to `pyrage` and collide with enroll's idempotent-overwrite semantics).
- **self-serve-rotation:** rec NO — confirmed. `can_rotate_encryption_key` is a dedicated admin gate; Phoenix's solo-operator model means the admin IS the actor; self-serve is net-new attack surface with no demand.
- **cache-invalidation:** rec (b) evict-on-rotate — confirmed, **with a `threading.Lock`** (the cache mutates at request time, unlike the startup-only shared registry) and a single-worker caveat (in-process eviction is correct only while the daemon is single-worker uvicorn; revisit when `--workers` ships).
- **migration-direction:** rec (a) mixed state — confirmed for the replay branch (absent → shared, present → per-actor; no query crashes). The v1 "query per-actor encrypted rows directly" audit-filter promise is **dropped** (the audit path parses `payload_json` client-side and never SELECTs prompt columns; a bare column wouldn't deliver it). Audit filtering on the payload field is a follow-up if needed.
- **keys-on-disk-permissions:** rec YES `0o700` on the actor subdir — confirmed, using the same `sys.platform != "win32"` chmod + Windows-WARN pattern as keygen's `0o600` discipline.

## 7 — Acceptance criteria (descoped plumbing)

13.x.8 v2 is complete when:

1. `phoenix/ledger/encryption_actors.py` ships the registry (`get_/set_/reset_prompt_encryptor_for_actor`, `encryptor_from_actor_default_layout`, `list_actors_with_keys`) with the `threading.Lock`, `PerActorKeyError`, and `_ACTOR_NAME_RE` + path-under-root validation.
2. **Fail-closed semantics:** directory-present + broken keys raises `PerActorKeyError`; directory-absent falls back to shared. Tested both ways.
3. `_replay_encrypted` reads `payload.get("prompt_encryption_actor_id")` and routes correctly (absent → shared, present → per-actor).
4. CLI `--actor` routes keys under `actors/<name>/`; admin rotate-key accepts optional `actor_name` + evicts cache on success; `GET /v1/admin/encryption/actors` enumeration ships (gated on `is_admin`).
5. **No migration, no new permission flag, no SQL column.** Identifier lives in `payload_json`.
6. Tests:
   - registry resolution (cache hit / disk-load / shared fallback when dir absent)
   - **fail-closed:** dir present + identity unreadable → raises (no silent downgrade)
   - **fail-closed:** dir present + loose-permission identity (`AgeKeyPermissionError`) → typed fail-closed
   - **path-traversal:** `actor_name="../primary"` → refused
   - mixed-ledger decrypt-routing: one absent-id (shared) payload + one per-actor payload both route to the right encryptor (hand-constructed payloads + injected encryptors)
   - CLI `--actor` writes under `actors/<name>/`
   - rotate-key with `actor_name` writes under `actors/<name>/` + fires cache eviction
   - enumeration endpoint lists configured actors, gated on `is_admin`
7. CHANGELOG entry documenting: the per-actor plumbing, the **explicit write-path deferral** (per 13-D2), payload-field storage, fail-closed semantics, and no-new-column/no-new-permission.
8. mypy --strict clean; ruff clean.

## 8 — File-level summary

**New files:**
- `phoenix/ledger/encryption_actors.py` (~180 lines: registry + lock + validation + `PerActorKeyError` + `encryptor_from_actor_default_layout` + `list_actors_with_keys`)
- `tests/cognition/test_encryption_actors.py` (~250 lines: registry resolution / fail-closed / path-traversal / lock)
- `tests/integration/test_admin_encryption_actors.py` (~150 lines: rotate-key with actor_name + enumeration endpoint)
- `tests/cli/test_admin_generate_encryption_key_actor.py` (~80 lines: CLI --actor)
- `tests/cognition/test_replay_per_actor_routing.py` (~120 lines: decrypt-routing on hand-constructed payloads)

**Modified files:**
- `phoenix/ledger/cognition_replay.py` — `_replay_encrypted` reads the payload field + routes
- `phoenix/admin/encryption_admin.py` — rotate-key optional `actor_name` + per-actor `keys_dir` + cache eviction; new enumeration endpoint (or a sibling `encryption_actors_admin.py` — design call at implementation)
- `phoenix/cli/commands/admin.py` + `phoenix/cli/entry.py` — `--actor` flag + routing
- `phoenix/ledger/README.md` — per-actor ceremony section
- `CHANGELOG.md`

**NO migration file. NO permissions.py change.**

**Total new tests:** ~28-32.

## 9 — Risks and mitigations

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Silent isolation downgrade on transient key-load failure | **was Medium → fixed** | Fail-closed by directory presence (§5.1). Dir present + load fails → raise. Tested. |
| Loose-permission per-actor key crashes encrypt uncleanly | Medium | `AgeKeyPermissionError` caught in the fail-closed branch → typed `PerActorKeyError`. Tested. |
| Path traversal via `actor_name` | Medium | `_ACTOR_NAME_RE` validation + path-under-root assertion before any `actors/<name>/` construction. Adversarial test (`../primary`). |
| Cache race at request time | Medium | `threading.Lock` on the per-actor dict. Single-worker caveat noted; revisit at `--workers`. |
| Plumbing ships but never fires (no write path) | **Accepted, documented** | CHANGELOG + §0 + §3 state plainly: per-actor isolation is wired + tested but not end-to-end-live until the deferred write path activates. Not over-claimed. |
| Over-claiming forensic attribution | was a goal → struck | Forensic attribution explicitly deferred (§1) — encrypt audit is `system.encryptor`; not claimed by 13.x.8. |

## 10 — Scope summary

**Medium scope** (smaller than v1's mistaken estimate, since the write path is correctly excluded). Realistic budget: ~2-3 hours via subagent-driven test/review cycles. Pure plumbing — no new cryptographic design, no migration, no permission flag.

**The honest one-liner for the PR:** "Per-actor encryption plumbing — registry, keygen routing, CLI/admin surface, and decrypt-routing — wired and tested fail-closed, ready for the day the deferred ENCRYPTED_OPT_IN write path activates. Does not itself make ENCRYPTED_OPT_IN end-to-end-live."
