"""Phoenix safety gate -- 9-stage request validation pipeline.

Per architecture v1 Section 7.4: every Phoenix request flows through
the safety gate before reaching Trinity Core. The gate enforces:

0. Kill switch (refuse-to-start posture per Section 11.5.1 RESOLVED).
1. Actor parsing + signature verification.
2. Actor name shape (lowercase ASCII).
3. Permissions registry lookup.
4. Capability-flag check for the requested action.
5. Rate-limit token-bucket deduction.
6. Frontier-physics authority check (Section 7.4 step 6 -- distinct
   from Phase 2's engine-boundary capability check; this is the
   actor-level "who is allowed to ask?" check).
7. Reproducibility-mode handling (Section 1 Decision 19).
8. Audit-event emission (Phase 7 wires this through
   :mod:`phoenix.audit`; one structured event per verify_request call,
   pass or fail).

Phase 6a wired Stages 0-6 functionally; Phase 7 Step 2 fills in
Stage 8 -- every :func:`verify_request` call now emits a
``safety.gate.<decision>`` audit event regardless of outcome. The
emit is in a ``finally`` block so failure-path exits also emit
before re-raising the typed exception.

Stage 7 (replay-mode session pinning) lands later in Phase 7 (Step 7
-- reproducibility modes).

The gate is a pure-function ``verify_request`` that takes a request
context and either returns successfully (allowing the request to
proceed) or raises a typed :class:`SafetyError` subclass.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from phoenix.audit import AuditEvent, get_emitter
from phoenix.safety.errors import AuthError, PermissionDenied
from phoenix.safety.kill_switch import KillSwitchEngaged
from phoenix.safety.kill_switch import get_store as get_kill_switch_store
from phoenix.safety.permissions import (
    ActorPermissions,
    get_registry,
)
from phoenix.safety.rate_limiter import (
    RateLimitExceeded,
    cost_for,
    cost_for_submit_at_rung,
    get_limiter,
)
from phoenix.trinity.solver.engine import (
    FRONTIER_REGIME_NAMES,
    FrontierPhysicsRefused,
)

if TYPE_CHECKING:
    from actor.actor import Actor

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SafetyDecision:
    """The gate's positive result.

    Returned from :func:`verify_request` when all stages pass. Carries
    the parsed Actor + permissions snapshot so the caller (routes.py)
    doesn't have to re-fetch.

    Phase 6a fields are minimal; Phase 6b extends with audit-event id
    once the state backend audit writes land.
    """

    actor: Actor
    permissions: ActorPermissions
    cost_charged: int


def verify_request(
    actor: Actor,
    *,
    action_key: str,
    requires_capability: str | None = None,
    rung_for_cost: str | None = None,
    requested_regime: str | None = None,
    task_frontier_physics_flag: bool = False,
    request_id: str | None = None,
) -> SafetyDecision:
    """Run the 9-stage pipeline over a request.

    Stage-by-stage:

    0. **Kill switch** -- :func:`KillSwitchStore.assert_disengaged`
       raises :class:`KillSwitchEngaged` if engaged.
    1. **Actor signature** -- the caller (routes.py) already extracted
       and verified the Actor via
       :func:`phoenix.identity.bootstrap.extract_or_bootstrap`. This
       function trusts the parsed Actor; defensive shape check below.
    2. **Actor name shape** -- lowercase ASCII enforced; raises
       :class:`AuthError` on violation.
    3. **Permissions lookup** -- registry returns existing permissions
       or default-for-name.
    4. **Capability flag** -- when ``requires_capability`` is set, the
       named field on the permissions record must be True; raises
       :class:`PermissionDenied`.
    5. **Rate limit** -- ``action_key`` (or ``rung_for_cost``) maps to
       a cost; rate limiter deducts; raises
       :class:`RateLimitExceeded` on insufficient tokens.
    6. **Frontier-physics authority** -- when
       ``requested_regime`` is in :data:`FRONTIER_REGIME_NAMES`, the
       actor's ``frontier_physics`` permission AND the task's
       ``frontier_physics`` flag both must be True. Section 7.4 step 6
       distinct from Phase 2's engine-boundary capability check.
    7. **Reproducibility mode** -- Phase 7 Step 7 fills in.
    8. **Audit event** -- Phase 7 Step 2 (this update): every
       verify_request call emits one ``safety.gate.<decision>``
       :class:`AuditEvent`, pass or fail. Emit is in ``finally`` so
       early-exit failure paths also produce an audit record before
       the typed exception propagates to the caller.

    Returns :class:`SafetyDecision` on success.

    The optional ``request_id`` kwarg is the front-door per-request
    UUID; passed through to the audit event so admins can correlate
    safety-gate decisions with downstream verification-gate events
    and the eventual ledger entry.
    """
    cost: int = 0
    decision_label = "denied.unknown"
    error_detail: str | None = None
    actor_name_safe = _safe_actor_name(actor)
    try:
        # Stage 0: kill switch.
        get_kill_switch_store().assert_disengaged()

        # Stage 1 + 2: Actor shape (signature already verified by caller).
        if not isinstance(actor.name, str) or not actor.name:
            raise AuthError("Actor name must be a non-empty string.")
        if not actor.name.islower() or not actor.name.isascii():
            raise AuthError(
                f"Actor name {actor.name!r} must be lowercase ASCII per Section 7.3.",
            )

        # Stage 3: permissions lookup.
        permissions = get_registry().get(actor.name)

        # Stage 4: capability flag check.
        if requires_capability is not None:
            if not _has_capability(permissions, requires_capability):
                raise PermissionDenied(
                    (f"Actor {actor.name!r} lacks {requires_capability!r} capability."),
                    actor_name=actor.name,
                    missing_capability=requires_capability,
                )

        # Stage 5: rate limit.
        if rung_for_cost is not None:
            cost = cost_for_submit_at_rung(rung_for_cost)
        else:
            cost = cost_for(action_key)
        get_limiter().check_and_consume(
            actor.name,
            tier=permissions.rate_limit_tier,
            cost=cost,
        )

        # Stage 6: frontier-physics authority check (Section 7.4 step 6).
        if requested_regime is not None and requested_regime in FRONTIER_REGIME_NAMES:
            if not permissions.frontier_physics:
                raise FrontierPhysicsRefused(
                    regime_name=requested_regime,
                    reason=(
                        f"Actor {actor.name!r} lacks frontier_physics permission "
                        f"for regime {requested_regime!r}. Section 7.4 step 6 "
                        f"authority check above Phase 2's engine-boundary "
                        f"capability check."
                    ),
                )
            # Defense-in-depth: caller intent must match the grant.
            if not task_frontier_physics_flag:
                raise FrontierPhysicsRefused(
                    regime_name=requested_regime,
                    reason=(
                        f"Actor {actor.name!r} has frontier_physics permission "
                        f"but task.tolerance.frontier_physics=False. Caller "
                        f"intent must match the grant; opt in by setting "
                        f"task.tolerance.frontier_physics=True."
                    ),
                )

        # Stage 7: reproducibility mode dispatch -- Phase 7 Step 7 fills in.
        # Stage 8: audit event -- handled in finally below.
        decision_label = "allowed"
        return SafetyDecision(
            actor=actor,
            permissions=permissions,
            cost_charged=cost,
        )
    except KillSwitchEngaged as exc:
        decision_label = "denied.kill_switch"
        error_detail = str(exc)
        raise
    except AuthError as exc:
        decision_label = "denied.auth"
        error_detail = str(exc)
        raise
    except PermissionDenied as exc:
        decision_label = "denied.permission"
        error_detail = str(exc)
        raise
    except RateLimitExceeded as exc:
        decision_label = "denied.rate_limit"
        error_detail = str(exc)
        raise
    except FrontierPhysicsRefused as exc:
        decision_label = "denied.frontier_physics"
        error_detail = str(exc)
        raise
    finally:
        _emit_safety_audit_event(
            actor_name=actor_name_safe,
            decision_label=decision_label,
            action_key=action_key,
            requires_capability=requires_capability,
            rung_for_cost=rung_for_cost,
            requested_regime=requested_regime,
            cost=cost,
            error_detail=error_detail,
            request_id=request_id,
        )


def _safe_actor_name(actor: Any) -> str:
    """Best-effort actor-name extraction for the audit emit.

    Even when an Actor object is malformed (Stage 1+2 raises AuthError
    on bad shape), the audit emit must still record SOMETHING in the
    ``actor_id`` field. Returns the actual name if it's a non-empty
    string, otherwise ``"unknown"``.
    """
    name = getattr(actor, "name", None)
    if isinstance(name, str) and name:
        return name
    return "unknown"


def _emit_safety_audit_event(
    *,
    actor_name: str,
    decision_label: str,
    action_key: str,
    requires_capability: str | None,
    rung_for_cost: str | None,
    requested_regime: str | None,
    cost: int,
    error_detail: str | None,
    request_id: str | None,
) -> None:
    """Build and emit the ``safety.gate.<decision>`` audit event.

    Wrapped in try/except so a misbehaving audit emitter doesn't take
    down the safety gate's hot path. ``log.exception`` captures the
    failure for ops debugging.
    """
    params: dict[str, Any] = {
        "action_key": action_key,
        "requires_capability": requires_capability,
        "rung_for_cost": rung_for_cost,
        "requested_regime": requested_regime,
        "cost_deducted": cost,
    }
    if error_detail is not None:
        params["error_detail"] = error_detail
    try:
        get_emitter().emit(
            AuditEvent(
                timestamp_unix=time.time(),
                actor_id=actor_name,
                layer="safety_gate",
                event_type=f"safety.gate.{decision_label}",
                parameters=params,
                result_hash="",
                request_id=request_id,
            )
        )
    except Exception:
        _log.exception("Safety gate audit emit failed (decision=%s)", decision_label)


def _has_capability(permissions: ActorPermissions, capability: str) -> bool:
    """Look up a capability flag by name on the permissions record.

    Stable mapping: known capability names map to ActorPermissions
    fields. Unknown names raise :class:`AttributeError` defensively
    (not a SafetyError; a coding bug surfacing).
    """
    return bool(getattr(permissions, capability))
