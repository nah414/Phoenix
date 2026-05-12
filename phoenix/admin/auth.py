"""Admin-actor authorization helper (Phase 8 Step 1).

Per architecture v1 Section 8.2: admin endpoints require an actor
with ``is_admin=True`` in its
:class:`~phoenix.safety.permissions.ActorPermissions` record. The
safety gate (Phase 6a Section 7.4) already enforces the standard
actor signature + rate-limit + capability checks; admin endpoints
layer one additional check on top: the actor must be an admin.

:func:`require_admin` is the dependency every admin handler invokes
after :func:`phoenix.safety.gate.verify_request` (the safety-gate
call upstream handles signature, rate limit, kill switch, capability
flags). The routes layer composes:

1. :func:`extract_or_bootstrap` -- parse the Actor header (Phase 6a).
2. :func:`verify_request` -- 9-stage safety gate including
   ``requires_capability`` for the specific admin action when one
   applies (e.g. ``can_override_human_review`` for the override
   endpoint).
3. :func:`require_admin` -- final guard ensuring the actor is an
   admin even when the specific capability check above is None.
4. The admin handler body.

Splitting the admin check from the safety gate's capability checks
keeps the safety gate's contract unchanged. The gate enforces a
single named capability per call; ``is_admin`` is a different
concept (Phase 6a permissions registry calls it
``ActorPermissions.is_admin``, treated as a privilege class rather
than a fine-grained capability). The dedicated admin helper makes
that distinction explicit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from phoenix.admin.errors import AdminPrivilegeRequired
from phoenix.safety.permissions import get_registry

if TYPE_CHECKING:
    from actor.actor import Actor


def require_admin(actor: Actor) -> None:
    """Raise :class:`AdminPrivilegeRequired` unless ``actor`` is admin.

    Looks up the actor's :class:`ActorPermissions` record from the
    singleton registry. The record's ``is_admin`` flag is the
    authoritative check.

    Args:
        actor: The verified :class:`Actor` returned from
            :func:`extract_or_bootstrap`. The safety gate has
            already validated the signature; this function only
            checks the privilege class.

    Raises:
        AdminPrivilegeRequired: when the actor's permissions record
            has ``is_admin=False``.
    """
    permissions = get_registry().get(actor.name)
    if not permissions.is_admin:
        raise AdminPrivilegeRequired(actor_name=actor.name)


__all__ = ["require_admin"]
