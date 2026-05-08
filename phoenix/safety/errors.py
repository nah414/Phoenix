"""Typed errors raised by Phoenix's safety gate.

Per architecture v1 Section 7.4: every safety-gate denial surfaces as
a specific subclass of :class:`SafetyError`. The front door (Step 8)
maps these to HTTP status codes:

- :class:`AuthError` -> 401 Unauthorized.
- :class:`PermissionDenied` -> 403 Forbidden.
- :class:`KillSwitchEngaged` -> 503 (re-exported from
  :mod:`phoenix.safety.kill_switch`).
- :class:`RateLimitExceeded` -> 429 (re-exported from
  :mod:`phoenix.safety.rate_limiter`).
- :class:`FrontierPhysicsRefused` -> 403 (re-exported from
  :mod:`phoenix.trinity.solver.engine`; Phase 2 already defined; the
  safety gate is the Section 7-layer authority check above the Phase 2
  engine-boundary capability check).

Phase 6a ships :class:`AuthError` and :class:`PermissionDenied`; the
others re-export from their owning modules.
"""

from __future__ import annotations


class SafetyError(Exception):
    """Base for all safety-gate denials."""


class AuthError(SafetyError):
    """The Actor's signature couldn't be verified.

    Wraps :class:`phoenix.identity.bootstrap.IdentityError` so the
    safety gate doesn't need to import the identity module's exception
    class. Carries the raw :class:`IdentityError` cause for audit log
    detail.
    """

    def __init__(self, message: str, *, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause


class PermissionDenied(SafetyError):
    """The Actor is authenticated but lacks the required capability.

    Carries the missing capability's name (e.g.
    ``"can_submit_tasks"``, ``"can_replay_tasks"``) and the actor name
    so audit logs and ops dashboards can surface "who tried to do
    what".
    """

    def __init__(
        self,
        message: str,
        *,
        actor_name: str,
        missing_capability: str,
    ) -> None:
        super().__init__(message)
        self.actor_name = actor_name
        self.missing_capability = missing_capability
