"""Phase 7 Step 6 -- verification-gate ledger composer.

The verification gate (:mod:`phoenix.verification.gate`) calls
:func:`compose_and_append_solve_entry` immediately after the
:class:`Result` envelope is built, just before returning. This
function:

1. Composes a :class:`SolveEntry` payload from the four canonical
   provenance sources per architecture §6.7 -- ``VerificationProvenance``
   (rung selection, axis exercise, distance matrix, drift state),
   ``RoutingProvenance`` (provider choice via :class:`OrchestrateProvenance`
   for Phase 7), ``TrinityCoreTrace`` (solver + control sub-traces),
   and the final ``Result`` envelope (value, error_bar, sigma,
   agreement_type).
2. Wraps the payload in a :class:`LedgerEntry` with a fresh UUID4
   ``entry_id`` and the calling actor's ``actor_id``.
3. Calls :meth:`OmegaLedger.append_entry`, which computes the
   ``SHA-256(parent_hash | entry_kind | payload_json)`` hash via the
   vendored primitive and persists via :class:`StateBackend`.
4. Returns the :class:`LedgerLink` so the caller can stamp
   ``omega_ledger_entry_id`` on :class:`ProvenanceTrace`.

**Audit emit:** the composer emits a ``ledger.solve_entry_appended``
audit event with the entry_id + parent_hash + entry_hash so the audit
log + ledger stay correlated. Audit failure does NOT propagate
(fire-and-forget contract).

**Failure handling:** if :class:`StateBackend.append_ledger_entry`
raises (Postgres connection lost, SQLite disk-full), the exception
propagates. The verification gate catches the exception, logs at
ERROR, and returns the :class:`Result` *without* the
``omega_ledger_entry_id`` field populated -- the user still gets
their answer; only the audit trail is degraded. Section 7.1's
"fail-closed on safety, fail-open on observability" guidance.

**Hash composition (Section 6.7):** the canonical JSON of the
:class:`SolveEntry` payload is the input to the hash. That payload
already concatenates VerificationProvenance + RoutingProvenance +
TrinityCoreTrace + Result fields under stable dict keys, so the hash
indirectly covers all four components. Sorting + ASCII-encoding the
JSON is what makes the hash deterministic across Python versions.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import TYPE_CHECKING, Any

from phoenix.ledger.entry_types import (
    LedgerEntry,
    SolveEntry,
    solve_to_ledger_entry,
)

if TYPE_CHECKING:
    from phoenix.ledger.omega_ledger import LedgerLink, OmegaLedger
    from phoenix.trinity.data_model import (
        ControlProvenance,
        OrchestrateProvenance,
        PhysicsTask,
        Result,
        SolverProvenance,
        VerificationProvenance,
    )

logger = logging.getLogger(__name__)


def _result_to_dict(result: Result) -> dict[str, Any]:
    """Serialize the user-visible :class:`Result` envelope.

    Keeps only the audit-relevant fields; the full Result has
    references to KPI bundle + ProvenanceTrace which are composed
    separately on the chain.
    """
    agreement_type_value = (
        result.agreement_type.value
        if hasattr(result.agreement_type, "value")
        else str(result.agreement_type)
    )
    return {
        "value": float(result.value),
        "error_bar": float(result.error_bar),
        "sigma": float(result.sigma),
        "agreement_type": agreement_type_value,
    }


def _verification_provenance_to_dict(prov: VerificationProvenance) -> dict[str, Any]:
    """Serialize :class:`VerificationProvenance` for the ledger payload."""
    return {
        "request_id": prov.request_id,
        "initial_rung": prov.initial_rung,
        "final_rung": prov.final_rung,
        "promotions": prov.promotions,
        "demotions": prov.demotions,
        "drift_state": prov.drift_state,
        "distance_matrix": prov.distance_matrix,
        "wobble_score_sigma": prov.wobble_score_sigma,
        "budget_bound": prov.budget_bound,
        "phase": prov.phase,
    }


def _solver_provenance_to_dict(prov: SolverProvenance | None) -> dict[str, Any]:
    """Serialize :class:`SolverProvenance` (or empty dict when None)."""
    if prov is None:
        return {}
    return {
        "request_id": prov.request_id,
        "dispatched_solver": prov.dispatched_solver,
        "n_grid_low": prov.n_grid_low,
        "n_grid_high": prov.n_grid_high,
        "wall_clock_ms_total": prov.wall_clock_ms_total,
        "phase": prov.phase,
    }


def _control_provenance_to_dict(prov: ControlProvenance | None) -> dict[str, Any]:
    """Serialize :class:`ControlProvenance` (or empty dict when None)."""
    if prov is None:
        return {}
    return {
        "request_id": prov.request_id,
        "dpd_n_blocks": prov.dpd_n_blocks,
        "probe_strengths_used": list(prov.probe_strengths_used),
        "total_backaction": prov.total_backaction,
        "trace_preservation": prov.trace_preservation,
        "positivity_check": prov.positivity_check,
        "wall_clock_ms": prov.wall_clock_ms,
        "phase": prov.phase,
    }


def _orchestrate_provenance_to_dict(prov: OrchestrateProvenance | None) -> dict[str, Any]:
    """Serialize :class:`OrchestrateProvenance` for routing-trace audit."""
    if prov is None:
        return {}
    return {
        "request_id": prov.request_id,
        "provider_id": prov.provider_id,
        "backend_name": prov.backend_name,
        "quantum_technology": prov.quantum_technology,
        "shots_used": prov.shots_used,
        "latency_us": prov.latency_us,
        "bundle_hash": prov.bundle_hash,
        "cloud_shots_recorded": prov.cloud_shots_recorded,
        "wall_clock_ms": prov.wall_clock_ms,
        "phase": prov.phase,
    }


def _compute_result_hash(payload: dict[str, Any]) -> str:
    """Compute a canonical SHA-256 over the SolveEntry-payload dict.

    Used as the ``result_hash`` field on :class:`SolveEntry` and as the
    cross-reference for the replay engine (Phase 7 Step 7 + 8). This
    is distinct from the LedgerEntry's ``entry_hash`` -- the entry_hash
    folds in the parent_hash too, so it changes when the chain reorders.
    The ``result_hash`` is invariant across replays (the deterministic
    portion of the solve produces the same bytes).
    """
    import json as _json

    canonical = _json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _read_vendor_manifest() -> dict[str, Any]:
    """Snapshot the current vendor-substrate manifest for replay verification.

    Reads :func:`phoenix._internal.version.read_vendor_version`. Best-
    effort: any IO error returns an empty dict so the audit path
    never fails on manifest unavailability.
    """
    try:
        from phoenix._internal.version import read_vendor_version

        manifest = read_vendor_version()
        return dict(manifest) if manifest else {}
    except Exception:
        logger.exception("vendor manifest read failed during ledger compose")
        return {}


def compose_and_append_solve_entry(
    *,
    task: PhysicsTask,
    result: Result,
    verification_provenance: VerificationProvenance,
    solver_provenance: SolverProvenance | None,
    control_provenance: ControlProvenance | None,
    orchestrate_provenance: OrchestrateProvenance | None,
    rung_used: str,
    ledger: OmegaLedger | None = None,
) -> LedgerLink:
    """Compose a :class:`SolveEntry` and append it to the chain.

    Pulls the four canonical provenance sources together into the
    typed ``SolveEntry`` payload per architecture §6.7. The
    composed entry is hashed + persisted via
    :meth:`OmegaLedger.append_entry`.

    The ``ledger`` argument is for test injection; production callers
    pass ``None`` and the singleton from :func:`get_ledger` is used.

    Raises any exception the underlying :class:`StateBackend` raises
    -- the verification gate wraps this call in a try/except to keep
    audit-path failures from breaking solve responses.
    """
    if ledger is None:
        from phoenix.ledger.omega_ledger import get_ledger

        ledger = get_ledger()

    # The actor name comes from the PhysicsTask. In production this is
    # always present (the safety gate set it); defensive fallback to
    # "unknown" mirrors the audit emitter's pattern.
    actor_id = "unknown"
    actor = getattr(task, "actor", None)
    if actor is not None:
        name = getattr(actor, "name", None)
        if isinstance(name, str) and name:
            actor_id = name

    # Compose the SolveEntry payload. The four sub-dicts together
    # constitute the §6.7 provenance composition. Each helper produces
    # a JSON-safe dict (no ndarrays, no enums).
    result_dict = _result_to_dict(result)
    verification_dict = _verification_provenance_to_dict(verification_provenance)
    routing_dict = _orchestrate_provenance_to_dict(orchestrate_provenance)
    trinity_dict: dict[str, Any] = {
        "solver": _solver_provenance_to_dict(solver_provenance),
        "control": _control_provenance_to_dict(control_provenance),
    }
    vendor_manifest = _read_vendor_manifest()
    calibration_profile_hash = str(vendor_manifest.get("calibration_profile_hash", ""))

    # The result_hash is computed over just the user-visible Result so
    # replay (Phase 7 Step 8) can compare bit-exactly without the
    # provenance noise.
    result_hash = _compute_result_hash(result_dict)

    solve_payload = SolveEntry(
        task_id=task.request_id,
        request_id=task.request_id,
        rung_used=rung_used,
        agreement_type=result_dict["agreement_type"],
        result_value=result_dict["value"],
        error_bar=result_dict["error_bar"],
        sigma=result_dict["sigma"],
        result_hash=result_hash,
        verification_provenance=verification_dict,
        routing_provenance=routing_dict,
        trinity_core_trace=trinity_dict,
        calibration_profile_hash=calibration_profile_hash,
        vendor_manifest=vendor_manifest,
    )

    ledger_entry: LedgerEntry = solve_to_ledger_entry(
        solve_payload,
        actor_id=actor_id,
        timestamp_unix=time.time(),
    )
    link = ledger.append_entry(ledger_entry)

    # Best-effort audit emit so the durable audit JSONL has a record
    # of the ledger append alongside the chain itself. Never propagate.
    _emit_ledger_audit(
        actor_id=actor_id,
        request_id=task.request_id,
        link=link,
    )

    return link


def _emit_ledger_audit(*, actor_id: str, request_id: str, link: LedgerLink) -> None:
    """Emit a ``ledger.solve_entry_appended`` audit event."""
    try:
        from phoenix.audit import AuditEvent, get_emitter

        get_emitter().emit(
            AuditEvent(
                timestamp_unix=time.time(),
                actor_id=actor_id,
                layer="ledger",
                event_type="ledger.solve_entry_appended",
                parameters={
                    "entry_id": link.entry_id,
                    "parent_hash": link.parent_hash,
                    "entry_hash": link.entry_hash,
                },
                result_hash="",
                request_id=request_id,
            )
        )
    except Exception:
        logger.exception("ledger audit emit failed for request_id=%r", request_id)


__all__ = ["compose_and_append_solve_entry"]
