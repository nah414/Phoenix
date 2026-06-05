"""Tests for the Phase 13.5 `can_capture_drift_baseline` permission flag."""

from __future__ import annotations

from phoenix.safety.permissions import ActorPermissions


class TestCaptureDriftBaselinePermission:
    def test_default_deny(self) -> None:
        perms = ActorPermissions()
        assert perms.can_capture_drift_baseline is False

    def test_explicit_grant(self) -> None:
        perms = ActorPermissions(can_capture_drift_baseline=True)
        assert perms.can_capture_drift_baseline is True
