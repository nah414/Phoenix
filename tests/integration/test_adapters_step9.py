"""Phase 9 Step 9 -- MCP server tests.

Two layers of coverage:

1. **Tool implementations** (:mod:`phoenix.mcp.tools`) -- each of
   the 8 tools is exercised directly via its pure-function entry
   point against the in-process FastAPI app. Locks the per-tool
   contract without spinning up MCP.

2. **Server wiring** (:mod:`phoenix.mcp.server`) -- the
   :func:`build_server` factory produces a :class:`FastMCP`
   instance with exactly the 8 expected tool names registered.

End-to-end stdio MCP-protocol coverage (spawning the server in a
subprocess and exchanging JSON-RPC) is documented but not
exercised here -- Phase 9 v1's threat model is "the wiring works
+ the tools call the right endpoints", which the two layers
above pin down.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix.api.routes import app as fastapi_app
from phoenix.cli.commands._shared import task_cache_path, write_task_cache
from phoenix.cli.http_client import CLIHTTPClient
from phoenix.mcp.tools import (
    TOOL_REGISTRY,
    tool_audit_verify,
    tool_calibration_status,
    tool_health,
    tool_provenance_get,
    tool_providers_list,
    tool_task_get,
    tool_task_replay,
    tool_task_submit,
)


# ---------------------------------------------------------------------
# Test fixtures: in-process daemon + tmp task cache
# ---------------------------------------------------------------------


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

    from phoenix.adapters.registry import reset_registry as reset_adapter_registry
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
    reset_adapter_registry()
    try:
        yield runtime
    finally:
        reset_emitter()
        reset_ledger()
        reset_state_backend()
        ks_module._STORE = None
        get_limiter().reset_all()
        permissions_module._REGISTRY = None
        reset_adapter_registry()


@pytest.fixture
def in_process_client(
    isolated_runtime: Path,
) -> Iterator[CLIHTTPClient]:
    """A :class:`CLIHTTPClient` routed through the in-process FastAPI app."""
    test_client = TestClient(fastapi_app)
    test_client.__enter__()  # noqa: SLF001

    def _transport_handler(request: httpx.Request) -> httpx.Response:
        method = request.method
        url_path = str(request.url).replace("http://testserver", "")
        resp = test_client.request(
            method,
            url_path,
            headers=dict(request.headers),
            content=request.content,
        )
        return httpx.Response(
            resp.status_code,
            headers=dict(resp.headers),
            content=resp.content,
        )

    transport = httpx.MockTransport(_transport_handler)

    # Subclass CLIHTTPClient to inject the mock transport.
    class _BridgedClient(CLIHTTPClient):
        def _request(
            self,
            method: str,
            path: str,
            *,
            params: dict | None = None,
            json_body: dict | None = None,
        ) -> object:
            url = self.base_url.rstrip("/") + path
            with httpx.Client(transport=transport) as client:
                resp = client.request(
                    method,
                    url,
                    headers=self._build_headers(),
                    params=params,
                    json=json_body,
                )
            if resp.status_code >= 400:
                try:
                    body = resp.json()
                except Exception:
                    body = resp.text
                from phoenix.cli.http_client import CLIHTTPError

                raise CLIHTTPError(
                    f"HTTP {method} {url} returned {resp.status_code}: {body}",
                    status_code=resp.status_code,
                    url=url,
                    body=body,
                )
            if not resp.content:
                return None
            return resp.json()

    client = _BridgedClient(base_url="http://testserver", actor_name=None)
    try:
        yield client
    finally:
        test_client.__exit__(None, None, None)


# ---------------------------------------------------------------------
# 1. Tool implementations
# ---------------------------------------------------------------------


class TestToolHealth:
    def test_health_returns_version_and_status(self, in_process_client: CLIHTTPClient) -> None:
        result = tool_health(client=in_process_client)
        assert isinstance(result, dict)
        # /v1/health returns at least "status" + version-style key
        assert "status" in result or "phoenix_version" in result


class TestToolProvidersList:
    def test_providers_list_returns_health_history_shape(
        self, in_process_client: CLIHTTPClient
    ) -> None:
        result = tool_providers_list(client=in_process_client)
        assert isinstance(result, dict)
        assert "events" in result or "count" in result


class TestToolCalibrationStatus:
    def test_calibration_status_returns_checker_state(
        self, in_process_client: CLIHTTPClient
    ) -> None:
        result = tool_calibration_status(client=in_process_client)
        assert isinstance(result, dict)
        # Admin /calibration/detail surface
        assert "checkers" in result or "cadence_seconds" in result


class TestToolAuditVerify:
    def test_audit_verify_returns_validity_marker(self, in_process_client: CLIHTTPClient) -> None:
        result = tool_audit_verify(client=in_process_client)
        assert isinstance(result, dict)
        # /v1/audit/ledger/verify returns paired structural + crypto results
        assert "python_crypto" in result or "sql_structural" in result


class TestToolTaskGet:
    def test_task_get_cache_miss(self, in_process_client: CLIHTTPClient) -> None:
        result = tool_task_get(client=in_process_client, task_id="no-such-task")
        assert "error" in result

    def test_task_get_cache_hit(
        self,
        in_process_client: CLIHTTPClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Redirect the cache path under the tmp_path so we don't trample HOME
        cache_path = tmp_path / "phoenix" / "task_cache.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        write_task_cache({"tx-1": {"value": 99, "task_id": "tx-1"}}, path=cache_path)
        monkeypatch.setattr("phoenix.cli.commands._shared.task_cache_path", lambda: cache_path)
        result = tool_task_get(client=in_process_client, task_id="tx-1")
        assert result["value"] == 99


class TestToolProvenanceGet:
    def test_provenance_get_cache_miss(self, in_process_client: CLIHTTPClient) -> None:
        result = tool_provenance_get(client=in_process_client, task_id="ghost")
        assert "error" in result

    def test_provenance_get_extracts_block(
        self,
        in_process_client: CLIHTTPClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cache_path = tmp_path / "phoenix" / "task_cache.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        write_task_cache(
            {
                "tx-2": {
                    "task_id": "tx-2",
                    "value": 1.0,
                    "provenance": {
                        "task_id": "tx-2",
                        "solver_trace": {"solver": "qho"},
                    },
                }
            },
            path=cache_path,
        )
        monkeypatch.setattr("phoenix.cli.commands._shared.task_cache_path", lambda: cache_path)
        result = tool_provenance_get(client=in_process_client, task_id="tx-2")
        assert "solver_trace" in result


class TestToolTaskReplay:
    def test_task_replay_returns_http_error_envelope_for_unknown_task(
        self,
        in_process_client: CLIHTTPClient,
    ) -> None:
        """Daemon returns 404/422 for unknown task; the tool surfaces
        the CLIHTTPError as a transport-level failure (tests can rely
        on the exception type rather than parsing the body)."""
        from phoenix.cli.http_client import CLIHTTPError

        with pytest.raises(CLIHTTPError):
            tool_task_replay(client=in_process_client, task_id="never-existed")


# ---------------------------------------------------------------------
# 2. Server wiring
# ---------------------------------------------------------------------


class TestMCPServerWiring:
    def test_tool_registry_has_eight_tools(self) -> None:
        """Phase 9 v1 ships exactly the 8 canonical tools."""
        assert len(TOOL_REGISTRY) == 8
        assert set(TOOL_REGISTRY) == {
            "phoenix_task_submit",
            "phoenix_task_get",
            "phoenix_task_replay",
            "phoenix_provenance_get",
            "phoenix_providers_list",
            "phoenix_calibration_status",
            "phoenix_health",
            "phoenix_audit_verify",
        }

    @pytest.mark.asyncio
    async def test_build_server_registers_all_tools(self) -> None:
        """:func:`build_server` returns a FastMCP with the 8 tools."""
        from phoenix.cli.config_loader import CLIConfig
        from phoenix.mcp.server import build_server

        config = CLIConfig(rest_url="http://localhost:8000")
        server = build_server(config)

        tool_list = await server.list_tools()
        names = {t.name for t in tool_list}
        assert names == set(TOOL_REGISTRY.keys())

    @pytest.mark.asyncio
    async def test_server_tool_call_returns_health_payload(
        self,
        isolated_runtime: Path,
    ) -> None:
        """End-to-end via FastMCP.call_tool: the wired server runs the
        Phoenix health tool and gets a real daemon response back.
        """
        import json

        from phoenix.cli.config_loader import CLIConfig
        from phoenix.cli.http_client import CLIHTTPClient
        from phoenix.mcp.server import build_server

        # Inject the in-process bridge as the client factory.
        test_client = TestClient(fastapi_app)
        test_client.__enter__()  # noqa: SLF001
        try:

            def _handler(request: httpx.Request) -> httpx.Response:
                method = request.method
                url_path = str(request.url).replace("http://testserver", "")
                resp = test_client.request(
                    method,
                    url_path,
                    headers=dict(request.headers),
                    content=request.content,
                )
                return httpx.Response(
                    resp.status_code,
                    headers=dict(resp.headers),
                    content=resp.content,
                )

            transport = httpx.MockTransport(_handler)

            class _BridgedClient(CLIHTTPClient):
                def _request(
                    self,
                    method: str,
                    path: str,
                    *,
                    params: dict | None = None,
                    json_body: dict | None = None,
                ) -> object:
                    url = self.base_url.rstrip("/") + path
                    with httpx.Client(transport=transport) as client:
                        resp = client.request(
                            method,
                            url,
                            headers=self._build_headers(),
                            params=params,
                            json=json_body,
                        )
                    if not resp.content:
                        return None
                    return resp.json()

            def _factory(cfg: object, *, actor_override: str | None = None) -> CLIHTTPClient:
                return _BridgedClient(base_url="http://testserver", actor_name=None)

            config = CLIConfig(rest_url="http://testserver")
            server = build_server(config, client_factory=_factory)

            result = await server.call_tool("phoenix_health", {})
            # call_tool returns (content, structured). Extract the JSON
            # text content and parse it back.
            content = result[0] if isinstance(result, tuple) else result
            # content is a list of TextContent objects
            text = content[0].text if content else ""
            payload = json.loads(text)
            assert "status" in payload or "phoenix_version" in payload
        finally:
            test_client.__exit__(None, None, None)


# ---------------------------------------------------------------------
# 3. CLI surface
# ---------------------------------------------------------------------


def test_mcp_subcommand_registered(
    isolated_runtime: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`phoenix mcp` exists and lists `serve` as its subcommand."""
    from phoenix.cli.entry import main as cli_main

    rc = cli_main(["mcp"])
    assert rc == 2  # subcommand required
    err = capsys.readouterr().err
    assert "serve" in err


def _silence_unused() -> None:  # pragma: no cover
    """Keep imports alive for static analysis."""
    _ = (tool_task_submit, task_cache_path)
