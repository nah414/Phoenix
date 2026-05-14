"""Phase 9 Step 3 -- REST adapter endpoints integration tests.

Verifies POST/GET/DELETE under ``/v1/adapters``:

- POST loads + validates + registers; returns the public metadata.
- GET returns the current registry contents (no capability required).
- DELETE unregisters; 404 when the adapter is unknown.
- POST refuses bad specs (400), broken adapters (503), already-
  registered names (409), and file-path specs (501).
- POST/DELETE require ``can_load_adapter`` / ``can_unload_adapter``
  respectively -- non-admin "alice" gets 403; bootstrap "adam"
  (all-True) succeeds.

The dev-mode "no Authorization header" path uses
:func:`extract_or_bootstrap`, which mints a bootstrap actor and
falls back to ``adam`` permissions -- so calls without a header
exercise the admin (load/unload-capable) path.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix.adapters import (
    IdentityAdapter,
    get_registry as get_adapter_registry,
    reset_registry as reset_adapter_registry,
)
from phoenix.api.routes import app


@pytest.fixture
def isolated_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[Path]:
    """Per-test fresh runtime + clean adapter registry."""
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
    """Build an Authorization header for non-admin actor 'alice'.

    Default permissions: can_submit_tasks=True, can_load_adapter=False,
    can_unload_adapter=False. So POST/DELETE /v1/adapters get 403.
    """
    from phoenix.identity.bootstrap import mint_bootstrap_actor

    alice = mint_bootstrap_actor("alice")
    payload = alice.to_payload()
    return "Phoenix-Actor " + base64.b64encode(json.dumps(payload).encode()).decode("ascii")


_IDENTITY_SPEC = "phoenix.adapters.identity_adapter:make_identity_adapter"


# ---------------------------------------------------------------------
# Happy path: POST -> GET -> DELETE
# ---------------------------------------------------------------------


class TestAdapterLifecycle:
    def test_post_loads_identity_adapter(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post("/v1/adapters", json={"spec": _IDENTITY_SPEC})
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["adapter_id"] == "identity"
        assert payload["name"] == "identity"
        assert payload["version"] == "1.0.0"
        assert "identity" in payload["capabilities"]
        assert len(payload["fingerprint"]) == 64
        # The load went through validation -> one history entry
        assert payload["validation_history_length"] == 1

    def test_get_after_post_shows_one_adapter(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            client.post("/v1/adapters", json={"spec": _IDENTITY_SPEC})
            resp = client.get("/v1/adapters")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["adapters"][0]["name"] == "identity"

    def test_delete_unregisters(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            client.post("/v1/adapters", json={"spec": _IDENTITY_SPEC})
            resp = client.delete("/v1/adapters/identity")
        assert resp.status_code == 200
        assert resp.json() == {"adapter_id": "identity", "unloaded": True}
        # Registry is now empty
        with TestClient(app) as client:
            resp = client.get("/v1/adapters")
        assert resp.json()["count"] == 0

    def test_get_with_empty_registry(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.get("/v1/adapters")
        assert resp.status_code == 200
        assert resp.json() == {"count": 0, "adapters": []}


# ---------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------


class TestAdapterErrors:
    def test_post_with_bad_module_returns_400(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/adapters",
                json={"spec": "nonexistent.module.path:thing"},
            )
        assert resp.status_code == 400
        assert "Cannot import" in resp.json()["detail"]

    def test_post_with_bad_spec_format_returns_400(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/adapters",
                json={"spec": "no_colon_form"},
            )
        assert resp.status_code == 400

    def test_post_with_file_path_spec_returns_501(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/adapters",
                json={"spec": "/tmp/my_adapter.py"},
            )
        assert resp.status_code == 501
        assert "Phase 9 v1" in resp.json()["detail"]

    def test_post_duplicate_returns_409(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            r1 = client.post("/v1/adapters", json={"spec": _IDENTITY_SPEC})
            assert r1.status_code == 200
            r2 = client.post("/v1/adapters", json={"spec": _IDENTITY_SPEC})
        assert r2.status_code == 409
        assert "identity" in r2.json()["detail"]

    def test_post_broken_adapter_returns_503(self, isolated_runtime: Path) -> None:
        """A factory that returns a whitespace-stripping adapter must
        be refused with 503 + failed_cases listing.
        """
        with TestClient(app) as client:
            resp = client.post(
                "/v1/adapters",
                json={"spec": ("tests.integration.test_adapters_step3:make_broken_adapter")},
            )
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["error"] == "adapter_validation_failed"
        assert detail["adapter_name"] == "broken-strip-rest"
        assert "hello world" in detail["failed_cases"]

    def test_post_empty_spec_returns_422(self, isolated_runtime: Path) -> None:
        """Pydantic rejects an empty string before the handler runs."""
        with TestClient(app) as client:
            resp = client.post("/v1/adapters", json={"spec": ""})
        # 422 from pydantic validation (min_length=1)
        assert resp.status_code == 422

    def test_post_missing_spec_returns_422(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post("/v1/adapters", json={})
        assert resp.status_code == 422

    def test_delete_unknown_returns_404(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.delete("/v1/adapters/never-loaded")
        assert resp.status_code == 404
        assert "never-loaded" in resp.json()["detail"]


# ---------------------------------------------------------------------
# Permission gating
# ---------------------------------------------------------------------


class TestAdapterPermissions:
    def test_post_without_can_load_adapter_returns_403(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/adapters",
                json={"spec": _IDENTITY_SPEC},
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403
        assert "can_load_adapter" in resp.json()["detail"]

    def test_delete_without_can_unload_adapter_returns_403(self, isolated_runtime: Path) -> None:
        # adam first registers it, then alice tries to delete.
        with TestClient(app) as client:
            client.post("/v1/adapters", json={"spec": _IDENTITY_SPEC})
            resp = client.delete(
                "/v1/adapters/identity",
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403
        assert "can_unload_adapter" in resp.json()["detail"]

    def test_get_is_open_to_non_admin(self, isolated_runtime: Path) -> None:
        """GET /v1/adapters requires no special capability."""
        with TestClient(app) as client:
            client.post("/v1/adapters", json={"spec": _IDENTITY_SPEC})
            resp = client.get(
                "/v1/adapters",
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 200
        assert resp.json()["count"] == 1


# ---------------------------------------------------------------------
# OpenAPI shape
# ---------------------------------------------------------------------


def test_openapi_advertises_three_adapter_routes(
    isolated_runtime: Path,
) -> None:
    """All three routes appear in the OpenAPI schema."""
    with TestClient(app) as client:
        schema = client.get("/v1/openapi.json").json()
    paths = schema["paths"]
    assert "/v1/adapters" in paths
    assert "post" in paths["/v1/adapters"]
    assert "get" in paths["/v1/adapters"]
    assert "/v1/adapters/{adapter_id}" in paths
    assert "delete" in paths["/v1/adapters/{adapter_id}"]


# ---------------------------------------------------------------------
# Broken-adapter factory exposed for the test above
# ---------------------------------------------------------------------


from dataclasses import dataclass, field  # noqa: E402


@dataclass
class _BrokenStripAdapter:
    """Whitespace-stripping adapter for the 503 test."""

    name: str = "broken-strip-rest"
    version: str = "0.0.0"
    base_model_fingerprint: str = "test"
    capabilities: list[str] = field(default_factory=lambda: ["test"])

    def encode_to_grammar(self, natural_language: str) -> str:
        return natural_language.strip().replace(" ", "")

    def decode_from_grammar(self, tokens: str) -> str:
        return tokens

    def fingerprint(self) -> str:
        return "broken-strip-rest-fp"


def make_broken_adapter() -> _BrokenStripAdapter:
    return _BrokenStripAdapter()


# ---------------------------------------------------------------------
# Smoke: registry singleton actually wired into routes
# ---------------------------------------------------------------------


def test_routes_see_same_registry_singleton(isolated_runtime: Path) -> None:
    """Routes use the same singleton get_registry() as direct callers.

    Regression guard for "imported-symbol shadowing": if routes.py
    imports get_registry under a different name, a test that
    pre-populates via the in-process singleton must still appear
    on the REST surface.
    """
    get_adapter_registry().register(IdentityAdapter())
    with TestClient(app) as client:
        resp = client.get("/v1/adapters")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1
    assert resp.json()["adapters"][0]["name"] == "identity"
