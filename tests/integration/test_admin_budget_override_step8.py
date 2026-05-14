"""Phase 10 Step 8 -- POST /v1/admin/budget/override tests.

Verifies the admin endpoint per Section 4.7 "Admin override path"
+ locked OPEN-4 (three explicit scopes) + locked OPEN-6
(BudgetOverrideEntry ledger kind):

- Admin (bootstrap "adam") issues a per-solve override; the
  response carries override_id + ledger_entry_hash; the row lands
  in the ``budget_overrides`` state-backend table.
- The override is also appended as a ``BudgetOverrideEntry`` to
  the Omega Ledger (entry_kind="budget_override").
- ``new_ceiling_usd <= 0`` is rejected at Pydantic validation
  with HTTP 422.
- ``expires_at_unix <= now`` is rejected at the handler with
  HTTP 400.
- Unknown ``scope`` is rejected with HTTP 400.
- Non-admin actors get HTTP 403 BEFORE the registry write
  (auth-runs-first invariant).
- The override flows through the ``LocalJobBudgetController`` --
  after override is in place, a previously-denied solve is allowed
  because the per-solve ceiling was raised.
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
    monkeypatch.setenv("PHOENIX_KEYSTORE_DIR", str(runtime / "keystore"))
    (runtime / "keystore").mkdir(parents=True, exist_ok=True)

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
    from phoenix.identity.bootstrap import mint_bootstrap_actor

    alice = mint_bootstrap_actor("alice")
    payload = alice.to_payload()
    return "Phoenix-Actor " + base64.b64encode(json.dumps(payload).encode()).decode("ascii")


def _future_ts() -> float:
    import time

    return time.time() + 3600.0


# ---------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------


class TestBudgetOverrideHappyPath:
    def test_admin_issues_per_solve_override(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/budget/override",
                json={
                    "actor_name": "bob",
                    "new_ceiling_usd": 100.0,
                    "expires_at_unix": _future_ts(),
                    "scope": "per_solve",
                    "rationale": "frontier-physics test run",
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["override_id"] > 0
        assert body["actor_name"] == "bob"
        assert body["scope"] == "per_solve"
        assert body["new_ceiling_usd"] == 100.0
        assert isinstance(body["ledger_entry_hash"], str)
        assert len(body["ledger_entry_hash"]) == 64  # SHA-256 hex

    def test_override_row_lands_in_state_backend(self, isolated_runtime: Path) -> None:
        from phoenix.state import get_state_backend

        with TestClient(app) as client:
            client.post(
                "/v1/admin/budget/override",
                json={
                    "actor_name": "bob",
                    "new_ceiling_usd": 250.0,
                    "expires_at_unix": _future_ts(),
                    "scope": "per_actor_24h",
                },
            )
        backend = get_state_backend()
        import time

        active = backend.list_active_budget_overrides("bob", as_of_unix=time.time())
        assert len(active) == 1
        assert active[0]["scope"] == "per_actor_24h"
        assert active[0]["new_ceiling_usd"] == pytest.approx(250.0)
        assert active[0]["created_by"] == "adam"  # bootstrap actor

    def test_ledger_entry_appended(self, isolated_runtime: Path) -> None:
        """The BudgetOverrideEntry lands as a ledger row with entry_kind=
        'budget_override' and the admin actor as actor_id.
        """
        from phoenix.state import get_state_backend

        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/budget/override",
                json={
                    "actor_name": "bob",
                    "new_ceiling_usd": 1000.0,
                    "expires_at_unix": _future_ts(),
                    "scope": "per_org_24h",
                    "rationale": "audit drill",
                },
            )
        ledger_hash = resp.json()["ledger_entry_hash"]

        rows = get_state_backend().list_ledger_entries(since_unix=0.0, limit=10_000)
        budget_rows = [r for r in rows if r["entry_kind"] == "budget_override"]
        assert len(budget_rows) == 1
        row = budget_rows[0]
        assert row["entry_hash"] == ledger_hash
        assert row["actor_id"] == "adam"
        payload = json.loads(row["payload_json"])
        assert payload["granted_actor_name"] == "bob"
        assert payload["granted_by"] == "adam"
        assert payload["scope"] == "per_org_24h"
        assert payload["new_ceiling_usd"] == 1000.0
        assert payload["rationale"] == "audit drill"


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------


class TestBudgetOverrideValidation:
    def test_zero_ceiling_returns_422(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/budget/override",
                json={
                    "actor_name": "bob",
                    "new_ceiling_usd": 0.0,
                    "expires_at_unix": _future_ts(),
                    "scope": "per_solve",
                },
            )
        assert resp.status_code == 422  # Pydantic gt=0 rejected

    def test_negative_ceiling_returns_422(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/budget/override",
                json={
                    "actor_name": "bob",
                    "new_ceiling_usd": -50.0,
                    "expires_at_unix": _future_ts(),
                    "scope": "per_solve",
                },
            )
        assert resp.status_code == 422

    def test_past_expiry_returns_400(self, isolated_runtime: Path) -> None:
        import time

        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/budget/override",
                json={
                    "actor_name": "bob",
                    "new_ceiling_usd": 100.0,
                    "expires_at_unix": time.time() - 1.0,  # already expired
                    "scope": "per_solve",
                },
            )
        assert resp.status_code == 400
        assert "future" in resp.json()["detail"].lower()

    def test_unknown_scope_returns_400(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/budget/override",
                json={
                    "actor_name": "bob",
                    "new_ceiling_usd": 100.0,
                    "expires_at_unix": _future_ts(),
                    "scope": "per_lifetime",
                },
            )
        assert resp.status_code == 400
        assert "scope" in resp.json()["detail"]

    def test_missing_field_returns_422(self, isolated_runtime: Path) -> None:
        """Pydantic rejects missing required fields before the handler runs."""
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/budget/override",
                json={
                    "actor_name": "bob",
                    "new_ceiling_usd": 100.0,
                    # missing scope + expires_at_unix
                },
            )
        assert resp.status_code == 422


# ---------------------------------------------------------------------
# Permission gating
# ---------------------------------------------------------------------


class TestBudgetOverridePermissions:
    def test_non_admin_returns_403_before_write(self, isolated_runtime: Path) -> None:
        from phoenix.state import get_state_backend
        import time

        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/budget/override",
                json={
                    "actor_name": "bob",
                    "new_ceiling_usd": 100.0,
                    "expires_at_unix": _future_ts(),
                    "scope": "per_solve",
                },
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403
        # Backend state untouched
        assert get_state_backend().list_active_budget_overrides("bob", as_of_unix=time.time()) == []


# ---------------------------------------------------------------------
# End-to-end: override raises ceiling and downstream budget seam sees it
# ---------------------------------------------------------------------


def test_override_flows_into_budget_seam(isolated_runtime: Path) -> None:
    """After override is in place, the LocalJobBudgetController returns
    allowed=True for a cost that would previously have been denied.

    Run the controller checks INSIDE the TestClient context so the
    state backend stays open across pre-check + admin call + post-check.
    """
    import time
    from dataclasses import dataclass

    from phoenix._internal.cloud_seams import LocalJobBudgetController
    from phoenix.safety.permissions import ActorPermissions, get_registry
    from phoenix.state import get_state_backend

    @dataclass
    class _FakeActor:
        name: str
        org_id: str | None = None

    with TestClient(app) as client:
        perms_registry = get_registry()
        perms_registry.set("bob", ActorPermissions(rate_limit_tier="default"))
        controller = LocalJobBudgetController(
            state_backend=get_state_backend(),
            permissions_registry=perms_registry,
        )
        # Without override: bob can't do a $10 solve ($5 default ceiling).
        pre = controller.check_solve_budget(
            actor=_FakeActor(name="bob"),
            estimated_cost_usd=10.0,
            reproducibility_mode="default",
        )
        assert pre.allowed is False

        # Admin issues a $100 per-solve override.
        client.post(
            "/v1/admin/budget/override",
            json={
                "actor_name": "bob",
                "new_ceiling_usd": 100.0,
                "expires_at_unix": time.time() + 3600.0,
                "scope": "per_solve",
            },
        )

        # Re-check: now allowed.
        post = controller.check_solve_budget(
            actor=_FakeActor(name="bob"),
            estimated_cost_usd=10.0,
            reproducibility_mode="default",
        )
        assert post.allowed is True
        assert post.ceiling_applied_usd == pytest.approx(100.0)


# ---------------------------------------------------------------------
# OpenAPI shape
# ---------------------------------------------------------------------


def test_openapi_advertises_endpoint(isolated_runtime: Path) -> None:
    with TestClient(app) as client:
        schema = client.get("/v1/openapi.json").json()
    assert "/v1/admin/budget/override" in schema["paths"]
    assert "Admin" in schema["paths"]["/v1/admin/budget/override"]["post"]["tags"]
