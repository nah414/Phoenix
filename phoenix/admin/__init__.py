"""Phoenix admin dev-ops backdoor (Phase 8).

Per architecture v1 Section 8: the privileged surface for inspection,
diagnostics, and the seven explicit manual interventions (kill
switch engage/release, manual provider quarantine/restore,
HUMAN_REVIEW override, calibration force-cycle, adapter
force-revalidate). All routes live under ``/v1/admin/...`` and
require ``is_admin=True`` on the actor's permissions record.

Phase 8 Step 1 ships the scaffolding (auth, audit, errors,
APIRouter aggregator); Steps 2-9 fill in the per-subsystem handlers.
"""

from phoenix.admin.audit_decorator import emit_admin_audit
from phoenix.admin.auth import require_admin
from phoenix.admin.errors import (
    AdapterNotLoaded,
    AdminError,
    AdminPrivilegeRequired,
    CalibrationRunInProgress,
    QuarantineDurationExceeded,
    TaskNotPendingReview,
)
from phoenix.admin.router import admin_router

# Side-effect imports: each module registers its handlers onto
# admin_router via the @admin_router.<verb>(...) decorator. Importing
# them here ensures the routes are live the moment the parent
# FastAPI app calls include_router(admin_router) (Phase 8 Step 1).
from phoenix.admin import calibration as _calibration  # noqa: F401
from phoenix.admin import health as _health  # noqa: F401
from phoenix.admin import kill_switch as _kill_switch  # noqa: F401
from phoenix.admin import router_inspect as _router_inspect  # noqa: F401
from phoenix.admin import verification_inspect as _verification_inspect  # noqa: F401

__all__ = [
    "AdapterNotLoaded",
    "AdminError",
    "AdminPrivilegeRequired",
    "CalibrationRunInProgress",
    "QuarantineDurationExceeded",
    "TaskNotPendingReview",
    "admin_router",
    "emit_admin_audit",
    "require_admin",
]
