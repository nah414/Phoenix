"""Phase 7 Step 8 -- replay engine + POST /v1/tasks/{task_id}/replay tests.

Coverage:

- Happy path: a strict-mode solve followed by a replay returns
  ``hashes_match=True`` with the original + replayed result_hash
  identical (bit-exact verification of the deterministic pipeline).
- The replay does NOT append a new ledger entry -- replay verifies;
  it doesn't extend the chain.
- Missing task_id: ``replay`` raises :class:`LedgerEntryNotFound`;
  the route surfaces HTTP 404.
- Default-mode original: replay raises :class:`ReplayEntryIncomplete`;
  the route surfaces HTTP 409.
- Divergence: when the recorded ``result_hash`` doesn't match the
  re-run's hash, ``replay`` raises :class:`ReplayDivergence` with
  both hashes attached; the route surfaces HTTP 500 with full
  divergence detail.
- The ``replay`` endpoint is gated on ``can_replay_tasks`` capability
  (admin actor passes; minimal-permission actors get 403).

Tests share an in-process FastAPI :class:`TestClient` and an isolated
per-test runtime (`PHOENIX_SQLITE_DB_PATH` + `PHOENIX_AUDIT_DIR`) so
chains start at GENESIS rather than inheriting prior test state.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix.api.routes import app
from phoenix.ledger import (
    LedgerEntryNotFound,
    ReplayDivergence,
    ReplayEntryIncomplete,
    replay,
)


@pytest.fixture
def isolated_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    """Per-test fresh Phoenix runtime so chains start at GENESIS."""
    runtime = tmp_path / "phoenix_runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PHOENIX_SQLITE_DB_PATH", str(runtime / "state.db"))
    monkeypatch.setenv("PHOENIX_AUDIT_DIR", str(runtime / "audit"))

    from phoenix.audit import reset_emitter
    from phoenix.ledger import reset_ledger
    from phoenix.safety.rate_limiter import get_limiter
    from phoenix.state import reset_state_backend
    from phoenix.trinity import reproducibility_context

    reset_emitter()
    reset_ledger()
    reset_state_backend()
    reproducibility_context.clear_all()
    get_limiter().reset_all()
    try:
        yield runtime
    finally:
        reset_emitter()
        reset_ledger()
        reset_state_backend()
        reproducibility_context.clear_all()
        get_limiter().reset_all()


def _qho_body(mode: str = "strict") -> dict:
    return {
        "physics_context": {
            "mass_kg": 9.1093837015e-31,
            "length_scale_m": 4e-9,
            "metadata": {"omega": 1e15, "n_grid_points": 200},
        },
        "tolerance": {
            "max_error_bar": 1e-3,
            "reproducibility_mode": mode,
            "latency_tier": "batch_realtime",
            "frontier_physics": False,
        },
        "metadata": {},
    }


# ---------------------------------------------------------------------------
# Happy path


def test_strict_solve_then_replay_matches_bit_exact(isolated_runtime: Path) -> None:
    """A strict-mode solve followed by replay produces matching hashes."""
    with TestClient(app) as client:
        original = client.post("/v1/tasks", json=_qho_body("strict"))
        assert original.status_code == 200
        task_id = original.json()["task_id"]
        original_hash = original.json()["provenance"]["omega_ledger_entry_id"]
        assert original_hash is not None

        replay_resp = client.post(f"/v1/tasks/{task_id}/replay")
    assert replay_resp.status_code == 200, replay_resp.text
    report = replay_resp.json()
    assert report["task_id"] == task_id
    assert report["hashes_match"] is True
    assert report["divergent_layer"] is None
    assert report["original_result_hash"] == report["replayed_result_hash"]
    assert report["original_result_hash"].startswith("sha256:")
    assert report["wall_clock_ms"] > 0


def test_replay_does_not_extend_chain(isolated_runtime: Path) -> None:
    """Replay verifies but does NOT add a new ledger entry."""
    from phoenix.state import get_state_backend

    with TestClient(app) as client:
        original = client.post("/v1/tasks", json=_qho_body("strict"))
        task_id = original.json()["task_id"]

        rows_before = get_state_backend().list_ledger_entries(since_unix=0, limit=100)
        assert len(rows_before) == 1

        replay_resp = client.post(f"/v1/tasks/{task_id}/replay")
        assert replay_resp.status_code == 200

        rows_after = get_state_backend().list_ledger_entries(since_unix=0, limit=100)
        # The chain length is unchanged after the replay verification.
        assert len(rows_after) == 1
        assert rows_after[0]["entry_id"] == rows_before[0]["entry_id"]


# ---------------------------------------------------------------------------
# Missing entries / incomplete entries


def test_replay_returns_404_for_unknown_task_id(isolated_runtime: Path) -> None:
    """POSTing to /replay with a non-existent task_id returns 404."""
    with TestClient(app) as client:
        resp = client.post("/v1/tasks/req_does_not_exist/replay")
    assert resp.status_code == 404
    assert "No ledger entry" in resp.json()["detail"]


def test_replay_returns_409_for_default_mode_original(
    isolated_runtime: Path,
) -> None:
    """Default-mode solves can't be replayed (no env snapshot)."""
    with TestClient(app) as client:
        original = client.post("/v1/tasks", json=_qho_body("default"))
        assert original.status_code == 200
        task_id = original.json()["task_id"]

        replay_resp = client.post(f"/v1/tasks/{task_id}/replay")
    assert replay_resp.status_code == 409
    detail = replay_resp.json()["detail"]
    assert "default" in detail or "strict" in detail


def test_replay_engine_direct_raises_typed_exceptions(
    isolated_runtime: Path,
) -> None:
    """Calling the replay engine directly raises typed exceptions
    that the route translates to HTTP codes."""
    with pytest.raises(LedgerEntryNotFound):
        replay("req_never_seen")

    with TestClient(app) as client:
        default_resp = client.post("/v1/tasks", json=_qho_body("default"))
        task_id = default_resp.json()["task_id"]
    with pytest.raises(ReplayEntryIncomplete):
        replay(task_id)


# ---------------------------------------------------------------------------
# Divergence


def test_replay_diverges_when_recorded_hash_was_tampered(
    isolated_runtime: Path,
) -> None:
    """Tamper with the recorded result_hash directly in SQL; the replay
    re-computes the real hash, compares, and raises ReplayDivergence."""
    import json
    import sqlite3

    with TestClient(app) as client:
        original = client.post("/v1/tasks", json=_qho_body("strict"))
        task_id = original.json()["task_id"]

    # Read the entry, mutate result_hash inside payload_json, write back.
    db_path = isolated_runtime / "state.db"
    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.execute("SELECT entry_id, payload_json FROM ledger_entries LIMIT 1")
        entry_id, payload_json = cur.fetchone()
        payload = json.loads(payload_json)
        payload["result_hash"] = "sha256:" + "f" * 64  # forged
        conn.execute(
            "UPDATE ledger_entries SET payload_json = ? WHERE entry_id = ?",
            (json.dumps(payload), entry_id),
        )
        conn.commit()

    with pytest.raises(ReplayDivergence) as exc_info:
        replay(task_id)
    exc = exc_info.value
    assert exc.task_id == task_id
    assert exc.original_result_hash.startswith("sha256:f" * 1)
    assert exc.replayed_result_hash.startswith("sha256:")
    assert exc.replayed_result_hash != exc.original_result_hash
    assert exc.divergent_layer == "result"


def test_replay_route_surfaces_500_on_divergence(
    isolated_runtime: Path,
) -> None:
    """The route translates ReplayDivergence to HTTP 500 with full detail."""
    import json
    import sqlite3

    with TestClient(app) as client:
        original = client.post("/v1/tasks", json=_qho_body("strict"))
        task_id = original.json()["task_id"]

    db_path = isolated_runtime / "state.db"
    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.execute("SELECT entry_id, payload_json FROM ledger_entries LIMIT 1")
        entry_id, payload_json = cur.fetchone()
        payload = json.loads(payload_json)
        payload["result_hash"] = "sha256:" + "a" * 64
        conn.execute(
            "UPDATE ledger_entries SET payload_json = ? WHERE entry_id = ?",
            (json.dumps(payload), entry_id),
        )
        conn.commit()

    with TestClient(app) as client:
        resp = client.post(f"/v1/tasks/{task_id}/replay")
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    # Detail is a dict carrying both hashes for ops triage.
    assert detail["error"] == "replay_divergence"
    assert detail["task_id"] == task_id
    assert detail["original_result_hash"].startswith("sha256:a")
    assert detail["replayed_result_hash"] != detail["original_result_hash"]
    assert detail["divergent_layer"] == "result"


# ---------------------------------------------------------------------------
# Authorization


def test_replay_endpoint_requires_can_replay_tasks_permission(
    isolated_runtime: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An actor explicitly stripped of can_replay_tasks gets 403.

    Phoenix's default-permissions-for-unknown-actors grants
    can_replay_tasks (the architecture's "open by default for
    Phoenix-recognized actors" stance). To exercise the denial path
    we use a tmp-path PermissionsRegistry monkey-patched in, set a
    custom record for "alice" with can_replay_tasks=False, and
    confirm the route returns 403.
    """
    import base64
    import json

    from phoenix.identity.bootstrap import mint_bootstrap_actor
    from phoenix.safety import permissions as perms_module
    from phoenix.safety.permissions import ActorPermissions, PermissionsRegistry

    # Build a throwaway registry pinned to a tmp file so we don't
    # touch the real permissions JSON.
    isolated_registry = PermissionsRegistry(path=tmp_path / "perms.json")
    isolated_registry.set(
        "alice",
        ActorPermissions(
            can_submit_tasks=True,
            can_replay_tasks=False,
            can_load_adapter=False,
            can_unload_adapter=False,
            frontier_physics=False,
            can_override_human_review=False,
            is_admin=False,
            rate_limit_tier="default",
        ),
    )
    monkeypatch.setattr(perms_module, "_REGISTRY", isolated_registry)

    alice = mint_bootstrap_actor("alice")
    payload = alice.to_payload()
    header = "Phoenix-Actor " + base64.b64encode(json.dumps(payload).encode()).decode("ascii")

    with TestClient(app) as client:
        resp = client.post(
            "/v1/tasks/req_anything/replay",
            headers={"Authorization": header},
        )
    assert resp.status_code == 403
    assert "can_replay_tasks" in resp.json()["detail"]


def test_replay_endpoint_passes_for_admin_actor(isolated_runtime: Path) -> None:
    """The bootstrap-fallback admin actor (adam) has can_replay_tasks."""
    # No actor header -> bootstrap-to-adam fallback; adam is admin so
    # the safety gate passes. The replay then 404s because no task
    # exists, which is still a "permission granted" outcome.
    with TestClient(app) as client:
        resp = client.post("/v1/tasks/req_anything/replay")
    # Not 401/403 -- permission was granted.
    assert resp.status_code in (404, 409, 500)
