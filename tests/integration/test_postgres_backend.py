"""Integration tests for :class:`PostgresStateBackend` (Phase 6b Step 3).

The Phase 6b dialect-symmetric test surface lives here and mirrors
``test_sqlite_backend.py``. Full SQLite-vs-Postgres parity parametrization
lands at Step 9; Step 3 ships a standalone Postgres suite gated behind
two skip conditions:

1. ``psycopg`` / ``psycopg_pool`` not installed (the ``postgres`` extra
   isn't present) -- skip the entire module via
   :func:`pytest.importorskip`.
2. ``$PHOENIX_POSTGRES_TEST_DSN`` not set -- per-fixture skip; CI sets
   this when a test Postgres instance is provisioned.

To run locally:

.. code-block:: powershell

    pip install -e .[postgres]
    $env:PHOENIX_POSTGRES_TEST_DSN = "postgresql://phoenix_test:phoenix_test@localhost:5432/phoenix_test"
    pytest tests/integration/test_postgres_backend.py -v

These tests are designed to run against an EMPTY database -- each test
constructs a fresh backend, runs migrations, exercises the methods, and
relies on a Postgres :class:`SAVEPOINT`-equivalent (transaction
rollback at fixture teardown) to leave the DB clean for the next test.
The current implementation drops + recreates the schema between tests
via the migration's :func:`revert` helper for simplicity.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("psycopg_pool")

# Import after the skip-checks so collection doesn't fail on Phase 6b
# Step 3 SQLite-only dev machines.
from phoenix.state.migrations import phase6b_initial  # noqa: E402
from phoenix.state.postgres_backend import PostgresStateBackend  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Iterator


_DSN_ENV = "PHOENIX_POSTGRES_TEST_DSN"


def _require_dsn() -> str:
    dsn = os.environ.get(_DSN_ENV)
    if not dsn:
        pytest.skip(f"{_DSN_ENV} not set; Postgres integration tests require a DSN")
    return dsn


@pytest.fixture()
def backend() -> Iterator[PostgresStateBackend]:
    """Construct a fresh PostgresStateBackend against the test DSN.

    Drops Phase 6b tables at teardown so the next test sees a clean
    slate. Relies on the migration's :func:`revert` to do the drop.
    """
    dsn = _require_dsn()
    b = PostgresStateBackend(dsn=dsn, min_size=1, max_size=2)
    try:
        yield b
    finally:
        # Drop schema for test isolation.
        try:
            with b._require_pool().connection() as conn:  # type: ignore[attr-defined]
                phase6b_initial.revert(conn)
        finally:
            b.close()


def test_postgres_migrations_apply_idempotently() -> None:
    """Two backend constructions against the same DB must not re-apply
    the migration (else the schema_version PRIMARY KEY violation would
    surface)."""
    dsn = _require_dsn()
    b1 = PostgresStateBackend(dsn=dsn, min_size=1, max_size=2)
    try:
        b1.set_kill_switch_state(
            {
                "engaged": True,
                "engaged_at_utc": "2026-05-10T16:00:00+00:00",
                "engaged_by": "ash",
                "reason": "postgres idempotency",
            }
        )
    finally:
        b1.close()

    b2 = PostgresStateBackend(dsn=dsn, min_size=1, max_size=2)
    try:
        state = b2.get_kill_switch_state()
        assert state["engaged"] is True
        assert state["engaged_by"] == "ash"
    finally:
        with b2._require_pool().connection() as conn:  # type: ignore[attr-defined]
            phase6b_initial.revert(conn)
        b2.close()


def test_postgres_kill_switch_round_trip(backend: PostgresStateBackend) -> None:
    initial = backend.get_kill_switch_state()
    assert initial == {
        "engaged": False,
        "engaged_at_utc": None,
        "engaged_by": None,
        "reason": None,
    }
    backend.set_kill_switch_state(
        {
            "engaged": True,
            "engaged_at_utc": "2026-05-10T12:00:00+00:00",
            "engaged_by": "ash",
            "reason": "pg round-trip",
        }
    )
    after = backend.get_kill_switch_state()
    assert after["engaged"] is True
    assert after["engaged_by"] == "ash"
    assert after["reason"] == "pg round-trip"


def test_postgres_solve_cost_round_trip(backend: PostgresStateBackend) -> None:
    record: dict[str, Any] = {
        "actor_id": "ash",
        "cost_usd_estimate": 0.42,
        "cost_usd_actual": None,
        "provider": "ibm_quantum",
        "submitted_at_unix": 1717000000.0,
        "completed_at_unix": None,
    }
    backend.put_solve_cost_record("solve_pg_1", record)
    fetched = backend.get_solve_cost_record("solve_pg_1")
    assert fetched is not None
    assert fetched["solve_id"] == "solve_pg_1"
    assert fetched["cost_usd_estimate"] == pytest.approx(0.42)


def test_postgres_audit_events_append_and_list(backend: PostgresStateBackend) -> None:
    base_ts = 1717100000.0
    for i in range(5):
        backend.append_audit_event(
            {
                "timestamp_unix": base_ts + i,
                "actor_id": "ash",
                "layer": "safety_gate",
                "event_type": "stage5.rate_limit_deducted",
                "parameters": {"cost": 5, "tier": "default", "i": i},
                "result_hash": f"sha256:{i:064d}",
            }
        )
    listed = backend.list_audit_events(since_unix=base_ts + 2, limit=10)
    assert len(listed) == 3
    assert listed[0]["parameters"]["i"] == 2


def test_postgres_pending_review_enqueue_list_resolve(
    backend: PostgresStateBackend,
) -> None:
    backend.enqueue_pending_review(
        {
            "review_id": "pg_rev_1",
            "task_id": "task_pg_xyz",
            "actor_id": "ash",
            "result": {"agreement_type": "DEGRADED", "value": 4.2},
            "agreement_type": "DEGRADED",
            "queued_at_unix": 1717200000.0,
        }
    )
    pending = backend.list_pending_reviews()
    assert [r["review_id"] for r in pending] == ["pg_rev_1"]

    backend.resolve_pending_review(
        "pg_rev_1",
        {
            "resolved_by": "adam",
            "resolved_at_unix": 1717200010.0,
            "verdict": "approved",
            "rationale": "ok",
        },
    )
    assert backend.list_pending_reviews() == []


def test_postgres_drift_state_snapshot_round_trip(
    backend: PostgresStateBackend,
) -> None:
    assert backend.get_drift_state_snapshot() is None
    backend.put_drift_state_snapshot(
        {
            "cycle_unix": 1717300000.0,
            "state": "warning",
            "firing_detectors": ["tier1_analytical"],
            "detector_summaries": {"tier1_analytical": "HO-1 outside tolerance"},
        }
    )
    fetched = backend.get_drift_state_snapshot()
    assert fetched is not None
    assert fetched["state"] == "warning"
    assert fetched["firing_detectors"] == ["tier1_analytical"]


def test_postgres_actor_permissions_shadow_round_trip(
    backend: PostgresStateBackend,
) -> None:
    backend.put_actor_permission(
        "ash",
        {
            "can_submit_tasks": True,
            "can_replay_tasks": True,
            "can_load_adapter": True,
            "can_unload_adapter": True,
            "frontier_physics": True,
            "can_override_human_review": True,
            "is_admin": True,
            "rate_limit_tier": "admin",
        },
    )
    perms = backend.list_actor_permissions()
    by_name = {p["actor_name"]: p for p in perms}
    assert by_name["ash"]["is_admin"] is True
    assert by_name["ash"]["rate_limit_tier"] == "admin"


def test_postgres_close_then_use_raises(backend: PostgresStateBackend) -> None:
    # Make a separate backend to test the close-then-use case without
    # disrupting the fixture teardown's revert path.
    dsn = _require_dsn()
    b = PostgresStateBackend(dsn=dsn, min_size=1, max_size=2)
    b.close()
    with pytest.raises(RuntimeError, match="closed"):
        b.get_kill_switch_state()


def test_postgres_missing_extra_raises_importerror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When psycopg_pool is not importable, construction raises a clear
    ImportError naming the extra to install."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> Any:
        if name.startswith("psycopg_pool"):
            raise ImportError("simulated missing psycopg_pool")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ImportError, match=r"\[postgres\]"):
        PostgresStateBackend(dsn="postgresql://does-not-matter/")
