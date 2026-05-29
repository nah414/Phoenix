# Phase 13.x.7 — Encryption admin CLI + rotate-key endpoint — Design

**Date:** 2026-05-28
**Author:** Adam (with Claude as design partner)
**Status:** DRAFT — awaiting Adam review
**Type:** v1.1 sub-improvement design (Phase 13.x track)

**Architectural reference:**
- `phoenix/ledger/encryption_age.py` (Phase 13.x.6, PR #21) — `AgePromptEncryptor` + `default_keys_dir()` + `encryptor_from_default_layout()`
- `phoenix/ledger/README.md` lines 60-138 (the encryption ceremony docs that explicitly call out 13.x.7's planned scope)
- Phase 13 build guide §0 design decision 13-D2 (privacy posture)

**Companion work shipped before this:**
- PR #21 (`cce7c26`) — `AgePromptEncryptor` + `default_keys_dir` + multi-recipient encryption primitive
- PR #22 (`6d2c043`) — Phase 13.x.4 classifier integration (independent, but proves the v1.1 sub-improvement workflow)

---

## 1 — Context

PR #21 shipped the age-based encryption primitive (`AgePromptEncryptor`) but left two ergonomic gaps that ops must currently fill manually:

1. **Key generation** — to enable `ENCRYPTED_OPT_IN` prompt disposition, ops must run `age-keygen` directly + `chmod 0600` the identity file + place files at `~/.phoenix/runtime/encryption_keys/`. The error message at `encryption_age.py:627` already references the planned `phoenix admin generate-encryption-key` CLI as the convenience replacement.

2. **Key rotation** — the existing `encryptor_from_default_layout()` supports multi-recipient encryption (lossless rotation primitive), but ops must manually generate the second keypair + drop it in the recipients directory + restart the daemon. There's no `POST /v1/admin/encryption/rotate-key` admin endpoint that does this in-process.

Phase 13.x.7 closes both gaps with a single shared key-generation primitive (`phoenix/ledger/keygen.py`) wrapped by a CLI subcommand and an admin endpoint.

## 2 — Goal

Add the convenience layer that lets ops generate + rotate encryption keys without leaving the Phoenix surface:

- **CLI:** `phoenix admin generate-encryption-key [--name <slug>] [--force]` — writes the identity + recipient files to `default_keys_dir()` with the right permissions.
- **Admin endpoint:** `POST /v1/admin/encryption/rotate-key` — generates a new keypair, drops the new recipient pub file into `recipients/`, returns the new recipient fingerprint. Daemon-restart is still required to pick up the new keys (matches the existing ceremony's restart-driven discipline).

## 3 — Out of scope (deferred to follow-up)

- **Batch decrypt-and-re-encrypt of existing ENCRYPTED_OPT_IN ledger rows.** The 13.x.6 README ceremony doc mentions this as part of rotation, but it's a substantially different concern (database transactions, progress tracking, partial-failure recovery, decrypt-with-old-key dependency). Will become its own follow-up slot (provisional name: `13.x.7-followup` or absorbed into 13.x.8 if per-actor isolation requires it). The current rotate-key endpoint produces a state where BOTH old and new recipients can decrypt new encrypts, and old encrypts still decryptable with the old identity — the rotation is **complete** for forward-going traffic; only legacy-data cleanup is deferred.
- **Identity revocation / key deletion.** After batch-rotate ships, the old-key cleanup story lands then. For 13.x.7: the rotate-key endpoint adds; it does not remove.
- **Cloud-KMS plugins.** `AwsKmsPromptEncryptor` / `GcpKmsPromptEncryptor` / `VaultPromptEncryptor` are v1.2.x per PR #21 CHANGELOG.
- **Per-actor key isolation.** That's Phase 13.x.8.

## 4 — API surface

### 4.1 New module `phoenix/ledger/keygen.py`

Single key-generation primitive. Used by both CLI and admin endpoint.

```python
@dataclass(frozen=True)
class GeneratedKeyPair:
    """Output of a single keygen invocation."""

    identity_path: Path        # Where the X25519 secret was written.
    recipient_path: Path       # Where the public key was written.
    identity_fingerprint: str  # 16-hex SHA-256 prefix of pub key text.
    recipient_fingerprint: str # Same value; surfaced separately for clarity.


def generate_age_keypair(
    *,
    keys_dir: Path | None = None,
    name: str = "primary",
    force: bool = False,
) -> GeneratedKeyPair: ...
```

Behavior:
- If `keys_dir is None` → use `default_keys_dir()`.
- Creates `keys_dir/recipients/` if missing.
- Writes identity to `keys_dir/identity.txt` if `name == "primary"`, else to `keys_dir/identity-<name>.txt`. (Default convention: `primary` writes `identity.txt`; named keys write to a suffixed file so the convenience constructor still finds the canonical identity.)
- Writes recipient to `keys_dir/recipients/<name>.pub`.
- Sets mode `0o600` on the identity file (POSIX); WARN-only on Windows (matches `AgePromptEncryptor._check_key_file_permissions` behavior).
- Refuses to overwrite existing files unless `force=True`; raises `KeyGenError("identity file already exists at <path>; use force=True to overwrite")`.

Uses `pyrage.x25519.Identity.generate()` for the key material. Lazy import to keep the module loadable without the `[encryption-age]` extra (same pattern as `encryption_age.py`).

### 4.2 New typed errors

```python
class KeyGenError(Exception):
    """Base for keygen failures."""

class KeyGenPathConflict(KeyGenError):
    """Refused to overwrite an existing key file without force=True."""

class KeyGenWriteError(KeyGenError):
    """Underlying filesystem write failed (permissions, disk full, etc.)."""
```

### 4.3 New CLI subcommand `phoenix admin generate-encryption-key`

Adds to existing `phoenix admin` command group (registered in `phoenix/cli/commands/`):

```
phoenix admin generate-encryption-key [--name SLUG] [--force] [--keys-dir PATH]

Generates an age (X25519) keypair for ENCRYPTED_OPT_IN ledger
encryption and writes the identity + recipient files to the
Phoenix encryption keys directory.

Options:
  --name SLUG       Name suffix for the keypair files. Default: 'primary'.
                    Identity is written to identity.txt (primary) or
                    identity-<slug>.txt (named). Recipient is always
                    recipients/<slug>.pub.
  --force           Overwrite existing files. Default: refuse.
  --keys-dir PATH   Override the default keys directory.
                    Default: $PHOENIX_ENCRYPTION_KEYS_DIR or
                    ~/.phoenix/runtime/encryption_keys/.
```

On success, prints (to stdout):
```
Identity:     <identity_path>      (mode 0o600)
Recipient:    <recipient_path>
Fingerprint:  <identity_fingerprint>

To activate, restart the Phoenix daemon (the encryptor reads keys
at startup via encryptor_from_default_layout()).
```

On `KeyGenPathConflict`: print the conflict message + suggested fix (`--force` or `--name <slug>`) to stderr; exit 1.

### 4.4 New admin endpoint `POST /v1/admin/encryption/rotate-key`

New module `phoenix/admin/encryption_admin.py` registered into the admin router.

**Request body:**
```json
{
  "name": "rotation-2026-05-28",
  "force": false
}
```

Both fields optional. `name` defaults to `f"rotation-{date}"`; `force` defaults to `false`.

**Response (200 OK):**
```json
{
  "identity_path": "/home/phoenix/.phoenix/runtime/encryption_keys/identity-rotation-2026-05-28.txt",
  "recipient_path": "/home/phoenix/.phoenix/runtime/encryption_keys/recipients/rotation-2026-05-28.pub",
  "identity_fingerprint": "a3f5b7d1c2e8f9a0",
  "recipient_fingerprint": "a3f5b7d1c2e8f9a0",
  "next_step": "Restart the Phoenix daemon to pick up the new recipient. Existing ENCRYPTED_OPT_IN data remains decryptable with the prior identity; new encrypts will use both old + new recipients (lossless rotation per the encryption_age.py multi-recipient design)."
}
```

**Error responses:**
- `409 Conflict` — `KeyGenPathConflict`. Includes `existing_path` + suggested `name` slug.
- `500 Internal Server Error` — `KeyGenWriteError`. Reason in body.
- `403 Forbidden` — actor lacks `can_rotate_encryption_key` permission.

**Permissions:** Requires the new `can_rotate_encryption_key` actor permission. Added to the registry via the same pattern as PR #21's permission additions (which extended for the `can_store_prompt_verbatim` and `can_store_prompt_encrypted` flags).

**Audit events:**
- `admin.encryption.rotate.success` — `{actor_id, recipient_fingerprint, recipient_path}` on success.
- `admin.encryption.rotate.failure` — `{actor_id, error_type}` on failure.

### 4.5 Permission registry addition

Add `can_rotate_encryption_key: bool = False` to the actor permission flags. Default deny; admin tier grants it.

Migration: not required if the permissions table allows additional columns at runtime (per Phase 13 Step 9's `phoenix.state.migrations.phase13_prompt_disposition` pattern). If a migration IS required, ship a `phase13x7_rotate_key_permission.py` migration that adds the column with default `false`.

### 4.6 No change to `AgePromptEncryptor`

The encryptor reads keys at construction time. Adding a new recipient file via the endpoint does **not** affect the running daemon. Ops must restart the daemon to pick up the new recipient. The endpoint response makes this explicit in `next_step`.

(Future option: a separate `POST /v1/admin/encryption/reload` endpoint that re-runs `encryptor_from_default_layout()` and swaps the global via `set_prompt_encryptor`. Deferred — daemon-restart matches the existing key-loading discipline and avoids state-swap edge cases mid-request.)

## 5 — Decision flow

### 5.1 CLI invocation

```
$ phoenix admin generate-encryption-key
  ↓
generate_age_keypair(keys_dir=None, name="primary", force=False)
  ↓
  1. resolve effective keys_dir (env override > default)
  2. resolve effective identity_path + recipient_path from name
  3. ensure keys_dir/recipients/ exists
  4. check identity_path + recipient_path don't already exist; raise KeyGenPathConflict if they do (unless force=True)
  5. pyrage.x25519.Identity.generate() → identity + recipient pub
  6. write identity to identity_path; chmod 0o600 (POSIX)
  7. write recipient to recipient_path; chmod 0o644 (POSIX)
  8. compute identity_fingerprint = SHA-256-16-hex(pub_text)
  9. return GeneratedKeyPair(identity_path, recipient_path, identity_fingerprint, ...)
  ↓
print summary to stdout
```

### 5.2 Admin endpoint invocation

```
POST /v1/admin/encryption/rotate-key
  ↓
auth: actor has can_rotate_encryption_key? (403 if no)
  ↓
parse body: {name?, force?}
  ↓
default name: f"rotation-{date.today().isoformat()}"
  ↓
generate_age_keypair(keys_dir=None, name=resolved_name, force=force)
  ↓
catch KeyGenPathConflict → 409
catch KeyGenWriteError → 500
  ↓
audit event: admin.encryption.rotate.success
  ↓
respond 200 with summary + next_step
```

## 6 — Open tensions

- **[OPEN: rotate-load-reload-endpoint]** — Whether to add a separate `POST /v1/admin/encryption/reload` endpoint that re-invokes `encryptor_from_default_layout()` + `set_prompt_encryptor()` mid-process. Today's design requires daemon-restart; reload would enable zero-downtime rotation. Deferred for v1.1.x follow-up; surfaces only when an ops user actually needs it.
- **[OPEN: rotate-key-identity-filename]** — The current design writes the identity to `identity-<name>.txt` for non-primary names. The convenience constructor `encryptor_from_default_layout()` looks for `identity.txt` only, so non-primary identities won't be auto-loaded. That's fine for adding-recipients-only rotation (the rotation case), but means a "promote-to-primary" operation needs explicit ops action (rename + restart). Acceptable for the deferred batch-rotate work to address; not blocking for 13.x.7.

## 7 — Acceptance criteria

The 13.x.7 implementation is complete when:

1. `phoenix/ledger/keygen.py` ships `GeneratedKeyPair` + `generate_age_keypair()` + three typed errors.
2. CLI subcommand `phoenix admin generate-encryption-key` works end-to-end and prints the summary block on success.
3. `POST /v1/admin/encryption/rotate-key` endpoint works end-to-end and returns the documented response on success.
4. `can_rotate_encryption_key` permission added to the actor permissions registry with default deny.
5. Audit events `admin.encryption.rotate.success` / `.failure` fire correctly.
6. Tests: keygen primitive (path-conflict + force + POSIX-permission + Windows-WARN + bad-name-slug), CLI (mocked keygen + happy-path + conflict-path), endpoint (auth-failure + happy-path + 409 + 500 + audit emission).
7. CHANGELOG entry under `## [1.1.0.dev0]` heading.
8. mypy --strict clean on the three touched/created modules.
9. `phoenix/ledger/README.md` updated to reflect the new CLI command (replaces the "until the CLI lands, use age-keygen directly" instruction).

## 8 — File-level summary

**New files:**
- `phoenix/ledger/keygen.py` (~120 lines: GeneratedKeyPair + generate_age_keypair + 3 typed errors)
- `phoenix/admin/encryption_admin.py` (~80 lines: rotate-key endpoint)
- `tests/cognition/test_keygen.py` (~150 lines: primitive tests)
- `tests/cognition/test_encryption_admin_endpoint.py` (~120 lines: endpoint tests)
- `tests/cli/test_admin_generate_encryption_key.py` (~80 lines: CLI tests)
- Possibly: `phoenix/state/migrations/phase13x7_rotate_key_permission.py` (~30 lines, if the permission registry needs a migration)

**Modified files:**
- `phoenix/cli/commands/<existing-admin-command>.py` — add the new subcommand
- `phoenix/safety/permissions.py` (or wherever actor permissions are registered) — add `can_rotate_encryption_key` flag
- `phoenix/admin/__init__.py` (or wherever the admin router is composed) — register the new module's router
- `phoenix/ledger/README.md` — update the setup ceremony to reference the new CLI
- `phoenix/ledger/encryption_age.py:627` — drop the "Phase 13.x.7 CLI" parenthetical (the CLI now exists)
- `CHANGELOG.md` — new entry

**Total new tests:** ~25-30 (keygen primitive ~12; CLI ~8; endpoint ~10).

## 9 — Risks and mitigations

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| `pyrage.x25519.Identity.generate()` API differs from my assumption | Medium | Implementer verifies against the installed pyrage version before writing code; falls back to subprocess `age-keygen` if needed (with optional-dependency check). |
| Concurrent rotate-key calls race on filename | Low | The conflict-with-existing-file check + force flag handles this; multi-actor concurrency is naturally serialized by the filesystem mode. |
| Daemon-restart vs running-daemon confusion | Medium | Response body's `next_step` message + CLI output both explicitly say "restart required." Tests assert the message contents. |
| Windows ACL gap for the new identity file | Low | Inherited from PR #21's encryption_age.py — WARN-only behavior preserved. Ceremony docs already note this. |
| Audit events not threading actor context | Low | Use the existing admin-endpoint actor-injection pattern (other admin endpoints already use it). Add a test that asserts `actor_id` is in the audit event. |
