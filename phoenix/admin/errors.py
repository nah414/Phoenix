"""Typed errors for the admin dev-ops backdoor (Phase 8 Step 1).

Per architecture v1 Section 8.7: admin endpoints raise these typed
exceptions; the routes layer maps each to an HTTP status code.
Centralizing them here keeps the admin handlers focused on business
logic and the route translation layer easy to audit.

HTTP status code mapping (per Section 8.7):

- :class:`AdminPrivilegeRequired` → 403 (actor lacks ``is_admin``).
- :class:`QuarantineDurationExceeded` → 400 (manual quarantine
  duration exceeds the 24-hour policy cap).
- :class:`TaskNotPendingReview` → 409 (override target task is not in
  the pending-review queue).
- :class:`CalibrationRunInProgress` → 409 (force-cycle requested while
  another cycle is already running).
- :class:`AdapterNotLoaded` → 404 (force-revalidate target adapter is
  not currently loaded).

All admin errors inherit from :class:`AdminError` so a single
``except AdminError`` in the routes layer catches the whole family.
"""

from __future__ import annotations


class AdminError(Exception):
    """Base class for admin-endpoint errors.

    Phase 8 routes catch this family and translate to HTTP status codes
    per the mapping table in this module's docstring.
    """


class AdminPrivilegeRequired(AdminError):
    """Actor lacks the ``is_admin`` flag on its
    :class:`~phoenix.safety.permissions.ActorPermissions` record.

    Raised by :func:`require_admin` before any admin handler runs.
    Maps to HTTP 403.
    """

    def __init__(self, actor_name: str) -> None:
        self.actor_name = actor_name
        super().__init__(
            f"Actor {actor_name!r} lacks is_admin; admin endpoints "
            f"require an actor with the admin flag set in the "
            f"permissions registry."
        )


class QuarantineDurationExceeded(AdminError):
    """Manual-quarantine ``duration_seconds`` exceeds policy cap.

    Phase 8 Step 7's `POST /v1/admin/providers/{id}/manual-quarantine`
    caps duration at 86400 seconds (24 hours) per the architecture
    spec's policy default. Maps to HTTP 400.
    """

    def __init__(self, *, requested_seconds: int, cap_seconds: int) -> None:
        self.requested_seconds = requested_seconds
        self.cap_seconds = cap_seconds
        super().__init__(
            f"Manual quarantine duration {requested_seconds}s exceeds the "
            f"{cap_seconds}s policy cap (24h)."
        )


class TaskNotPendingReview(AdminError):
    """Override target task is not in the pending-review queue.

    Either the task_id doesn't exist, the task isn't a HUMAN_REVIEW
    classification, or it was already resolved by a prior override.
    Maps to HTTP 409.
    """

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(
            f"Task {task_id!r} is not in the pending-review queue; "
            f"either it doesn't exist, isn't HUMAN_REVIEW, or has "
            f"already been resolved."
        )


class CalibrationRunInProgress(AdminError):
    """A drift cycle is already running when force-cycle was requested.

    Phase 8 Step 4's `POST /v1/admin/calibration/run` raises this when
    the :class:`DriftDetector` already has an in-flight cycle. Maps to
    HTTP 409.
    """


class AdapterNotLoaded(AdminError):
    """Force-revalidate target adapter is not currently loaded.

    Phase 9 wires the real handler; the Phase 8 stub returns 501
    before reaching this error path. Kept defined here so the Phase 9
    handler can use it without a re-import. Maps to HTTP 404.
    """

    def __init__(self, adapter_id: str) -> None:
        self.adapter_id = adapter_id
        super().__init__(
            f"Adapter {adapter_id!r} is not currently loaded; "
            f"force-revalidate requires an already-loaded adapter."
        )


__all__ = [
    "AdapterNotLoaded",
    "AdminError",
    "AdminPrivilegeRequired",
    "CalibrationRunInProgress",
    "QuarantineDurationExceeded",
    "TaskNotPendingReview",
]
