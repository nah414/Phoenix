"""Phoenix hashchained provenance store (Phase 7 Step 4).

Per architecture v1 Section 1 Decision 15: every Phoenix solve
produces a tamper-evident ledger entry chained to its predecessor
via SHA-256. The replay subsystem (Phase 7 Step 7) reconstructs any
historical solve from its ledger entry.

Phase 7 Step 4 ships:

- Typed entry-kind dataclasses (:class:`SolveEntry`,
  :class:`OverrideByOperatorEntry`, :class:`KillSwitchEntry`,
  :class:`EnrollmentEntry`) plus the common :class:`LedgerEntry`
  on-chain shape.
- :class:`OmegaLedger` -- a thin Phoenix adapter over the slim
  vendored hashchain primitive (:mod:`vendor.omega.ledger`).
- Singleton lifecycle via :func:`get_ledger` /
  :func:`reset_ledger`, matching the Phase 6b factory pattern.

Steps 5 + 6 land state-backend persistence + verification-gate
composition; Step 7 lands the replay engine.
"""

from phoenix.ledger.entry_types import (
    BudgetOverrideEntry,
    EnrollmentEntry,
    KillSwitchEntry,
    LedgerEntry,
    OverrideByOperatorEntry,
    PermissionGrantEntry,
    SolveEntry,
    budget_override_to_ledger_entry,
    enrollment_to_ledger_entry,
    kill_switch_to_ledger_entry,
    override_to_ledger_entry,
    permission_grant_to_ledger_entry,
    solve_to_ledger_entry,
)
from phoenix.ledger.omega_ledger import (
    ChainVerificationReport,
    LedgerLink,
    OmegaLedger,
    get_ledger,
    reset_ledger,
)
from phoenix.ledger.replay_engine import (
    LedgerEntryNotFound,
    ReplayDivergence,
    ReplayEntryIncomplete,
    ReplayError,
    ReplayProviderUnavailable,
    ReplayReport,
    replay,
)
from phoenix.ledger.solve_composer import compose_and_append_solve_entry

__all__ = [
    "BudgetOverrideEntry",
    "ChainVerificationReport",
    "EnrollmentEntry",
    "KillSwitchEntry",
    "LedgerEntry",
    "LedgerEntryNotFound",
    "LedgerLink",
    "OmegaLedger",
    "OverrideByOperatorEntry",
    "PermissionGrantEntry",
    "ReplayDivergence",
    "ReplayEntryIncomplete",
    "ReplayError",
    "ReplayProviderUnavailable",
    "ReplayReport",
    "SolveEntry",
    "budget_override_to_ledger_entry",
    "compose_and_append_solve_entry",
    "enrollment_to_ledger_entry",
    "get_ledger",
    "kill_switch_to_ledger_entry",
    "override_to_ledger_entry",
    "permission_grant_to_ledger_entry",
    "replay",
    "reset_ledger",
    "solve_to_ledger_entry",
]
