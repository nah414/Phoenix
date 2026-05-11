"""Phase 7 Step 6 -- end-to-end solve → ledger composition test.

Verifies that a POST /v1/tasks request:

1. Returns 200 with ``provenance.omega_ledger_entry_id`` populated
   (the durable correlation key for the ledger row).
2. Writes a row to the StateBackend's ``ledger_entries`` table.
3. The row's ``parent_hash`` correctly anchors at GENESIS for the
   first solve in the chain.
4. The row's ``entry_hash`` recomputes to the same value from the
   stored ``payload_json`` -- the load-bearing replay property.
5. A second solve chains: its ``parent_hash`` equals the first
   solve's ``entry_hash``.
6. The StateBackend's SQL structural check AND the OmegaLedger's
   Python crypto walk both pass on a clean chain.

These tests share an in-process TestClient and a single StateBackend
singleton across the two solves -- the chain has to thread through
the same backend for the parent_hash linkage to work.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix.api.routes import app


@pytest.fixture
def isolated_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    """Point all Phoenix runtime singletons at a per-test tmp directory.

    Resets the state-backend + ledger + audit singletons so a fresh
    chain starts at GENESIS rather than inheriting whatever the
    previous test left in the user's real ``~/.phoenix/runtime``.
    """
    runtime = tmp_path / "phoenix_runtime"
    runtime.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("PHOENIX_SQLITE_DB_PATH", str(runtime / "state.db"))
    monkeypatch.setenv("PHOENIX_AUDIT_DIR", str(runtime / "audit"))
    # Phase 6b's drift-detector singleton also persists state; reset.
    monkeypatch.setenv("PHOENIX_STATE_BACKEND", "sqlite")

    from phoenix.audit import reset_emitter
    from phoenix.ledger import reset_ledger
    from phoenix.state import reset_state_backend

    reset_emitter()
    reset_ledger()
    reset_state_backend()
    try:
        yield runtime
    finally:
        reset_emitter()
        reset_ledger()
        reset_state_backend()


def _qho_body() -> dict:
    """Minimal QHO request body matching the existing solve_endpoint tests."""
    return {
        "physics_context": {
            "mass_kg": 9.1093837015e-31,
            "length_scale_m": 4e-9,
            "metadata": {"omega": 1e15, "n_grid_points": 200},
        },
        "tolerance": {
            "max_error_bar": 1e-3,
            "reproducibility_mode": "default",
            "latency_tier": "batch_realtime",
            "frontier_physics": False,
        },
        "metadata": {},
    }


def test_single_solve_appends_genesis_ledger_entry(isolated_runtime: Path) -> None:
    """First solve in a fresh ledger anchors at GENESIS."""
    with TestClient(app) as client:
        resp = client.post("/v1/tasks", json=_qho_body())
    assert resp.status_code == 200, resp.text
    payload = resp.json()

    # The provenance carries the correlation key.
    entry_id = payload["provenance"]["omega_ledger_entry_id"]
    assert entry_id is not None
    assert len(entry_id) >= 32  # UUID4 string length

    # The StateBackend has exactly one row.
    from phoenix.state import get_state_backend

    rows = get_state_backend().list_ledger_entries(since_unix=0, limit=100)
    assert len(rows) == 1
    row = rows[0]
    assert row["entry_id"] == entry_id
    assert row["entry_kind"] == "solve"
    assert row["actor_id"] == "adam"  # bootstrap actor
    assert row["parent_hash"] == "GENESIS"
    assert len(row["entry_hash"]) == 64


def test_ledger_entry_hash_is_reproducible_from_payload(
    isolated_runtime: Path,
) -> None:
    """The stored entry_hash equals SHA-256(parent_hash | kind | payload_json).

    This is the load-bearing replay-verification property: anyone with
    access to the row can recompute the hash from its fields, no
    Phoenix-side state needed.
    """
    with TestClient(app) as client:
        resp = client.post("/v1/tasks", json=_qho_body())
    assert resp.status_code == 200

    from phoenix.state import get_state_backend

    rows = get_state_backend().list_ledger_entries(since_unix=0, limit=100)
    row = rows[0]

    # Recompute via the same primitive the vendored module uses.
    canonical = f"{row['parent_hash']}|{row['entry_kind']}|{row['payload_json']}"
    recomputed = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert recomputed == row["entry_hash"]


def test_two_solves_chain_correctly(isolated_runtime: Path) -> None:
    """A second solve's parent_hash equals the first solve's entry_hash."""
    with TestClient(app) as client:
        r1 = client.post("/v1/tasks", json=_qho_body())
        r2 = client.post("/v1/tasks", json=_qho_body())

    assert r1.status_code == 200
    assert r2.status_code == 200

    from phoenix.state import get_state_backend

    rows = get_state_backend().list_ledger_entries(since_unix=0, limit=100)
    assert len(rows) == 2
    first, second = rows[0], rows[1]
    assert first["parent_hash"] == "GENESIS"
    assert second["parent_hash"] == first["entry_hash"]
    assert second["entry_id"] == r2.json()["provenance"]["omega_ledger_entry_id"]


def test_solve_entry_payload_contains_provenance_blocks(
    isolated_runtime: Path,
) -> None:
    """The composed SolveEntry payload contains the four §6.7 sub-blocks."""
    with TestClient(app) as client:
        resp = client.post("/v1/tasks", json=_qho_body())
    assert resp.status_code == 200

    from phoenix.state import get_state_backend

    rows = get_state_backend().list_ledger_entries(since_unix=0, limit=100)
    payload = json.loads(rows[0]["payload_json"])

    # SolveEntry-defined fields.
    assert payload["task_id"].startswith("req_")
    assert payload["rung_used"].startswith("R")
    assert payload["agreement_type"] in {"hedged_consensus", "unknown"}
    assert payload["result_value"] > 0
    assert payload["error_bar"] > 0
    assert payload["sigma"] >= 0
    assert payload["result_hash"].startswith("sha256:")

    # Sub-provenance blocks.
    assert "verification_provenance" in payload
    assert payload["verification_provenance"]["initial_rung"].startswith("R")
    assert "drift_state" in payload["verification_provenance"]

    assert "trinity_core_trace" in payload
    assert "solver" in payload["trinity_core_trace"]
    assert payload["trinity_core_trace"]["solver"]["request_id"] == payload["task_id"]

    assert "routing_provenance" in payload
    assert payload["routing_provenance"]["provider_id"] == "phoenix.local_simulator"

    # Vendor-manifest snapshot for replay.
    assert "vendor_manifest" in payload
    assert isinstance(payload["vendor_manifest"], dict)


def test_chain_verifies_in_both_layers(isolated_runtime: Path) -> None:
    """After 3 solves, both the SQL structural check and the Python
    crypto walk report ``valid``."""
    with TestClient(app) as client:
        for _ in range(3):
            resp = client.post("/v1/tasks", json=_qho_body())
            assert resp.status_code == 200

    from phoenix.ledger import get_ledger
    from phoenix.state import get_state_backend

    sql_report = get_state_backend().verify_ledger_integrity()
    py_report = get_ledger().verify_chain()
    assert sql_report["valid"] is True
    assert sql_report["entries_checked"] == 3
    assert py_report.valid is True
    assert py_report.entries_checked == 3


def test_ledger_audit_event_emitted_per_solve(isolated_runtime: Path) -> None:
    """The composer emits a ``ledger.solve_entry_appended`` audit event
    so the audit JSONL has a record of every ledger append."""
    with TestClient(app) as client:
        resp = client.post("/v1/tasks", json=_qho_body())
    assert resp.status_code == 200

    from phoenix.audit import reset_emitter

    # Drain the queue by closing the emitter so the JSONL file flushes.
    reset_emitter()

    audit_files = list((isolated_runtime / "audit").glob("events-*.jsonl"))
    assert len(audit_files) == 1
    content = audit_files[0].read_text(encoding="utf-8")
    # The ledger composer's audit event should appear with the same
    # request_id as the API request.
    assert "ledger.solve_entry_appended" in content
    # Sanity: it carries the entry_id + hashes.
    entry_id = resp.json()["provenance"]["omega_ledger_entry_id"]
    assert entry_id in content
