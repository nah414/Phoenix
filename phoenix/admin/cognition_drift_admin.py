"""Cognition drift baseline admin endpoints (Phase 13.5).

Two endpoints under the standard ``/v1/admin`` prefix:

- ``POST /v1/admin/drift/cognition-baseline/capture`` — computes the
  current :class:`CognitionDriftFeatures` from recent ledger entries
  (via :class:`CognitionFeatureProvider`) and writes it as the
  "known-healthy" baseline for the running Phoenix version (via
  :class:`CognitionDriftBaseline`).
- ``GET /v1/admin/drift/cognition-baseline`` — returns the persisted
  baseline for the running Phoenix version.

Both endpoints gate on:

1. The standard admin auth chain (mirrors
   :mod:`phoenix.admin.encryption_admin`): identity bootstrap +
   :func:`verify_request` + :func:`require_admin`.
2. The dedicated Phase 13.5 permission flag
   :attr:`ActorPermissions.can_capture_drift_baseline`. The dedicated
   flag exists so an org can grant drift-baseline capture without
   granting the full ``is_admin`` privilege bundle.

The baseline file path may be overridden via the
``PHOENIX_COGNITION_DRIFT_BASELINE_PATH`` environment variable; the
default lives at ``~/.phoenix/runtime/cognition_drift_baseline.json``.

**Audit:** Each endpoint emits a per-error-kind audit event under the
``admin.drift.cognition_baseline.<op>`` prefix and an
``admin.drift.cognition_baseline.<op>.success`` event on the happy
path. The audit emit shape matches :mod:`encryption_admin`.
"""

from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import Header, HTTPException, Request

import phoenix
from phoenix.admin.audit_decorator import emit_admin_audit
from phoenix.admin.auth import require_admin
from phoenix.admin.errors import AdminPrivilegeRequired
from phoenix.admin.router import admin_router
from phoenix.identity.bootstrap import IdentityError, extract_or_bootstrap
from phoenix.safety.errors import AuthError, PermissionDenied
from phoenix.safety.gate import verify_request
from phoenix.safety.kill_switch import KillSwitchEngaged
from phoenix.safety.permissions import get_registry as get_permissions_registry
from phoenix.safety.rate_limiter import RateLimitExceeded
from phoenix.state import get_state_backend
from phoenix.verification.cognition_drift_baseline import (
    BaselineSchemaVersionMismatch,
    CognitionDriftBaseline,
)
from phoenix.verification.cognition_drift_features import CognitionFeatureProvider


_EVENT_PREFIX_CAPTURE = "admin.drift.cognition_baseline.capture"
_EVENT_PREFIX_GET = "admin.drift.cognition_baseline.get"

_BASELINE_PATH_ENV = "PHOENIX_COGNITION_DRIFT_BASELINE_PATH"


def _resolve_baseline_path() -> Path | None:
    """Return the override path from the env var, or None for the default."""
    override = os.environ.get(_BASELINE_PATH_ENV)
    if override:
        return Path(override)
    return None


def _admin_authn(
    request: Request,
    authorization: str | None,
    *,
    event_prefix: str,
) -> Any:
    """Standard admin auth chain + Phase 13.5 permission gate.

    Mirrors :mod:`phoenix.admin.encryption_admin._admin_authn`. The
    additional gate at the bottom enforces
    :attr:`ActorPermissions.can_capture_drift_baseline`.
    """
    request_id: str = request.state.request_id

    try:
        actor, _ = extract_or_bootstrap(authorization)
    except IdentityError as exc:
        emit_admin_audit(
            actor=None,
            event_type=f"{event_prefix}.error.identity",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(status_code=401, detail=f"identity error: {exc}") from exc

    try:
        verify_request(
            actor,
            action_key="admin.mutate",
            request_id=request_id,
            skip_kill_switch_check=True,
        )
    except KillSwitchEngaged as exc:
        emit_admin_audit(
            actor=actor,
            event_type=f"{event_prefix}.error.kill_switch",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(status_code=503, detail=f"kill switch engaged: {exc}") from exc
    except AuthError as exc:
        emit_admin_audit(
            actor=actor,
            event_type=f"{event_prefix}.error.auth",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(status_code=401, detail=f"auth error: {exc}") from exc
    except PermissionDenied as exc:
        emit_admin_audit(
            actor=actor,
            event_type=f"{event_prefix}.error.permission",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RateLimitExceeded as exc:
        emit_admin_audit(
            actor=actor,
            event_type=f"{event_prefix}.error.rate_limit",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(int(exc.retry_after_seconds + 1))},
        ) from exc

    try:
        require_admin(actor)
    except AdminPrivilegeRequired as exc:
        emit_admin_audit(
            actor=actor,
            event_type=f"{event_prefix}.error.privilege",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    # Phase 13.5 additional gate: the dedicated drift-baseline permission.
    # Bootstrap actors (adam/ash) get this granted by default; revoke via
    # the grant-prompt-verbatim sibling endpoint family is not currently
    # wired for this flag (admin-tier construction is the only grant
    # path in v1.1).
    perms = get_permissions_registry().get(actor.name)
    if not perms.can_capture_drift_baseline:
        emit_admin_audit(
            actor=actor,
            event_type=f"{event_prefix}.error.permission",
            parameters={"error": "actor lacks can_capture_drift_baseline"},
            request_id=request_id,
        )
        raise HTTPException(
            status_code=403,
            detail=(
                f"actor {actor.name!r} lacks 'can_capture_drift_baseline' "
                f"permission required for this endpoint."
            ),
        )

    return actor


@admin_router.post("/drift/cognition-baseline/capture")
def capture_cognition_drift_baseline(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Compute and persist a fresh cognition drift baseline.

    Effects:

    1. Reads recent cognition ledger entries via
       :class:`CognitionFeatureProvider` (24h rolling window,
       min_sample_size=50).
    2. If insufficient data, returns 409 with ``insufficient cognition
       data`` and emits an error audit event.
    3. On success, writes the snapshot via
       :meth:`CognitionDriftBaseline.write_current` (overwriting any
       prior baseline) and emits a success audit event.

    Status codes:

    - 200: baseline captured; body returns phoenix_version,
      captured_unix, and features_summary.
    - 401: missing / bad actor signature.
    - 403: actor lacks ``is_admin`` OR ``can_capture_drift_baseline``.
    - 409: insufficient cognition data in the rolling window.
    - 429: rate-limited.
    - 503: kill switch engaged.
    """
    request_id: str = request.state.request_id
    actor = _admin_authn(request, authorization, event_prefix=_EVENT_PREFIX_CAPTURE)

    provider = CognitionFeatureProvider(state_backend=get_state_backend())
    features = provider.compute()
    if features is None:
        emit_admin_audit(
            actor=actor,
            event_type=f"{_EVENT_PREFIX_CAPTURE}.error.insufficient_data",
            parameters={"min_sample_size": provider._min_sample_size},
            request_id=request_id,
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "insufficient cognition data in the rolling window to "
                "compute a baseline; capture again once the substrate "
                "has accumulated more entries."
            ),
        )

    baseline = CognitionDriftBaseline(baseline_path=_resolve_baseline_path())
    phoenix_version = phoenix.__version__
    baseline.write_current(features, phoenix_version=phoenix_version)

    features_dict = asdict(features)
    emit_admin_audit(
        actor=actor,
        event_type=f"{_EVENT_PREFIX_CAPTURE}.success",
        parameters={
            "phoenix_version": phoenix_version,
            "baseline_path": str(baseline.baseline_path),
            "sample_size": features.sample_size,
        },
        request_id=request_id,
    )

    # Read the captured_unix back off disk so the response matches the
    # baseline file's recorded timestamp.
    import json as _json

    record = _json.loads(baseline.baseline_path.read_text(encoding="utf-8"))
    return {
        "phoenix_version": phoenix_version,
        "captured_unix": record.get("captured_unix"),
        "features_summary": features_dict,
    }


@admin_router.get("/drift/cognition-baseline")
def get_cognition_drift_baseline(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Return the persisted cognition drift baseline for the running version.

    Status codes:

    - 200: baseline returned (phoenix_version + features dict).
    - 401: missing / bad actor signature.
    - 403: actor lacks ``is_admin`` OR ``can_capture_drift_baseline``.
    - 404: no baseline file for the running Phoenix version.
    - 409: on-disk schema_version mismatches the current code (ops
      must recapture).
    """
    request_id: str = request.state.request_id
    actor = _admin_authn(request, authorization, event_prefix=_EVENT_PREFIX_GET)

    phoenix_version = phoenix.__version__
    baseline = CognitionDriftBaseline(baseline_path=_resolve_baseline_path())

    try:
        features = baseline.read_baseline_for_version(phoenix_version)
    except BaselineSchemaVersionMismatch as exc:
        emit_admin_audit(
            actor=actor,
            event_type=f"{_EVENT_PREFIX_GET}.error.schema_mismatch",
            parameters={
                "on_disk_schema": exc.on_disk,
                "expected_schema": exc.expected,
            },
            request_id=request_id,
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if features is None:
        emit_admin_audit(
            actor=actor,
            event_type=f"{_EVENT_PREFIX_GET}.error.not_found",
            parameters={"phoenix_version": phoenix_version},
            request_id=request_id,
        )
        raise HTTPException(
            status_code=404,
            detail=(
                f"no cognition drift baseline found for phoenix_version "
                f"{phoenix_version!r}; capture one via POST "
                f"/v1/admin/drift/cognition-baseline/capture."
            ),
        )

    emit_admin_audit(
        actor=actor,
        event_type=f"{_EVENT_PREFIX_GET}.success",
        parameters={
            "phoenix_version": phoenix_version,
            "sample_size": features.sample_size,
        },
        request_id=request_id,
    )

    return {
        "phoenix_version": phoenix_version,
        "features": asdict(features),
    }


__all__ = [
    "capture_cognition_drift_baseline",
    "get_cognition_drift_baseline",
]
