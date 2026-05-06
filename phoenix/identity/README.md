# phoenix/identity

## Purpose
**Per-install Ed25519 identity + org enrollment storage** per architecture v1 Section 1 Decisions 10–12 and Section 7.6. Each Phoenix deployment generates its own Ed25519 keypair on first run, stored via OS-native protection (DPAPI on Windows, Keychain on macOS, libsecret on Linux). Org enrollment is opt-in: a deployment derives a per-install subkey from the org root via HKDF, so revoking one compromised install revokes its subkey, not the whole org. Mirrors dr-frank-and-eddy v6.6 unchanged.

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 1 Decisions 10 (per-install Ed25519), 11 (org enrollment opt-in with HKDF subkeys), 12 (vendored Actor pattern), Section 7.2 (Actor pattern + threat model), Section 7.6 (org enrollment ceremony).

## Key files and their roles
| File | Role |
|---|---|
| `__init__.py` | Empty Phase 0 stub. |
| `keystore.py` | (Phase 6) DPAPI / Keychain / libsecret abstractions. Phoenix calls one `KeyStore.put(name, secret)` interface per platform. |
| `bootstrap.py` | (Phase 6) First-run keypair generation + storage. |
| `org.py` | (Phase 6) Org subkey derivation (HKDF) + storage; bootstrap-token verification; revocation handling. |

## Vendored substrate
The signing/verification logic lives in `vendor/actor/` (vendored from `evolution/knowledge/actor.py` v6.6). `phoenix/identity/` provides the OS-keystore wrappers and the org-derivation flow.

## Common failure modes
- `KeystoreUnavailable` — DPAPI/Keychain/libsecret could not be reached. Phoenix refuses to start without identity; fail-closed.
- `BootstrapTokenInvalid` — org enrollment rejected the token (signature, expiry, or nonce replay).
- `IdentityCorrupt` — stored keypair fails self-test on read; rotation flow required.

## Troubleshooting
- Bootstrap token replay protection: each token is single-use; observed-once tokens can't enroll a second install even if intercepted.
- The stored Ed25519 master key is per-OS-user. A malicious local process running as the same OS user can read it. This is the honest threat-model limitation named in Section 7.2; OS-keychain attestation for `frontier_physics` signing is deferred to v1.x per Section 11.4.1.
- Revocation: `phoenix identity revoke-self` on the install side; `POST /v1/identity/revoke` from an admin actor for org-level revocation.

## Tests
- `tests/unit/test_smoke.py` — asserts `phoenix.identity` imports.
- `evals/audit/` (Phase 7+) — enrollment + revocation events land in the audit log + ledger.

## Recent changes
- 2026-05-06 — Phase 0: module created as empty stub.
