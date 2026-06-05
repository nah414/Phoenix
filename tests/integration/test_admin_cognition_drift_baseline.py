"""Integration tests for cognition drift baseline admin endpoints (Phase 13.5).

Two endpoints under test:

- ``POST /v1/admin/drift/cognition-baseline/capture`` — computes and
  persists a baseline snapshot from recent cognition ledger entries.
- ``GET /v1/admin/drift/cognition-baseline`` — returns the persisted
  baseline for the running Phoenix version.

Test isolation follows :mod:`test_admin_step9_endpoints`: per-test
fresh runtime + reset of all singletons (audit emitter, ledger,
state backend, kill switch, permissions registry, rate limiter).
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix.api.routes import app


# ---------------------------------------------------------------------------
# Per-test isolated runtime (mirrors test_admin_step9_endpoints).


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
    monkeypatch.setenv("PHOENIX_KEYSTORE_DIR", str(runtime / "keystore"))
    (runtime / "keystore").mkdir(parents=True, exist_ok=True)

    baseline_path = runtime / "cognition_drift_baseline.json"
    monkeypatch.setenv("PHOENIX_COGNITION_DRIFT_BASELINE_PATH", str(baseline_path))

    from phoenix._internal.cloud_seams import reset_seams
    from phoenix.audit import reset_emitter
    from phoenix.ledger import reset_ledger
    from phoenix.safety import kill_switch as ks_module
    from phoenix.safety import permissions as permissions_module
    from phoenix.safety.rate_limiter import get_limiter
    from phoenix.state import reset_state_backend

    reset_emitter()
    reset_ledger()
    reset_state_backend()
    ks_module._STORE = None
    get_limiter().reset_all()
    permissions_module._REGISTRY = None
    reset_seams()
    try:
        yield runtime
    finally:
        reset_emitter()
        reset_ledger()
        reset_state_backend()
        ks_module._STORE = None
        get_limiter().reset_all()
        permissions_module._REGISTRY = None
        reset_seams()


def _alice_header() -> str:
    """Non-admin actor header (alice has is_admin=False +
    can_capture_drift_baseline=False by default)."""
    from phoenix.identity.bootstrap import mint_bootstrap_actor

    alice = mint_bootstrap_actor("alice")
    payload = alice.to_payload()
    return "Phoenix-Actor " + base64.b64encode(json.dumps(payload).encode()).decode("ascii")


def _populate_cognition_entries(count: int = 80) -> None:
    """Insert enough cognition ledger entries that the feature provider
    can produce a vector (min_sample_size = 50)."""
    from phoenix.state import get_state_backend

    backend = get_state_backend()
    now = time.time()
    parent_hash = "GENESIS"
    for i in range(count):
        payload = {
            "axis": "cross_model",
            "prompt_disposition": "HASH_ONLY",
            "cognition_provenance": {
                "provider_id": "anthropic",
                "model": "test",
                "temperature": 0.0,
                "latency_ms": 250.0,
            },
            "verdict": "bit_exact",
            "classification": {
                "disagreement_type": "factual_agreement",
                "confidence": 0.95,
                "classifier_version": "test-v1",
            },
            "cognition_disagreement_metric": {"distance": 0.1},
        }
        payload_json = json.dumps(payload, sort_keys=True)
        entry_id = str(uuid.uuid4())
        # entry_hash doesn't need to be cryptographically valid for the
        # feature provider; it only reads payload_json + entry_kind.
        entry_hash = f"deadbeef{i:056x}"
        backend.append_ledger_entry(
            {
                "entry_id": entry_id,
                "entry_kind": "cognition",
                "timestamp_unix": now - (count - i),
                "actor_id": "adam",
                "parent_hash": parent_hash,
                "entry_hash": entry_hash,
                "payload_json": payload_json,
            }
        )
        parent_hash = entry_hash


# ---------------------------------------------------------------------------
# POST /v1/admin/drift/cognition-baseline/capture


class TestCaptureBaselineEndpoint:
    def test_happy_path_returns_200(self, isolated_runtime: Path) -> None:
        baseline_path = isolated_runtime / "cognition_drift_baseline.json"
        _populate_cognition_entries(80)
        with TestClient(app) as client:
            resp = client.post("/v1/admin/drift/cognition-baseline/capture")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "phoenix_version" in body
        assert "captured_unix" in body
        assert "features_summary" in body
        assert baseline_path.is_file()

    def test_unauthorized_returns_403(self, isolated_runtime: Path) -> None:
        _populate_cognition_entries(80)
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/drift/cognition-baseline/capture",
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403, resp.text

    def test_insufficient_data_returns_409(self, isolated_runtime: Path) -> None:
        """No cognition entries in the backend → 409 with insufficient_data."""
        with TestClient(app) as client:
            resp = client.post("/v1/admin/drift/cognition-baseline/capture")
        assert resp.status_code == 409, resp.text
        assert "insufficient" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# GET /v1/admin/drift/cognition-baseline


class TestGetBaselineEndpoint:
    def test_get_returns_404_when_no_baseline(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.get("/v1/admin/drift/cognition-baseline")
        assert resp.status_code == 404, resp.text

    def test_get_returns_baseline_when_captured(self, isolated_runtime: Path) -> None:
        _populate_cognition_entries(80)
        with TestClient(app) as client:
            capture = client.post("/v1/admin/drift/cognition-baseline/capture")
            assert capture.status_code == 200, capture.text
            resp = client.get("/v1/admin/drift/cognition-baseline")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "phoenix_version" in body
        assert "features" in body
