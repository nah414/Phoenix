"""Phase 10 Step 2 -- solve_cost_ledger 24h-window + budget_overrides tests.

Exercises the four new StateBackend methods landed at Step 2:

- ``record_solve_cost`` — last-write-wins on request_id.
- ``query_actor_24h_spend`` — sums under the rolling 24h window.
- ``query_org_24h_spend`` — same shape on org_id.
- ``insert_budget_override`` + ``list_active_budget_overrides`` —
  CRUD over budget overrides, with expiry filtering.

Tests run against SQLite (the default dev backend). Postgres
coverage is exercised by the existing ``test_postgres_backend.py``
test family, which is auto-skipped when Postgres isn't running.

We also confirm the migration runner picks up the new
``phase10_cost_ledger`` migration and applies it on a fresh DB.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix.state.sqlite_backend import SQLiteStateBackend


@pytest.fixture
def fresh_backend(tmp_path: Path) -> Iterator[SQLiteStateBackend]:
    """Per-test fresh SQLite backend with all migrations applied.

    The :class:`SQLiteStateBackend` constructor runs the migration
    chain on first connect; no explicit ``open()`` is needed.
    """
    db_path = tmp_path / "phoenix_state.db"
    backend = SQLiteStateBackend(db_path=db_path)
    try:
        yield backend
    finally:
        backend.close()


_AS_OF = 1_700_000_000.0  # Stable test clock; well inside the 24h window's range.


class TestRecordSolveCost:
    def test_record_and_query_round_trip(self, fresh_backend: SQLiteStateBackend) -> None:
        fresh_backend.record_solve_cost(
            request_id="tx-1",
            actor_name="adam",
            org_id="org-a",
            timestamp_unix=_AS_OF - 1.0,
            actual_cost_usd=2.50,
            reproducibility_mode="default",
        )
        spend = fresh_backend.query_actor_24h_spend("adam", as_of_unix=_AS_OF)
        assert spend == pytest.approx(2.50)

    def test_record_idempotent_last_write_wins(self, fresh_backend: SQLiteStateBackend) -> None:
        """Per locked OPEN-2: re-recording the same request_id overwrites."""
        fresh_backend.record_solve_cost(
            request_id="tx-1",
            actor_name="adam",
            org_id="org-a",
            timestamp_unix=_AS_OF - 1.0,
            actual_cost_usd=2.50,
            reproducibility_mode="default",
        )
        fresh_backend.record_solve_cost(
            request_id="tx-1",
            actor_name="adam",
            org_id="org-a",
            timestamp_unix=_AS_OF - 1.0,
            actual_cost_usd=5.75,
            reproducibility_mode="strict",
        )
        spend = fresh_backend.query_actor_24h_spend("adam", as_of_unix=_AS_OF)
        assert spend == pytest.approx(5.75)  # not 2.50 + 5.75

    def test_multiple_actors_independent(self, fresh_backend: SQLiteStateBackend) -> None:
        fresh_backend.record_solve_cost(
            request_id="tx-a",
            actor_name="adam",
            org_id="org-shared",
            timestamp_unix=_AS_OF - 100.0,
            actual_cost_usd=10.0,
            reproducibility_mode="default",
        )
        fresh_backend.record_solve_cost(
            request_id="tx-b",
            actor_name="bob",
            org_id="org-shared",
            timestamp_unix=_AS_OF - 100.0,
            actual_cost_usd=3.5,
            reproducibility_mode="default",
        )
        assert fresh_backend.query_actor_24h_spend("adam", as_of_unix=_AS_OF) == pytest.approx(10.0)
        assert fresh_backend.query_actor_24h_spend("bob", as_of_unix=_AS_OF) == pytest.approx(3.5)
        # Org sum aggregates both actors
        assert fresh_backend.query_org_24h_spend("org-shared", as_of_unix=_AS_OF) == pytest.approx(
            13.5
        )

    def test_empty_actor_returns_zero(self, fresh_backend: SQLiteStateBackend) -> None:
        assert fresh_backend.query_actor_24h_spend("no-such-actor", as_of_unix=_AS_OF) == 0.0

    def test_empty_org_returns_zero(self, fresh_backend: SQLiteStateBackend) -> None:
        assert fresh_backend.query_org_24h_spend("no-such-org", as_of_unix=_AS_OF) == 0.0


class TestWindowSemantics:
    def test_record_older_than_24h_excluded(self, fresh_backend: SQLiteStateBackend) -> None:
        """A solve recorded 25 hours ago doesn't count toward today's spend."""
        fresh_backend.record_solve_cost(
            request_id="tx-old",
            actor_name="adam",
            org_id="org-a",
            timestamp_unix=_AS_OF - 25 * 3600.0,
            actual_cost_usd=99.0,
            reproducibility_mode="default",
        )
        fresh_backend.record_solve_cost(
            request_id="tx-new",
            actor_name="adam",
            org_id="org-a",
            timestamp_unix=_AS_OF - 1.0,
            actual_cost_usd=1.0,
            reproducibility_mode="default",
        )
        # Only the new solve counts
        assert fresh_backend.query_actor_24h_spend("adam", as_of_unix=_AS_OF) == pytest.approx(1.0)

    def test_record_at_boundary_included(self, fresh_backend: SQLiteStateBackend) -> None:
        """A solve exactly 24h ago is on the inclusive lower bound."""
        fresh_backend.record_solve_cost(
            request_id="tx-edge",
            actor_name="adam",
            org_id="org-a",
            timestamp_unix=_AS_OF - 86400.0,
            actual_cost_usd=7.0,
            reproducibility_mode="default",
        )
        assert fresh_backend.query_actor_24h_spend("adam", as_of_unix=_AS_OF) == pytest.approx(7.0)

    def test_query_org_excludes_null_org_id_solves(self, fresh_backend: SQLiteStateBackend) -> None:
        """Solves recorded with org_id=None don't contribute to any
        per-org spend query (the resolver falls back to actor-name
        scoping for solo developers).
        """
        fresh_backend.record_solve_cost(
            request_id="tx-solo",
            actor_name="solo",
            org_id=None,
            timestamp_unix=_AS_OF - 1.0,
            actual_cost_usd=5.0,
            reproducibility_mode="default",
        )
        # Actor query catches it
        assert fresh_backend.query_actor_24h_spend("solo", as_of_unix=_AS_OF) == pytest.approx(5.0)
        # Org query (for any org) does NOT
        assert fresh_backend.query_org_24h_spend("any-org", as_of_unix=_AS_OF) == 0.0


class TestBudgetOverrides:
    def test_insert_returns_override_id(self, fresh_backend: SQLiteStateBackend) -> None:
        override_id = fresh_backend.insert_budget_override(
            actor_name="adam",
            scope="per_solve",
            new_ceiling_usd=100.0,
            expires_at_unix=_AS_OF + 3600.0,
            created_by="ash",
            rationale="frontier physics test",
        )
        assert override_id > 0

    def test_list_active_returns_unexpired(self, fresh_backend: SQLiteStateBackend) -> None:
        fresh_backend.insert_budget_override(
            actor_name="adam",
            scope="per_solve",
            new_ceiling_usd=100.0,
            expires_at_unix=_AS_OF + 3600.0,
            created_by="ash",
        )
        active = fresh_backend.list_active_budget_overrides("adam", as_of_unix=_AS_OF)
        assert len(active) == 1
        assert active[0]["scope"] == "per_solve"
        assert active[0]["new_ceiling_usd"] == pytest.approx(100.0)
        assert active[0]["created_by"] == "ash"

    def test_list_active_excludes_expired(self, fresh_backend: SQLiteStateBackend) -> None:
        fresh_backend.insert_budget_override(
            actor_name="adam",
            scope="per_solve",
            new_ceiling_usd=100.0,
            expires_at_unix=_AS_OF - 1.0,  # expired
            created_by="ash",
        )
        fresh_backend.insert_budget_override(
            actor_name="adam",
            scope="per_actor_24h",
            new_ceiling_usd=200.0,
            expires_at_unix=_AS_OF + 1.0,  # not yet expired
            created_by="ash",
        )
        active = fresh_backend.list_active_budget_overrides("adam", as_of_unix=_AS_OF)
        # Only the unexpired one
        assert len(active) == 1
        assert active[0]["scope"] == "per_actor_24h"

    def test_list_active_orders_by_creation(self, fresh_backend: SQLiteStateBackend) -> None:
        """Multiple overrides for the same actor return ordered by created_at."""
        for i in range(3):
            fresh_backend.insert_budget_override(
                actor_name="adam",
                scope="per_solve",
                new_ceiling_usd=10.0 + i,
                expires_at_unix=_AS_OF + 3600.0,
                created_by="ash",
            )
        active = fresh_backend.list_active_budget_overrides("adam", as_of_unix=_AS_OF)
        assert len(active) == 3
        # created_at_unix ascending
        created_times = [r["created_at_unix"] for r in active]
        assert created_times == sorted(created_times)

    def test_list_active_isolates_by_actor(self, fresh_backend: SQLiteStateBackend) -> None:
        fresh_backend.insert_budget_override(
            actor_name="adam",
            scope="per_solve",
            new_ceiling_usd=100.0,
            expires_at_unix=_AS_OF + 3600.0,
            created_by="ash",
        )
        # Bob has no overrides
        active = fresh_backend.list_active_budget_overrides("bob", as_of_unix=_AS_OF)
        assert active == []


def test_migration_runner_picks_up_phase10(tmp_path: Path) -> None:
    """A fresh backend goes through Phase 6b -> 7 -> 10 cleanly."""
    db_path = tmp_path / "phoenix_state.db"
    backend = SQLiteStateBackend(db_path=db_path)
    try:
        # If the migration didn't run, the new columns won't exist
        # and record_solve_cost would raise OperationalError.
        backend.record_solve_cost(
            request_id="tx-smoke",
            actor_name="adam",
            org_id="org-a",
            timestamp_unix=_AS_OF,
            actual_cost_usd=1.0,
            reproducibility_mode="default",
        )
        # And the new table works:
        oid = backend.insert_budget_override(
            actor_name="adam",
            scope="per_solve",
            new_ceiling_usd=10.0,
            expires_at_unix=_AS_OF + 3600.0,
            created_by="ash",
        )
        assert oid > 0
    finally:
        backend.close()
