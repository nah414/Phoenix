"""Phase 7 Step 4 -- Omega Ledger integration tests.

Covers:

- :class:`LedgerEntry` canonical JSON encoding is stable across runs
  (load-bearing: replay verification recomputes the hash from the
  same canonical bytes).
- :meth:`OmegaLedger.append_entry` returns a :class:`LedgerLink` with
  the correct genesis anchor + chain links.
- :meth:`OmegaLedger.verify_chain` reports ``valid=True`` on a clean
  chain and ``valid=False`` (with the offending entry_id named) when
  a row is tampered.
- :meth:`OmegaLedger.read_entry` round-trips a typed payload through
  storage and back to an identical :class:`LedgerEntry`.
- The 4 typed-payload converters
  (:func:`solve_to_ledger_entry`,
  :func:`override_to_ledger_entry`,
  :func:`kill_switch_to_ledger_entry`,
  :func:`enrollment_to_ledger_entry`) produce ``LedgerEntry`` objects
  with the right ``entry_kind`` discriminator + ``actor_id``.
- :func:`get_ledger` is a singleton; :func:`reset_ledger` clears it.

Tests use ``db_path=":memory:"`` for the per-test fresh ledger so
the real ``~/.phoenix/runtime/ledger.db`` is never touched.
"""

from __future__ import annotations

import sqlite3
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
    def test_first_entry_anchors_at_genesis(self) -> None:
        ledger = OmegaLedger(db_path=":memory:")
        try:
            solve = _make_solve(0)
            entry = solve_to_ledger_entry(solve, actor_id="adam")
            link = ledger.append_entry(entry)
            assert isinstance(link, LedgerLink)
            assert link.parent_hash == "GENESIS"
            # entry_hash is 64-char hex (SHA-256).
            assert len(link.entry_hash) == 64
            assert all(c in "0123456789abcdef" for c in link.entry_hash)
        finally:
            ledger.close()

    def test_second_entry_chains_to_first(self) -> None:
        ledger = OmegaLedger(db_path=":memory:")
        try:
            link1 = ledger.append_entry(solve_to_ledger_entry(_make_solve(0), actor_id="adam"))
            link2 = ledger.append_entry(solve_to_ledger_entry(_make_solve(1), actor_id="adam"))
            # link2's parent_hash is exactly link1's entry_hash.
            assert link2.parent_hash == link1.entry_hash
            assert link2.entry_hash != link1.entry_hash

        finally:
            ledger.close()

    def test_count_tracks_appends(self) -> None:
        ledger = OmegaLedger(db_path=":memory:")
        try:
            assert ledger.count() == 0
            for i in range(5):
                ledger.append_entry(solve_to_ledger_entry(_make_solve(i), actor_id="adam"))
            assert ledger.count() == 5
        finally:
            ledger.close()

    def test_close_blocks_further_appends(self) -> None:
        ledger = OmegaLedger(db_path=":memory:")
        ledger.close()
        with pytest.raises(RuntimeError, match="closed"):
            ledger.append_entry(solve_to_ledger_entry(_make_solve(0), actor_id="adam"))

    def test_close_is_idempotent(self) -> None:
        ledger = OmegaLedger(db_path=":memory:")
        ledger.close()
        ledger.close()  # must not raise


# ---------------------------------------------------------------------------
# read_entry round-trip


class TestReadEntry:
    def test_read_entry_returns_full_record(self) -> None:
        ledger = OmegaLedger(db_path=":memory:")
        try:
            solve = _make_solve(0, result_value=1.234)
            entry = solve_to_ledger_entry(solve, actor_id="ash")
            link = ledger.append_entry(entry)
            # Note: the vendored seal mints its own UUID, so the
            # entry_id we get back is the vendored module's, not the
            # client-side one we put inside payload.
            stored = ledger.read_entry(link.entry_id)
            assert stored is not None
            assert stored.entry_id == link.entry_id
            assert stored.entry_kind == "solve"
            assert stored.actor_id == "ash"
            assert stored.parent_hash == link.parent_hash
            assert stored.entry_hash == link.entry_hash
            assert stored.payload["task_id"] == "req_0"
            assert stored.payload["result_value"] == pytest.approx(1.234)
        finally:
            ledger.close()

    def test_read_entry_returns_none_for_missing_id(self) -> None:
        ledger = OmegaLedger(db_path=":memory:")
        try:
            assert ledger.read_entry("no-such-id") is None
        finally:
            ledger.close()


# ---------------------------------------------------------------------------
# verify_chain


class TestVerifyChain:
    def test_empty_chain_is_valid(self) -> None:
        ledger = OmegaLedger(db_path=":memory:")
        try:
            report = ledger.verify_chain()
            assert isinstance(report, ChainVerificationReport)
            assert report.valid is True
            assert report.entries_checked == 0
            assert report.first_broken_entry_id is None
        finally:
            ledger.close()

    def test_clean_chain_verifies(self) -> None:
        ledger = OmegaLedger(db_path=":memory:")
        try:
            for i in range(3):
                ledger.append_entry(solve_to_ledger_entry(_make_solve(i), actor_id="adam"))
            report = ledger.verify_chain()
            assert report.valid is True
            assert report.entries_checked == 3
        finally:
            ledger.close()

    def test_tampering_breaks_chain(self, tmp_path: Path) -> None:
        """Modify a stored payload directly; verify_chain detects."""
        db_path = str(tmp_path / "ledger.db")
        ledger = OmegaLedger(db_path=db_path)
        try:
            links = [
                ledger.append_entry(solve_to_ledger_entry(_make_solve(i), actor_id="adam"))
                for i in range(3)
            ]
        finally:
            ledger.close()

        # Tamper with entry 1's contract_json directly.
        target_id = links[1].entry_id
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE omega_entries SET contract_json = ? WHERE entry_id = ?",
                ('{"task_id":"FORGED","request_id":"FORGED"}', target_id),
            )
            conn.commit()

        # Re-open and verify -- chain should be broken at entry 1.
        ledger2 = OmegaLedger(db_path=db_path)
        try:
            report = ledger2.verify_chain()
            assert report.valid is False
            assert report.first_broken_entry_id == target_id
            assert report.reason is not None
            assert "hash mismatch" in report.reason
        finally:
            ledger2.close()


# ---------------------------------------------------------------------------
# Typed-payload converters


class TestTypedConverters:
    def test_solve_to_ledger_entry(self) -> None:
        solve = _make_solve(0)
        entry = solve_to_ledger_entry(solve, actor_id="ash")
        assert entry.entry_kind == "solve"
        assert entry.actor_id == "ash"
        assert entry.payload["task_id"] == "req_0"
        # Default empty provenance/manifest blocks.
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
        # operator_id flows up to the ledger entry's actor_id.
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
        # actor_id is the ADMIN enrolling, not the new actor.
        assert entry.actor_id == "adam"
        assert entry.payload["enrolled_actor_name"] == "alice"


# ---------------------------------------------------------------------------
# Full append → verify with mixed entry kinds


class TestMixedKinds:
    def test_chain_works_across_all_four_kinds(self) -> None:
        ledger = OmegaLedger(db_path=":memory:")
        try:
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
        finally:
            ledger.close()


# ---------------------------------------------------------------------------
# Singleton lifecycle


class TestSingleton:
    def test_get_ledger_is_singleton(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("PHOENIX_LEDGER_DB", str(tmp_path / "ledger.db"))
        reset_ledger()
        try:
            first = get_ledger()
            second = get_ledger()
            assert first is second
        finally:
            reset_ledger()

    def test_reset_ledger_clears_singleton(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("PHOENIX_LEDGER_DB", str(tmp_path / "ledger.db"))
        reset_ledger()
        try:
            first = get_ledger()
            reset_ledger()
            second = get_ledger()
            assert first is not second
        finally:
            reset_ledger()

    def test_env_override_drives_db_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        custom = tmp_path / "custom_ledger.db"
        monkeypatch.setenv("PHOENIX_LEDGER_DB", str(custom))
        reset_ledger()
        try:
            ledger = get_ledger()
            assert ledger.db_path == str(custom)
            # The file got created on construction.
            assert custom.exists()
        finally:
            reset_ledger()


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
