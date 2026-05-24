# phoenix/ledger

## Purpose
**Hashchained provenance store** — Phoenix's Omega Ledger per architecture v1 Section 1 Decision 15. Every Phoenix solve produces a ledger entry containing: input hash, calibration profile hash, library-version manifest hash, full Trinity Core trace, output hash, prior-entry hash. The chain is append-only and tamper-evident. The replay subsystem reconstructs any historical solve from its ledger entry under three reproducibility modes (`default`, `strict`, `replay`).

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 1 Decisions 15 (hashchained provenance with bit-exact replay), 19–21 (three reproducibility modes; bit-exact for deterministic portion of pipeline; cloud shots recorded once), Section 6.7 (verification provenance composition), Section 8.2 (`/v1/admin/ledger/integrity-report`).

## Key files and their roles
| File | Role |
|---|---|
| `__init__.py` | Empty Phase 0 stub. |
| `omega_ledger.py` | (Phase 7) Vendored Omega Ledger pattern, extended for replay support. SHA-256 hashchain. |
| `entry_types.py` | (Phase 7) Typed ledger-entry shapes: `SOLVE`, `OVERRIDE_BY_OPERATOR`, `KILL_SWITCH_ENGAGED`, `KILL_SWITCH_RELEASED`, `ENROLLMENT`, `REVOCATION`, `PROPOSED_BY_AGENT`. |
| `replay_engine.py` | (Phase 7) Section 1 Decision 19's replay path. Refuses to run if `requirements.lock` doesn't match the ledger entry's recorded versions. |

## Vendored substrate
Vendors the Omega Ledger pattern from dr-frank-and-eddy. Phoenix extends with replay-mode support; the underlying SHA-256 hashchain semantics are preserved.

## Common failure modes
- `ReplayDivergence` — replay re-execution produced a different hash than recorded; pinpoints which `RunRecord` diverged.
- `LedgerCorruption` — hashchain walk detects a broken link; Section 8.2's `/v1/admin/ledger/integrity-report` surfaces the position.
- `ReplayProviderUnavailable` — strict/replay mode requires the original provider, which is degraded.
- `AdapterVersionMismatch` — strict-mode replay sees a different LoRA adapter fingerprint than recorded.

## Troubleshooting
- **Cloud-shot reproducibility limit** (Section 1 Decision 20): cloud-quantum shots are intrinsically nondeterministic and recorded once. Replay reads from the recorded shots rather than re-running on hardware. The Result envelope's `provenance.cloud_shots_recorded=True` flag makes this explicit.
- **Default mode** has no replay guarantee; `strict` adds bit-exact local replay; `replay` mode re-executes and verifies before returning. Strict and replay force single-threaded BLAS and disable some vectorization, costing 15–30% wall-clock vs default.
- Ledger integrity walk: `GET /v1/audit/ledger/verify` (any `can_submit_tasks` actor) or `GET /v1/admin/ledger/integrity-report` (admin, fuller report).

## Tests
- `tests/unit/test_smoke.py` — asserts `phoenix.ledger` imports.
- `evals/ledger/` (Phase 7+) — hashchain stays valid under all operations.
- `evals/replay/` (Phase 7+) — strict and replay modes produce bit-exact match for the deterministic portion. Long-window replay test (Phase 0 acceptance §10.7): 6+ months between original and replay across CI hardware + clean Linux container + clean macOS runner.

## Encryption ceremony (Phase 13.x.6)

Phase 13 Step 8 shipped the `prompt_encrypted` BLOB column +
`PromptEncryptor` Protocol with `NullPromptEncryptor` as the default
(raises `EncryptedDispositionNotConfigured` on every call).
Phase 13.x.6 ships the **age-based reference implementation**
(`AgePromptEncryptor` in `encryption_age.py`) that ops install + wire
to enable real `ENCRYPTED_OPT_IN` prompt-disposition storage.

### Threat model

**Protects against:** offline attackers with filesystem access to
the encrypted blobs (e.g., stolen `ledger_entries.db`); remote
attackers without keys; ciphertext tampering (ChaCha20-Poly1305
AEAD); wrong-key decrypt attempts.

**Does NOT protect against:** in-process attackers with daemon
memory access (prompts are plaintext in memory during the encrypt
op); OS-level keyloggers; identity-file theft from disk; coercion
of the key holder.

For deployments needing tamper-evident **external** audit (regulated
industries where Phoenix's own ledger isn't a sufficient witness),
a v1.2.x `AwsKmsPromptEncryptor` / `VaultPromptEncryptor` plugs into
the same `PromptEncryptor` Protocol via `set_prompt_encryptor()`.
The age impl is the local-default reference; the cloud-KMS impls
are enterprise add-ons.

### Setup (one-time per install)

1. Install the optional extra:

   ```bash
   pip install "phoenix-middleware[encryption-age]"
   ```

2. Generate an age keypair. Until the
   `phoenix admin generate-encryption-key` CLI lands at Phase 13.x.7,
   use the `age-keygen` binary directly:

   ```bash
   mkdir -p ~/.phoenix/runtime/encryption_keys/recipients
   age-keygen -o ~/.phoenix/runtime/encryption_keys/identity.txt
   chmod 0600 ~/.phoenix/runtime/encryption_keys/identity.txt
   age-keygen -y ~/.phoenix/runtime/encryption_keys/identity.txt \
       > ~/.phoenix/runtime/encryption_keys/recipients/primary.pub
   ```

3. Wire it into Phoenix at daemon startup:

   ```python
   from phoenix.ledger.encryption import set_prompt_encryptor
   from phoenix.ledger.encryption_age import encryptor_from_default_layout

   set_prompt_encryptor(encryptor_from_default_layout())
   ```

   The convenience constructor reads `identity.txt` + every `*.pub`
   under `recipients/` from the conventional `default_keys_dir()`
   (override via `$PHOENIX_ENCRYPTION_KEYS_DIR`).

### Key rotation (lossless via multi-recipient encryption)

age supports multi-recipient encryption natively, which gives
lossless key rotation:

1. Generate a second keypair (`identity-v2.txt` + `v2.pub`).
2. Restart the daemon. `encryptor_from_default_layout()` picks up
   the new recipient automatically (globs `recipients/*.pub`). New
   encrypts go to `{primary, v2}`; old encrypts remain decryptable
   with either identity.
3. After the transition window, batch-rotate (admin command lands
   at Phase 13.x.7): decrypt with old identity, re-encrypt to
   `{v2}` only, delete old identity + `primary.pub`.

### Audit events

Every encrypt + decrypt op emits a structured audit event:

- `cognition.prompt.encrypted` — `recipient_fingerprints`,
  `plaintext_bytes`, `ciphertext_bytes`.
- `cognition.prompt.decrypted` — `identity_fingerprint`.
- `cognition.prompt.encrypt.failed` / `cognition.prompt.decrypt.failed`
  — fingerprints + `error_type`. **Phoenix does NOT retry decrypts.**

Fingerprints are 16-hex-char SHA-256 prefixes of the recipient/
identity public-key strings — stable correlators ops can reproduce
with `sha256sum`.

### Safety invariants (enforced by the encryptor)

- **POSIX file-permission check:** identity files MUST have mode
  `0o600`. Phoenix refuses to load looser permissions; the error
  message includes the `chmod 0600 <path>` fix command. No opt-out.
- **Identity contents are never logged.** Audit events carry only
  fingerprints, never key material.
- **Failed decrypts are not retried.** A failed decrypt is
  structural (tampering or wrong key), not transient.

### What's NOT shipped at 13.x.6

- **Phase 13.x.7** — `phoenix admin generate-encryption-key` CLI +
  `POST /v1/admin/encryption/rotate-key` admin endpoint.
- **Phase 13.x.8** — per-actor key isolation.
- **v1.2.x** — `AwsKmsPromptEncryptor` / `GcpKmsPromptEncryptor` /
  `VaultPromptEncryptor` Protocol-conforming plugins.

## Recent changes
- 2026-05-23 — Phase 13.x.6: `AgePromptEncryptor` + ceremony docs.
- 2026-05-06 — Phase 0: module created as empty stub.
