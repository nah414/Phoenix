"""Deterministic SolveEntry fixture builder (Phase 11 Step 6).

Per locked OPEN-3 (Phase 11): the long-window replay test uses a
hand-built fixture solve rather than depending on a captured
production solve. The fixture submits a deterministic QHO task
via the in-process Phoenix pipeline at ``strict`` reproducibility
mode, captures the resulting Result + result_hash, and returns
the bundle the test needs to verify bit-exact replay.

The fixture is **self-contained**:

- Pinned physics context (single-electron QHO at fixed omega).
- Pinned tolerance (``max_error_bar=1e-3``, mode=``strict``).
- Pinned solver: local classical simulator (no cloud shots, so
  Decision 20's cloud-shots-recorded gate doesn't apply).
- Pinned result_hash: captured from the actual ``solve()`` call,
  so any vendor drift between solve and replay would make the
  test fail.

The fixture's "deterministic" guarantee comes from Phoenix's
strict-mode discipline:
:func:`phoenix._internal.reproducibility.capture_environment`
snapshots BLAS thread count + NumPy RNG state + PYTHONHASHSEED;
the replay engine restores this exact env before re-running.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi.testclient import TestClient


# Pinned QHO task spec (single-electron, omega=1e15, fine grid).
# Matches the existing test_replay_engine.py + tier1 benchmarks.
_PINNED_QHO_BODY = {
    "physics_context": {
        "mass_kg": 9.1093837015e-31,
        "length_scale_m": 4e-9,
        "metadata": {"omega": 1e15, "n_grid_points": 200},
    },
    "tolerance": {
        "max_error_bar": 1e-3,
        "reproducibility_mode": "strict",
        "latency_tier": "batch_realtime",
        "frontier_physics": False,
    },
    "metadata": {},
}


@dataclass(frozen=True)
class FixtureSolve:
    """Result of building the deterministic fixture solve.

    Fields:
      - ``task_id``: the request_id Phoenix minted for the solve
        (also used as the replay key).
      - ``result_value`` / ``error_bar`` / ``sigma``: pinned numeric
        outputs from the deterministic solve.
      - ``agreement_type``: the gate's classification of the solve.
      - ``omega_ledger_entry_id``: UUID of the ledger entry (from
        ``Result.provenance.omega_ledger_entry_id``). NOT the same
        as ``result_hash`` -- the result_hash is the SHA-256 over
        the canonical-JSON Result payload, computed internally by
        the replay engine. The fixture doesn't need to capture
        result_hash because the replay engine fetches the
        original's hash from the ledger entry on every replay call.
      - ``solve_unix_timestamp``: wall-clock at fixture build time.
        The long-window test advances the clock past this by
        180+ days.
    """

    task_id: str
    result_value: float
    error_bar: float
    sigma: float
    agreement_type: str
    omega_ledger_entry_id: str
    solve_unix_timestamp: float


def build_strict_mode_qho_fixture(runtime: Path) -> FixtureSolve:
    """Submit a strict-mode QHO solve and capture the fixture bundle.

    Uses the in-process FastAPI TestClient so the full request-path
    runs (safety gate, verification gate, orchestrate, ledger
    composer, audit emitter). The resulting SolveEntry is written
    to the ledger; subsequent ``replay(task_id)`` reads it back.

    Parameters:
      - ``runtime``: the test's per-isolated-runtime directory.
        Used here mainly to confirm the caller has set up the
        env vars Phoenix needs (the actual reading happens via
        the env). Returned :class:`FixtureSolve` doesn't reference
        the path -- it's purely for sanity asserting the runtime
        is the one Phoenix sees.

    Returns:
      :class:`FixtureSolve` carrying the task_id + result fields
      the long-window test will compare against post-replay.
    """
    import time

    from phoenix.api.routes import app

    # Sanity: runtime exists (the calling fixture should have set
    # it up). This catches misconfigured tests that forget to wire
    # the isolated_runtime fixture.
    assert runtime.exists(), f"expected runtime dir to exist: {runtime}"

    solve_unix = time.time()

    with TestClient(app) as client:
        resp = client.post("/v1/tasks", json=_PINNED_QHO_BODY)
        assert resp.status_code == 200, f"fixture solve failed: {resp.text}"
        body = resp.json()
        task_id = body["task_id"]
        ledger_entry_id = body.get("provenance", {}).get("omega_ledger_entry_id")
        assert (
            ledger_entry_id is not None
        ), f"fixture solve missing omega_ledger_entry_id; got: {body}"

    return FixtureSolve(
        task_id=task_id,
        result_value=float(body["value"]),
        error_bar=float(body["error_bar"]),
        sigma=float(body["sigma"]),
        agreement_type=str(body["agreement_type"]),
        omega_ledger_entry_id=str(ledger_entry_id),
        solve_unix_timestamp=solve_unix,
    )


__all__ = ["FixtureSolve", "build_strict_mode_qho_fixture"]
