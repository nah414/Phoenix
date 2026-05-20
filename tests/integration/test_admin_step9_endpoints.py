"""Phase 13 Step 9 — admin endpoint integration tests.

Covers the three new endpoints:

- ``POST /v1/identity/permissions/grant-prompt-verbatim`` — grants
  the privacy-bearing flag, updates the registry, writes a
  :class:`PermissionGrantEntry` to the ledger.
- ``POST /v1/admin/budget/cognition-override`` — cognition-specific
  budget bump with cognition-only scope tokens.
- ``GET /v1/admin/audit/cognition-spend`` — aggregates per-actor
  cognition spend over a rolling window.

Test isolation follows :mod:`test_admin_budget_override_step8`:
per-test runtime + reset of all singletons (audit emitter, ledger,
state backend, kill switch, permissions registry, rate limiter).
"""

from __future__ import annotations

import base64
import json
import time
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
    """Non-admin actor header (alice doesn't get bootstrap-tier defaults)."""
    from phoenix.identity.bootstrap import mint_bootstrap_actor

    alice = mint_bootstrap_actor("alice")
    payload = alice.to_payload()
    return "Phoenix-Actor " + base64.b64encode(json.dumps(payload).encode()).decode("ascii")


def _future_ts() -> float:
    return time.time() + 3600.0


# ---------------------------------------------------------------------------
# POST /v1/identity/permissions/grant-prompt-verbatim


class TestGrantPromptVerbatim:
    def test_admin_grants_verbatim_capability(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/identity/permissions/grant-prompt-verbatim",
                json={
                    "target_actor": "alice",
                    "capability": "can_store_prompt_verbatim",
                    "new_value": True,
                    "rationale": "audit drill — alice needs verbatim for forensics",
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["target_actor"] == "alice"
        assert body["capability"] == "can_store_prompt_verbatim"
        assert body["new_value"] is True
        assert isinstance(body["ledger_entry_hash"], str)
        assert len(body["ledger_entry_hash"]) == 64

    def test_grant_lands_in_registry(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            client.post(
                "/v1/identity/permissions/grant-prompt-verbatim",
                json={
                    "target_actor": "alice",
                    "capability": "can_store_prompt_verbatim",
                    "new_value": True,
                    "rationale": "audit drill",
                },
            )
        from phoenix.safety.permissions import get_registry

        alice_perms = get_registry().get("alice")
        assert alice_perms.can_store_prompt_verbatim is True

    def test_grant_appended_to_ledger(self, isolated_runtime: Path) -> None:
        from phoenix.state import get_state_backend

        with TestClient(app) as client:
            client.post(
                "/v1/identity/permissions/grant-prompt-verbatim",
                json={
                    "target_actor": "alice",
                    "capability": "can_store_prompt_encrypted",
                    "new_value": True,
                    "rationale": "encryption-pilot enrollment",
                },
            )
        rows = get_state_backend().list_ledger_entries(since_unix=0.0, limit=100)
        grant_rows = [r for r in rows if r["entry_kind"] == "permission_grant"]
        assert len(grant_rows) == 1
        payload = json.loads(grant_rows[0]["payload_json"])
        assert payload["granted_actor_name"] == "alice"
        assert payload["granted_by"] == "adam"  # bootstrap actor
        assert payload["capability"] == "can_store_prompt_encrypted"
        assert payload["new_value"] is True
        assert payload["rationale"] == "encryption-pilot enrollment"

    def test_revoke_via_new_value_false(self, isolated_runtime: Path) -> None:
        """Setting new_value=False revokes the capability."""
        with TestClient(app) as client:
            # Grant first.
            client.post(
                "/v1/identity/permissions/grant-prompt-verbatim",
                json={
                    "target_actor": "alice",
                    "capability": "can_store_prompt_verbatim",
                    "new_value": True,
                    "rationale": "initial grant",
                },
            )
            # Then revoke.
            resp = client.post(
                "/v1/identity/permissions/grant-prompt-verbatim",
                json={
                    "target_actor": "alice",
                    "capability": "can_store_prompt_verbatim",
                    "new_value": False,
                    "rationale": "incident closed; revoking grant",
                },
            )
        assert resp.status_code == 200
        from phoenix.safety.permissions import get_registry

        assert get_registry().get("alice").can_store_prompt_verbatim is False

    def test_non_grantable_capability_rejected(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/identity/permissions/grant-prompt-verbatim",
                json={
                    "target_actor": "alice",
                    "capability": "is_admin",  # not in _GRANTABLE_CAPABILITIES
                    "new_value": True,
                    "rationale": "trying to escalate via the privacy endpoint",
                },
            )
        assert resp.status_code == 400
        assert "not grantable" in resp.text

    def test_past_expiry_rejected(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/identity/permissions/grant-prompt-verbatim",
                json={
                    "target_actor": "alice",
                    "capability": "can_store_prompt_verbatim",
                    "new_value": True,
                    "rationale": "test",
                    "expires_at_unix": time.time() - 10.0,
                },
            )
        assert resp.status_code == 400
        assert "future" in resp.text.lower()

    def test_non_admin_rejected(self, isolated_runtime: Path) -> None:
        """Alice (non-admin) can't grant herself the verbatim cap."""
        with TestClient(app) as client:
            resp = client.post(
                "/v1/identity/permissions/grant-prompt-verbatim",
                json={
                    "target_actor": "alice",
                    "capability": "can_store_prompt_verbatim",
                    "new_value": True,
                    "rationale": "self-grant attempt",
                },
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /v1/admin/budget/cognition-override


class TestCognitionBudgetOverride:
    def test_admin_per_cognition_call_override(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/budget/cognition-override",
                json={
                    "actor_name": "bob",
                    "new_ceiling_usd": 50.0,
                    "expires_at_unix": _future_ts(),
                    "scope": "per_cognition_call",
                    "rationale": "evaluation run with elevated cap",
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["scope"] == "per_cognition_call"
        assert body["new_ceiling_usd"] == 50.0
        assert isinstance(body["override_id"], int)

    def test_per_actor_24h_cognition_override(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/budget/cognition-override",
                json={
                    "actor_name": "bob",
                    "new_ceiling_usd": 200.0,
                    "expires_at_unix": _future_ts(),
                    "scope": "per_actor_24h_cognition",
                },
            )
        assert resp.status_code == 200
        # Row landed in state backend with cognition-scope.
        from phoenix.state import get_state_backend

        active = get_state_backend().list_active_budget_overrides("bob", as_of_unix=time.time())
        assert any(r["scope"] == "per_actor_24h_cognition" for r in active)

    def test_physics_scope_rejected_on_cognition_endpoint(self, isolated_runtime: Path) -> None:
        """The physics-scope tokens are NOT valid on the cognition endpoint."""
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/budget/cognition-override",
                json={
                    "actor_name": "bob",
                    "new_ceiling_usd": 100.0,
                    "expires_at_unix": _future_ts(),
                    "scope": "per_solve",  # physics scope
                },
            )
        assert resp.status_code == 400
        assert "cognition" in resp.text.lower()

    def test_zero_ceiling_returns_422(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/budget/cognition-override",
                json={
                    "actor_name": "bob",
                    "new_ceiling_usd": 0.0,
                    "expires_at_unix": _future_ts(),
                    "scope": "per_cognition_call",
                },
            )
        assert resp.status_code == 422  # Pydantic gt=0

    def test_non_admin_rejected(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/admin/budget/cognition-override",
                json={
                    "actor_name": "alice",
                    "new_ceiling_usd": 100.0,
                    "expires_at_unix": _future_ts(),
                    "scope": "per_cognition_call",
                },
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /v1/admin/audit/cognition-spend


class TestCognitionSpendAudit:
    def test_empty_ledger_returns_zero_spend(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.get("/v1/admin/audit/cognition-spend")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_spend_usd"] == 0.0
        assert body["total_entries"] == 0
        assert body["window_hours"] == 24
        assert body["per_actor_spend_usd"] == {}

    def test_aggregates_cognition_entries(self, isolated_runtime: Path) -> None:
        """Append a fake cognition entry; verify it aggregates."""
        from phoenix.ledger import LedgerEntry, get_ledger

        # Manually inject a cognition ledger entry with cost in payload.
        ledger = get_ledger()
        ledger.append_entry(
            LedgerEntry(
                entry_id="cog-1",
                entry_kind="cognition",
                timestamp_unix=time.time(),
                actor_id="ash",
                parent_hash="",
                entry_hash="",
                payload={
                    "axis": "cross_model",
                    "cost_usd": 0.42,
                },
            )
        )

        with TestClient(app) as client:
            resp = client.get("/v1/admin/audit/cognition-spend")
        body = resp.json()
        assert body["total_entries"] == 1
        assert body["total_spend_usd"] == pytest.approx(0.42)
        assert body["per_actor_spend_usd"]["ash"] == pytest.approx(0.42)

    def test_actor_filter_narrows_results(self, isolated_runtime: Path) -> None:
        from phoenix.ledger import LedgerEntry, get_ledger

        ledger = get_ledger()
        for actor, cost in (("ash", 0.10), ("adam", 0.50), ("ash", 0.30)):
            ledger.append_entry(
                LedgerEntry(
                    entry_id=f"e-{actor}-{cost}",
                    entry_kind="cognition",
                    timestamp_unix=time.time(),
                    actor_id=actor,
                    parent_hash="",
                    entry_hash="",
                    payload={"cost_usd": cost},
                )
            )

        with TestClient(app) as client:
            resp = client.get("/v1/admin/audit/cognition-spend?actor_filter=ash")
        body = resp.json()
        assert body["total_entries"] == 2
        assert body["per_actor_spend_usd"] == {"ash": pytest.approx(0.40)}

    def test_non_cognition_entries_ignored(self, isolated_runtime: Path) -> None:
        """Solve entries and kill-switch entries don't contribute."""
        from phoenix.ledger import KillSwitchEntry, get_ledger, kill_switch_to_ledger_entry

        ledger = get_ledger()
        ledger.append_entry(
            kill_switch_to_ledger_entry(
                KillSwitchEntry(transition="engaged", by="adam", reason="test")
            )
        )

        with TestClient(app) as client:
            resp = client.get("/v1/admin/audit/cognition-spend")
        body = resp.json()
        assert body["total_entries"] == 0

    def test_window_hours_param(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.get("/v1/admin/audit/cognition-spend?window_hours=72")
        body = resp.json()
        assert body["window_hours"] == 72

    def test_window_hours_clamped_at_max(self, isolated_runtime: Path) -> None:
        """Beyond 30 days = 422 (Pydantic le=30*24)."""
        with TestClient(app) as client:
            resp = client.get("/v1/admin/audit/cognition-spend?window_hours=10000")
        assert resp.status_code == 422

    def test_non_admin_rejected(self, isolated_runtime: Path) -> None:
        with TestClient(app) as client:
            resp = client.get(
                "/v1/admin/audit/cognition-spend",
                headers={"Authorization": _alice_header()},
            )
        assert resp.status_code == 403
