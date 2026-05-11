"""Phase 7 Step 4 + Step 6 -- Omega Ledger integration tests.

Covers:

- :class:`LedgerEntry` canonical JSON encoding is stable across runs
  (load-bearing: replay verification recomputes the hash from the
  same canonical bytes).
- :meth:`OmegaLedger.append_entry` returns a :class:`LedgerLink` with
  the correct genesis anchor + chain links.
- :meth:`OmegaLedger.verify_chain` reports ``valid=True`` on a clean
  chain and ``valid=False`` (with the offending entry_id named) when
  a row is tampered cryptographically (payload_json modified after the
  fact) -- complements
  :meth:`StateBackend.verify_ledger_integrity`'s SQL structural check.
- :meth:`OmegaLedger.read_entry` round-trips a typed payload through
  storage and back to an identical :class:`LedgerEntry`.
- The 4 typed-payload converters
  (:func:`solve_to_ledger_entry`,
  :func:`override_to_ledger_entry`,
  :func:`kill_switch_to_ledger_entry`,
  :func:`enrollment_to_ledger_entry`) produce ``LedgerEntry`` objects
  with the right ``entry_kind`` discriminator + ``actor_id``.
- :func:`get_ledger` is a singleton; :func:`reset_ledger` clears it.

Step 6 refactor: tests now construct an :class:`OmegaLedger` backed by
a fresh :class:`SQLiteStateBackend` against a tmp directory rather
than the vendored SQLite glue. The Step 4 vendored-SQLite path was
scaffolding; Step 6 makes the StateBackend the durable home and
keeps OmegaLedger as a hash-math + chain-walk facade over it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix.ledger import (
    ChainVerificationReport,
    EnrollmentEntry,
    KillSwitchEntry,
    LedgerEntry,
    LedgerLink,
    OmegaLedger,
    OverrideByOperatorEntry,
    SolveEntry,
    enrollment_to_ledger_entry,
    get_ledger,
    kill_switch_to_ledger_entry,
    override_to_ledger_entry,
    reset_ledger,
    solve_to_ledger_entry,
)
from phoenix.state.sqlite_backend import SQLiteStateBackend


@pytest.fixture
def fresh_backend(tmp_path: Path) -> Iterator[SQLiteStateBackend]:
    """Construct a fresh per-test :class:`SQLiteStateBackend`.

    Each test gets its own SQLite file under ``tmp_path`` so the
    ledger chain starts at GENESIS and rows from one test never
    leak into another. The backend's :meth:`close` is called in
    teardown.
    """
    backend = SQLiteStateBackend(db_path=tmp_path / "state.db")
    yield backend
    backend.close()


# ---------------------------------------------------------------------------
# LedgerEntry canonical JSON


class TestCanonicalJSON:
    def test_canonical_json_is_deterministic(self) -> None:
        """Same payload dict → same JSON string, regardless of dict
        construction order. This is the property the hashchain
        depends on."""
        entry_a = LedgerEntry(
            entry_id="e1",
            entry_kind="solve",
            timestamp_unix=1.0,
            actor_id="adam",
            parent_hash="",
            entry_hash="",
            payload={"b": 2, "a": 1, "c": {"y": 4, "x": 3}},
        )
        entry_b = LedgerEntry(
            entry_id="e1",
            entry_kind="solve",
            timestamp_unix=1.0,
            actor_id="adam",
            parent_hash="",
            entry_hash="",
            payload={"a": 1, "c": {"x": 3, "y": 4}, "b": 2},
        )
        assert entry_a.to_canonical_payload_json() == entry_b.to_canonical_payload_json()

    def test_canonical_json_has_no_whitespace(self) -> None:
        entry = LedgerEntry(
            entry_id="e1",
            entry_kind="t",
            timestamp_unix=1.0,
            actor_id="adam",
            parent_hash="",
            entry_hash="",
            payload={"a": 1, "b": 2},
        )
        line = entry.to_canonical_payload_json()
        # No whitespace inside JSON; separators are (',', ':').
        assert line == '{"a":1,"b":2}'

    def test_canonical_json_rejects_nan(self) -> None:
        """allow_nan=False guards against NaN/Inf in the payload,
        which would otherwise serialize to non-standard JSON tokens
        and break replay across language boundaries."""
        entry = LedgerEntry(
            entry_id="e1",
            entry_kind="t",
            timestamp_unix=1.0,
            actor_id="adam",
            parent_hash="",
            entry_hash="",
            payload={"bad": float("nan")},
        )
        with pytest.raises(ValueError):
            entry.to_canonical_payload_json()


# ---------------------------------------------------------------------------
# Append + chain link semantics


class TestAppendEntry:
    def test_first_entry_anchors_at_genesis(self, fresh_backend: SQLiteStateBackend) -> None:
        ledger = OmegaLedger(state_backend=fresh_backend)
        solve = _make_solve(0)
        entry = solve_to_ledger_entry(solve, actor_id="adam")
        link = ledger.append_entry(entry)
        assert isinstance(link, LedgerLink)
        assert link.parent_hash == "GENESIS"
        # entry_hash is 64-char hex (SHA-256).
        assert len(link.entry_hash) == 64
        assert all(c in "0123456789abcdef" for c in link.entry_hash)
        # The Phoenix-side entry_id from solve_to_ledger_entry flows through.
        assert link.entry_id == entry.entry_id

    def test_second_entry_chains_to_first(self, fresh_backend: SQLiteStateBackend) -> None:
        ledger = OmegaLedger(state_backend=fresh_backend)
        link1 = ledger.append_entry(solve_to_ledger_entry(_make_solve(0), actor_id="adam"))
        link2 = ledger.append_entry(solve_to_ledger_entry(_make_solve(1), actor_id="adam"))
        # link2's parent_hash is exactly link1's entry_hash.
        assert link2.parent_hash == link1.entry_hash
        assert link2.entry_hash != link1.entry_hash

    def test_count_tracks_appends(self, fresh_backend: SQLiteStateBackend) -> None:
        ledger = OmegaLedger(state_backend=fresh_backend)
        assert ledger.count() == 0
        for i in range(5):
            ledger.append_entry(solve_to_ledger_entry(_make_solve(i), actor_id="adam"))
        assert ledger.count() == 5

    def test_close_is_idempotent(self, fresh_backend: SQLiteStateBackend) -> None:
        """OmegaLedger.close is a no-op (StateBackend owns its lifecycle)
        but must be safely callable multiple times."""
        ledger = OmegaLedger(state_backend=fresh_backend)
        ledger.close()
        ledger.close()  # must not raise


# ---------------------------------------------------------------------------
# read_entry round-trip


class TestReadEntry:
    def test_read_entry_returns_full_record(self, fresh_backend: SQLiteStateBackend) -> None:
        ledger = OmegaLedger(state_backend=fresh_backend)
        solve = _make_solve(0, result_value=1.234)
        entry = solve_to_ledger_entry(solve, actor_id="ash")
        link = ledger.append_entry(entry)
        stored = ledger.read_entry(link.entry_id)
        assert stored is not None
        assert stored.entry_id == link.entry_id == entry.entry_id
        assert stored.entry_kind == "solve"
        assert stored.actor_id == "ash"
        assert stored.parent_hash == link.parent_hash
        assert stored.entry_hash == link.entry_hash
        assert stored.payload["task_id"] == "req_0"
        assert stored.payload["result_value"] == pytest.approx(1.234)

    def test_read_entry_returns_none_for_missing_id(
        self, fresh_backend: SQLiteStateBackend
    ) -> None:
        ledger = OmegaLedger(state_backend=fresh_backend)
        assert ledger.read_entry("no-such-id") is None


# ---------------------------------------------------------------------------
# verify_chain (Python crypto walk -- catches payload tampering)


class TestVerifyChain:
    def test_empty_chain_is_valid(self, fresh_backend: SQLiteStateBackend) -> None:
        ledger = OmegaLedger(state_backend=fresh_backend)
        report = ledger.verify_chain()
        assert isinstance(report, ChainVerificationReport)
        assert report.valid is True
        assert report.entries_checked == 0
        assert report.first_broken_entry_id is None

    def test_clean_chain_verifies(self, fresh_backend: SQLiteStateBackend) -> None:
        ledger = OmegaLedger(state_backend=fresh_backend)
        for i in range(3):
            ledger.append_entry(solve_to_ledger_entry(_make_solve(i), actor_id="adam"))
        report = ledger.verify_chain()
        assert report.valid is True
        assert report.entries_checked == 3

    def test_payload_tampering_breaks_chain(self, tmp_path: Path) -> None:
        """Modify a stored payload_json directly via SQL; verify_chain
        detects the cryptographic mismatch (which the SQL structural
        check would miss because the parent_hash chain stays intact)."""
        backend = SQLiteStateBackend(db_path=tmp_path / "state.db")
        try:
            ledger = OmegaLedger(state_backend=backend)
            links = [
                ledger.append_entry(solve_to_ledger_entry(_make_solve(i), actor_id="adam"))
                for i in range(3)
            ]
        finally:
            backend.close()

        # Tamper with entry 1's payload_json directly.
        target_id = links[1].entry_id
        with sqlite3.connect(str(tmp_path / "state.db")) as conn:
            conn.execute(
                "UPDATE ledger_entries SET payload_json = ? WHERE entry_id = ?",
                ('{"task_id":"FORGED","request_id":"FORGED"}', target_id),
            )
            conn.commit()

        # Re-open and verify -- the entry_hash no longer matches the
        # recomputed SHA-256.
        backend2 = SQLiteStateBackend(db_path=tmp_path / "state.db")
        try:
            ledger2 = OmegaLedger(state_backend=backend2)
            report = ledger2.verify_chain()
            assert report.valid is False
            assert report.first_broken_entry_id == target_id
            assert report.reason is not None
            assert "entry_hash mismatch" in report.reason
        finally:
            backend2.close()


# ---------------------------------------------------------------------------
# Typed-payload converters (unchanged from Step 4)


class TestTypedConverters:
    def test_solve_to_ledger_entry(self) -> None:
        solve = _make_solve(0)
        entry = solve_to_ledger_entry(solve, actor_id="ash")
        assert entry.entry_kind == "solve"
        assert entry.actor_id == "ash"
        assert entry.payload["task_id"] == "req_0"
        assert entry.payload["verification_provenance"] == {}
        assert entry.payload["vendor_manifest"] == {}

    def test_override_to_ledger_entry(self) -> None:
        override = OverrideByOperatorEntry(
            operator_id="adam",
            affected_task_id="req_abc",
            override_disposition="ship-as-degraded",
            reason="visual review: result looks reasonable but R5 wobbles",
        )
        entry = override_to_ledger_entry(override)
        assert entry.entry_kind == "override_by_operator"
        assert entry.actor_id == "adam"
        assert entry.payload["affected_task_id"] == "req_abc"

    def test_kill_switch_to_ledger_entry_engage(self) -> None:
        event = KillSwitchEntry(
            transition="engaged",
            by="ash",
            reason="ops emergency",
        )
        entry = kill_switch_to_ledger_entry(event)
        assert entry.entry_kind == "kill_switch"
        assert entry.actor_id == "ash"
        assert entry.payload["transition"] == "engaged"
        assert entry.payload["engaged_at_unix"] is None

    def test_kill_switch_to_ledger_entry_release(self) -> None:
        event = KillSwitchEntry(
            transition="released",
            by="adam",
            reason="all clear",
            engaged_at_unix=1717400000.0,
        )
        entry = kill_switch_to_ledger_entry(event)
        assert entry.payload["transition"] == "released"
        assert entry.payload["engaged_at_unix"] == 1717400000.0

    def test_enrollment_to_ledger_entry(self) -> None:
        enrollment = EnrollmentEntry(
            enrolled_actor_name="alice",
            enrolled_by="adam",
            permissions={"can_submit_tasks": True, "frontier_physics": False},
            identity_fingerprint="abc123",
        )
        entry = enrollment_to_ledger_entry(enrollment)
        assert entry.entry_kind == "enrollment"
        assert entry.actor_id == "adam"
        assert entry.payload["enrolled_actor_name"] == "alice"


# ---------------------------------------------------------------------------
# Full append → verify with mixed entry kinds


class TestMixedKinds:
    def test_chain_works_across_all_four_kinds(self, fresh_backend: SQLiteStateBackend) -> None:
        ledger = OmegaLedger(state_backend=fresh_backend)
        ledger.append_entry(solve_to_ledger_entry(_make_solve(0), actor_id="adam"))
        ledger.append_entry(
            kill_switch_to_ledger_entry(
                KillSwitchEntry(transition="engaged", by="ash", reason="test")
            )
        )
        ledger.append_entry(
            override_to_ledger_entry(
                OverrideByOperatorEntry(
                    operator_id="adam",
                    affected_task_id="req_0",
                    override_disposition="ship-as-degraded",
                    reason="judgment call",
                )
            )
        )
        ledger.append_entry(
            enrollment_to_ledger_entry(
                EnrollmentEntry(
                    enrolled_actor_name="bob",
                    enrolled_by="adam",
                    permissions={"can_submit_tasks": True},
                )
            )
        )
        assert ledger.count() == 4
        report = ledger.verify_chain()
        assert report.valid is True
        assert report.entries_checked == 4


# ---------------------------------------------------------------------------
# StateBackend + OmegaLedger parity: both verifiers should agree


class TestBackendLedgerParity:
    """The SQL structural check (:meth:`verify_ledger_integrity`) and the
    Python crypto walk (:meth:`verify_chain`) should both report
    ``valid`` on a clean chain. They report different things on
    tampering (SQL catches parent_hash chain breaks; Python catches
    payload tampering) -- a complementary pair."""

    def test_clean_chain_validates_in_both_layers(self, fresh_backend: SQLiteStateBackend) -> None:
        ledger = OmegaLedger(state_backend=fresh_backend)
        for i in range(3):
            ledger.append_entry(solve_to_ledger_entry(_make_solve(i), actor_id="adam"))

        sql_report = fresh_backend.verify_ledger_integrity()
        py_report = ledger.verify_chain()
        assert sql_report["valid"] is True
        assert py_report.valid is True
        assert sql_report["entries_checked"] == py_report.entries_checked == 3


# ---------------------------------------------------------------------------
# Singleton lifecycle


class TestSingleton:
    def test_get_ledger_is_singleton(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Point the state-backend factory at a tmp file so the singleton
        # doesn't touch ~/.phoenix/runtime/state.db.
        monkeypatch.setenv("PHOENIX_SQLITE_DB_PATH", str(tmp_path / "state.db"))
        from phoenix.state import reset_state_backend

        reset_state_backend()
        reset_ledger()
        try:
            first = get_ledger()
            second = get_ledger()
            assert first is second
        finally:
            reset_ledger()
            reset_state_backend()

    def test_reset_ledger_clears_singleton(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("PHOENIX_SQLITE_DB_PATH", str(tmp_path / "state.db"))
        from phoenix.state import reset_state_backend

        reset_state_backend()
        reset_ledger()
        try:
            first = get_ledger()
            reset_ledger()
            second = get_ledger()
            assert first is not second
        finally:
            reset_ledger()
            reset_state_backend()


# ---------------------------------------------------------------------------
# helpers


def _make_solve(i: int, *, result_value: float = 1.0) -> SolveEntry:
    """Build a minimal :class:`SolveEntry` with sequential task ids."""
    return SolveEntry(
        task_id=f"req_{i}",
        request_id=f"req_{i}",
        rung_used="R3_TWO_AXES",
        agreement_type="hedged_consensus",
        result_value=result_value + i * 0.001,
        error_bar=1e-3,
        sigma=0.5,
        result_hash=f"sha256:{i:08x}" + "0" * 56,
    )
