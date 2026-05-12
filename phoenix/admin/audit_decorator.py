"""Top-priority audit emit for every admin call (Phase 8 Step 1).

Per architecture v1 Section 8.1: every admin call is a top-priority
audit event with the operator's full actor identity. The audit
emit is non-negotiable — even read-only admin calls fire an audit
event so the audit trail tells the full operator-activity story.

:func:`emit_admin_audit` is the helper every admin handler calls
just before returning (success path) or in an exception path
(failure path). Two of the seven mutations
(kill-switch engage/release, HUMAN_REVIEW override) ALSO write
an Omega Ledger entry per architecture §8.4; those handlers call
:func:`compose_and_append_solve_entry`-style helpers separately.

Pattern (success path)::

    @router.get("/v1/admin/governor")
    def governor(...):
        actor, _ = extract_or_bootstrap(authorization)
        verify_request(actor, action_key="admin.governor", ...)
        require_admin(actor)
        body = _governor_snapshot()
        emit_admin_audit(
            actor=actor,
            event_type="admin.governor.read",
            parameters={},
            request_id=request_id,
        )
        return body

Pattern (failure path)::

    try:
        ...
    except AdminError as exc:
        emit_admin_audit(
            actor=actor,
            event_type=f"admin.governor.error.{type(exc).__name__}",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise

The emit is wrapped in a fire-and-forget try/except so a misbehaving
audit emitter cannot take down the admin handler hot path -- matches
the Phase 7 Step 1 contract.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from phoenix.audit import AuditEvent, get_emitter

if TYPE_CHECKING:
    from actor.actor import Actor

_log = logging.getLogger(__name__)


def emit_admin_audit(
    *,
    actor: Actor | None,
    event_type: str,
    parameters: dict[str, Any],
    request_id: str | None = None,
    result_hash: str = "",
) -> None:
    """Emit a top-priority admin audit event.

    The event lands in the ``admin`` audit layer with ``event_type``
    the caller specifies (e.g. ``admin.kill_switch.engaged``,
    ``admin.governor.read``, ``admin.calibration.run.started``).
    The audit layer makes admin events trivially filterable in
    ``/v1/admin/audit/replay`` and in OTel exports.

    ``actor`` may be ``None`` in extreme failure paths where the
    actor couldn't be parsed; the event then records
    ``actor_id="unknown"`` (matches the safety gate's defensive
    fallback for malformed actors).

    Never propagates exceptions: a broken audit emitter falls to
    ``log.exception`` rather than failing the admin handler. The
    audit guarantee is "best effort, never block".
    """
    actor_name = "unknown"
    if actor is not None:
        name = getattr(actor, "name", None)
        if isinstance(name, str) and name:
            actor_name = name

    now = time.time()
    params_copy = dict(parameters)
    try:
        get_emitter().emit(
            AuditEvent(
                timestamp_unix=now,
                actor_id=actor_name,
                layer="admin",
                event_type=event_type,
                parameters=params_copy,
                result_hash=result_hash,
                request_id=request_id,
            )
        )
    except Exception:
        _log.exception(
            "Admin audit emit failed for event_type=%r request_id=%r",
            event_type,
            request_id,
        )
    # Phase 8 mirror to state_backend.audit_events so the admin
    # history endpoints (/v1/admin/audit/replay,
    # /v1/admin/providers/health-history, /v1/admin/calibration/history)
    # surface admin actions. Without this, admin emits go only to
    # JSONL and the SQL-queryable audit log is silent on admin
    # activity. Best-effort: a state-backend failure here must not
    # propagate (Phase 7 §7.1 fire-and-forget contract).
    try:
        from phoenix.state import get_state_backend

        get_state_backend().append_audit_event(
            {
                "timestamp_unix": now,
                "actor_id": actor_name,
                "layer": "admin",
                "event_type": event_type,
                "parameters": params_copy,
                "result_hash": result_hash,
            }
        )
    except Exception:
        _log.exception(
            "Admin audit mirror to state_backend failed for event_type=%r",
            event_type,
        )


__all__ = ["emit_admin_audit"]
