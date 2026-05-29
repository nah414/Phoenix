# Phase 13.x.8 — Per-actor key isolation — Design

**Date:** 2026-05-28
**Author:** Adam (with Claude as design partner)
**Status:** DRAFT (design-only; no implementation in this session) — awaiting Adam review
**Type:** v1.1 sub-improvement design (Phase 13.x track)

**Architectural reference:**
- `phoenix/ledger/encryption_age.py` (PR #21, Phase 13.x.6) — `AgePromptEncryptor` reference impl with multi-recipient lossless rotation
- `phoenix/ledger/keygen.py` (PR #23, Phase 13.x.7) — `generate_age_keypair()` primitive + CLI + rotate-key endpoint
- `phoenix/safety/permissions.py` — `ActorPermissions` flag registry
- `phoenix/ledger/encryption.py` — `PromptEncryptor` Protocol + global registry pattern

**Companion work needed BEFORE this can ship:**
- PR #23 (Phase 13.x.7) merged to main. 13.x.8 depends on the `keygen.py` primitive + the rotate-key endpoint shape.

---

## 1 — Context

Phase 13.x.6 + 13.x.7 ship a **single shared encryption substrate**: one daemon-level identity file + recipients directory, one global `set_prompt_encryptor()`, all ENCRYPTED_OPT_IN ledger rows are encrypted with the same recipient set.

This works for single-tenant deployments where the daemon is operated as one trusted unit. It does NOT work for multi-tenant or compliance scenarios where:

- **Horizontal blast radius matters** — an attacker who steals one identity file can decrypt every actor's prompts.
- **Per-tenant cryptographic separation is required** by SOC 2 / FedRAMP / HIPAA frameworks.
- **Forensic attribution** — answering "whose encrypted prompts were accessed in this breach?" requires per-tenant key boundaries.
- **Per-actor revocation** — rotating one actor's keys after a credential leak shouldn't disrupt other actors.

Phase 13.x.8 introduces per-actor key isolation: each Phoenix actor gets their own age identity + recipient pair, and the encrypt/decrypt path looks up the actor's encryptor at use time.

## 2 — Goal

Ship per-actor cryptographic isolation for `ENCRYPTED_OPT_IN` prompt-disposition data, such that:

1. Each actor (per `actor_id`) has their own age identity + recipient files.
2. The encrypt path resolves the actor → that actor's encryptor.
3. The decrypt path uses the same actor lookup.
4. The ledger row records WHICH actor's recipient was used (so replay finds the right keys).
5. CLI + admin endpoint gain `--actor <id>` parameters.
6. **Backward-compatible:** existing 13.x.6/.x.7 single-key deployments continue to work unchanged when `phoenix.ledger.encryption_actors` is not configured.

## 3 — Out of scope (deferred to follow-ups)

- **Batch migration of existing shared-key ENCRYPTED_OPT_IN rows to per-actor keys.** Would require: decrypt with shared key, re-encrypt to per-actor recipient, update ledger row. Same family of "DB-transactions + partial-failure" work as 13.x.7's deferred batch-rotate. Gets its own follow-up.
- **Cross-actor delegated decrypt** — letting an authorized auditor decrypt across actors. Important for compliance audits but architecturally distinct (involves group/role keys, not per-actor). v1.2.x+.
- **Hardware-backed actor keys** (HSM, secure enclave). v1.2.x.
- **Per-actor key escrow / recovery** if an actor loses their identity file. Out of scope; current design assumes ops/admin can regenerate the actor's keys (losing prior encrypted data).
- **KMS-backed per-actor keys** (`AwsKmsPromptEncryptor` etc. per-tenant). v1.2.x.

## 4 — API surface

### 4.1 Directory layout

Extends the existing `~/.phoenix/runtime/encryption_keys/` layout:

```
encryption_keys/
├── identity.txt                          # Shared/default (13.x.6 layout; preserved for back-compat)
├── recipients/
│   ├── primary.pub                       # Shared/default
│   └── rotation-*.pub                    # Shared rotation recipients
└── actors/                               # NEW (Phase 13.x.8)
    ├── adam/
    │   ├── identity.txt                  # Actor adam's identity (mode 0o600)
    │   └── recipients/
    │       ├── primary.pub
    │       └── rotation-2026-06-01.pub
    ├── ash/
    │   ├── identity.txt
    │   └── recipients/primary.pub
    └── <actor_id>/...
```

**Convention:** if `actors/<actor_id>/` exists with valid identity + recipients, that actor gets per-actor isolation. If not, the actor falls back to the shared `identity.txt` (13.x.6 behavior preserved).

### 4.2 New module `phoenix/ledger/encryption_actors.py`

Per-actor encryptor registry. Pattern adapts the existing `phoenix/ledger/encryption.py` global-registry pattern to be keyed by actor_id.

```python
def get_prompt_encryptor_for_actor(actor_id: str) -> PromptEncryptor:
    """Returns the encryptor for this actor.

    Resolution order:
    1. Per-actor encryptor registered via set_prompt_encryptor_for_actor(actor_id, ...) — highest precedence.
    2. Per-actor encryptor loaded from disk at actors/<actor_id>/ via encryptor_from_actor_default_layout(actor_id).
    3. Fall back to the daemon-level get_prompt_encryptor() (13.x.6 shared key) — preserves back-compat.
    """

def set_prompt_encryptor_for_actor(actor_id: str, encryptor: PromptEncryptor) -> None: ...

def reset_prompt_encryptor_for_actor(actor_id: str | None = None) -> None: ...
    # actor_id=None → reset all per-actor encryptors.

def encryptor_from_actor_default_layout(actor_id: str) -> AgePromptEncryptor:
    """Build an AgePromptEncryptor from actors/<actor_id>/ — same shape as
    encryptor_from_default_layout() but rooted at the actor's subdir."""

def list_actors_with_keys() -> list[str]:
    """Return actor_ids that have a valid keys directory on disk.
    Used by the admin enumeration endpoint."""
```

### 4.3 Ledger schema extension

The `ledger_entries` table (per Phase 13 Step 8 migration) gains one new column:

```sql
ALTER TABLE ledger_entries
  ADD COLUMN prompt_encryption_actor_id TEXT NULL;
```

Semantics:
- `NULL` → encrypted with the daemon-level shared key (13.x.6 row; replay uses `get_prompt_encryptor()`).
- `<actor_id>` → encrypted with that actor's key (13.x.8 row; replay uses `get_prompt_encryptor_for_actor(<actor_id>)`).

Migration: `phoenix/state/migrations/phase13x8_per_actor_keys.py` — additive column with `NULL` default. No data backfill; existing rows stay at NULL (shared key) until the deferred batch-migration ships.

### 4.4 Encrypt/decrypt write-side changes

The ledger writer that records ENCRYPTED_OPT_IN entries needs to:

1. Resolve the actor for the request (already available in the auth context).
2. Call `get_prompt_encryptor_for_actor(actor.id)`.
3. Write the resulting ciphertext to `prompt_encrypted`.
4. Write `actor.id` to the new `prompt_encryption_actor_id` column.

The replay path (`phoenix/ledger/cognition_replay.py::_replay_encrypted`) needs to:

1. Read the `prompt_encryption_actor_id` from the entry.
2. If NULL: use existing `get_prompt_encryptor()` (back-compat).
3. If not NULL: use `get_prompt_encryptor_for_actor(<that_actor_id>)`.

### 4.5 CLI + admin endpoint extensions

**CLI:** `phoenix admin generate-encryption-key --actor <id>` adds the `--actor` flag. When set, the keypair goes under `actors/<id>/...` instead of the shared layout. Without `--actor`, behavior is unchanged (shared key).

**Admin endpoint:** `POST /v1/admin/encryption/rotate-key` body gains an optional `actor_id` field. When set, the new keypair goes under `actors/<actor_id>/recipients/...`. Permission gate `can_rotate_encryption_key` still required (the rotate-key admin action is privileged regardless of which actor's keys are involved).

New optional **enumeration** endpoint: `GET /v1/admin/encryption/actors` — returns list of actor_ids with per-actor key directories. Useful for ops auditing "who has per-actor isolation configured?"

### 4.6 New permission flag (optional — see [OPEN: self-serve-rotation])

- `[OPEN: self-serve-rotation]` — Should an actor be able to rotate their OWN keys via a self-serve endpoint? Current proposal: NO. Rotation stays admin-gated via the existing `can_rotate_encryption_key`. This is conservative; actors don't usually self-rotate identity-bearing keys.

## 5 — Decision flow

### 5.1 Encrypt path (write)

```
Actor submits task with prompt_disposition=ENCRYPTED_OPT_IN
  ↓
Cognition writer extracts actor.id from auth context
  ↓
encryptor = get_prompt_encryptor_for_actor(actor.id)
  ↓
ciphertext = encryptor.encrypt(canonical_prompt_form)
  ↓
ledger_row.prompt_encrypted = ciphertext
ledger_row.prompt_encryption_actor_id = actor.id  (NULL if encryptor == global fallback)
ledger_row.prompt_disposition = "ENCRYPTED_OPT_IN"
```

### 5.2 Decrypt path (replay)

```
replay_cognition_entry(entry_id)
  ↓
Read entry; if disposition=ENCRYPTED_OPT_IN:
  ↓
actor_id = entry.prompt_encryption_actor_id  (may be NULL)
  ↓
if actor_id is None:
    encryptor = get_prompt_encryptor()  (13.x.6 shared key)
else:
    encryptor = get_prompt_encryptor_for_actor(actor_id)
  ↓
plaintext = encryptor.decrypt(entry.prompt_encrypted)
  ↓
proceed with replay
```

### 5.3 Per-actor encryptor resolution

```python
_per_actor_encryptors: dict[str, PromptEncryptor] = {}

def get_prompt_encryptor_for_actor(actor_id: str) -> PromptEncryptor:
    # 1. Explicit set at startup (highest priority).
    if actor_id in _per_actor_encryptors:
        return _per_actor_encryptors[actor_id]

    # 2. Disk layout: try to load actors/<actor_id>/.
    try:
        encryptor = encryptor_from_actor_default_layout(actor_id)
        _per_actor_encryptors[actor_id] = encryptor  # cache for subsequent calls
        return encryptor
    except AgeKeyLoadError:
        pass  # no per-actor keys; fall through.

    # 3. Fall back to daemon-level shared encryptor.
    return get_prompt_encryptor()
```

The disk-load → cache pattern means the first encrypt call per actor pays the disk-load cost; subsequent calls are O(1).

## 6 — Open tensions

- **[OPEN: actor-bootstrap]** — When a new actor is enrolled (Phase 13 `POST /v1/identity/enroll`), should the daemon auto-generate per-actor keys? Two options:
  - (a) **Auto-generate on enroll:** every actor gets per-actor keys by default. Forces the discipline. Operational burden: per-actor key files multiply.
  - (b) **Opt-in via admin command:** ops explicitly runs `phoenix admin generate-encryption-key --actor <id>` for each actor that needs isolation. New actors default to shared key.
  Recommendation: (b) — explicit opt-in matches the spirit of `ENCRYPTED_OPT_IN` itself (the disposition is per-request, not per-actor; per-actor isolation should be per-actor opt-in too).

- **[OPEN: self-serve-rotation]** — See §4.6. Recommendation: NO self-serve rotation in 13.x.8; stay admin-gated.

- **[OPEN: cache-invalidation]** — The `_per_actor_encryptors` cache loads at first use. If ops rotates an actor's keys via the admin endpoint, the cache holds the stale encryptor until daemon-restart. Two options:
  - (a) Accept daemon-restart requirement (matches 13.x.7's discipline).
  - (b) Add `reset_prompt_encryptor_for_actor(actor_id)` call inside the rotate-key handler so the cache evicts that one entry.
  Recommendation: (b) — small operational improvement, no architectural cost.

- **[OPEN: migration-direction]** — 13.x.8 SHIPS the actor_id column but does NOT migrate existing rows. Three reasonable behaviors when an actor with per-actor keys writes a NEW ENCRYPTED_OPT_IN row:
  - (a) The new row uses per-actor keys (`actor_id` populated); old rows keep `NULL` (shared key). Mixed-state ledger forever until batch-migration ships.
  - (b) On first per-actor write, daemon batch-migrates that actor's old rows. Expensive at runtime; surprising side effect.
  - (c) Admin command to migrate-on-demand.
  Recommendation: (a) — simplest semantics; mixed state is OK as long as replay handles both branches (which §5.2 already does).

- **[OPEN: keys-on-disk-permissions]** — `actors/<actor_id>/` directory permissions: should the directory itself be `0o700` (only daemon user can list)? Currently the spec defers to the encryption_age.py per-file `0o600` discipline. Adding directory-level `0o700` is a small hardening win.
  Recommendation: YES — set `0o700` on the actor subdirectory. Matches `ssh_agent` / `.ssh/` discipline.

## 7 — Acceptance criteria

The 13.x.8 implementation is complete when:

1. `phoenix/ledger/encryption_actors.py` ships the per-actor registry + disk-load helpers.
2. `phoenix/state/migrations/phase13x8_per_actor_keys.py` adds the `prompt_encryption_actor_id` column.
3. The ledger writer populates the new column on every ENCRYPTED_OPT_IN write.
4. The cognition replay engine consults the column to pick the correct encryptor.
5. CLI gains `--actor` flag; admin endpoint gains optional `actor_id` field.
6. `GET /v1/admin/encryption/actors` enumeration endpoint ships.
7. The rotate-key endpoint invalidates the per-actor cache on success (per [OPEN: cache-invalidation] recommendation).
8. Tests: registry resolution (per-actor / fallback / cached), disk loader, encrypt-with-actor / decrypt-with-actor end-to-end, CLI with-actor flag, endpoint with actor_id, enumeration endpoint, ledger writer populating the column.
9. CHANGELOG entry under `[1.1.0.dev0]` documenting the column addition, the back-compat guarantee, and the deferred batch-migration.
10. mypy --strict clean.

## 8 — File-level summary

**New files:**
- `phoenix/ledger/encryption_actors.py` (~150 lines: registry + 5 functions)
- `phoenix/state/migrations/phase13x8_per_actor_keys.py` (~50 lines: column-add migration)
- `phoenix/admin/encryption_actors_admin.py` (~80 lines: enumeration endpoint + actor_id-aware rotate-key wrapper) — OR extension of `encryption_admin.py`; design call during implementation
- `tests/cognition/test_encryption_actors.py` (~200 lines: registry + disk loader tests)
- `tests/integration/test_admin_encryption_actors.py` (~150 lines: endpoint tests)
- `tests/cli/test_admin_generate_encryption_key_actor.py` (~80 lines: CLI --actor tests)

**Modified files:**
- `phoenix/cli/commands/admin.py` — add `--actor` arg + route through per-actor layout
- `phoenix/admin/encryption_admin.py` — accept optional `actor_id` in rotate-key body + cache invalidation
- `phoenix/ledger/` — the ledger writer (whichever module writes ENCRYPTED_OPT_IN rows) populates the new column
- `phoenix/ledger/cognition_replay.py` — _replay_encrypted reads the new column
- `phoenix/ledger/README.md` — ceremony docs add per-actor section
- `CHANGELOG.md` — new entry

**Total new tests:** ~25-30.

## 9 — Risks and mitigations

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Back-compat break: existing rows (actor_id=NULL) become undecryptable | Low | Replay explicitly handles NULL → shared encryptor (§5.2). Tested. |
| Cache holds stale encryptor after rotation | Medium | Rotate-key endpoint invalidates the cached entry (per [OPEN: cache-invalidation] recommendation b). |
| Per-actor key directory accidentally world-readable | Medium | Directory `0o700` on creation (per [OPEN] recommendation). |
| Confusion between "actor's ENCRYPTED row" vs "shared-key ENCRYPTED row" in ledger queries | Low | Add `prompt_encryption_actor_id` to the audit-log filter shape so ops can query per-actor encrypted rows directly. |
| Performance regression: disk load on every first-use per actor | Low | Cache hit after first call. Disk load is small (one identity file + one or two recipient files). |
| Actor enumeration leaks actor IDs to admins | Low (design-correct) | Enumeration endpoint is admin-only (`can_use_admin_endpoints`); actor IDs are operational identifiers, not secrets. |

## 10 — Scope summary

This is a **medium-large** scope sub-improvement. Comparable to 13.x.4 (classifier integration) in size but with more cross-cutting touchpoints (ledger writer + replay reader + migration + CLI + endpoint). Realistic implementation budget: ~3-4 hours including subagent-driven test/review cycles. Not recommended as same-session work alongside 13.5 (drift detector design).

Two-step follow-up after 13.x.8 ships:
- **Deferred batch-migration** of existing shared-key rows to per-actor (a separate slot; same scope class as 13.x.7's deferred batch-rotate).
- **Cross-actor delegated decrypt** for compliance audits (v1.2.x).
