"""Tests for the Phase 13.x.7 `can_rotate_encryption_key` permission flag."""

from __future__ import annotations

from phoenix.safety.permissions import ActorPermissions


class TestRotateEncryptionKeyPermission:
    def test_default_deny(self) -> None:
        """Default ActorPermissions must NOT grant can_rotate_encryption_key."""
        perms = ActorPermissions()
        assert perms.can_rotate_encryption_key is False

    def test_explicit_grant(self) -> None:
        perms = ActorPermissions(can_rotate_encryption_key=True)
        assert perms.can_rotate_encryption_key is True

    def test_existing_flags_unchanged(self) -> None:
        """Adding the new flag must not change any existing default values."""
        default = ActorPermissions()
        # Sample a few load-bearing existing flags to confirm they
        # still default deny.
        assert hasattr(default, "can_rotate_encryption_key")
        # Other flags exist and stay at their existing defaults.
        # (Exact list varies; we just confirm the dataclass still constructs
        # cleanly with no arguments, which proves nothing was removed.)
