"""Security regression: a missing Authorization header never means ``adam``.

Until 2026-09-16, ``phoenix.identity.bootstrap.extract_or_bootstrap`` minted
the all-privileged ``adam`` bootstrap actor for any request that omitted
``Authorization`` (CWE-306, missing authentication for a critical function).
Anyone who could reach the port was treated as the install owner: a Docker
container published on ``0.0.0.0``, a tailnet peer, or a browser page using
CSRF or DNS rebinding against ``127.0.0.1``.

This file pins the fixed behaviour:

(a) Requests with a missing, empty or whitespace-only header get HTTP 401 on
    admin and other authenticated routes, and cause no side effect.
(b) Garbage, unsigned, forged, wrong-key, tampered and expired
    ``Phoenix-Actor`` headers get 401, and so do malformed payloads that used
    to escape verification as HTTP 500 (``issued_at`` of ``1e400``, deeply
    nested JSON).
(c) A valid signed admin actor still succeeds, from any peer address, and a
    signed non-admin is still refused with 403.
(d) The cognition UI has no header-less mode. ``PHOENIX_UI_LOOPBACK_NO_TOKEN``
    (a short-lived pass-1 opt-in) has no effect. A configured
    ``PHOENIX_UI_TOKEN`` is always required, a signed actor is accepted when no
    token is configured, and neither a missing header nor the UI token ever
    authenticates any other route.
(e) The ``phoenix`` CLI (and so ``phoenix mcp serve``) never signs as ``adam``
    implicitly: no configured actor means no header. A configured actor gets a
    header the daemon actually verifies, and a ``default_actor`` from the config
    file is signed only for a loopback IP ``rest_url`` unless ``--actor`` was
    given on the invocation. The name ``localhost`` is not trusted: it can
    resolve to ``::1``, where a squatter can listen beside a daemon bound to
    ``127.0.0.1``. ``/v1/health`` is never signed, and the default ``rest_url``
    is the daemon's own ``127.0.0.1:8003``, so neither a wrong port nor a probe
    hands out a replayable header. Local requests never go through an
    environment or registry proxy, and a ``default_actor`` this machine has no
    key for (a host CLI pointed at a daemon in Docker) does not break
    unauthenticated commands.
"""

from __future__ import annotations

import base64
import http.server
import ipaddress
import json
import os
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import phoenix  # noqa: F401  -- triggers sys.path injection for vendored modules
from phoenix.api.routes import app
from phoenix.cli.config_loader import CLIConfig
from tests._signed_actor import actor_header, signed_client

_REMOTE_PEER = ("203.0.113.7", 40000)  # TEST-NET-3: never loopback
_TAILNET_PEER = ("100.101.102.103", 40000)  # Tailscale CGNAT range
_LOOPBACK_PEER = ("127.0.0.1", 50000)
_LOOPBACK_BASE = "http://127.0.0.1:8003"
_TAILNET_URL = "http://100.101.102.103:8003"  # a non-loopback rest_url


# ---------------------------------------------------------------------------
# fixtures + helpers


@pytest.fixture
def isolated_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Fresh state for every test; nothing here may touch the real profile.

    The header-less requests below would have mutated admin state on the
    unfixed code (enroll, kill switch, MCP registry, encryption keys), so
    every sink those routes write to is redirected under ``tmp_path``.
    """
    runtime = tmp_path / "phoenix_runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "corpora").mkdir()
    monkeypatch.setenv("PHOENIX_SQLITE_DB_PATH", str(runtime / "state.db"))
    monkeypatch.setenv("PHOENIX_AUDIT_DIR", str(runtime / "audit"))
    monkeypatch.setenv("PHOENIX_KILL_SWITCH_PATH", str(runtime / "kill_switch.json"))
    monkeypatch.setenv("PHOENIX_ENCRYPTION_KEYS_DIR", str(runtime / "encryption_keys"))
    monkeypatch.setenv("PHOENIX_CORPUS_DIR", str(runtime / "corpora"))
    monkeypatch.delenv("PHOENIX_UI_TOKEN", raising=False)
    monkeypatch.delenv("PHOENIX_UI_LOOPBACK_NO_TOKEN", raising=False)

    from phoenix.adapters.registry import reset_registry as reset_adapter_registry
    from phoenix.audit import reset_emitter
    from phoenix.ledger import reset_ledger
    from phoenix.mcp import server_registry as mcp_registry_module
    from phoenix.safety import kill_switch as ks_module
    from phoenix.safety import permissions as permissions_module
    from phoenix.safety.rate_limiter import get_limiter
    from phoenix.state import reset_state_backend

    reset_emitter()
    reset_ledger()
    reset_state_backend()
    ks_module._STORE = None
    get_limiter().reset_all()
    reset_adapter_registry()
    monkeypatch.setattr(
        permissions_module,
        "_REGISTRY",
        permissions_module.PermissionsRegistry(path=runtime / "actor_permissions.json"),
    )
    mcp_registry_module.reset_registry(persistence_path=runtime / "mcp_servers.json")
    try:
        yield runtime
    finally:
        try:
            ks_module.get_store().release()
        except Exception:
            pass
        reset_emitter()
        reset_ledger()
        reset_state_backend()
        ks_module._STORE = None
        get_limiter().reset_all()
        reset_adapter_registry()
        mcp_registry_module._REGISTRY_SINGLETON = None


def _encode_payload(payload: Any) -> str:
    return "Phoenix-Actor " + base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def _signed_payload(name: str, *, issued_at: int | None = None, key: bytes | None = None) -> dict:
    from actor.actor import Actor

    from phoenix.identity.keystore import get_install_fingerprint, load_or_generate_master_key

    master_key = key if key is not None else load_or_generate_master_key()
    signed = Actor.sign(
        name,
        master_key=master_key,
        fingerprint=get_install_fingerprint(),
        issued_at=issued_at,
    )
    return signed.to_payload()


def _request(client: TestClient, method: str, path: str, body: Any, headers: dict) -> Any:
    if body is None:
        return client.request(method, path, headers=headers)
    return client.request(method, path, json=body, headers=headers)


def _qho_body() -> dict:
    return {
        "physics_context": {
            "mass_kg": 9.1093837015e-31,
            "length_scale_m": 4e-9,
            "metadata": {"omega": 1e15, "n_grid_points": 200},
        },
        "tolerance": {
            "max_error_bar": 1e-3,
            "reproducibility_mode": "default",
            "latency_tier": "batch_realtime",
            "frontier_physics": False,
        },
        "metadata": {},
    }


# Valid bodies, so that FastAPI's 422 body validation cannot mask a missing
# auth check: every one of these reached the handler (and succeeded) on the
# unfixed code.
_HEADERLESS_SINKS: list[tuple[str, str, Any]] = [
    ("POST", "/v1/identity/enroll", {"actor_name": "mallory", "permissions": {"is_admin": True}}),
    ("POST", "/v1/admin/kill-switch/engage", {"rationale": "header-less engage"}),
    ("POST", "/v1/admin/kill-switch/release", {"rationale": "header-less release"}),
    ("GET", "/v1/admin/kill-switch/status", None),
    ("POST", "/v1/admin/encryption/rotate-key", {"name": "headerless", "force": True}),
    (
        "POST",
        "/v1/admin/mcp-servers/evil",
        {"transport": "stdio", "endpoint": "evil-binary", "allowed_tools": ["run"]},
    ),
    ("GET", "/v1/admin/mcp-servers", None),
    ("DELETE", "/v1/admin/mcp-servers/evil", None),
    ("GET", "/v1/admin/_ping", None),
    ("GET", "/v1/admin/health/detailed", None),
    ("GET", "/v1/audit/events", None),
    ("GET", "/v1/audit/ledger/verify", None),
    ("POST", "/v1/adapters", {"spec": "phoenix.adapters.identity_adapter:make_identity_adapter"}),
    ("GET", "/v1/adapters", None),
    ("POST", "/v1/identity/ws-token", None),
    ("POST", "/v1/tasks", _qho_body()),
]

_MISSING_HEADER_VARIANTS: dict[str, dict[str, str]] = {
    "absent": {},
    "empty": {"Authorization": ""},
    "whitespace": {"Authorization": "   "},
}


# ---------------------------------------------------------------------------
# (a) header-less requests are 401 and have no side effect


@pytest.mark.parametrize("variant", sorted(_MISSING_HEADER_VARIANTS))
@pytest.mark.parametrize(
    ("method", "path", "body"),
    _HEADERLESS_SINKS,
    ids=[f"{m} {p}" for m, p, _ in _HEADERLESS_SINKS],
)
def test_missing_authorization_header_is_401(
    isolated_runtime: Path, method: str, path: str, body: Any, variant: str
) -> None:
    with TestClient(app) as client:
        resp = _request(client, method, path, body, _MISSING_HEADER_VARIANTS[variant])
    assert resp.status_code == 401, resp.text
    assert "adam" not in resp.text


def test_headerless_request_from_loopback_peer_is_still_401(isolated_runtime: Path) -> None:
    """Loopback is not a credential: the same-machine browser/CSRF case."""
    with TestClient(app, base_url=_LOOPBACK_BASE, client=_LOOPBACK_PEER) as client:
        assert client.get("/v1/admin/_ping").status_code == 401
        assert client.post("/v1/identity/enroll", json={"actor_name": "mallory"}).status_code == 401


def test_headerless_engage_leaves_kill_switch_disengaged(isolated_runtime: Path) -> None:
    from phoenix.safety.kill_switch import get_store

    with TestClient(app, client=_REMOTE_PEER) as client:
        resp = client.post("/v1/admin/kill-switch/engage", json={"rationale": "dos"})
    assert resp.status_code == 401, resp.text
    assert get_store().read().engaged is False


def test_headerless_enroll_grants_nothing(isolated_runtime: Path) -> None:
    from phoenix.safety.permissions import get_registry

    with TestClient(app, client=_REMOTE_PEER) as client:
        resp = client.post(
            "/v1/identity/enroll",
            json={"actor_name": "mallory", "permissions": {"is_admin": True}},
        )
    assert resp.status_code == 401, resp.text
    registry = get_registry()
    assert "mallory" not in registry.all_actors()
    assert registry.get("mallory").is_admin is False


def test_headerless_mcp_registration_registers_nothing(isolated_runtime: Path) -> None:
    from phoenix.mcp.server_registry import get_registry as get_mcp_registry

    with TestClient(app, client=_REMOTE_PEER) as client:
        resp = client.post(
            "/v1/admin/mcp-servers/evil",
            json={"transport": "stdio", "endpoint": "evil-binary", "allowed_tools": ["run"]},
        )
    assert resp.status_code == 401, resp.text
    assert get_mcp_registry().get("evil") is None


def _authenticated_routes() -> list[APIRoute]:
    routes = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        header_names = {param.alias.lower() for param in route.dependant.header_params}
        if "authorization" in header_names:
            routes.append(route)
    return routes


_AUTH_ROUTES = _authenticated_routes()


def test_route_sweep_covers_the_admin_surface() -> None:
    """Guard against the sweep below silently shrinking to nothing."""
    paths = {route.path for route in _AUTH_ROUTES}
    assert "/v1/identity/enroll" in paths
    assert "/v1/admin/kill-switch/engage" in paths
    assert "/v1/admin/encryption/rotate-key" in paths
    assert len(_AUTH_ROUTES) >= 40


@pytest.mark.parametrize(
    "route",
    _AUTH_ROUTES,
    ids=[f"{sorted(r.methods)[0]} {r.path}" for r in _AUTH_ROUTES],
)
def test_every_authenticated_route_rejects_headerless(
    isolated_runtime: Path, route: APIRoute
) -> None:
    """Every route that reads ``Authorization`` refuses a request without one.

    Path parameters get a probe value. Routes with a JSON body get ``{}``: a
    required-field model answers 422 before the handler runs, which is still
    a refusal. Nothing may answer 2xx, 403 (an actor was resolved) or 5xx.
    """
    method = sorted(route.methods)[0]
    path = route.path
    for name in route.param_convertors:
        path = path.replace("{" + name + "}", "headerless-probe")
    with TestClient(app, client=_REMOTE_PEER, raise_server_exceptions=False) as client:
        if route.body_field is not None:
            resp = client.request(method, path, json={})
        else:
            resp = client.request(method, path)
    allowed = {401, 422} if route.body_field is not None else {401}
    assert resp.status_code in allowed, f"{method} {path} -> {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# (b) garbage / unsigned / forged / expired headers are 401


def _bad_headers() -> dict[str, Any]:
    """Build lazily: some cases need the real keystore."""
    return {
        "wrong-scheme": "Bearer not-a-phoenix-actor",
        "scheme-only": "Phoenix-Actor",
        "scheme-space-only": "Phoenix-Actor ",
        "not-base64": "Phoenix-Actor not!base64!@#",
        "base64-not-json": "Phoenix-Actor " + base64.b64encode(b"\xff\xfe junk").decode("ascii"),
        "json-list": _encode_payload([]),
        # The exact shape the pre-fix CLI sent: a bare name, no signature.
        "unsigned-name-only": _encode_payload({"name": "adam"}),
        "forged-zero-signature": _encode_payload(
            {
                **_signed_payload("adam"),
                "signature": base64.b64encode(b"\x00" * 32).decode("ascii"),
            }
        ),
        "wrong-master-key": _encode_payload(_signed_payload("adam", key=os.urandom(32))),
        "tampered-name": _encode_payload({**_signed_payload("alice"), "name": "adam"}),
        "expired": _encode_payload(_signed_payload("adam", issued_at=int(time.time()) - 3600)),
        "future-dated": _encode_payload(_signed_payload("adam", issued_at=int(time.time()) + 3600)),
    }


_BAD_HEADER_IDS = [
    "wrong-scheme",
    "scheme-only",
    "scheme-space-only",
    "not-base64",
    "base64-not-json",
    "json-list",
    "unsigned-name-only",
    "forged-zero-signature",
    "wrong-master-key",
    "tampered-name",
    "expired",
    "future-dated",
]


@pytest.mark.parametrize("case", _BAD_HEADER_IDS)
def test_bad_authorization_header_is_401(isolated_runtime: Path, case: str) -> None:
    header = _bad_headers()[case]
    with TestClient(app) as client:
        ping = client.get("/v1/admin/_ping", headers={"Authorization": header})
        enroll = client.post(
            "/v1/identity/enroll",
            json={"actor_name": "mallory", "permissions": {"is_admin": True}},
            headers={"Authorization": header},
        )
        engage = client.post(
            "/v1/admin/kill-switch/engage",
            json={"rationale": "forged"},
            headers={"Authorization": header},
        )
    assert ping.status_code == 401, ping.text
    assert enroll.status_code == 401, enroll.text
    assert engage.status_code == 401, engage.text


def _raw_header(raw: bytes) -> str:
    return "Phoenix-Actor " + base64.b64encode(raw).decode("ascii")


_DEEP = 5000  # far past the interpreter recursion limit the JSON decoder honours

# Payloads that made verification raise something other than IdentityError
# (OverflowError, RecursionError), which escaped as HTTP 500 before 2026-09-16.
_MALFORMED_PAYLOADS: dict[str, bytes] = {
    "issued-at-1e400": (
        b'{"name": "adam", "identity_fingerprint": "x", "issued_at": 1e400, "signature": "AAAA"}'
    ),
    "issued-at-infinity": (
        b'{"name": "adam", "identity_fingerprint": "x", "issued_at": Infinity, "signature": "AAAA"}'
    ),
    "issued-at-nan": (
        b'{"name": "adam", "identity_fingerprint": "x", "issued_at": NaN, "signature": "AAAA"}'
    ),
    "deeply-nested-list": b"[" * _DEEP + b"]" * _DEEP,
    "deeply-nested-name": (
        b'{"name": ' + b"[" * _DEEP + b"]" * _DEEP + b', "identity_fingerprint": "x", '
        b'"issued_at": 1, "signature": "AAAA"}'
    ),
    "huge-int-issued-at": (
        b'{"name": "adam", "identity_fingerprint": "x", "issued_at": '
        + b"9" * 5000
        + b', "signature": "AAAA"}'
    ),
}


@pytest.mark.parametrize("case", sorted(_MALFORMED_PAYLOADS))
def test_malformed_actor_payload_is_401_not_500(isolated_runtime: Path, case: str) -> None:
    header = {"Authorization": _raw_header(_MALFORMED_PAYLOADS[case])}
    with TestClient(app, raise_server_exceptions=False) as client:
        responses = {
            "admin ping": client.get("/v1/admin/_ping", headers=header),
            "enroll": client.post(
                "/v1/identity/enroll",
                json={"actor_name": "mallory", "permissions": {"is_admin": True}},
                headers=header,
            ),
            "tasks": client.post("/v1/tasks", json=_qho_body(), headers=header),
            "cognition ui": client.get("/v1/cognition/corpora", headers=header),
        }
    for name, resp in responses.items():
        assert resp.status_code == 401, f"{name}: {resp.status_code} {resp.text[:300]}"
        assert "identity error" in resp.json()["detail"], name


# ---------------------------------------------------------------------------
# (c) a valid signed actor keeps working, from any peer


def test_signed_admin_ping_succeeds(isolated_runtime: Path) -> None:
    with TestClient(app) as client:
        resp = client.get("/v1/admin/_ping", headers={"Authorization": actor_header("adam")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["actor"] == "adam"


def test_signed_admin_succeeds_from_non_loopback_peer(isolated_runtime: Path) -> None:
    """Signed requests are authenticated by the signature, not the address."""
    with signed_client(app, client=_REMOTE_PEER) as client:
        ping = client.get("/v1/admin/_ping")
        status = client.get("/v1/admin/kill-switch/status")
    assert ping.status_code == 200, ping.text
    assert status.status_code == 200, status.text


def test_signed_admin_can_enroll(isolated_runtime: Path) -> None:
    from phoenix.safety.permissions import get_registry

    with signed_client(app) as client:
        resp = client.post(
            "/v1/identity/enroll",
            json={"actor_name": "carol", "permissions": {"can_load_adapter": True}},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["enrolled_by"] == "adam"
    assert get_registry().get("carol").can_load_adapter is True


def test_signed_admin_can_engage_and_release_kill_switch(isolated_runtime: Path) -> None:
    from phoenix.safety.kill_switch import get_store

    with signed_client(app) as client:
        engage = client.post("/v1/admin/kill-switch/engage", json={"rationale": "drill"})
        assert engage.status_code == 200, engage.text
        assert get_store().read().engaged is True
        release = client.post("/v1/admin/kill-switch/release", json={"rationale": "drill over"})
    assert release.status_code == 200, release.text
    assert get_store().read().engaged is False


def test_signed_non_admin_is_still_403(isolated_runtime: Path) -> None:
    with TestClient(app) as client:
        resp = client.get("/v1/admin/_ping", headers={"Authorization": actor_header("alice")})
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# (d) cognition UI: a UI token or a signed actor, never header-less

_CORPORA = "/v1/cognition/corpora"
_UI_TOKEN = "s3cret-token"
# Pass 1 briefly shipped this opt-in; it was replaced by the per-launch token.
_OBSOLETE_LOOPBACK_FLAG = "PHOENIX_UI_LOOPBACK_NO_TOKEN"

_UI_ENDPOINTS: list[tuple[str, str, Any]] = [
    ("GET", _CORPORA, None),
    ("POST", "/v1/cognition/audit", {"corpus": "missing.jsonl"}),
    ("POST", "/v1/cognition/evaluate", {"corpus": "missing.jsonl", "stub": True}),
    ("POST", "/v1/cognition/adapt", {"dataset": "felm", "path": "in.jsonl", "out": "out.jsonl"}),
    ("POST", "/v1/cognition/train", {"corpus": "missing.jsonl", "out": "model.txt"}),
    ("GET", "/v1/cognition/jobs/job_missing", None),
]

# Every request shape the removed loopback mode admitted without a token:
# loopback peer (IPv4, IPv6, IPv4-mapped), loopback Host, loopback or no Origin.
_LOOPBACK_REQUESTS = [
    pytest.param("127.0.0.1:8003", _LOOPBACK_PEER, None, id="ipv4"),
    pytest.param("localhost:8003", _LOOPBACK_PEER, "http://localhost:8003", id="localhost"),
    # Starlette's TestClient cannot parse an IPv6 base_url, so the bracketed
    # Host is sent explicitly.
    pytest.param("[::1]:8003", ("::1", 50000), "http://[::1]:8003", id="ipv6"),
    pytest.param(
        "127.0.0.1:8003", ("::ffff:127.0.0.1", 50000), "http://127.0.0.1:8003", id="ipv4-mapped"
    ),
]


def test_ui_default_is_closed_even_on_loopback(isolated_runtime: Path) -> None:
    with TestClient(app, base_url=_LOOPBACK_BASE, client=_LOOPBACK_PEER) as client:
        resp = client.get(_CORPORA)
    assert resp.status_code == 401, resp.text


@pytest.mark.parametrize("peer", [_LOOPBACK_PEER, _REMOTE_PEER, _TAILNET_PEER, ("testclient", 5)])
@pytest.mark.parametrize(
    ("method", "path", "body"), _UI_ENDPOINTS, ids=[f"{m} {p}" for m, p, _ in _UI_ENDPOINTS]
)
def test_ui_no_header_and_no_token_is_401(
    isolated_runtime: Path, peer: tuple[str, int], method: str, path: str, body: Any
) -> None:
    with TestClient(app, base_url=_LOOPBACK_BASE, client=peer) as client:
        resp = _request(client, method, path, body, {})
    assert resp.status_code == 401, resp.text


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
@pytest.mark.parametrize(("host", "peer", "origin"), _LOOPBACK_REQUESTS)
def test_ui_loopback_flag_env_var_has_no_effect(
    isolated_runtime: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
    host: str,
    peer: tuple[str, int],
    origin: str | None,
) -> None:
    """The shapes the removed loopback mode answered 200 are now all 401."""
    monkeypatch.setenv(_OBSOLETE_LOOPBACK_FLAG, value)
    headers = {"Host": host}
    if origin:
        headers["Origin"] = origin
    with TestClient(app, base_url=_LOOPBACK_BASE, client=peer) as client:
        resp = client.get(_CORPORA, headers=headers)
    assert resp.status_code == 401, resp.text


def test_ui_module_has_no_loopback_mode() -> None:
    from phoenix.api import cognition_ui

    for name in (
        "UI_LOOPBACK_NO_TOKEN_ENV",
        "_loopback_no_token_allowed",
        "_host_header_is_loopback",
        "_origin_is_loopback_or_absent",
    ):
        assert not hasattr(cognition_ui, name), name


def test_ui_loopback_flag_never_authenticates_any_route(
    isolated_runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_OBSOLETE_LOOPBACK_FLAG, "1")
    with TestClient(app, base_url=_LOOPBACK_BASE, client=_LOOPBACK_PEER) as client:
        assert client.get(_CORPORA).status_code == 401
        assert client.get("/v1/admin/_ping").status_code == 401
        assert client.post("/v1/identity/enroll", json={"actor_name": "mallory"}).status_code == 401
        assert (
            client.post("/v1/admin/kill-switch/engage", json={"rationale": "x"}).status_code == 401
        )


@pytest.mark.parametrize(("host", "peer", "origin"), _LOOPBACK_REQUESTS)
def test_ui_token_is_required_when_configured_even_on_loopback(
    isolated_runtime: Path,
    monkeypatch: pytest.MonkeyPatch,
    host: str,
    peer: tuple[str, int],
    origin: str | None,
) -> None:
    monkeypatch.setenv("PHOENIX_UI_TOKEN", _UI_TOKEN)
    monkeypatch.setenv(_OBSOLETE_LOOPBACK_FLAG, "1")
    headers = {"Host": host}
    if origin:
        headers["Origin"] = origin
    with TestClient(app, base_url=_LOOPBACK_BASE, client=peer) as client:
        assert client.get(_CORPORA, headers=headers).status_code == 401
        wrong = {**headers, "X-Phoenix-UI-Token": "wrong"}
        assert client.get(_CORPORA, headers=wrong).status_code == 401
        prefix = {**headers, "X-Phoenix-UI-Token": _UI_TOKEN[:-1]}
        assert client.get(_CORPORA, headers=prefix).status_code == 401
        right = {**headers, "X-Phoenix-UI-Token": _UI_TOKEN}
        assert client.get(_CORPORA, headers=right).status_code == 200


def test_ui_token_is_required_when_configured_even_with_signed_actor(
    isolated_runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PHOENIX_UI_TOKEN", _UI_TOKEN)
    with TestClient(app, client=_REMOTE_PEER) as client:
        signed_only = client.get(_CORPORA, headers={"Authorization": actor_header("adam")})
        both = client.get(
            _CORPORA,
            headers={"Authorization": actor_header("adam"), "X-Phoenix-UI-Token": _UI_TOKEN},
        )
    assert signed_only.status_code == 401, signed_only.text
    assert both.status_code == 200, both.text


def test_ui_token_admits_remote_peer(
    isolated_runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The documented Tailscale phone flow: token, non-loopback peer."""
    monkeypatch.setenv("PHOENIX_UI_TOKEN", _UI_TOKEN)
    token = {"X-Phoenix-UI-Token": _UI_TOKEN}
    with TestClient(app, client=_TAILNET_PEER) as client:
        ok = client.get(_CORPORA, headers=token)
        # The UI token is not an actor credential for the rest of the API.
        admin = client.get("/v1/admin/_ping", headers=token)
        enroll = client.post("/v1/identity/enroll", json={"actor_name": "mallory"}, headers=token)
        engage = client.post("/v1/admin/kill-switch/engage", json={"rationale": "x"}, headers=token)
    assert ok.status_code == 200, ok.text
    assert admin.status_code == 401, admin.text
    assert enroll.status_code == 401, enroll.text
    assert engage.status_code == 401, engage.text


@pytest.mark.parametrize("peer", [_REMOTE_PEER, _LOOPBACK_PEER])
def test_ui_signed_actor_accepted_when_no_token_configured(
    isolated_runtime: Path, monkeypatch: pytest.MonkeyPatch, peer: tuple[str, int]
) -> None:
    with TestClient(app, base_url=_LOOPBACK_BASE, client=peer) as client:
        signed = client.get(_CORPORA, headers={"Authorization": actor_header("adam")})
        job = client.get(
            "/v1/cognition/jobs/job_missing", headers={"Authorization": actor_header()}
        )
    assert signed.status_code == 200, signed.text
    # Admitted by the gate; the handler then reports the unknown job.
    assert job.status_code == 404, job.text


def test_ui_bad_signed_header_is_401_even_with_obsolete_flag(
    isolated_runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_OBSOLETE_LOOPBACK_FLAG, "1")
    with TestClient(app, base_url=_LOOPBACK_BASE, client=_LOOPBACK_PEER) as client:
        bad = client.get(_CORPORA, headers={"Authorization": _encode_payload({"name": "adam"})})
    assert bad.status_code == 401, bad.text


# ---------------------------------------------------------------------------
# (e) the CLI signs only as an actor the operator configured


@pytest.fixture
def local_master_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A fresh install keystore under ``tmp_path``, with the key generated in setup.

    Simulates a fresh home: the CLI signing tests below never depend on an
    earlier test (or an earlier daemon run) having created the master key, and
    never read or write the real ``~/.phoenix``. The daemon side verifies
    against the same key because both go through ``keystore._keystore_dir``.
    """
    from phoenix.identity import keystore

    keystore_dir = tmp_path / "fresh-home" / ".phoenix" / "runtime"
    monkeypatch.setattr(keystore, "_keystore_dir", lambda: keystore_dir)
    keystore.load_or_generate_master_key()
    assert (keystore_dir / "master_key.bin").is_file()
    return keystore_dir


@pytest.fixture
def cli_to_app(
    isolated_runtime: Path, local_master_key: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[list[httpx.Request]]:
    """Route every ``httpx.Client`` the CLI builds into the in-process app.

    Works for any ``rest_url`` host (nothing touches the network) and records
    each request the CLI sent, so tests can assert on its headers.
    """
    seen: list[httpx.Request] = []
    test_client = TestClient(app, client=_REMOTE_PEER)
    test_client.__enter__()  # noqa: SLF001 -- run the app lifespan
    real_init = httpx.Client.__init__

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        resp = test_client.request(
            request.method,
            request.url.raw_path.decode("ascii"),
            headers=dict(request.headers),
            content=request.content,
        )
        return httpx.Response(resp.status_code, headers=dict(resp.headers), content=resp.content)

    def _patched_init(self: httpx.Client, *args: Any, **kwargs: Any) -> None:
        if not isinstance(self, TestClient):
            kwargs["transport"] = httpx.MockTransport(_handler)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", _patched_init)
    try:
        yield seen
    finally:
        test_client.__exit__(None, None, None)


_LOOPBACK_REST_URLS = [
    "http://127.0.0.1:8003",
    "http://[::1]:8003",
    "http://127.20.30.40:8003",
    "https://127.0.0.1:8443",
    "http://[::ffff:127.0.0.1]:8003",
]

# The name ``localhost`` is local (no proxy) but not trusted for default_actor:
# it can resolve to ::1 before 127.0.0.1. Each maps to the URL the refusal suggests.
_LOCALHOST_REST_URLS = {
    "http://localhost:8003": "http://127.0.0.1:8003",
    "https://localhost:8443": "https://127.0.0.1:8443",
    "http://LOCALHOST:8003": "http://127.0.0.1:8003",
    "http://localhost.:8003": "http://127.0.0.1:8003",
}

_NON_LOOPBACK_REST_URLS = [
    "http://100.101.102.103:8003",  # a tailnet peer
    "http://203.0.113.7:8003",
    "http://phoenix.example:8003",
    "http://localhost.attacker.example:8003",
    "http://127.0.0.1.attacker.example:8003",
    "http://127.0.0.1@attacker.example:8003",  # userinfo: the host is attacker.example
    "http://[::2]:8003",
    "http://0.0.0.0:8003",
    "http://2130706433:8003",  # integer spelling of 127.0.0.1: refused, not guessed
    "localhost:8003",  # no scheme, so no host: refused
]


def test_cli_default_header_absent_without_configured_actor(
    isolated_runtime: Path, local_master_key: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No ``--actor`` and no ``default_actor``: no header at all, never an implicit adam."""
    from phoenix.cli.http_client import SIGNING_NO_ACTOR, build_client

    client = build_client(CLIConfig(rest_url=_LOOPBACK_BASE))
    assert client.actor_name is None
    assert client.signing_actor is None
    assert client.signing_state == SIGNING_NO_ACTOR
    headers = client._build_headers()
    assert "Authorization" not in headers
    assert capsys.readouterr().err == ""
    with TestClient(app, base_url=_LOOPBACK_BASE, client=_LOOPBACK_PEER) as tc:
        resp = tc.get("/v1/admin/_ping", headers=headers)
    assert resp.status_code == 401, resp.text
    assert "adam" not in resp.text


def test_cli_default_header_absent_401_says_how_to_configure_an_actor(
    cli_to_app: list[httpx.Request],
) -> None:
    from phoenix.cli.http_client import CLIHTTPError, build_client

    client = build_client(CLIConfig(rest_url=_LOOPBACK_BASE))
    with pytest.raises(CLIHTTPError) as excinfo:
        client.get("/v1/admin/_ping")
    assert excinfo.value.status_code == 401
    message = str(excinfo.value)
    assert "default_actor" in message
    assert "config.yaml" in message
    assert "--actor" in message
    assert "authorization" not in cli_to_app[-1].headers
    # Unauthenticated routes still work without an actor.
    assert client.get("/v1/health")["status"] == "ok"


@pytest.mark.parametrize("rest_url", _LOOPBACK_REST_URLS)
def test_cli_default_header_verifies_as_adam(
    isolated_runtime: Path,
    local_master_key: Path,
    capsys: pytest.CaptureFixture[str],
    rest_url: str,
) -> None:
    """The one-time setup (``default_actor: adam``) signs for a loopback rest_url."""
    from phoenix.cli.http_client import SIGNING_SIGNED, build_client

    client = build_client(CLIConfig(rest_url=rest_url, default_actor="adam"))
    assert client.actor_from_flag is False
    assert client.signing_state == SIGNING_SIGNED
    headers = client._build_headers()
    assert headers.get("Authorization", "").startswith("Phoenix-Actor ")
    assert capsys.readouterr().err == ""
    with TestClient(app, client=_REMOTE_PEER) as tc:
        resp = tc.get("/v1/admin/_ping", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["actor"] == "adam"


@pytest.mark.parametrize("rest_url", _NON_LOOPBACK_REST_URLS + sorted(_LOCALHOST_REST_URLS))
def test_cli_default_actor_is_refused_for_non_loopback_rest_url(
    isolated_runtime: Path,
    local_master_key: Path,
    capsys: pytest.CaptureFixture[str],
    rest_url: str,
) -> None:
    """``default_actor`` alone never sends a replayable header to a remote host (or ``localhost``)."""
    from phoenix.cli.http_client import SIGNING_REFUSED_REMOTE, build_client

    client = build_client(CLIConfig(rest_url=rest_url, default_actor="adam"))
    assert client.signing_refused is True
    assert client.signing_actor is None
    assert client.signing_state == SIGNING_REFUSED_REMOTE
    assert "Authorization" not in client._build_headers()
    assert "Authorization" not in client._build_headers()
    err = capsys.readouterr().err
    # A clear refusal, printed once per client rather than once per request.
    assert err.count("not signing as default_actor 'adam'") == 1, err
    assert "--actor adam" in err


@pytest.mark.parametrize(("rest_url", "suggested"), sorted(_LOCALHOST_REST_URLS.items()))
def test_cli_default_actor_refusal_for_localhost_names_the_loopback_ip_url(
    isolated_runtime: Path,
    local_master_key: Path,
    capsys: pytest.CaptureFixture[str],
    rest_url: str,
    suggested: str,
) -> None:
    """The ``localhost`` refusal says why (``::1``) and which ``rest_url`` to use instead."""
    from phoenix.cli.http_client import build_client, is_loopback_url

    assert is_loopback_url(rest_url) is False
    assert is_loopback_url(suggested) is True
    client = build_client(CLIConfig(rest_url=rest_url, default_actor="adam"))
    message = client.signing_refusal_message()
    assert "'localhost'" in message
    assert "::1" in message
    assert f"Set rest_url to {suggested} " in message
    assert "--actor adam" in message
    assert "Authorization" not in client._build_headers()
    assert f"Set rest_url to {suggested} " in capsys.readouterr().err


def test_cli_default_actor_refusal_is_explained_on_401(
    cli_to_app: list[httpx.Request], capsys: pytest.CaptureFixture[str]
) -> None:
    from phoenix.cli.http_client import CLIHTTPError, build_client

    client = build_client(CLIConfig(rest_url=_TAILNET_URL, default_actor="adam"))
    with pytest.raises(CLIHTTPError) as excinfo:
        client.get("/v1/admin/_ping")
    assert excinfo.value.status_code == 401
    assert "--actor adam" in str(excinfo.value)
    assert all("authorization" not in request.headers for request in cli_to_app)
    assert "not signing as default_actor 'adam'" in capsys.readouterr().err


@pytest.mark.parametrize("rest_url", [_TAILNET_URL, "http://phoenix.example:8003"])
def test_cli_actor_flag_signs_for_non_loopback_rest_url(
    cli_to_app: list[httpx.Request], capsys: pytest.CaptureFixture[str], rest_url: str
) -> None:
    """``--actor`` on the invocation is the explicit opt-in to sign for a remote daemon."""
    from phoenix.cli.http_client import SIGNING_SIGNED, build_client

    client = build_client(CLIConfig(rest_url=rest_url, default_actor="bob"), actor_override="adam")
    assert client.actor_name == "adam"
    assert client.actor_from_flag is True
    assert client.signing_state == SIGNING_SIGNED
    assert client.get("/v1/admin/_ping")["actor"] == "adam"
    assert cli_to_app[-1].headers["authorization"].startswith("Phoenix-Actor ")
    assert capsys.readouterr().err == ""


def test_cli_main_applies_the_signing_policy_end_to_end(
    cli_to_app: list[httpx.Request], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from phoenix.cli.entry import main as cli_main

    bare = tmp_path / "bare.yaml"
    bare.write_text(f"rest_url: {_LOOPBACK_BASE}\n", encoding="utf-8")
    configured = tmp_path / "configured.yaml"
    configured.write_text(f"rest_url: {_LOOPBACK_BASE}\ndefault_actor: adam\n", encoding="utf-8")
    verify = ["--format", "json", "audit", "verify"]

    # No actor configured: unsigned, 401, and the CLI says how to configure one.
    assert cli_main(["--config", str(bare), *verify]) == 3
    err = capsys.readouterr().err
    assert "default_actor" in err and "--actor" in err
    assert "authorization" not in cli_to_app[-1].headers

    # default_actor + loopback rest_url: signed.
    assert cli_main(["--config", str(configured), *verify]) == 0
    assert cli_to_app[-1].headers["authorization"].startswith("Phoenix-Actor ")
    capsys.readouterr()

    # default_actor + non-loopback rest_url: refused, sent unsigned, 401.
    remote = ["--config", str(configured), "--rest-url", _TAILNET_URL]
    assert cli_main([*remote, *verify]) == 3
    err = capsys.readouterr().err
    assert "not signing as default_actor 'adam'" in err
    assert "authorization" not in cli_to_app[-1].headers

    # --actor + non-loopback rest_url: signed.
    assert cli_main([*remote, "--actor", "adam", *verify]) == 0
    assert cli_to_app[-1].headers["authorization"].startswith("Phoenix-Actor ")


def test_cli_named_actor_header_verifies_as_that_actor(
    isolated_runtime: Path, local_master_key: Path
) -> None:
    from phoenix.cli.http_client import CLIHTTPClient

    headers = CLIHTTPClient(base_url=_LOOPBACK_BASE, actor_name="alice")._build_headers()
    with TestClient(app) as client:
        resp = client.get("/v1/admin/_ping", headers=headers)
    # Verified as alice (not 401), who is not an admin.
    assert resp.status_code == 403, resp.text


def test_cli_without_local_keystore_never_creates_one(
    isolated_runtime: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from phoenix.cli.http_client import CLIHTTPClient, CLIHTTPError
    from phoenix.identity import keystore

    empty = tmp_path / "no-keystore"
    monkeypatch.setattr(keystore, "_keystore_dir", lambda: empty)

    implicit = CLIHTTPClient(base_url=_LOOPBACK_BASE, actor_name=None)._build_headers()
    assert "Authorization" not in implicit
    # An explicit --actor this machine cannot sign is a hard failure ...
    with pytest.raises(CLIHTTPError, match="No Phoenix master key"):
        CLIHTTPClient(
            base_url=_LOOPBACK_BASE, actor_name="adam", actor_from_flag=True
        )._build_headers()
    # ... while a default_actor goes out unsigned, keeping the reason for a 401.
    configured = CLIHTTPClient(base_url=_LOOPBACK_BASE, actor_name="adam")
    assert "Authorization" not in configured._build_headers()
    assert "No Phoenix master key" in (configured._signing_error or "")
    assert "No Phoenix master key" in (configured.signing_problem() or "")
    assert not (empty / "master_key.bin").exists()


def test_cli_default_actor_without_local_key_still_reaches_unauthenticated_routes(
    cli_to_app: list[httpx.Request],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The Docker host case: ``default_actor`` is set, but this machine has no install key.

    ``phoenix health`` needs no actor and must keep working; a protected route's
    401 names the keystore problem and the ``docker exec`` way to sign.
    """
    from phoenix.cli import http_client
    from phoenix.cli.entry import main as cli_main
    from phoenix.identity import keystore

    host_without_key = tmp_path / "host-home" / ".phoenix" / "runtime"
    monkeypatch.setattr(keystore, "_keystore_dir", lambda: host_without_key)
    config = tmp_path / "config.yaml"
    config.write_text(f"rest_url: {_LOOPBACK_BASE}\ndefault_actor: adam\n", encoding="utf-8")

    assert cli_main(["--config", str(config), "--format", "json", "health"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
    assert "authorization" not in cli_to_app[-1].headers

    assert cli_main(["--config", str(config), "--format", "json", "audit", "verify"]) == 3
    err = capsys.readouterr().err
    assert "401" in err
    assert "could not sign as default_actor 'adam'" in err
    assert "No Phoenix master key" in err
    assert "docker exec" in err
    assert "authorization" not in cli_to_app[-1].headers

    # identity show reports the problem instead of claiming "signed", for a
    # default_actor and an explicit --actor alike, and the daemon is reachable.
    for extra in ([], ["--actor", "adam"]):
        show = ["--config", str(config), *extra, "--format", "json", "identity", "show"]
        assert cli_main(show) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["signing"] == http_client.SIGNING_UNAVAILABLE
        assert payload["daemon_reachable"] is True
        assert "No Phoenix master key" in payload["hint"]
        assert "docker exec" in payload["hint"]
    assert not (host_without_key / "master_key.bin").exists()


def test_cli_signature_the_daemon_cannot_verify_gets_a_foreign_key_hint(
    cli_to_app: list[httpx.Request], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A host key that differs from the daemon's (Docker, another OS user): 401 plus a hint."""
    from phoenix.cli import http_client
    from phoenix.cli.http_client import CLIHTTPError, build_client
    from phoenix.identity import keystore

    daemon_keystore = keystore._keystore_dir()
    host_keystore = tmp_path / "other-host-home" / ".phoenix" / "runtime"
    real_sign = http_client._sign_actor

    def _sign_with_the_host_key(name: str) -> str:
        monkeypatch.setattr(keystore, "_keystore_dir", lambda: host_keystore)
        try:
            keystore.load_or_generate_master_key()
            return real_sign(name)
        finally:
            monkeypatch.setattr(keystore, "_keystore_dir", lambda: daemon_keystore)

    monkeypatch.setattr(http_client, "_sign_actor", _sign_with_the_host_key)
    client = build_client(CLIConfig(rest_url=_LOOPBACK_BASE, default_actor="adam"))
    with pytest.raises(CLIHTTPError) as excinfo:
        client.get("/v1/admin/_ping")
    assert excinfo.value.status_code == 401
    assert cli_to_app[-1].headers["authorization"].startswith("Phoenix-Actor ")
    message = str(excinfo.value)
    assert "did not accept the header signed as 'adam'" in message
    assert "docker exec" in message


class _RecordingHandler(http.server.BaseHTTPRequestHandler):
    """Answers any GET with JSON naming its listener, and records what it received."""

    def do_GET(self) -> None:  # noqa: N802 -- http.server naming
        server: Any = self.server
        server.seen.append((self.requestline, {k.lower(): v for k, v in self.headers.items()}))
        body = json.dumps({"status": "ok", "via": server.label}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


@contextmanager
def _recording_listener(label: str, host: str = "127.0.0.1", port: int = 0) -> Iterator[Any]:
    """A throwaway HTTP listener on a loopback address (never any other interface).

    Binds the first address ``host`` resolves to, the one a client tries first,
    so ``localhost`` does not wait on a refused ``::1`` connection. ``port`` 0
    picks a free port; a given port lets a second listener share it on the other
    address family.
    """
    first = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0]
    family, sockaddr = first[0], first[4]
    server_class = type("_Listener", (http.server.ThreadingHTTPServer,), {"address_family": family})
    server: Any = server_class((sockaddr[0], port), _RecordingHandler)
    assert ipaddress.ip_address(server.server_address[0]).is_loopback
    server.seen = []
    server.label = label
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    ("host", "actor_override"),
    [
        pytest.param("127.0.0.1", None, id="127.0.0.1-default_actor"),
        # default_actor is not signed for the name localhost, so sign explicitly.
        pytest.param("localhost", "adam", id="localhost-actor-flag"),
    ],
)
def test_cli_loopback_requests_never_go_through_an_env_proxy(
    isolated_runtime: Path,
    local_master_key: Path,
    monkeypatch: pytest.MonkeyPatch,
    host: str,
    actor_override: str | None,
) -> None:
    """An ``HTTP_PROXY`` never receives the signed header meant for the local daemon.

    Real sockets, loopback only: one listener stands in for a proxy, one for the
    daemon. A non-loopback control request proves the proxy setting is live.
    """
    from phoenix.cli.http_client import build_client

    with _recording_listener("proxy") as proxy, _recording_listener("daemon", host) as daemon:
        proxy_url = f"http://127.0.0.1:{proxy.server_address[1]}"
        for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            monkeypatch.setenv(var, proxy_url)
            monkeypatch.setenv(var.lower(), proxy_url)
        for var in ("NO_PROXY", "no_proxy"):
            monkeypatch.delenv(var, raising=False)

        # Control: a non-loopback rest_url keeps the environment proxy (and
        # default_actor is not signed for it).
        remote = build_client(
            CLIConfig(rest_url="http://phoenix.example:8003", default_actor="adam")
        )
        assert remote.get("/v1/health")["via"] == "proxy"
        requestline, headers = proxy.seen[-1]
        assert requestline.startswith("GET http://phoenix.example:8003/v1/health")
        assert "authorization" not in headers
        proxy.seen.clear()

        client = build_client(
            CLIConfig(rest_url=f"http://{host}:{daemon.server_address[1]}", default_actor="adam"),
            actor_override=actor_override,
        )
        assert client.get("/v1/health")["via"] == "daemon"
        assert client.get("/v1/admin/_ping")["via"] == "daemon"
        assert proxy.seen == []
        assert len(daemon.seen) == 2
        (health_line, health_headers), (ping_line, ping_headers) = daemon.seen
        assert health_line.startswith("GET /v1/health ")
        assert "authorization" not in health_headers  # /v1/health is never signed
        assert ping_line.startswith("GET /v1/admin/_ping ")
        assert ping_headers["authorization"].startswith("Phoenix-Actor ")


@pytest.mark.parametrize(
    ("rest_url", "trust_env"),
    [(url, False) for url in _LOOPBACK_REST_URLS + sorted(_LOCALHOST_REST_URLS)]
    + [(url, True) for url in ("http://100.101.102.103:8003", "http://phoenix.example:8003")],
)
def test_cli_client_ignores_proxy_settings_only_for_loopback(
    cli_to_app: list[httpx.Request],
    monkeypatch: pytest.MonkeyPatch,
    rest_url: str,
    trust_env: bool,
) -> None:
    from phoenix.cli.http_client import build_client

    built: list[bool] = []
    bridged_init = httpx.Client.__init__

    def _spy(self: httpx.Client, *args: Any, **kwargs: Any) -> None:
        if not isinstance(self, TestClient):
            built.append(kwargs.get("trust_env", True))
        bridged_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", _spy)
    client = build_client(CLIConfig(rest_url=rest_url, default_actor="adam"), actor_override="adam")
    client.get("/v1/health")
    assert built == [trust_env]


def _clear_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(var, raising=False)
        monkeypatch.delenv(var.lower(), raising=False)


def test_cli_default_actor_for_localhost_never_reaches_an_ipv6_squatter(
    isolated_runtime: Path,
    local_master_key: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: ``localhost`` resolved to ``::1`` first and handed the header to a squatter.

    The daemon binds ``127.0.0.1`` only, so another process can listen on
    ``[::1]`` at the same port without a bind conflict. On Windows
    ``getaddrinfo('localhost')`` returns ``::1`` first, and a client signing
    ``default_actor`` for ``http://localhost:<port>`` delivered its header there.
    Real sockets, loopback only.
    """
    from phoenix.cli.http_client import build_client

    _clear_proxy_env(monkeypatch)
    with ExitStack() as stack:
        daemon = stack.enter_context(_recording_listener("daemon", "127.0.0.1"))
        port = daemon.server_address[1]
        try:
            squatter = stack.enter_context(_recording_listener("squatter", "::1", port))
        except OSError as exc:
            pytest.skip(f"cannot listen on [::1]:{port} here: {exc}")

        client = build_client(CLIConfig(rest_url=f"http://localhost:{port}", default_actor="adam"))
        # Answered by whichever listener 'localhost' reached first (the squatter on Windows).
        assert client.get("/v1/admin/_ping")["via"] in {"daemon", "squatter"}
        received = daemon.seen + squatter.seen
        assert len(received) == 1
        assert all("authorization" not in headers for _line, headers in received), received

        # Control: the 127.0.0.1 form is signed and reaches only the IPv4 daemon.
        signed = build_client(CLIConfig(rest_url=f"http://127.0.0.1:{port}", default_actor="adam"))
        assert signed.get("/v1/admin/_ping")["via"] == "daemon"
        assert daemon.seen[-1][1]["authorization"].startswith("Phoenix-Actor ")
        assert all("authorization" not in headers for _line, headers in squatter.seen)


def test_cli_and_mcp_health_are_never_signed(
    isolated_runtime: Path,
    local_master_key: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A health probe of a port that is not Phoenix hands out no replayable header.

    Regression: the CLI default ``rest_url`` was ``localhost:8000`` (the daemon
    listens on 8003), so with only ``default_actor: adam`` configured,
    ``phoenix health`` sent a signed header to whatever owned port 8000. The
    listener here stands in for that process, on a loopback port Phoenix does
    not use.
    """
    from phoenix.cli.config_loader import load_config
    from phoenix.cli.entry import main as cli_main
    from phoenix.cli.http_client import build_client
    from phoenix.mcp.tools import tool_health

    _clear_proxy_env(monkeypatch)
    monkeypatch.delenv("PHOENIX_REST_URL", raising=False)
    with _recording_listener("not-phoenix") as squatter:
        config = tmp_path / "config.yaml"
        config.write_text(
            f"rest_url: http://127.0.0.1:{squatter.server_address[1]}\ndefault_actor: adam\n",
            encoding="utf-8",
        )
        base = ["--config", str(config), "--format", "json"]

        assert cli_main([*base, "health"]) == 0
        assert json.loads(capsys.readouterr().out)["via"] == "not-phoenix"
        assert cli_main([*base, "--actor", "adam", "health"]) == 0
        capsys.readouterr()
        assert cli_main([*base, "identity", "show"]) == 0
        assert json.loads(capsys.readouterr().out)["daemon_reachable"] is True
        client = build_client(load_config(config_path=config, env={}))
        assert tool_health(client=client)["via"] == "not-phoenix"  # phoenix_health MCP tool

        assert len(squatter.seen) == 4
        for requestline, headers in squatter.seen:
            assert requestline.startswith("GET /v1/health ")
            assert "authorization" not in headers

        # Control: the same configured client does sign an authenticated route.
        client.get("/v1/admin/_ping")
        assert squatter.seen[-1][1]["authorization"].startswith("Phoenix-Actor ")


def test_cli_default_rest_url_is_the_daemon_default_not_port_8000(
    cli_to_app: list[httpx.Request],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The documented one-time setup with ``default_actor`` alone targets ``127.0.0.1:8003``."""
    from phoenix.cli.entry import main as cli_main

    monkeypatch.delenv("PHOENIX_REST_URL", raising=False)
    config = tmp_path / "config.yaml"
    config.write_text("default_actor: adam\n", encoding="utf-8")
    base = ["--config", str(config), "--format", "json"]

    assert cli_main([*base, "health"]) == 0
    assert str(cli_to_app[-1].url) == "http://127.0.0.1:8003/v1/health"
    assert "authorization" not in cli_to_app[-1].headers
    assert cli_main([*base, "audit", "verify"]) == 0
    assert str(cli_to_app[-1].url) == "http://127.0.0.1:8003/v1/audit/ledger/verify"
    assert cli_to_app[-1].headers["authorization"].startswith("Phoenix-Actor ")
    assert capsys.readouterr().err == ""


def test_cli_unauthenticated_paths_match_the_daemon() -> None:
    """The CLI leaves exactly the daemon's header-less ``/v1`` routes unsigned."""
    from phoenix.cli.http_client import UNAUTHENTICATED_PATHS

    open_api_routes = {
        route.path
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path.startswith("/v1/")
        and "authorization" not in {p.alias.lower() for p in route.dependant.header_params}
    }
    assert UNAUTHENTICATED_PATHS == open_api_routes == {"/v1/health"}


def test_cli_identity_header_prints_a_verifiable_header(
    isolated_runtime: Path,
    local_master_key: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``phoenix identity header`` is the documented curl / Swagger path."""
    from phoenix.cli.entry import main as cli_main

    config = tmp_path / "config.yaml"
    config.write_text("rest_url: http://127.0.0.1:1\ndefault_actor: adam\n", encoding="utf-8")

    assert cli_main(["--config", str(config), "identity", "header"]) == 0
    header = capsys.readouterr().out.strip()
    assert header.startswith("Phoenix-Actor ")
    with TestClient(app, client=_REMOTE_PEER) as client:
        ping = client.get("/v1/admin/_ping", headers={"Authorization": header})
    assert ping.status_code == 200, ping.text
    assert ping.json()["actor"] == "adam"

    assert cli_main(["--config", str(config), "--actor", "alice", "identity", "header"]) == 0
    alice_header = capsys.readouterr().out.strip()
    with TestClient(app) as client:
        assert (
            client.get("/v1/admin/_ping", headers={"Authorization": alice_header}).status_code
            == 403
        )


def test_cli_identity_header_without_configured_actor_exits_4(
    isolated_runtime: Path,
    local_master_key: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A key on disk is not a reason to sign as adam: no configured actor, no header."""
    from phoenix.cli.entry import main as cli_main

    config = tmp_path / "config.yaml"
    config.write_text("rest_url: http://127.0.0.1:1\n", encoding="utf-8")

    assert cli_main(["--config", str(config), "identity", "header"]) == 4
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no actor configured" in captured.err
    assert "default_actor" in captured.err
    assert "--actor" in captured.err


def test_cli_identity_header_without_keystore_exits_4(
    isolated_runtime: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from phoenix.cli.entry import main as cli_main
    from phoenix.identity import keystore

    empty = tmp_path / "no-keystore"
    monkeypatch.setattr(keystore, "_keystore_dir", lambda: empty)
    config = tmp_path / "config.yaml"
    config.write_text("rest_url: http://127.0.0.1:1\ndefault_actor: adam\n", encoding="utf-8")

    assert cli_main(["--config", str(config), "identity", "header"]) == 4
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "No Phoenix master key" in captured.err
    assert not (empty / "master_key.bin").exists()


@pytest.mark.parametrize(
    ("config_text", "extra_args", "actor", "source", "state_name", "hint_fragment"),
    [
        pytest.param(
            f"rest_url: {_LOOPBACK_BASE}\n",
            [],
            None,
            None,
            "SIGNING_NO_ACTOR",
            "default_actor",
            id="no-actor",
        ),
        pytest.param(
            f"rest_url: {_LOOPBACK_BASE}\ndefault_actor: adam\n",
            [],
            "adam",
            "default_actor",
            "SIGNING_SIGNED",
            None,
            id="default-actor-loopback",
        ),
        pytest.param(
            f"rest_url: {_TAILNET_URL}\ndefault_actor: adam\n",
            [],
            "adam",
            "default_actor",
            "SIGNING_REFUSED_REMOTE",
            "--actor adam",
            id="default-actor-remote",
        ),
        pytest.param(
            "rest_url: http://localhost:8003\ndefault_actor: adam\n",
            [],
            "adam",
            "default_actor",
            "SIGNING_REFUSED_REMOTE",
            "Set rest_url to http://127.0.0.1:8003 ",
            id="default-actor-localhost",
        ),
        pytest.param(
            f"rest_url: {_TAILNET_URL}\ndefault_actor: adam\n",
            ["--actor", "alice"],
            "alice",
            "--actor",
            "SIGNING_SIGNED",
            None,
            id="actor-flag-remote",
        ),
    ],
)
def test_cli_identity_show_reports_the_signing_state(
    cli_to_app: list[httpx.Request],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    config_text: str,
    extra_args: list[str],
    actor: str | None,
    source: str | None,
    state_name: str,
    hint_fragment: str | None,
) -> None:
    from phoenix.cli import http_client
    from phoenix.cli.entry import main as cli_main

    config = tmp_path / "config.yaml"
    config.write_text(config_text, encoding="utf-8")
    assert (
        cli_main(["--config", str(config), *extra_args, "--format", "json", "identity", "show"])
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["actor"] == actor
    assert payload["actor_source"] == source
    assert payload["signing"] == getattr(http_client, state_name)
    assert payload["daemon_reachable"] is True
    if hint_fragment is None:
        assert "hint" not in payload
    else:
        assert hint_fragment in payload["hint"]
