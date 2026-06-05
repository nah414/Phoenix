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

import sys
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
    actor_name: str | None = Field(
        default=None,
        description=(
            "Phase 13.x.8: when set, the keypair is written under "
            "'actors/<actor_name>/' (per-actor isolation) instead of the "
            "shared keys dir, and the per-actor encryptor cache is evicted "
            "on success. Must match phoenix.ledger.encryption_actors's "
            "actor-name regex; an invalid/escaping name returns 400."
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

    # Phase 13.x.8: when ``actor_name`` is set, route the keypair under
    # ``actors/<actor_name>/`` and pin the filename slug to ``primary`` so
    # the identity lands at ``actors/<actor_name>/identity.txt`` (the layout
    # ``encryptor_from_actor_default_layout`` + ``list_actors_with_keys``
    # expect). ``rotate_keys_dir=None`` preserves the shared-layout default.
    actor_name = payload.actor_name
    rotate_keys_dir = None  # shared layout (existing behavior)
    if actor_name:
        from phoenix.ledger.encryption_actors import (
            PerActorKeyError,
            _actor_keys_dir,
        )

        try:
            rotate_keys_dir = _actor_keys_dir(actor_name)
        except PerActorKeyError as exc:
            emit_admin_audit(
                actor=actor,
                event_type=f"{_EVENT_PREFIX}.error.actor_name",
                parameters={"actor_name": actor_name, "error": str(exc)},
                request_id=request_id,
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        name = "primary"
    else:
        name = payload.name or _default_name()

    try:
        result = generate_age_keypair(
            keys_dir=rotate_keys_dir,
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
            "actor_name": actor_name,
            "recipient_fingerprint": result.recipient_fingerprint,
            "recipient_path": str(result.recipient_path),
            "identity_path": str(result.identity_path),
            "force": payload.force,
        },
        request_id=request_id,
    )

    # Phase 13.x.8: on a per-actor rotation, evict the cached encryptor so a
    # single-worker daemon picks up the new key without a restart, and tighten
    # the actor key directory to 0o700 on POSIX (keygen chmods the identity
    # *file* 0o600 but leaves the *directory* at the default umask — mirrors
    # the CLI --actor path).
    if actor_name:
        from phoenix.ledger.encryption_actors import (
            reset_prompt_encryptor_for_actor,
        )

        reset_prompt_encryptor_for_actor(actor_name)
        if sys.platform != "win32":
            import os

            os.chmod(_actor_keys_dir(actor_name), 0o700)

    return {
        "identity_path": str(result.identity_path),
        "recipient_path": str(result.recipient_path),
        "identity_fingerprint": result.identity_fingerprint,
        "recipient_fingerprint": result.recipient_fingerprint,
        "next_step": _NEXT_STEP_MESSAGE,
    }


# ---------------------------------------------------------------------------
# Phase 13.x.8: enumeration endpoint.

_ACTORS_EVENT_PREFIX = "admin.encryption.actors"


def _admin_authn_read(
    request: Request,
    authorization: str | None,
) -> Any:
    """Admin auth chain for the read-only enumeration endpoint.

    Mirrors :func:`_admin_authn` (identity → ``verify_request`` →
    ``require_admin``) but DELIBERATELY omits the
    ``can_rotate_encryption_key`` gate: listing actors with per-actor
    keys is a read, so plain ``is_admin`` suffices. The dedicated
    rotate-key permission only guards the mutating rotate endpoint.

    Uses the ``admin.encryption.actors`` audit event prefix so the
    enumeration endpoint's audit trail is distinguishable from the
    rotate endpoint's.
    """
    request_id: str = request.state.request_id

    try:
        actor, _ = extract_or_bootstrap(authorization)
    except IdentityError as exc:
        emit_admin_audit(
            actor=None,
            event_type=f"{_ACTORS_EVENT_PREFIX}.error.identity",
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
            event_type=f"{_ACTORS_EVENT_PREFIX}.error.kill_switch",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(status_code=503, detail=f"kill switch engaged: {exc}") from exc
    except AuthError as exc:
        emit_admin_audit(
            actor=actor,
            event_type=f"{_ACTORS_EVENT_PREFIX}.error.auth",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(status_code=401, detail=f"auth error: {exc}") from exc
    except PermissionDenied as exc:
        emit_admin_audit(
            actor=actor,
            event_type=f"{_ACTORS_EVENT_PREFIX}.error.permission",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RateLimitExceeded as exc:
        emit_admin_audit(
            actor=actor,
            event_type=f"{_ACTORS_EVENT_PREFIX}.error.rate_limit",
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
            event_type=f"{_ACTORS_EVENT_PREFIX}.error.privilege",
            parameters={"error": str(exc)},
            request_id=request_id,
        )
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    return actor


@admin_router.get("/encryption/actors")
def list_encryption_actors(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """List ``actor_name``s that have per-actor encryption keys configured.

    Admin-only (``is_admin`` via :func:`require_admin`). Unlike the
    rotate endpoint, this read does NOT require
    ``can_rotate_encryption_key`` — listing is non-mutating. Phase
    13.x.8.

    Returns ``{"actors": [<name>, ...]}`` (sorted; only actors with a
    valid ``actors/<name>/identity.txt`` on disk).

    Status codes:

    - 200: actor is admin; body returns the actor-name list.
    - 401: missing / bad actor signature.
    - 403: actor lacks ``is_admin``.
    - 429: rate-limited.
    - 503: kill switch engaged.
    """
    request_id: str = request.state.request_id
    actor = _admin_authn_read(request, authorization)

    from phoenix.ledger.encryption_actors import list_actors_with_keys

    actors = list_actors_with_keys()
    emit_admin_audit(
        actor=actor,
        event_type=f"{_ACTORS_EVENT_PREFIX}.success",
        parameters={"count": len(actors)},
        request_id=request_id,
    )
    return {"actors": actors}


__all__ = [
    "RotateKeyPayload",
    "list_encryption_actors",
    "rotate_encryption_key",
]
