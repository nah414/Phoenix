"""Phase 13 Step 9 — extended ActorPermissions tests.

Verifies the seven new capability flags + their defaults per 13-D2 +
13-D4 (locked 2026-05-18):

- ``can_call_cognition`` default ``True`` (cognition is a first-class
  Phoenix capability for all default-tier actors).
- ``can_call_mcp_server`` default ``False`` (conservative posture).
- ``can_register_mcp_server`` default ``False`` (admin-only grant).
- ``can_store_prompt_verbatim`` default ``False`` (13-D2 privacy floor).
- ``can_store_prompt_encrypted`` default ``False`` (13-D2 privacy floor).
- ``can_store_raw_provider_body`` default ``False`` (P13-1 conservative).
- ``can_receive_token_stream`` default ``True`` (token streams are
  functional, not privacy-bearing).

Plus: bootstrap actors (``adam``, ``ash``) get all-True on every new
flag so the development experience isn't gated on a permissions
dance.
"""

from __future__ import annotations

import phoenix  # noqa: F401  -- triggers sys.path injection


def test_default_actor_permissions_phase13_flags() -> None:
    """Default-tier (non-bootstrap) actor: privacy floors held."""
    from phoenix.safety.permissions import ActorPermissions

    p = ActorPermissions()
    # Functional capabilities — granted by default.
    assert p.can_call_cognition is True
    assert p.can_receive_token_stream is True
    # Conservative defaults — admin must grant.
    assert p.can_call_mcp_server is False
    assert p.can_register_mcp_server is False
    assert p.can_store_prompt_verbatim is False
    assert p.can_store_prompt_encrypted is False
    assert p.can_store_raw_provider_body is False


def test_bootstrap_actors_get_all_phase13_grants() -> None:
    """``adam`` and ``ash`` get every Phase 13 flag granted."""
    from phoenix.safety.permissions import _default_permissions_for

    for name in ("adam", "ash"):
        p = _default_permissions_for(name)
        assert p.can_call_cognition is True
        assert p.can_call_mcp_server is True
        assert p.can_register_mcp_server is True
        assert p.can_store_prompt_verbatim is True
        assert p.can_store_prompt_encrypted is True
        assert p.can_store_raw_provider_body is True
        assert p.can_receive_token_stream is True


def test_unknown_actor_default_permissions_phase13_floor() -> None:
    """Any non-bootstrap actor gets the safe-minimum Phase 13 floor."""
    from phoenix.safety.permissions import _default_permissions_for

    p = _default_permissions_for("alice")
    # Same as the dataclass default.
    assert p.can_store_prompt_verbatim is False
    assert p.can_store_prompt_encrypted is False
    assert p.can_store_raw_provider_body is False
    assert p.can_call_mcp_server is False
    assert p.can_register_mcp_server is False


def test_permissions_round_trip_preserves_phase13_flags(tmp_path) -> None:
    """JSON-file round-trip preserves the new flags (no silent truncation)."""
    from phoenix.safety.permissions import ActorPermissions, PermissionsRegistry

    p = tmp_path / "perms.json"
    reg1 = PermissionsRegistry(path=p)
    reg1.set(
        "alice",
        ActorPermissions(
            can_submit_tasks=True,
            can_call_cognition=True,
            can_store_prompt_verbatim=True,
            can_receive_token_stream=False,
        ),
    )

    # Force a fresh load from disk.
    reg2 = PermissionsRegistry(path=p)
    loaded = reg2.get("alice")
    assert loaded.can_call_cognition is True
    assert loaded.can_store_prompt_verbatim is True
    assert loaded.can_receive_token_stream is False
    # Untouched fields default to ActorPermissions() defaults.
    assert loaded.can_store_prompt_encrypted is False


def test_permissions_dataclass_is_frozen() -> None:
    """ActorPermissions remains immutable after Step 9 extension."""
    import dataclasses

    import pytest

    from phoenix.safety.permissions import ActorPermissions

    p = ActorPermissions()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.can_store_prompt_verbatim = True  # type: ignore[misc]
