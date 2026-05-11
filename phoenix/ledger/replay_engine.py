"""Phase 7 Step 8 -- replay engine + typed errors.

Per architecture v1 Section 1 Decisions 19-21: a Phoenix solve sealed
under ``reproducibility_mode="strict"`` or ``"replay"`` records the
deterministic environment + the original task spec on its ledger
entry. The replay engine reads that entry back, reconstructs the
:class:`PhysicsTask`, restores the captured
:class:`~phoenix._internal.reproducibility.EnvSnapshot`, re-runs the
deterministic pipeline, computes the new ``result_hash``, and
compares against the recorded value.

**Three terminating outcomes:**

1. **Hashes match** -- the replay verifies the original solve bit-
   exactly. Returns a :class:`ReplayReport` with
   ``hashes_match=True``. Wall-clock + the original entry_id are
   captured for the audit trail.
2. **Hashes diverge** -- raises :class:`ReplayDivergence` carrying
   both the original and the replayed ``result_hash`` plus an
   informational ``divergent_layer`` field (Phase 7 v1 sets this
   to ``"result"`` since we don't yet thread per-layer hashes; v1.x
   refines once Section 2's RunRecord tracks per-layer hashes).
3. **Replay isn't possible** -- raises one of:
   - :class:`LedgerEntryNotFound` if no entry has the requested
     ``task_id`` on its payload.
   - :class:`ReplayEntryIncomplete` if the entry was sealed before
     Phase 7 Step 7 added ``environment_snapshot`` + ``task_spec``,
     or under ``reproducibility_mode="default"`` (no env captured).
   - :class:`ReplayProviderUnavailable` if the entry has
     ``cloud_shots_recorded=True`` and the recorded shots aren't
     retrievable (Phoenix v1 doesn't yet store cloud shots in the
     ledger; this case raises until the cloud-shot retention path
     lands).

**What gets re-run:** the *deterministic* portion of the pipeline --
solver → control → orchestrate (with the local simulator path).
The verification gate's wobble axes + rung selection re-execute
implicitly because we go through :func:`phoenix.trinity.pipeline.solve`
with the original task. The replay solve's per-request context is
marked via
:func:`phoenix.trinity.reproducibility_context.set_replay_active`
so the ledger composer skips the durable append (replay verifies;
it doesn't extend the chain).

**Authorization:** ``POST /v1/tasks/{task_id}/replay`` is gated on
the ``can_replay_tasks`` permission (Phase 6a permissions registry).
The replay engine itself runs in the calling thread's safety
context; permissions are checked at the route layer.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from phoenix._internal.latency import LatencyTier
from phoenix._internal.reproducibility import (
    EnvSnapshot,
    deterministic_environment,
)
from phoenix.ledger.solve_composer import compute_result_hash_for_result
from phoenix.trinity import reproducibility_context
from phoenix.trinity.data_model import PhysicsTask, ToleranceSpec

if TYPE_CHECKING:
    from phoenix.ledger.entry_types import LedgerEntry
    from phoenix.state.backend_protocol import StateBackend

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Typed exceptions.


class ReplayError(Exception):
    """Base class for replay-engine errors.

    Routes catch this and translate to HTTP status codes per Section 5.2
    of the architecture spec.
    """


class LedgerEntryNotFound(ReplayError):
    """No ledger entry exists with the requested ``task_id``.

    Maps to HTTP 404. The task may have completed before Phase 7's
    ledger composition landed, or the task_id may simply be wrong.
    """


class ReplayEntryIncomplete(ReplayError):
    """The found entry lacks fields the replay engine requires.

    Specifically: either ``task_spec`` or ``environment_snapshot`` is
    missing, OR ``reproducibility_mode`` is ``"default"``. Maps to
    HTTP 409 -- replay needs a strict-mode original.
    """


class ReplayProviderUnavailable(ReplayError):
    """The original solve used cloud shots that can't be re-fetched.

    Per architecture Decision 20: cloud shots are recorded ONCE and
    replay reads them from the entry rather than re-running on
    hardware. Phoenix v1's ledger does not yet store cloud-shot
    arrays; cloud-path replays raise this until the retention layer
    ships. Maps to HTTP 409.
    """


class ReplayDivergence(ReplayError):
    """The replay's result_hash differs from the recorded value.

    The original entry's ``result_hash`` and the replay's recomputed
    ``result_hash`` are both attached so the caller can surface them
    in error responses + audit logs. Maps to HTTP 500.
    """

    def __init__(
        self,
        *,
        task_id: str,
        original_entry_id: str,
        original_result_hash: str,
        replayed_result_hash: str,
        divergent_layer: str,
    ) -> None:
        self.task_id = task_id
        self.original_entry_id = original_entry_id
        self.original_result_hash = original_result_hash
        self.replayed_result_hash = replayed_result_hash
        self.divergent_layer = divergent_layer
        super().__init__(
            f"Replay divergence for task_id={task_id!r}: "
            f"original={original_result_hash[:24]}..., "
            f"replayed={replayed_result_hash[:24]}..., "
            f"divergent_layer={divergent_layer!r}"
        )


# ---------------------------------------------------------------------------
# Typed report.


@dataclass(frozen=True)
class ReplayReport:
    """The result returned on successful replay (``hashes_match=True``).

    Fields:

    - ``task_id`` -- the task_id we replayed.
    - ``original_entry_id`` -- ledger entry the replay verified
      against.
    - ``original_result_hash`` -- recorded ``result_hash`` from the
      original solve.
    - ``replayed_result_hash`` -- ``result_hash`` of the just-completed
      replay. Equal to ``original_result_hash`` for a successful
      replay (hashes_match=True).
    - ``hashes_match`` -- always True on the success path. Kept as
      an explicit field so JSON responses make the verification
      result self-documenting.
    - ``divergent_layer`` -- always None on success.
    - ``wall_clock_ms`` -- how long the replay took (informational).
    """

    task_id: str
    original_entry_id: str
    original_result_hash: str
    replayed_result_hash: str
    hashes_match: bool
    divergent_layer: str | None
    wall_clock_ms: float


# ---------------------------------------------------------------------------
# Entry lookup.


def _find_entry_by_task_id(
    task_id: str,
    *,
    state_backend: StateBackend | None = None,
) -> LedgerEntry | None:
    """Linear-scan the ledger for an entry whose payload.task_id matches.

    Phase 7 v1 v1: linear scan via
    :meth:`StateBackend.list_ledger_entries`. Acceptable for hundreds
    of solves per day; v1.x adds a SQL index on
    ``payload_json->>'task_id'`` (Postgres) / a virtual column
    (SQLite) when chain depth justifies it.

    Returns the most recent matching entry (later replays might
    re-record under the same task_id; we use the latest).
    """
    from phoenix.ledger.entry_types import LedgerEntry
    from phoenix.state import get_state_backend

    backend = state_backend if state_backend is not None else get_state_backend()
    rows = backend.list_ledger_entries(since_unix=0.0, limit=1_000_000)
    match: LedgerEntry | None = None
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        if payload.get("task_id") == task_id:
            match = LedgerEntry(
                entry_id=str(row["entry_id"]),
                entry_kind=str(row["entry_kind"]),
                timestamp_unix=float(row["timestamp_unix"]),
                actor_id=str(row["actor_id"]),
                parent_hash=str(row["parent_hash"]),
                entry_hash=str(row["entry_hash"]),
                payload=payload,
            )
    return match


# ---------------------------------------------------------------------------
# Task reconstruction.


def _reconstruct_task_and_actor(
    entry_payload: dict[str, Any],
) -> PhysicsTask:
    """Rebuild a :class:`PhysicsTask` from the SolveEntry payload's
    ``task_spec`` block.

    Raises :class:`ReplayEntryIncomplete` when the spec is missing
    or malformed.

    The Actor is reconstructed via
    :func:`phoenix.identity.bootstrap.mint_bootstrap_actor` using the
    recorded actor name -- replay runs under the bootstrap key, not
    a fresh user signature, since the original signature's 5-minute
    window has long since expired.
    """
    spec = entry_payload.get("task_spec") or {}
    if not spec:
        raise ReplayEntryIncomplete(
            "Ledger entry lacks task_spec; the original solve predates "
            "Phase 7 Step 8 or the field was stripped."
        )

    # Lazy import: synthesis.equations.base is vendor-side and only
    # available after sys.path injection.
    from synthesis.equations.base import PhysicsContext

    pc_dict = spec.get("physics_context") or {}
    tol_dict = spec.get("tolerance") or {}
    if not pc_dict or not tol_dict:
        raise ReplayEntryIncomplete("task_spec missing physics_context or tolerance block.")

    ctx = PhysicsContext(
        mass_kg=float(pc_dict["mass_kg"]),
        length_scale_m=float(pc_dict["length_scale_m"]),
        metadata=dict(pc_dict.get("metadata") or {}),
    )
    latency_tier = LatencyTier(tol_dict.get("latency_tier", "batch_realtime"))
    tolerance = ToleranceSpec(
        max_error_bar=float(tol_dict["max_error_bar"]),
        reproducibility_mode=str(tol_dict.get("reproducibility_mode", "strict")),
        latency_tier=latency_tier,
        frontier_physics=bool(tol_dict.get("frontier_physics", False)),
    )

    actor_name = str(spec.get("actor_name") or "adam")
    from phoenix.identity.bootstrap import mint_bootstrap_actor

    actor = mint_bootstrap_actor(actor_name)

    return PhysicsTask(
        physics_context=ctx,
        tolerance=tolerance,
        actor=actor,
        # Replay uses the same task_id as the original so the result_hash
        # comparison is apples-to-apples (the verification gate stamps
        # task_id into the SolveEntry payload, which feeds the hash).
        request_id=str(entry_payload["task_id"]),
        metadata=dict(spec.get("metadata") or {}),
    )


# ---------------------------------------------------------------------------
# Replay entry point.


def replay(
    task_id: str,
    *,
    state_backend: StateBackend | None = None,
) -> ReplayReport:
    """Replay the solve identified by ``task_id`` and verify the hash.

    Look up the most recent ledger entry whose payload ``task_id``
    matches; reconstruct a :class:`PhysicsTask`; restore the
    captured :class:`EnvSnapshot`; re-run the pipeline with a
    replay-active marker so the composer skips the durable append;
    compute the new ``result_hash``; compare against the recorded
    value.

    Raises:
        LedgerEntryNotFound: no entry with that task_id.
        ReplayEntryIncomplete: entry lacks task_spec or
            environment_snapshot, or was recorded in default mode.
        ReplayProviderUnavailable: entry's
            ``routing_provenance.cloud_shots_recorded`` is True and
            v1 cannot re-fetch cloud shots.
        ReplayDivergence: re-run produced a different result_hash.

    Returns:
        :class:`ReplayReport` with ``hashes_match=True`` on success.
    """
    t_start = time.perf_counter()

    entry = _find_entry_by_task_id(task_id, state_backend=state_backend)
    if entry is None:
        raise LedgerEntryNotFound(
            f"No ledger entry has payload.task_id={task_id!r}; "
            f"the task may not have completed or predates Phase 7."
        )

    payload = entry.payload

    # Validate the entry has what replay needs.
    mode = str(payload.get("reproducibility_mode", "default"))
    if mode == "default":
        raise ReplayEntryIncomplete(
            f"Original solve ran in reproducibility_mode={mode!r}; replay "
            f"requires the original to have used 'strict' or 'replay'."
        )
    snapshot_dict = payload.get("environment_snapshot") or {}
    if not snapshot_dict:
        raise ReplayEntryIncomplete(
            "Ledger entry lacks environment_snapshot; the original solve "
            "predates Phase 7 Step 7 or recorded an empty snapshot."
        )

    # Cloud-shots gate per Decision 20.
    routing = payload.get("routing_provenance") or {}
    if bool(routing.get("cloud_shots_recorded", False)):
        raise ReplayProviderUnavailable(
            "Original solve recorded cloud shots; Phoenix v1's ledger "
            "does not yet retain the shot bytes for replay. Cloud-path "
            "replay support lands with the shot-retention layer (v1.x)."
        )

    original_result_hash = str(payload.get("result_hash") or "")
    if not original_result_hash:
        raise ReplayEntryIncomplete("Ledger entry lacks result_hash; replay cannot verify.")

    # Reconstruct the task + restore the env + re-execute the pipeline.
    task = _reconstruct_task_and_actor(payload)
    snapshot = EnvSnapshot.from_dict(snapshot_dict)

    # Lazy import to avoid a top-level circular: pipeline imports
    # verification.gate which imports phoenix.ledger which imports us.
    from phoenix.trinity.pipeline import solve

    reproducibility_context.set_replay_active(task.request_id)
    try:
        with deterministic_environment(snapshot):
            result = solve(task)
    finally:
        reproducibility_context.clear_replay_active(task.request_id)

    replayed_result_hash = compute_result_hash_for_result(result)
    wall_clock_ms = (time.perf_counter() - t_start) * 1000.0

    if replayed_result_hash != original_result_hash:
        raise ReplayDivergence(
            task_id=task_id,
            original_entry_id=entry.entry_id,
            original_result_hash=original_result_hash,
            replayed_result_hash=replayed_result_hash,
            divergent_layer="result",
        )

    return ReplayReport(
        task_id=task_id,
        original_entry_id=entry.entry_id,
        original_result_hash=original_result_hash,
        replayed_result_hash=replayed_result_hash,
        hashes_match=True,
        divergent_layer=None,
        wall_clock_ms=wall_clock_ms,
    )


__all__ = [
    "LedgerEntryNotFound",
    "ReplayDivergence",
    "ReplayEntryIncomplete",
    "ReplayError",
    "ReplayProviderUnavailable",
    "ReplayReport",
    "replay",
]
