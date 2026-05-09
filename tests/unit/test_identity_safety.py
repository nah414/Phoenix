"""Phase 6a Steps 1-2-3-4-5 -- identity + permissions + rate limiter +
kill switch + safety gate.

Combined into one test file for tight coverage of the small modules.
"""

from __future__ import annotations

import phoenix  # noqa: F401  -- triggers sys.path injection for vendored modules


# ----- Identity -----


def test_keystore_round_trip() -> None:
    """First call generates; second call loads same key."""
    from phoenix.identity.keystore import (
        get_install_fingerprint,
        load_or_generate_master_key,
    )

    key1 = load_or_generate_master_key()
    key2 = load_or_generate_master_key()
    assert key1 == key2
    assert len(key1) == 32

    fp1 = get_install_fingerprint()
    fp2 = get_install_fingerprint()
    assert fp1 == fp2
    assert len(fp1) == 32  # 32 hex chars = 16 bytes truncated


def test_mint_bootstrap_actor_signs_validly() -> None:
    from phoenix.identity.bootstrap import BOOTSTRAP_ACTOR_NAME, mint_bootstrap_actor

    actor = mint_bootstrap_actor()
    assert actor.name == BOOTSTRAP_ACTOR_NAME
    assert actor.is_valid_now()


def test_extract_or_bootstrap_with_header_round_trip() -> None:
    """Sign -> serialize -> header -> parse -> matches original."""
    import base64
    import json

    from phoenix.identity.bootstrap import extract_or_bootstrap, mint_bootstrap_actor

    original = mint_bootstrap_actor()
    payload = original.to_payload()
    header = "Phoenix-Actor " + base64.b64encode(json.dumps(payload).encode()).decode("ascii")
    parsed, was_bootstrapped = extract_or_bootstrap(header)
    assert was_bootstrapped is False
    assert parsed.name == original.name
    assert parsed.signature == original.signature


def test_extract_or_bootstrap_without_header_mints() -> None:
    from phoenix.identity.bootstrap import extract_or_bootstrap

    actor, was_bootstrapped = extract_or_bootstrap(None)
    assert was_bootstrapped is True
    assert actor.name == "adam"


def test_extract_actor_rejects_bad_header() -> None:
    import pytest

    from phoenix.identity.bootstrap import IdentityError, extract_actor_from_header

    with pytest.raises(IdentityError):
        extract_actor_from_header("Bearer not-a-phoenix-actor-header")
    with pytest.raises(IdentityError):
        extract_actor_from_header("Phoenix-Actor not!base64!@#")


# ----- Permissions registry -----


def test_actor_permissions_default_for_bootstrap() -> None:
    from phoenix.safety.permissions import _default_permissions_for

    adam = _default_permissions_for("adam")
    assert adam.is_admin is True
    assert adam.frontier_physics is True
    assert adam.rate_limit_tier == "admin"

    unknown = _default_permissions_for("alice")
    assert unknown.is_admin is False
    assert unknown.frontier_physics is False
    assert unknown.rate_limit_tier == "default"


def test_permissions_registry_round_trip(tmp_path) -> None:
    from phoenix.safety.permissions import ActorPermissions, PermissionsRegistry

    p = tmp_path / "perms.json"
    reg1 = PermissionsRegistry(path=p)
    reg1.set(
        "alice",
        ActorPermissions(can_submit_tasks=True, frontier_physics=True, rate_limit_tier="elevated"),
    )
    # Fresh instance reads from same file.
    reg2 = PermissionsRegistry(path=p)
    alice = reg2.get("alice")
    assert alice.frontier_physics is True
    assert alice.rate_limit_tier == "elevated"


def test_permissions_invalid_tier_raises(tmp_path) -> None:
    import pytest

    from phoenix.safety.permissions import ActorPermissions, PermissionsRegistry

    reg = PermissionsRegistry(path=tmp_path / "perms.json")
    with pytest.raises(ValueError):
        reg.set("alice", ActorPermissions(rate_limit_tier="warp"))


# ----- Rate limiter -----


def test_rate_limiter_default_tier_capacity() -> None:
    """Default tier has capacity 100; 21 cost-5 requests exhaust it."""
    import pytest

    from phoenix.safety.rate_limiter import RateLimitExceeded, RateLimiter

    limiter = RateLimiter()
    for _ in range(20):
        limiter.check_and_consume("alice", tier="default", cost=5)
    with pytest.raises(RateLimitExceeded):
        limiter.check_and_consume("alice", tier="default", cost=5)


def test_rate_limiter_admin_unlimited() -> None:
    """Admin tier never blocks."""
    from phoenix.safety.rate_limiter import RateLimiter

    limiter = RateLimiter()
    for _ in range(100):
        limiter.check_and_consume("adam", tier="admin", cost=25)


def test_rate_limiter_zero_cost_free() -> None:
    """Cost 0 endpoints (health probe) never touch the bucket."""
    from phoenix.safety.rate_limiter import RateLimiter

    limiter = RateLimiter()
    for _ in range(10000):
        limiter.check_and_consume("alice", tier="default", cost=0)


# ----- Kill switch -----


def test_kill_switch_engage_release(tmp_path) -> None:
    import pytest

    from phoenix.safety.kill_switch import KillSwitchEngaged, KillSwitchStore

    store = KillSwitchStore(path=tmp_path / "ks.json")
    store.assert_disengaged()  # disengaged by default

    store.engage(by="ops_test", reason="unit test")
    with pytest.raises(KillSwitchEngaged) as exc_info:
        store.assert_disengaged()
    assert exc_info.value.engaged_by == "ops_test"

    store.release()
    store.assert_disengaged()  # released; no raise


def test_kill_switch_persists_across_instance(tmp_path) -> None:
    """Refuse-to-start posture: new KillSwitchStore on same file sees engaged state."""
    import pytest

    from phoenix.safety.kill_switch import KillSwitchEngaged, KillSwitchStore

    p = tmp_path / "ks.json"
    s1 = KillSwitchStore(path=p)
    s1.engage(by="ops", reason="persist test")

    s2 = KillSwitchStore(path=p)
    with pytest.raises(KillSwitchEngaged):
        s2.assert_disengaged()


# ----- Safety gate -----


def _reset_global_state() -> None:
    """Tests share module-level singletons; reset between cases."""
    from phoenix.safety.kill_switch import get_store as get_ks_store
    from phoenix.safety.rate_limiter import get_limiter

    get_limiter().reset_all()
    get_ks_store().release()


def test_gate_admin_tier_passes_high_cost() -> None:
    """Admin tier (adam) passes R5 cost (25) without rate limit hit."""
    from phoenix.identity.bootstrap import mint_bootstrap_actor
    from phoenix.safety.gate import verify_request

    _reset_global_state()
    adam = mint_bootstrap_actor()
    decision = verify_request(
        adam,
        action_key="tasks_submit_r5",
        requires_capability="can_submit_tasks",
        rung_for_cost="R5_REPLICATED",
    )
    assert decision.cost_charged == 25
    assert decision.permissions.is_admin is True


def test_gate_default_tier_actor_capability_denied() -> None:
    """Non-bootstrap actor lacks can_load_adapter -> PermissionDenied."""
    import pytest

    from phoenix.identity.bootstrap import mint_bootstrap_actor
    from phoenix.safety.errors import PermissionDenied
    from phoenix.safety.gate import verify_request

    _reset_global_state()
    # Mint as 'alice' (not adam/ash) so default tier kicks in.
    alice = mint_bootstrap_actor("alice")
    with pytest.raises(PermissionDenied) as exc_info:
        verify_request(
            alice,
            action_key="adapters_post",
            requires_capability="can_load_adapter",
        )
    assert exc_info.value.missing_capability == "can_load_adapter"


def test_gate_kill_switch_blocks_everything(tmp_path, monkeypatch) -> None:
    """Stage 0 kill-switch check fires before any other stage."""
    import pytest

    from phoenix.identity.bootstrap import mint_bootstrap_actor
    from phoenix.safety import kill_switch as ks_module
    from phoenix.safety.gate import verify_request
    from phoenix.safety.kill_switch import KillSwitchEngaged, KillSwitchStore

    _reset_global_state()
    # Swap the singleton store to a tmp-path one so we don't touch the
    # user's real ~/.phoenix/runtime/kill_switch.json.
    store = KillSwitchStore(path=tmp_path / "ks.json")
    store.engage(by="test", reason="gate kill-switch test")
    monkeypatch.setattr(ks_module, "_STORE", store)

    adam = mint_bootstrap_actor()
    with pytest.raises(KillSwitchEngaged):
        verify_request(adam, action_key="tasks_submit", requires_capability="can_submit_tasks")
    store.release()


def test_gate_uppercase_actor_name_rejected() -> None:
    """Section 7.3 enforces lowercase ASCII actor names."""
    import pytest

    from phoenix.safety.errors import AuthError
    from phoenix.safety.gate import verify_request

    _reset_global_state()

    class FakeActor:
        name = "ADAM"
        identity_fingerprint = "abc"

    with pytest.raises(AuthError):
        verify_request(FakeActor(), action_key="tasks_submit_r3")
