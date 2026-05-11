"""
Dr. Frank & Eddy v6.0.0 -- Omega Ledger (Phoenix vendored slim profile)
Phase 3: Append-only truth store with SHA-256 hashchain.

Every sealed fact carries a JSON payload and is linked to the previous
entry via SHA-256 hash. Tampering with any entry breaks the chain.

Append-only by design: this class has NO update, edit, modify, or delete
methods.

DB: SQLite, configurable path (Phoenix uses ``~/.phoenix/runtime/ledger.db``
by default per architecture v1; DF&E uses ``C:/frank-data/omega/events.db``).

---

**Phoenix vendoring note (Phase 7 Step 4, locked OPEN-3, 2026-05-11):**

This file is the *slim profile* of the upstream
``C:\\frank-data\\omega\\ledger.py`` (2666 lines). The hashchain primitives
were copied verbatim; the DF&E-specific superstructure was dropped per
Adam's Option B choice on 2026-05-11. The dropped surface is:

- ``PhysicsContract`` dependency (DF&E-specific result schema).
- ``sigma_level`` column on ``omega_entries`` (DF&E confidence tier).
- ``query()`` method's sigma_level / model filters (DF&E search shape).
- Evolution Lab tables: ``evolution_epochs``,
  ``evolution_epoch_transitions``, ``evolution_adapters``,
  ``evolution_policy``, ``evolution_schedule_log``, and the four
  associated independent hashchains (LoRA training pipeline).
- MDL snapshot + proposal chains (rule-discovery activity).
- Distillation lifecycle chain (DISTILLATION_SKIPPED / _RETRACTED).
- Capability-announcement chain (qwen3:routing / coauthor:* manifests).
- Process-memory store + Sunya binding state columns.
- ``purge_events`` table + the ``purge()`` admin method (Phoenix v1
  has no purge path; the kill switch is the only "stop" lever).

The :class:`OmegaLedger.seal` signature was minimally adapted: upstream
takes ``(fact, contract: PhysicsContract, sealed_by)`` and computes
``SHA-256(prev_hash | fact | contract_json)``. The slim profile takes
``(fact, payload_json: str, sealed_by)`` and computes the same hash --
the Phoenix adapter (``phoenix.ledger.omega_ledger``) serializes a typed
:class:`LedgerEntry` to ``payload_json`` and passes it through. The
hash algorithm and chain semantics are bit-identical to the upstream
profile, so DF&E hashchain-correctness audits transfer.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

GENESIS_HASH = "GENESIS"


def _compute_entry_hash(prev_hash: str, fact: str, contract_json: str) -> str:
    """Compute SHA-256(prev_hash | fact | contract_json)."""
    payload = f"{prev_hash}|{fact}|{contract_json}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class OmegaLedger:
    """
    Append-only truth store with SHA-256 hashchain.

    Each sealed entry's hash includes the previous entry's hash,
    creating a tamper-evident chain. The first entry chains from GENESIS.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._lock = threading.Lock()

        if db_path == ":memory:":
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            dir_part = os.path.dirname(db_path)
            if dir_part:
                os.makedirs(dir_part, exist_ok=True)
            self._conn = sqlite3.connect(db_path, check_same_thread=False)

        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS omega_entries (
                entry_id TEXT PRIMARY KEY,
                prev_hash TEXT NOT NULL,
                entry_hash TEXT NOT NULL UNIQUE,
                fact TEXT NOT NULL,
                contract_json TEXT NOT NULL,
                sealed_by TEXT NOT NULL DEFAULT 'system',
                timestamp REAL NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_omega_timestamp
            ON omega_entries (timestamp)
        """)
        self._conn.commit()

    def seal(
        self,
        fact: str,
        payload_json: str,
        sealed_by: str = "system",
    ) -> dict[str, Any]:
        """
        Seal a fact + payload into the ledger.

        Computes entry_hash = SHA-256(prev_hash | fact | payload_json).
        Thread-safe: lock held across last_hash read + compute + insert.
        """
        now = time.time()
        entry_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        with self._lock:
            prev_hash = self._last_hash_unlocked()
            entry_hash = _compute_entry_hash(prev_hash, fact, payload_json)

            self._conn.execute(
                """INSERT INTO omega_entries
                   (entry_id, prev_hash, entry_hash, fact, contract_json,
                    sealed_by, timestamp, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry_id,
                    prev_hash,
                    entry_hash,
                    fact,
                    payload_json,
                    sealed_by,
                    now,
                    created_at,
                ),
            )
            self._conn.commit()

        return {
            "entry_id": entry_id,
            "entry_hash": entry_hash,
            "prev_hash": prev_hash,
            "timestamp": now,
            "created_at": created_at,
        }

    def get_entry(self, entry_id: str) -> Optional[dict[str, Any]]:
        """Retrieve a single entry by ID."""
        cur = self._conn.execute(
            "SELECT * FROM omega_entries WHERE entry_id = ?", (entry_id,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row, strict=False))

    def verify_chain(self, limit: Optional[int] = None) -> dict[str, Any]:
        """
        Walk the hashchain recomputing hashes. Returns verification result.

        Returns: {valid: bool, checked: int, first_broken: str|None}
        """
        sql = (
            "SELECT entry_id, prev_hash, entry_hash, fact, contract_json "
            "FROM omega_entries ORDER BY rowid ASC"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"

        cur = self._conn.execute(sql)
        rows = cur.fetchall()

        if not rows:
            return {"valid": True, "checked": 0, "first_broken": None}

        expected_prev = GENESIS_HASH
        for idx, (entry_id, prev_hash, entry_hash, fact, contract_json) in enumerate(
            rows
        ):
            if prev_hash != expected_prev:
                return {
                    "valid": False,
                    "checked": idx,
                    "first_broken": entry_id,
                    "reason": (
                        f"prev_hash mismatch: expected {expected_prev[:16]}... "
                        f"got {prev_hash[:16]}..."
                    ),
                }

            recomputed = _compute_entry_hash(prev_hash, fact, contract_json)
            if recomputed != entry_hash:
                return {
                    "valid": False,
                    "checked": idx,
                    "first_broken": entry_id,
                    "reason": (
                        f"hash mismatch: expected {recomputed[:16]}... "
                        f"got {entry_hash[:16]}..."
                    ),
                }

            expected_prev = entry_hash

        return {"valid": True, "checked": len(rows), "first_broken": None}

    def count(self) -> int:
        """Total number of sealed entries."""
        cur = self._conn.execute("SELECT COUNT(*) FROM omega_entries")
        return int(cur.fetchone()[0])

    def _last_hash_unlocked(self) -> str:
        """Internal: get last hash without acquiring lock (caller holds it)."""
        cur = self._conn.execute(
            "SELECT entry_hash FROM omega_entries ORDER BY rowid DESC LIMIT 1"
        )
        row = cur.fetchone()
        return str(row[0]) if row else GENESIS_HASH

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
