"""Phase 9 Step 4 -- admin adapter management endpoints.

Phase 8 Step 9 shipped a 501 stub for ``/v1/admin/adapters/{id}/
force-revalidate`` (locked OPEN-6 deferral). Phase 9 Step 4
replaces the stub with the real handler and adds ``GET
/v1/admin/adapters/{id}/round-trip-history``.

This file verifies:

- Force-revalidate on a loaded identity adapter returns
  ``validation_passed=True`` + appends a history entry.
- Force-revalidate on a broken adapter returns
  ``validation_passed=False`` + the failed_cases breakdown.
- Force-revalidate on an unloaded adapter returns 404 +
  ``admin.adapters.force_revalidate.not_loaded`` audit event.
- Round-trip-history reflects every force-revalidate call +
  the initial load-time validation entry.
- Round-trip-history on an unloaded adapter returns 404.
- Non-admin actors get 403 BEFORE the registry lookup runs.
- Both endpoints appear in OpenAPI with the ``Admin`` tag.
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


@pytest.fixture
def isolated_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[Path]:
    runtime = tmp_path / "phoenix_runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PHOENIX_SQLITE_DB_PATH", str(runtime / "state.db"))
    monkeypatch.setenv("PHOENIX_AUDIT_DIR", str(runtime / "audit"))
    monkeypatch.setenv("PHOENIX_KILL_SWITCH_PATH", str(runtime / "kill_switch.json"))

    from phoenix.adapters.registry import reset_registry as reset_adapter_registry
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
    reset_adapter_registry()
    try:
        yield runtime
    finally:
        reset_emitter()
        reset_ledger()
        reset_state_backend()
        ks_module._STORE = None
        get_limiter().reset_all()
        reset_adapter_registry()


def _alice_header() -> str:
    from phoenix.identity.bootstrap import mint_bootstrap_actor

    alice = mint_bootstrap_actor("alice")
    payload = alice.to_payload()
    return "Phoenix-Actor " + base64.b64encode(json.dumps(payload).encode()).decode("ascii")


_IDENTITY_SPEC = "phoenix.adapters.identity_adapter:make_identity_adapter"
_BROKEN_SPEC = "tests.integration.test_admin_adapters:make_broken_adapter"


# ---------------------------------------------------------------------
# Broken adapter factory (used by the 503/false-positive test)
# ---------------------------------------------------------------------


from dataclasses import dataclass, field  # noqa: E402


@dataclass
class _BrokenStripAdapter:
    name: str = "broken-admin-strip"
    version: str = "0.0.0"
    base_model_fingerprint: str = "test"
    capabilities: list[str] = field(default_factory=lambda: ["test"])

    def encode_to_grammar(self, natural_language: str) -> str:
        return natural_language.strip().replace(" ", "")

    def decode_from_grammar(self, tokens: str) -> str:
        return tokens

    def fingerprint(self) -> str:
        return "broken-admin-strip-fp"


def make_broken_adapter() -> _BrokenStripAdapter:
    return _BrokenStripAdapter()


# ---------------------------------------------------------------------
# Force-revalidate happy path
# ---------------------------------------------------------------------


class TestForceRevalidate:
    def test_identity_adapter_revalidates_successfully(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            # Load the identity adapter first
            client.post("/v1/adapters", json={"spec": _IDENTITY_SPEC})
            resp = client.post("/v1/admin/adapters/identity/force-revalidate")
        assert resp.status_code == 200
        body = resp.json()
        assert body["adapter_id"] == "identity"
        assert body["validation_passed"] is True
        assert body["failed_cases"] == []
        assert body["wall_clock_ms"] >= 0.0
        assert body["timestamp_unix"] > 0.0

    def test_force_revalidate_appends_to_history(self, isolated_runtime: Path) -> None:
        """Each force-revalidate call must add one entry to the ring."""
        with TestClient(app) as client:
            client.post("/v1/adapters", json={"spec": _IDENTITY_SPEC})
            # Load adds 1 history entry; force-revalidate twice adds 2 more
            client.post("/v1/admin/adapters/identity/force-revalidate")
            client.post("/v1/admin/adapters/identity/force-revalidate")
            resp = client.get("/v1/admin/adapters/identity/round-trip-history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["adapter_id"] == "identity"
        assert body["count"] == 3
        assert all(entry["passed"] is True for entry in body["history"])

    def test_force_revalidate_unloaded_returns_404(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post("/v1/admin/adapters/never-loaded/force-revalidate")
        assert resp.status_code == 404
        assert "never-loaded" in resp.json()["detail"]

    def test_force_revalidate_emits_audit_event(self, isolated_runtime: Path) -> None:
        from phoenix.audit import AuditEvent, get_emitter, reset_emitter

        class _Recorder:
            def __init__(self) -> None:
                self.events: list[AuditEvent] = []

            def emit(self, event: AuditEvent) -> None:
                self.events.append(event)

            def close(self, drain_timeout_seconds: float = 5.0) -> None:
                pass

        reset_emitter()
        sink = _Recorder()
        get_emitter().add_sink(sink)
        try:
            with TestClient(app) as client:
                client.post("/v1/adapters", json={"spec": _IDENTITY_SPEC})
                client.post("/v1/admin/adapters/identity/force-revalidate")
            success_events = [
                e for e in sink.events if e.event_type == "admin.adapters.force_revalidate.success"
            ]
            assert len(success_events) == 1
            assert success_events[0].parameters["adapter_id"] == "identity"
            assert success_events[0].parameters["passed"] is True
        finally:
            reset_emitter()


# ---------------------------------------------------------------------
# Force-revalidate broken adapter
# ---------------------------------------------------------------------


class TestForceRevalidateBroken:
    def test_broken_adapter_revalidation_fails(self, isolated_runtime: Path) -> None:
        """A registered-but-broken adapter (loaded via validate=False
        in-process) re-validates as failed without bringing the
        request down. Operators see the failed_cases breakdown.
        """
        # Register via direct loader call with validate=False so the
        # broken adapter is in the registry without having passed
        # initial validation.
        from phoenix.adapters import load_adapter

        load_adapter(_BROKEN_SPEC, validate=False)

        with TestClient(app) as client:
            resp = client.post("/v1/admin/adapters/broken-admin-strip/force-revalidate")
        assert resp.status_code == 200
        body = resp.json()
        assert body["validation_passed"] is False
        assert "hello world" in body["failed_cases"]
        assert " leading-trailing " in body["failed_cases"]

    def test_broken_revalidation_emits_failed_audit(self, isolated_runtime: Path) -> None:
        from phoenix.adapters import load_adapter
        from phoenix.audit import AuditEvent, get_emitter, reset_emitter

        load_adapter(_BROKEN_SPEC, validate=False)

        class _Recorder:
            def __init__(self) -> None:
                self.events: list[AuditEvent] = []

            def emit(self, event: AuditEvent) -> None:
                self.events.append(event)

            def close(self, drain_timeout_seconds: float = 5.0) -> None:
                pass

        reset_emitter()
        sink = _Recorder()
        get_emitter().add_sink(sink)
        try:
            with TestClient(app) as client:
                client.post("/v1/admin/adapters/broken-admin-strip/force-revalidate")
            failed_events = [
                e for e in sink.events if e.event_type == "admin.adapters.force_revalidate.failed"
            ]
            assert len(failed_events) == 1
            assert failed_events[0].parameters["passed"] is False
            assert "hello world" in failed_events[0].parameters["failed_cases"]
        finally:
            reset_emitter()


# ---------------------------------------------------------------------
# Round-trip history
# ---------------------------------------------------------------------


class TestRoundTripHistory:
    def test_history_after_load_has_one_entry(self, isolated_runtime: Path) -> None:
        """POST /v1/adapters validates -> one history entry."""
        with TestClient(app) as client:
            client.post("/v1/adapters", json={"spec": _IDENTITY_SPEC})
            resp = client.get("/v1/admin/adapters/identity/round-trip-history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["adapter_id"] == "identity"
        assert body["count"] == 1
        assert body["history"][0]["passed"] is True

    def test_history_empty_when_loaded_without_validation(self, isolated_runtime: Path) -> None:
        """An adapter registered with validate=False has empty history."""
        from phoenix.adapters import load_adapter

        load_adapter(_IDENTITY_SPEC, validate=False)
        with TestClient(app) as client:
            resp = client.get("/v1/admin/adapters/identity/round-trip-history")
        assert resp.status_code == 200
        assert resp.json() == {
            "adapter_id": "identity",
            "count": 0,
            "history": [],
        }

    def test_history_unloaded_returns_404(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.get("/v1/admin/adapters/never-loaded/round-trip-history")
        assert resp.status_code == 404


# ---------------------------------------------------------------------
# Permission gating
# ---------------------------------------------------------------------


class TestPermissionGating:
    def test_force_revalidate_non_admin_returns_403_before_lookup(
        self, isolated_runtime: Path
    ) -> None:
        """Auth chain runs BEFORE the registry lookup -- a non-admin
        gets 403 even when the adapter would have been a 404 case.
        Prevents leaking "this adapter exists / doesn't exist" via
        the HTTP code.
        """
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/adapters/never-loaded/force-revalidate",
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403

    def test_round_trip_history_non_admin_returns_403(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.get(
                "/v1/admin/adapters/never-loaded/round-trip-history",
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------
# OpenAPI shape
# ---------------------------------------------------------------------


def test_openapi_advertises_both_admin_routes(isolated_runtime: Path) -> None:
    with TestClient(app) as client:
        schema = client.get("/v1/openapi.json").json()
    paths = schema["paths"]
    fr_path = "/v1/admin/adapters/{adapter_id}/force-revalidate"
    history_path = "/v1/admin/adapters/{adapter_id}/round-trip-history"
    assert fr_path in paths
    assert "post" in paths[fr_path]
    assert "Admin" in paths[fr_path]["post"]["tags"]
    assert history_path in paths
    assert "get" in paths[history_path]
    assert "Admin" in paths[history_path]["get"]["tags"]
