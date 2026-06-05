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
from phoenix.admin.router import admin_router

# Phase 13.x.8 step 5 fixup: reuse the house-standard parameterized admin
# auth ladder instead of carrying module-local copies. The canonical helper
# runs identity → verify_request → require_admin → optional capability check;
# its ``action_key`` + ``require_capability`` params let one function serve
# both the read-only enumeration endpoint and the capability-gated rotate
# endpoint. Imported from verification_inspect (the parameterized variant
# 4+ call sites already share).
from phoenix.admin.verification_inspect import _admin_authn
from phoenix.ledger.keygen import (
    KeyGenError,
    KeyGenPathConflict,
    generate_age_keypair,
)


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
    # Auth ladder: identity → verify_request (admin.mutate cost) → require_admin
    # → can_rotate_encryption_key capability gate. The capability check is now
    # folded into verify_request via ``require_capability`` (Phase 13.x.7's
    # dedicated rotate-key permission); ``event_prefix`` preserves the existing
    # ``admin.encryption.rotate.*`` audit-event names.
    actor = _admin_authn(
        request,
        authorization,
        event_prefix=_EVENT_PREFIX,
        action_key="admin.mutate",
        require_capability="can_rotate_encryption_key",
    )

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

            # ``rotate_keys_dir`` already holds ``_actor_keys_dir(actor_name)``
            # (resolved above when actor_name was set) — reuse it instead of
            # re-resolving.
            os.chmod(rotate_keys_dir, 0o700)

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

    Returns ``{"actors": [<name>, ...], "count": <int>}`` (sorted; only
    actors with a valid ``actors/<name>/identity.txt`` on disk).

    Status codes:

    - 200: actor is admin; body returns the actor-name list.
    - 401: missing / bad actor signature.
    - 403: actor lacks ``is_admin``.
    - 429: rate-limited.
    - 503: kill switch engaged.
    """
    request_id: str = request.state.request_id
    # Read-only enumeration: ``admin.read`` cost (1, not the mutate cost 5)
    # and NO ``require_capability`` — plain ``is_admin`` suffices for a
    # non-mutating list. The dedicated ``can_rotate_encryption_key`` gate
    # only guards the mutating rotate endpoint.
    actor = _admin_authn(
        request,
        authorization,
        event_prefix=_ACTORS_EVENT_PREFIX,
        action_key="admin.read",
        require_capability=None,
    )

    from phoenix.ledger.encryption_actors import list_actors_with_keys

    actors = list_actors_with_keys()
    emit_admin_audit(
        actor=actor,
        event_type=f"{_ACTORS_EVENT_PREFIX}.success",
        parameters={"count": len(actors)},
        request_id=request_id,
    )
    return {"actors": actors, "count": len(actors)}


__all__ = [
    "RotateKeyPayload",
    "list_encryption_actors",
    "rotate_encryption_key",
]
