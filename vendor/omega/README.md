# `vendor/omega/`

Vendored Omega Ledger -- the slim hashchain primitive Phoenix's
ledger composer wraps. Ships `ledger.py` with `_compute_entry_hash`
+ `omega_entries`-shaped data model. Phoenix's
`phoenix/ledger/omega_ledger.py` is a thin adapter that translates
the typed `LedgerEntry` dataclass (Section 6.7) into the
hashchain-shaped writes the vendored module expects.

Per Section 11.7.1's vendor-verbatim discipline, kept unchanged
from upstream -- the hash function definition is load-bearing for
bit-exact replay (Phase 11 Step 7), so vendor-side drift here
would manifest as a long-window replay divergence.

Architectural reference: `PHOENIX_ARCHITECTURE_v1.md` Section 1
Decision 15 (every solve produces a hashchained ledger entry),
Section 6.7 (verification provenance composition),
Section 11.7.1 (vendor discipline).
