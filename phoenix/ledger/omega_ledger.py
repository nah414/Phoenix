"""Phoenix Omega Ledger adapter (Phase 7 Step 4 + Step 6 refactor).

Wraps the vendored hashchain primitives (``_compute_entry_hash`` +
``GENESIS_HASH`` from :mod:`vendor.omega.ledger`) with a Phoenix-shaped
API:

- :meth:`OmegaLedger.append_entry` takes a typed :class:`LedgerEntry`,
  serializes its payload with canonical JSON, computes
  ``SHA-256(parent_hash | entry_kind | payload_json)``, persists via
  :meth:`StateBackend.append_ledger_entry`, and returns a typed
  :class:`LedgerLink`.
- :meth:`OmegaLedger.read_entry` looks up a single entry by ID.
- :meth:`OmegaLedger.verify_chain` walks the chain in Python and
  recomputes each row's SHA-256, returning a typed
  :class:`ChainVerificationReport`. Slower than
  :meth:`StateBackend.verify_ledger_integrity` (which uses SQL window
  functions for the structural check) but catches cryptographic
  tampering of ``payload_json`` that the SQL check can't see.

**Step 6 refactor:** in Step 4 the adapter wrapped the vendored
:class:`vendor.omega.ledger.OmegaLedger` class with its own SQLite
file. Step 6 redirects persistence to the
:class:`~phoenix.state.StateBackend` introduced in Step 5 -- the
``ledger_entries`` table is now the single canonical home for the
chain. This means Postgres-backed installs get replicated ledger
storage for free, and Phoenix avoids the dual-write coherence
problem (which entry_id wins when two stores diverge?).

The vendored module's hashchain primitives are still used verbatim
(per the locked OPEN-3 Option B vendoring); only the SQLite glue
was a Step 4 scaffolding choice that Step 6 supersedes.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

import phoenix  # noqa: F401  -- triggers sys.path injection for vendored modules
from omega.ledger import GENESIS_HASH, _compute_entry_hash

from phoenix.ledger.entry_types import LedgerEntry

if TYPE_CHECKING:
    from phoenix.state.backend_protocol import StateBackend

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Typed adapter results.


@dataclass(frozen=True)
class LedgerLink:
    """The hashchain link returned from :meth:`OmegaLedger.append_entry`.

    Fields:

    - ``entry_id`` -- echo of the input entry's ID (UUID4 minted by the
      caller in :func:`solve_to_ledger_entry` / similar factories).
    - ``parent_hash`` -- previous entry's ``entry_hash`` at the moment
      of append; ``"GENESIS"`` for the first entry.
    - ``entry_hash`` -- ``SHA-256(parent_hash | entry_kind | payload_json)``
      computed via the vendored
      :func:`vendor.omega.ledger._compute_entry_hash` primitive.
    - ``timestamp_unix`` -- when the entry was sealed (server clock).

    This is the value callers (the verification gate's post-solve
    composer, the safety gate's override path, the kill-switch
    transition logger, the enrollment endpoint) record back into
    their own provenance so request-side audit logs can correlate
    with the ledger.
    """

    entry_id: str
    parent_hash: str
    entry_hash: str
    timestamp_unix: float


@dataclass(frozen=True)
class ChainVerificationReport:
    """Typed result from :meth:`OmegaLedger.verify_chain`.

    Fields:

    - ``valid`` -- True if the full walk succeeded.
    - ``entries_checked`` -- how many rows the walk visited before
      either reaching the end (``valid=True``) or hitting a broken
      link (``valid=False``).
    - ``first_broken_entry_id`` -- on failure, the entry_id of the
      first tampered/corrupted link. None on success.
    - ``reason`` -- on failure, a human-readable explanation
      (truncated-hex hash comparison). None on success.
    """

    valid: bool
    entries_checked: int
    first_broken_entry_id: str | None
    reason: str | None


# Upper bound for the chain walk in a single Python pass. Phoenix v1 is
# single-install; even years of solves stay well under this. v1.x can
# extend StateBackend with a streaming cursor for unbounded chains.
_CHAIN_WALK_HARD_LIMIT = 1_000_000


# ---------------------------------------------------------------------------
# The adapter class itself.


class OmegaLedger:
    """Phoenix-shaped wrapper over the vendored hashchain primitives.

    Constructed via :func:`get_ledger` (singleton) or directly with
    an explicit ``state_backend`` (tests + ops one-shot tooling).

    Thread-safety: an internal :class:`threading.Lock` serializes
    ``append_entry`` so concurrent solver threads don't race the
    read-prev-hash-compute-insert sequence. The StateBackend's own
    locking layered underneath handles the SQL transaction.
    """

    def __init__(self, *, state_backend: StateBackend | None = None) -> None:
        """Construct the adapter.

        ``state_backend`` is the durable persistence layer. When
        ``None``, the singleton from
        :func:`phoenix.state.get_state_backend` is used (the standard
        production path). Tests inject their own throwaway backend
        (typically :class:`SQLiteStateBackend` against a tmp directory).
        """
        self._explicit_backend: StateBackend | None = state_backend
        self._lock = threading.Lock()
        # Cache of the most recent entry_hash. None means "not yet read
        # from the backend"; on first append we initialize from the
        # current backend state. Subsequent appends update in place.
        self._last_hash_cache: str | None = None

    def _backend(self) -> StateBackend:
        """Resolve the StateBackend lazily.

        Lazy resolution lets test fixtures construct an
        :class:`OmegaLedger` *before* the production state backend
        singleton is set up.
        """
        if self._explicit_backend is not None:
            return self._explicit_backend
        from phoenix.state import get_state_backend

        return get_state_backend()

    def _initialize_last_hash(self) -> None:
        """Load the most recent entry's hash from the backend.

        Called once per :class:`OmegaLedger` instance on first append.
        After this the cache is updated in place on every append, so
        subsequent appends don't re-query the backend.

        For empty chains, the cache is set to :data:`GENESIS_HASH` --
        the same anchor the vendored module uses for its first entry.
        """
        rows = self._backend().list_ledger_entries(
            since_unix=0.0,
            limit=_CHAIN_WALK_HARD_LIMIT,
        )
        if not rows:
            self._last_hash_cache = GENESIS_HASH
        else:
            # list_ledger_entries returns rows in ASC timestamp order;
            # the last row is the most recent.
            self._last_hash_cache = str(rows[-1]["entry_hash"])

    def append_entry(self, entry: LedgerEntry) -> LedgerLink:
        """Append a :class:`LedgerEntry` to the chain.

        Computes ``SHA-256(parent_hash | entry_kind | payload_json)``
        where ``payload_json`` is
        :meth:`LedgerEntry.to_canonical_payload_json` (deterministic
        encoding with ``sort_keys=True``).

        Persists to the configured :class:`StateBackend` and returns
        the typed :class:`LedgerLink`. The input ``entry`` is unchanged
        (frozen dataclass); callers wanting the full updated record
        should call :meth:`read_entry` with the returned ``entry_id``.

        The ``entry.entry_id`` field is preserved verbatim into the
        StateBackend's row -- Phoenix mints UUIDs callers can correlate
        with their request_id / task_id without going through the
        ledger.
        """
        with self._lock:
            if self._last_hash_cache is None:
                self._initialize_last_hash()
            parent_hash = self._last_hash_cache
            assert parent_hash is not None  # _initialize_last_hash sets it

            payload_json = entry.to_canonical_payload_json()
            entry_hash = _compute_entry_hash(parent_hash, entry.entry_kind, payload_json)

            self._backend().append_ledger_entry(
                {
                    "entry_id": entry.entry_id,
                    "entry_kind": entry.entry_kind,
                    "timestamp_unix": entry.timestamp_unix,
                    "actor_id": entry.actor_id,
                    "parent_hash": parent_hash,
                    "entry_hash": entry_hash,
                    "payload_json": payload_json,
                }
            )

            link = LedgerLink(
                entry_id=entry.entry_id,
                parent_hash=parent_hash,
                entry_hash=entry_hash,
                timestamp_unix=entry.timestamp_unix,
            )
            self._last_hash_cache = entry_hash
            return link

    def read_entry(self, entry_id: str) -> LedgerEntry | None:
        """Read a single ledger entry by ID; return None if not found.

        Phase 7 v1: implemented as a linear scan over
        :meth:`StateBackend.list_ledger_entries`. The chain is small
        enough (~thousands of solves per day) that this is acceptable.
        v1.x adds a primary-key lookup method to the Protocol when the
        chain grows past the linear-scan budget.
        """
        rows = self._backend().list_ledger_entries(
            since_unix=0.0,
            limit=_CHAIN_WALK_HARD_LIMIT,
        )
        for row in rows:
            if row["entry_id"] == entry_id:
                return LedgerEntry(
                    entry_id=str(row["entry_id"]),
                    entry_kind=str(row["entry_kind"]),
                    timestamp_unix=float(row["timestamp_unix"]),
                    actor_id=str(row["actor_id"]),
                    parent_hash=str(row["parent_hash"]),
                    entry_hash=str(row["entry_hash"]),
                    payload=json.loads(row["payload_json"]),
                )
        return None

    def verify_chain(self, *, limit: int | None = None) -> ChainVerificationReport:
        """Walk the hashchain and return a typed verification report.

        Recomputes ``SHA-256`` for each entry and compares against the
        stored ``entry_hash``. Catches both structural breaks
        (parent_hash chain rewritten) AND cryptographic tampering
        (``payload_json`` modified after the fact). The latter is what
        :meth:`StateBackend.verify_ledger_integrity` can't catch with
        SQL alone -- both layers complement each other.

        The ``limit`` kwarg caps how many rows the walk visits;
        ``None`` (default) walks all rows.
        """
        rows = self._backend().list_ledger_entries(
            since_unix=0.0,
            limit=limit if limit is not None else _CHAIN_WALK_HARD_LIMIT,
        )
        if not rows:
            return ChainVerificationReport(
                valid=True,
                entries_checked=0,
                first_broken_entry_id=None,
                reason=None,
            )

        expected_prev = GENESIS_HASH
        for idx, row in enumerate(rows):
            stored_parent = str(row["parent_hash"])
            stored_hash = str(row["entry_hash"])
            entry_kind = str(row["entry_kind"])
            payload_json = str(row["payload_json"])

            if stored_parent != expected_prev:
                return ChainVerificationReport(
                    valid=False,
                    entries_checked=idx,
                    first_broken_entry_id=str(row["entry_id"]),
                    reason=(
                        f"parent_hash mismatch: expected {expected_prev[:16]}... "
                        f"got {stored_parent[:16]}..."
                    ),
                )

            recomputed = _compute_entry_hash(stored_parent, entry_kind, payload_json)
            if recomputed != stored_hash:
                return ChainVerificationReport(
                    valid=False,
                    entries_checked=idx,
                    first_broken_entry_id=str(row["entry_id"]),
                    reason=(
                        f"entry_hash mismatch: expected {recomputed[:16]}... "
                        f"got {stored_hash[:16]}..."
                    ),
                )

            expected_prev = stored_hash

        return ChainVerificationReport(
            valid=True,
            entries_checked=len(rows),
            first_broken_entry_id=None,
            reason=None,
        )

    def count(self) -> int:
        """Total number of sealed entries currently in the ledger."""
        return len(
            self._backend().list_ledger_entries(
                since_unix=0.0,
                limit=_CHAIN_WALK_HARD_LIMIT,
            )
        )

    def close(self) -> None:
        """No-op: the StateBackend owns its connection lifecycle.

        Kept for API compatibility with Step 4's interface; the
        backend's :meth:`StateBackend.close` is called via
        :func:`reset_state_backend` at daemon shutdown.
        """
        return


# ---------------------------------------------------------------------------
# Singleton + reset (matches Phase 6b factory pattern).

_LEDGER: OmegaLedger | None = None
_LEDGER_LOCK = threading.Lock()


def get_ledger() -> OmegaLedger:
    """Return the singleton :class:`OmegaLedger`.

    Constructed lazily on first call against the default
    :class:`StateBackend` singleton (``phoenix.state.get_state_backend``).
    """
    global _LEDGER
    with _LEDGER_LOCK:
        if _LEDGER is None:
            _LEDGER = OmegaLedger()
        return _LEDGER


def reset_ledger() -> None:
    """Close and clear the singleton. Tests + FastAPI lifespan
    shutdown use this. Idempotent.
    """
    global _LEDGER
    with _LEDGER_LOCK:
        if _LEDGER is not None:
            try:
                _LEDGER.close()
            except Exception:
                logger.exception("OmegaLedger close failed on reset")
            _LEDGER = None


__all__ = [
    "ChainVerificationReport",
    "LedgerLink",
    "OmegaLedger",
    "get_ledger",
    "reset_ledger",
]
