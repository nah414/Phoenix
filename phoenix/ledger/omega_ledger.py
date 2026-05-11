"""Phoenix Omega Ledger adapter (Phase 7 Step 4).

Wraps the slim vendored hashchain primitive at
:mod:`vendor.omega.ledger` with a Phoenix-shaped API:

- :meth:`OmegaLedger.append_entry` takes a typed :class:`LedgerEntry`,
  serializes its payload with canonical JSON, calls the vendored
  ``seal``, and returns a :class:`LedgerLink` carrying the freshly-
  set ``parent_hash`` + ``entry_hash``.
- :meth:`OmegaLedger.verify_chain` returns a typed
  :class:`ChainVerificationReport` instead of the vendored dict.
- :meth:`OmegaLedger.read_entry` parses the stored row back into a
  :class:`LedgerEntry` instance.

The adapter does **not** add new persistence behavior beyond what
the vendored module ships -- per the locked OPEN-3 (Option B):
*"vendor the hashchain primitives + thin Phoenix adapter that
handles the delta"*. The "delta" handled here is:

1. Typed-payload (de)serialization via the canonical JSON encoder
   on :class:`LedgerEntry`.
2. Translation of the vendored ``{valid, checked, first_broken,
   reason}`` dict into the typed :class:`ChainVerificationReport`.
3. Phoenix-shaped path defaulting (``~/.phoenix/runtime/ledger.db``
   instead of ``C:/frank-data/omega/events.db``).

The singleton pattern follows Phase 6b's
:func:`phoenix.state.get_state_backend` / :func:`phoenix.audit.get_emitter`:
:func:`get_ledger` lazily constructs on first call, :func:`reset_ledger`
closes + clears for test isolation.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from phoenix.ledger.entry_types import LedgerEntry

# Vendored substrate resolves via the sys.path injection done in
# phoenix/__init__.py. The TYPE_CHECKING guard keeps mypy happy while
# the runtime import happens lazily inside the class (matches Phase 1
# vendor-import discipline).
if TYPE_CHECKING:
    from omega.ledger import OmegaLedger as _VendoredLedger

logger = logging.getLogger(__name__)


# Default ledger DB path per architecture v1 Section 1 Decision 15.
# The audit JSONL writer uses ~/.phoenix/runtime/audit/; the ledger
# uses a SQLite file in the same runtime root for the same
# "Phoenix daemon owns this state" pattern.
_DEFAULT_LEDGER_DIR = Path.home() / ".phoenix" / "runtime"
_DEFAULT_LEDGER_FILENAME = "ledger.db"
PHOENIX_LEDGER_DB_ENV = "PHOENIX_LEDGER_DB"


def _resolve_default_db_path() -> str:
    """Compute the default ledger SQLite path.

    Respects ``$PHOENIX_LEDGER_DB`` if set so tests + dev
    environments can pin to a tmp directory without monkey-patching.
    """
    env_override = os.environ.get(PHOENIX_LEDGER_DB_ENV)
    if env_override:
        return env_override
    return str(_DEFAULT_LEDGER_DIR / _DEFAULT_LEDGER_FILENAME)


# ---------------------------------------------------------------------------
# Typed adapter results.


@dataclass(frozen=True)
class LedgerLink:
    """The hashchain link returned from :meth:`OmegaLedger.append_entry`.

    Fields:

    - ``entry_id`` -- echo of the input entry's ID (UUID4).
    - ``parent_hash`` -- previous entry's ``entry_hash`` at the moment
      of append; ``"GENESIS"`` for the first entry.
    - ``entry_hash`` -- ``SHA-256(parent_hash | entry_kind | payload_json)``
      computed by the vendored module.
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


# ---------------------------------------------------------------------------
# The adapter class itself.


class OmegaLedger:
    """Phoenix-shaped wrapper over the vendored hashchain primitive.

    Constructed via :func:`get_ledger` (singleton) or directly with
    an explicit ``db_path`` (tests + ops one-shot tooling).

    Thread-safety: the underlying vendored module already holds a
    threading lock across read-prev-hash + compute-hash + insert, so
    concurrent appends from multiple solver threads are serialized
    correctly. The adapter adds no further locking.
    """

    def __init__(self, *, db_path: str | None = None) -> None:
        # Lazy import: vendored ``omega.ledger`` resolves via the
        # sys.path injection in phoenix/__init__.py. Importing at
        # module load time would fail on fresh clones before Phase 1
        # vendor sync.
        import phoenix  # noqa: F401  -- triggers sys.path injection

        from omega.ledger import OmegaLedger as _VendoredLedger

        resolved_path = db_path if db_path is not None else _resolve_default_db_path()
        self._db_path = resolved_path
        self._vendored: _VendoredLedger = _VendoredLedger(db_path=resolved_path)
        self._closed = False

    @property
    def db_path(self) -> str:
        """The SQLite file backing this ledger."""
        return self._db_path

    def append_entry(self, entry: LedgerEntry) -> LedgerLink:
        """Append a :class:`LedgerEntry` to the chain.

        The vendored module computes
        ``SHA-256(prev_hash | entry_kind | payload_json)`` where
        ``payload_json`` is :meth:`LedgerEntry.to_canonical_payload_json`
        (deterministic JSON encoding with ``sort_keys=True``).

        Returns the :class:`LedgerLink` with the freshly-set
        ``parent_hash`` + ``entry_hash``. The input ``entry``
        object is unchanged (frozen dataclass); callers wanting the
        full updated record should call :meth:`read_entry` with the
        returned ``entry_id``.

        The vendored ``seal`` uses its own UUID for the row primary
        key, so we pass ``entry.entry_kind`` as the ``fact`` argument
        and pass the canonical payload JSON as ``contract_json``. The
        ``entry.entry_id`` field is preserved by storing it inside
        the canonical payload (Phoenix-side discipline; replay reads
        it back from the payload).
        """
        if self._closed:
            raise RuntimeError("OmegaLedger is closed; construct a new instance.")
        payload_json = entry.to_canonical_payload_json()
        result = self._vendored.seal(
            fact=entry.entry_kind,
            payload_json=payload_json,
            sealed_by=entry.actor_id,
        )
        return LedgerLink(
            entry_id=str(result["entry_id"]),
            parent_hash=str(result["prev_hash"]),
            entry_hash=str(result["entry_hash"]),
            timestamp_unix=float(result["timestamp"]),
        )

    def read_entry(self, entry_id: str) -> LedgerEntry | None:
        """Read a single ledger entry by ID; return None if not found.

        The vendored row carries ``contract_json`` (the canonical
        payload). We parse it back into a dict and rebuild the
        :class:`LedgerEntry` with the stored ``parent_hash`` +
        ``entry_hash`` fields populated.
        """
        import json as _json

        row = self._vendored.get_entry(entry_id)
        if row is None:
            return None
        return LedgerEntry(
            entry_id=str(row["entry_id"]),
            entry_kind=str(row["fact"]),
            timestamp_unix=float(row["timestamp"]),
            actor_id=str(row["sealed_by"]),
            parent_hash=str(row["prev_hash"]),
            entry_hash=str(row["entry_hash"]),
            payload=_json.loads(row["contract_json"]),
        )

    def verify_chain(self, *, limit: int | None = None) -> ChainVerificationReport:
        """Walk the hashchain and return a typed verification report.

        The ``limit`` kwarg caps how many rows the walk visits --
        useful for periodic integrity checks that don't want to
        re-hash the full chain every cycle. ``None`` (default) walks
        all rows.
        """
        raw = self._vendored.verify_chain(limit=limit)
        return ChainVerificationReport(
            valid=bool(raw["valid"]),
            entries_checked=int(raw["checked"]),
            first_broken_entry_id=raw["first_broken"],
            reason=raw.get("reason"),
        )

    def count(self) -> int:
        """Total number of sealed entries currently in the ledger."""
        return int(self._vendored.count())

    def close(self) -> None:
        """Close the underlying SQLite connection. Idempotent."""
        if self._closed:
            return
        self._closed = True
        try:
            self._vendored.close()
        except Exception:
            logger.exception("OmegaLedger close failed")


# ---------------------------------------------------------------------------
# Singleton + reset (matches Phase 6b factory pattern).

_LEDGER: OmegaLedger | None = None
_LEDGER_LOCK = threading.Lock()


def get_ledger() -> OmegaLedger:
    """Return the singleton :class:`OmegaLedger`.

    Constructed lazily on first call. The DB path comes from
    ``$PHOENIX_LEDGER_DB`` if set, otherwise
    ``~/.phoenix/runtime/ledger.db``.
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
    "PHOENIX_LEDGER_DB_ENV",
    "ChainVerificationReport",
    "LedgerLink",
    "OmegaLedger",
    "get_ledger",
    "reset_ledger",
]
