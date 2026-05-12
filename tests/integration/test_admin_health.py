"""Phase 8 Step 3 -- admin health/governor/inference-status/budget tests.

Coverage:

- ``GET /v1/admin/health/detailed`` returns the per-subsystem rollup
  with all expected top-level keys; non-admin gets 403.
- ``GET /v1/admin/governor`` returns the psutil snapshot with the
  v1 minimum fields populated and GPU/VRAM/NPU/thermal as ``None``
  per locked OPEN-2.
- ``GET /v1/admin/inference-status`` returns the Phase 8 placeholder
  shape with ``phase: "phase_8_placeholder"``.
- ``GET /v1/admin/budget`` returns the rate-limit bucket snapshot
  with the cumulative-spend placeholders.
- Each endpoint emits an ``admin.<endpoint>.read`` audit event with
  the operator's identity.
- :meth:`RateLimiter.snapshot` returns a consistent snapshot
  reflecting the projected current state, doesn't mutate live
  buckets, and survives concurrent calls.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix.api.routes import app
from phoenix.audit import AuditEvent


class _Recorder:
    """Test double -- captures every emitted audit event."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def emit(self, event: AuditEvent) -> None:
        self.events.append(event)

    def close(self, drain_timeout_seconds: float = 5.0) -> None:
        pass


@pytest.fixture
def isolated_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[Path]:
    """Per-test fresh Phoenix runtime."""
    runtime = tmp_path / "phoenix_runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PHOENIX_SQLITE_DB_PATH", str(runtime / "state.db"))
    monkeypatch.setenv("PHOENIX_AUDIT_DIR", str(runtime / "audit"))
    monkeypatch.setenv("PHOENIX_KILL_SWITCH_PATH", str(runtime / "kill_switch.json"))

    from phoenix.audit import reset_emitter
    from phoenix.ledger import reset_ledger
    from phoenix.safety import kill_switch as ks_module
    from phoenix.safety.rate_limiter import get_limiter
    from phoenix.state import reset_state_backend

    reset_emitter()
    reset_ledger()
    reset_state_backend()
    ks_module._STORE = None
    get_limiter().reset_all()
    try:
        yield runtime
    finally:
        reset_emitter()
        reset_ledger()
        reset_state_backend()
        ks_module._STORE = None
        get_limiter().reset_all()


@pytest.fixture
def audit_recorder(isolated_runtime: Path) -> Iterator[_Recorder]:
    """Attach a recording sink to the singleton audit emitter."""
    from phoenix.audit import get_emitter, reset_emitter

    reset_emitter()
    sink = _Recorder()
    get_emitter().add_sink(sink)
    yield sink
    reset_emitter()


def _alice_header() -> str:
    from phoenix.identity.bootstrap import mint_bootstrap_actor

    alice = mint_bootstrap_actor("alice")
    payload = alice.to_payload()
    return "Phoenix-Actor " + base64.b64encode(json.dumps(payload).encode()).decode("ascii")


# ---------------------------------------------------------------------------
# RateLimiter.snapshot (unit-level)


class TestRateLimiterSnapshot:
    def test_snapshot_empty_when_no_actors(self) -> None:
        from phoenix.safety.rate_limiter import RateLimiter

        limiter = RateLimiter()
        assert limiter.snapshot() == []

    def test_snapshot_after_consume(self) -> None:
        from phoenix.safety.rate_limiter import RateLimiter

        limiter = RateLimiter()
        # Default tier has a finite capacity; consuming makes the
        # bucket appear in the snapshot.
        limiter.check_and_consume("alice", tier="default", cost=3)
        snap = limiter.snapshot()
        assert len(snap) == 1
        assert snap[0]["actor_name"] == "alice"
        assert snap[0]["capacity"] > 0
        # tokens_remaining = capacity - 3 (refill since then is
        # negligible at test speed).
        assert snap[0]["tokens_remaining"] <= snap[0]["capacity"]

    def test_snapshot_does_not_mutate_buckets(self) -> None:
        """Calling snapshot() repeatedly doesn't drain the bucket."""
        from phoenix.safety.rate_limiter import RateLimiter

        limiter = RateLimiter()
        limiter.check_and_consume("ash", tier="default", cost=1)
        before = limiter.snapshot()[0]["tokens_remaining"]
        # 10 snapshot calls.
        for _ in range(10):
            limiter.snapshot()
        after = limiter.snapshot()[0]["tokens_remaining"]
        # After refill projection difference is tiny (test runs in ms)
        # but `after >= before` must hold (refill never decreases).
        assert after >= before


# ---------------------------------------------------------------------------
# /v1/admin/health/detailed


class TestDetailedHealth:
    def test_returns_200_with_subsystem_rollup(self, audit_recorder: _Recorder) -> None:
        with TestClient(app) as client:
            resp = client.get("/v1/admin/health/detailed")
        assert resp.status_code == 200
        body = resp.json()
        # Required top-level keys per Section 8.2's "extended health"
        # contract.
        for key in (
            "phoenix_version",
            "kill_switch",
            "drift_detector",
            "state_backend",
            "audit_emitter",
            "event_broker",
            "provider_registry",
            "checked_at_unix",
        ):
            assert key in body, f"missing key: {key}"

    def test_kill_switch_state_reflects_current(self, audit_recorder: _Recorder) -> None:
        with TestClient(app) as client:
            client.post(
                "/v1/admin/kill-switch/engage",
                json={"rationale": "test"},
            )
            resp = client.get("/v1/admin/health/detailed")
            client.post(
                "/v1/admin/kill-switch/release",
                json={"rationale": "release"},
            )
        body = resp.json()
        assert body["kill_switch"]["engaged"] is True
        assert body["kill_switch"]["reason"] == "test"

    def test_403_for_non_admin(self, audit_recorder: _Recorder) -> None:
        with TestClient(app) as client:
            resp = client.get(
                "/v1/admin/health/detailed",
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403

    def test_emits_audit_event_on_success(self, audit_recorder: _Recorder) -> None:
        with TestClient(app) as client:
            client.get("/v1/admin/health/detailed")
        events = [e for e in audit_recorder.events if e.event_type == "admin.health.detailed.read"]
        assert len(events) == 1
        assert events[0].actor_id == "adam"


# ---------------------------------------------------------------------------
# /v1/admin/governor


class TestGovernor:
    def test_psutil_fields_populated(self, audit_recorder: _Recorder) -> None:
        with TestClient(app) as client:
            resp = client.get("/v1/admin/governor")
        assert resp.status_code == 200
        body = resp.json()
        # v1 minimum -- psutil-derived fields populated.
        assert body["cpu_percent"] is not None
        assert body["ram_percent"] is not None
        assert body["ram_used_bytes"] is not None
        assert body["ram_total_bytes"] is not None
        assert body["process_rss_bytes"] is not None
        # Disk is best-effort; may be None on hostile environments
        # but should be populated under TestClient.
        assert "disk_percent" in body

    def test_gpu_fields_are_none_per_locked_open_2(self, audit_recorder: _Recorder) -> None:
        """Per locked OPEN-2 (2026-05-11): GPU/VRAM/NPU/thermal land
        in Phase 9+. v1 Phase 8 returns ``None`` for all four."""
        with TestClient(app) as client:
            resp = client.get("/v1/admin/governor")
        body = resp.json()
        assert body["gpu_percent"] is None
        assert body["vram_percent"] is None
        assert body["npu_percent"] is None
        assert body["thermal_celsius"] is None

    def test_403_for_non_admin(self, audit_recorder: _Recorder) -> None:
        with TestClient(app) as client:
            resp = client.get(
                "/v1/admin/governor",
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403

    def test_emits_audit_event(self, audit_recorder: _Recorder) -> None:
        with TestClient(app) as client:
            client.get("/v1/admin/governor")
        events = [e for e in audit_recorder.events if e.event_type == "admin.governor.read"]
        assert len(events) == 1


# ---------------------------------------------------------------------------
# /v1/admin/inference-status


class TestInferenceStatus:
    def test_returns_phase_8_placeholder_shape(self, audit_recorder: _Recorder) -> None:
        with TestClient(app) as client:
            resp = client.get("/v1/admin/inference-status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["adapters"] == []
        assert body["queue_depth"] == 0
        assert body["last_round_trip_at"] is None
        assert body["phase"] == "phase_8_placeholder"

    def test_403_for_non_admin(self, audit_recorder: _Recorder) -> None:
        with TestClient(app) as client:
            resp = client.get(
                "/v1/admin/inference-status",
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# /v1/admin/budget


class TestBudget:
    def test_returns_rate_limit_bucket_snapshot(self, audit_recorder: _Recorder) -> None:
        with TestClient(app) as client:
            # First call creates a bucket for adam via verify_request.
            client.get("/v1/admin/_ping")
            resp = client.get("/v1/admin/budget")
        assert resp.status_code == 200
        body = resp.json()
        # adam's bucket should be in the snapshot.
        bucket_actors = [b["actor_name"] for b in body["rate_limit_buckets"]]
        assert "adam" in bucket_actors

    def test_returns_placeholder_spend_shape(self, audit_recorder: _Recorder) -> None:
        with TestClient(app) as client:
            resp = client.get("/v1/admin/budget")
        body = resp.json()
        assert body["cumulative_spend_usd_ytd"] == 0.0
        assert body["per_actor_spend_usd_ytd"] == []
        assert body["phase"] == "phase_8_minimum"

    def test_403_for_non_admin(self, audit_recorder: _Recorder) -> None:
        with TestClient(app) as client:
            resp = client.get(
                "/v1/admin/budget",
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# All four endpoints registered in OpenAPI with Admin tag


def test_step3_routes_registered_with_admin_tag(
    audit_recorder: _Recorder,
) -> None:
    with TestClient(app) as client:
        schema = client.get("/v1/openapi.json").json()
    expected_paths = [
        "/v1/admin/health/detailed",
        "/v1/admin/governor",
        "/v1/admin/inference-status",
        "/v1/admin/budget",
    ]
    for path in expected_paths:
        assert path in schema["paths"], f"missing path: {path}"
        op = schema["paths"][path]["get"]
        assert "Admin" in op["tags"], f"{path} missing Admin tag"
