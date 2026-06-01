"""``POST /v1/admin/encryption/rotate-key`` admin endpoint (Phase 13.x.7).

Generates a new age keypair via
:func:`phoenix.ledger.keygen.generate_age_keypair`, writes it under
the conventional Phoenix encryption-keys directory, and returns a
summary with paths + fingerprints.

The endpoint is permission-gated TWICE: the standard admin auth chain
enforces ``is_admin`` (defense in depth), then a dedicated check
verifies the actor has ``can_rotate_encryption_key`` set. The
dedicated flag exists so an org can grant rotate-key without granting
the full ``is_admin`` privilege bundle (Phase 13.x.7 Task 3 design).

Daemon-restart is still required to pick up the new recipient (the
running :class:`AgePromptEncryptor` reads keys at startup). The
response body's ``next_step`` field tells the caller this explicitly.

**SAFETY:** The audit emit includes ``recipient_fingerprint`` and
``recipient_path`` but never the identity (secret) contents. Matches
the discipline encoded in :mod:`phoenix.ledger.encryption_age` —
secrets never appear in audit events.
"""

from __future__ import annotations

from typing import Any

from fastapi import Header, HTTPException, Request
from pydantic import BaseModel, Field

from phoenix.admin.audit_decorator import emit_admin_audit
from phoenix.admin.auth import require_admin
from phoenix.admin.errors import AdminPrivilegeRequired
from phoenix.admin.router import admin_router
from phoenix.identity.bootstrap import IdentityError, extract_or_bootstrap
from phoenix.ledger.keygen import (
    KeyGenError,
    KeyGenPathConflict,
    generate_age_keypair,
)
from phoenix.safety.errors import AuthError, PermissionDenied
from phoenix.safety.gate import verify_request
from phoenix.safety.kill_switch import KillSwitchEngaged
from phoenix.safety.permissions import get_registry as get_permissions_registry
from phoenix.safety.rate_limiter import RateLimitExceeded


_EVENT_PREFIX = "admin.encryption.rotate"

_NEXT_STEP_MESSAGE = (
    "Restart the Phoenix daemon to pick up the new recipient. "
    "Existing ENCRYPTED_OPT_IN data remains decryptable with the prior "
    "identity; new encrypts will use both old + new recipients "
    "(lossless rotation per the encryption_age.py multi-recipient design)."
)


class RotateKeyPayload(BaseModel):
    """Body for ``POST /v1/admin/encryption/rotate-key``.

    ``name`` defaults to ``"rotation-<date.today().isoformat()>"`` when
    omitted; ``force=False`` causes a 409 conflict on existing files.
    """

    name: str | None = Field(
        default=None,
        description=(
            "Slug for the new keypair filenames. Defaults to "
            "'rotation-<date.today().isoformat()>'. Must match "
            "phoenix.ledger.keygen's allowed-name regex."
        ),
    )
    force: bool = Field(
        default=False,
        description=(
            "Overwrite an existing identity/recipient at the resolved "
            "path. Default False: a conflict returns 409."
        ),
    )


def _admin_authn(
    request: Request,
    authorization: str | None,
) -> Any:
    """Standard admin auth chain (mirrors :mod:`phoenix.admin.kill_switch`).

    Returns the verified :class:`Actor` on success. Raises the
    appropriate :class:`HTTPException` on failure; the per-error audit
    emit happens here so the routes don't need to repeat it.
    """
    request_id: str = request.state.request_id

    try:
        actor, _ = extract_or_bootstrap(authorization)
    except IdentityError as exc:
        emit_admin_audit(
            actor=None,
            event_type=f"{_EVENT_PREFIX}.error.identity",
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
            event_type=f"{_EVENT_PREFIX}.error.kill_switch",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(status_code=503, detail=f"kill switch engaged: {exc}") from exc
    except AuthError as exc:
        emit_admin_audit(
            actor=actor,
            event_type=f"{_EVENT_PREFIX}.error.auth",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(status_code=401, detail=f"auth error: {exc}") from exc
    except PermissionDenied as exc:
        emit_admin_audit(
            actor=actor,
            event_type=f"{_EVENT_PREFIX}.error.permission",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RateLimitExceeded as exc:
        emit_admin_audit(
            actor=actor,
            event_type=f"{_EVENT_PREFIX}.error.rate_limit",
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
            event_type=f"{_EVENT_PREFIX}.error.privilege",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    # Phase 13.x.7 additional gate: the dedicated rotate-key permission.
    # Bootstrap actors get this granted by default; revoke it via the
    # grant-prompt-verbatim sibling endpoint family.
    perms = get_permissions_registry().get(actor.name)
    if not perms.can_rotate_encryption_key:
        emit_admin_audit(
            actor=actor,
            event_type=f"{_EVENT_PREFIX}.error.permission",
            parameters={"error": "actor lacks can_rotate_encryption_key"},
            request_id=request_id,
        )
        raise HTTPException(
            status_code=403,
            detail=(
                f"actor {actor.name!r} lacks 'can_rotate_encryption_key' "
                f"permission required for this endpoint."
            ),
        )

    return actor


def _default_name() -> str:
    """Default keypair slug -- ``rotation-<today.iso>``.

    Factored so the test suite doesn't have to bind to a fixed name and
    so the operator-facing CLI can share the convention later.
    """
    from datetime import date

    return f"rotation-{date.today().isoformat()}"


@admin_router.post("/encryption/rotate-key")
def rotate_encryption_key(
    payload: RotateKeyPayload,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Generate a fresh age keypair for lossless multi-recipient rotation.

    Effects:

    1. Generates a new X25519 keypair via
       :func:`phoenix.ledger.keygen.generate_age_keypair` (lazy-imports
       ``pyrage``; the keypair lands in the configured
       ``PHOENIX_ENCRYPTION_KEYS_DIR``).
    2. Emits ``admin.encryption.rotate.success`` audit event with the
       recipient fingerprint + path (never the secret).

    Status codes:

    - 200: keypair generated; body returns paths + fingerprints +
      ``next_step``.
    - 401: missing / bad actor signature.
    - 403: actor lacks ``is_admin`` OR ``can_rotate_encryption_key``.
    - 409: target path already exists and ``force=False``.
    - 429: rate-limited.
    - 500: keygen failed (filesystem error, ``pyrage`` failure).
    - 503: kill switch engaged.

    Daemon restart is required for the running encryptor to pick up the
    new recipient; the response body says so explicitly.
    """
    request_id: str = request.state.request_id
    actor = _admin_authn(request, authorization)

    name = payload.name or _default_name()

    try:
        result = generate_age_keypair(
            keys_dir=None,
            name=name,
            force=payload.force,
        )
    except KeyGenPathConflict as exc:
        emit_admin_audit(
            actor=actor,
            event_type=f"{_EVENT_PREFIX}.error.conflict",
            parameters={
                "name": name,
                "existing_path": str(exc.existing_path),
            },
            request_id=request_id,
        )
        raise HTTPException(
            status_code=409,
            detail=(
                f"keygen path conflict at {exc.existing_path}: pass "
                f"force=true or choose a different name."
            ),
        ) from exc
    except KeyGenError as exc:
        emit_admin_audit(
            actor=actor,
            event_type=f"{_EVENT_PREFIX}.error.keygen",
            parameters={"name": name, "error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(
            status_code=500,
            detail=f"keygen failed: {exc}",
        ) from exc
    except ImportError as exc:
        emit_admin_audit(
            actor=actor,
            event_type=f"{_EVENT_PREFIX}.error.pyrage_missing",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc

    emit_admin_audit(
        actor=actor,
        event_type=f"{_EVENT_PREFIX}.success",
        parameters={
            "name": name,
            "recipient_fingerprint": result.recipient_fingerprint,
            "recipient_path": str(result.recipient_path),
            "identity_path": str(result.identity_path),
            "force": payload.force,
        },
        request_id=request_id,
    )

    return {
        "identity_path": str(result.identity_path),
        "recipient_path": str(result.recipient_path),
        "identity_fingerprint": result.identity_fingerprint,
        "recipient_fingerprint": result.recipient_fingerprint,
        "next_step": _NEXT_STEP_MESSAGE,
    }


__all__ = ["RotateKeyPayload", "rotate_encryption_key"]
