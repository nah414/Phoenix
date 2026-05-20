"""Phase 13 Step 9 — safety gate stage 6b (cognition routing) tests.

Verifies the new ``task_kind="cognition"`` branch in
:func:`phoenix.safety.gate.verify_request`:

- Cognition tasks route through stage 6b (cognition capability checks)
  instead of stage 6 (frontier-physics authority check).
- Each cognition capability gate raises :class:`PermissionDenied`
  with the right ``missing_capability`` name.
- An actor with all cognition grants passes a cognition task; the
  same actor with no grants still passes a physics task (the two
  paths are independent).
- ``task_kind`` defaults to ``"physics"`` so existing callers are
  unaffected (regression check).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import phoenix  # noqa: F401  -- triggers sys.path injection


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Reset the permissions + audit + rate-limiter singletons per test."""
    monkeypatch.setenv("PHOENIX_KEYSTORE_DIR", str(tmp_path))

    from phoenix.audit import reset_emitter
    from phoenix.safety import permissions as permissions_module
    from phoenix.safety.kill_switch import KillSwitchStore
    from phoenix.safety.kill_switch import get_store as get_kill_switch_store
    from phoenix.safety.rate_limiter import get_limiter

    reset_emitter()
    permissions_module._REGISTRY = None
    # Reset kill switch via the public store contract.
    store = get_kill_switch_store()
    assert isinstance(store, KillSwitchStore)
    get_limiter().reset_all()
    try:
        yield tmp_path
    finally:
        reset_emitter()
        permissions_module._REGISTRY = None
        get_limiter().reset_all()


def _make_actor(name: str):
    from phoenix.identity.bootstrap import mint_bootstrap_actor

    return mint_bootstrap_actor(name)


# ---------------------------------------------------------------------------
# Default routing — task_kind defaults to "physics" (back-compat)


def test_task_kind_defaults_to_physics_for_back_compat(isolated_registry: Path) -> None:
    """A call without task_kind passes the physics path (Stage 6)."""
    from phoenix.safety.gate import verify_request

    actor = _make_actor("adam")
    decision = verify_request(actor, action_key="task_submit")
    assert decision.actor.name == "adam"


# ---------------------------------------------------------------------------
# Cognition routing — stage 6b


def test_cognition_task_with_bootstrap_actor_passes(isolated_registry: Path) -> None:
    """Bootstrap actor has all cognition grants → cognition task passes."""
    from phoenix.safety.gate import verify_request

    actor = _make_actor("adam")
    decision = verify_request(
        actor,
        action_key="task_submit",
        task_kind="cognition",
        cognition_prompt_disposition="HASH_ONLY",
    )
    assert decision.actor.name == "adam"


def test_cognition_skips_frontier_physics_check(isolated_registry: Path) -> None:
    """Cognition tasks DON'T trigger the frontier_physics regime check
    even when requested_regime is in FRONTIER_REGIME_NAMES.

    Why this matters: if an admin sets up a cognition task that
    happens to share a code path with frontier physics, the
    frontier-physics check shouldn't fire on the cognition path.
    """
    from phoenix.safety.gate import verify_request
    from phoenix.safety.permissions import ActorPermissions, get_registry

    # Alice has NO frontier_physics permission AND no cognition flags
    # cleared (she gets ActorPermissions() defaults: can_call_cognition=True).
    get_registry().set("alice", ActorPermissions())
    actor = _make_actor("alice")

    # A cognition task with "frontier" regime arg passes because stage 6b
    # routing bypasses the frontier-physics check.
    decision = verify_request(
        actor,
        action_key="task_submit",
        task_kind="cognition",
        requested_regime="WHEELER_DEWITT",  # would fail in physics path
        cognition_prompt_disposition="HASH_ONLY",
    )
    assert decision.actor.name == "alice"


def test_cognition_task_without_can_call_cognition_denied(isolated_registry: Path) -> None:
    """Actor with can_call_cognition=False is rejected at stage 6b."""
    import pytest

    from phoenix.safety.errors import PermissionDenied
    from phoenix.safety.gate import verify_request
    from phoenix.safety.permissions import ActorPermissions, get_registry

    get_registry().set("alice", ActorPermissions(can_call_cognition=False))
    actor = _make_actor("alice")

    with pytest.raises(PermissionDenied) as exc_info:
        verify_request(
            actor,
            action_key="task_submit",
            task_kind="cognition",
        )
    assert exc_info.value.missing_capability == "can_call_cognition"


def test_verbatim_disposition_requires_grant(isolated_registry: Path) -> None:
    """Cognition with prompt_disposition=VERBATIM requires the grant."""
    import pytest

    from phoenix.safety.errors import PermissionDenied
    from phoenix.safety.gate import verify_request
    from phoenix.safety.permissions import ActorPermissions, get_registry

    # Alice can call cognition but lacks verbatim grant.
    get_registry().set(
        "alice",
        ActorPermissions(can_call_cognition=True, can_store_prompt_verbatim=False),
    )
    actor = _make_actor("alice")

    with pytest.raises(PermissionDenied) as exc_info:
        verify_request(
            actor,
            action_key="task_submit",
            task_kind="cognition",
            cognition_prompt_disposition="VERBATIM",
        )
    assert exc_info.value.missing_capability == "can_store_prompt_verbatim"


def test_verbatim_disposition_passes_with_grant(isolated_registry: Path) -> None:
    """The same actor with the grant passes."""
    from phoenix.safety.gate import verify_request
    from phoenix.safety.permissions import ActorPermissions, get_registry

    get_registry().set(
        "alice",
        ActorPermissions(can_call_cognition=True, can_store_prompt_verbatim=True),
    )
    actor = _make_actor("alice")
    decision = verify_request(
        actor,
        action_key="task_submit",
        task_kind="cognition",
        cognition_prompt_disposition="VERBATIM",
    )
    assert decision.actor.name == "alice"


def test_encrypted_disposition_requires_grant(isolated_registry: Path) -> None:
    import pytest

    from phoenix.safety.errors import PermissionDenied
    from phoenix.safety.gate import verify_request
    from phoenix.safety.permissions import ActorPermissions, get_registry

    get_registry().set("alice", ActorPermissions(can_call_cognition=True))
    actor = _make_actor("alice")
    with pytest.raises(PermissionDenied) as exc_info:
        verify_request(
            actor,
            action_key="task_submit",
            task_kind="cognition",
            cognition_prompt_disposition="ENCRYPTED_OPT_IN",
        )
    assert exc_info.value.missing_capability == "can_store_prompt_encrypted"


def test_raw_provider_body_requires_grant(isolated_registry: Path) -> None:
    import pytest

    from phoenix.safety.errors import PermissionDenied
    from phoenix.safety.gate import verify_request
    from phoenix.safety.permissions import ActorPermissions, get_registry

    get_registry().set("alice", ActorPermissions(can_call_cognition=True))
    actor = _make_actor("alice")
    with pytest.raises(PermissionDenied) as exc_info:
        verify_request(
            actor,
            action_key="task_submit",
            task_kind="cognition",
            cognition_uses_raw_body=True,
        )
    assert exc_info.value.missing_capability == "can_store_raw_provider_body"


def test_token_stream_requires_grant_when_revoked(isolated_registry: Path) -> None:
    """can_receive_token_stream defaults True; an admin can revoke it."""
    import pytest

    from phoenix.safety.errors import PermissionDenied
    from phoenix.safety.gate import verify_request
    from phoenix.safety.permissions import ActorPermissions, get_registry

    get_registry().set(
        "alice",
        ActorPermissions(can_call_cognition=True, can_receive_token_stream=False),
    )
    actor = _make_actor("alice")
    with pytest.raises(PermissionDenied) as exc_info:
        verify_request(
            actor,
            action_key="task_submit",
            task_kind="cognition",
            cognition_uses_token_stream=True,
        )
    assert exc_info.value.missing_capability == "can_receive_token_stream"


def test_mcp_call_requires_grant(isolated_registry: Path) -> None:
    """A cognition task that lists MCP tools requires can_call_mcp_server."""
    import pytest

    from phoenix.safety.errors import PermissionDenied
    from phoenix.safety.gate import verify_request
    from phoenix.safety.permissions import ActorPermissions, get_registry

    get_registry().set("alice", ActorPermissions(can_call_cognition=True))
    actor = _make_actor("alice")
    with pytest.raises(PermissionDenied) as exc_info:
        verify_request(
            actor,
            action_key="task_submit",
            task_kind="cognition",
            cognition_uses_mcp=True,
        )
    assert exc_info.value.missing_capability == "can_call_mcp_server"


def test_hash_only_disposition_no_grant_needed(isolated_registry: Path) -> None:
    """HASH_ONLY is permission-free (13-D2 default)."""
    from phoenix.safety.gate import verify_request
    from phoenix.safety.permissions import ActorPermissions, get_registry

    get_registry().set(
        "alice",
        ActorPermissions(
            can_call_cognition=True,
            can_store_prompt_verbatim=False,
            can_store_prompt_encrypted=False,
        ),
    )
    actor = _make_actor("alice")
    decision = verify_request(
        actor,
        action_key="task_submit",
        task_kind="cognition",
        cognition_prompt_disposition="HASH_ONLY",
    )
    assert decision.actor.name == "alice"


# ---------------------------------------------------------------------------
# Physics path still works as before (regression check)


def test_physics_path_still_checks_frontier_physics(isolated_registry: Path) -> None:
    """A physics task with frontier regime + no frontier_physics permission → 403."""
    import pytest

    from phoenix.safety.errors import PermissionDenied  # noqa: F401
    from phoenix.safety.gate import verify_request
    from phoenix.safety.permissions import ActorPermissions, get_registry
    from phoenix.trinity.solver.engine import FrontierPhysicsRefused

    get_registry().set("alice", ActorPermissions(frontier_physics=False))
    actor = _make_actor("alice")
    with pytest.raises(FrontierPhysicsRefused):
        verify_request(
            actor,
            action_key="task_submit",
            requested_regime="WHEELER_DEWITT",
            task_frontier_physics_flag=True,
            # task_kind defaults to "physics".
        )
