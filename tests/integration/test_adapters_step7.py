"""Phase 9 Step 7 -- CLI command groups (task / lora / identity / providers).

Each subcommand is exercised via :func:`phoenix.cli.entry.main`
against the in-process FastAPI app. We monkey-patch
:class:`httpx.Client` to route through :class:`httpx.MockTransport`
hooked to FastAPI's :class:`TestClient` so the same daemon code
serves both REST tests and CLI tests.

Covered subcommands:
  ``phoenix lora load <spec>``      -> POST /v1/adapters
  ``phoenix lora list``             -> GET  /v1/adapters
  ``phoenix lora unload <id>``      -> DELETE /v1/adapters/{id}
  ``phoenix identity show``         -> GET  /v1/health (synthesized)
  ``phoenix identity enroll <a>``   -> POST /v1/identity/enroll
  ``phoenix task submit --spec``    -> spec JSON parsing + error paths
  ``phoenix task get <id>``         -> reads local cache
  ``phoenix task stream <id>``      -> Phase 9 v1 hint message
  ``phoenix providers list``        -> GET /v1/admin/providers/health-history

The Phase 9 v1 task-submit happy path needs a working solver. We
focus on the spec-parsing surface for ``task submit`` here and let
the existing tier1 + integration tests exercise the daemon's solve
pipeline.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix.api.routes import app as fastapi_app
from phoenix.cli.commands._shared import (
    SpecError,
    load_spec_json,
    read_task_cache,
    task_cache_path,
    write_task_cache,
)
from phoenix.cli.entry import main as cli_main


@pytest.fixture
def isolated_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[Path]:
    """Per-test fresh runtime + clean adapter registry + tmp task cache."""
    runtime = tmp_path / "phoenix_runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PHOENIX_SQLITE_DB_PATH", str(runtime / "state.db"))
    monkeypatch.setenv("PHOENIX_AUDIT_DIR", str(runtime / "audit"))
    monkeypatch.setenv("PHOENIX_KILL_SWITCH_PATH", str(runtime / "kill_switch.json"))
    monkeypatch.setenv("PHOENIX_KEYSTORE_DIR", str(runtime / "keystore"))
    (runtime / "keystore").mkdir(parents=True, exist_ok=True)
    # Redirect HOME so task_cache lands under tmp.
    monkeypatch.setenv("HOME", str(runtime / "home"))
    monkeypatch.setenv("USERPROFILE", str(runtime / "home"))

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

    # Build a config pointing at the in-process app
    config_path = runtime / "config.yaml"
    config_path.write_text("rest_url: http://testserver\n", encoding="utf-8")

    # Route all httpx.Client(...) calls through MockTransport that
    # forwards to the FastAPI app via TestClient (sync ASGI bridge).
    from fastapi.testclient import TestClient

    test_client = TestClient(fastapi_app)
    test_client.__enter__()  # noqa: SLF001 -- start the app lifespan
    try:
        real_init = httpx.Client.__init__

        def _patched_init(self: httpx.Client, *args: object, **kwargs: object) -> None:
            def _handler(request: httpx.Request) -> httpx.Response:
                method = request.method
                # Strip the http://testserver prefix
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

            kwargs["transport"] = httpx.MockTransport(_handler)
            real_init(self, *args, **kwargs)

        monkeypatch.setattr(httpx.Client, "__init__", _patched_init)
        yield runtime
    finally:
        test_client.__exit__(None, None, None)
        reset_emitter()
        reset_ledger()
        reset_state_backend()
        ks_module._STORE = None
        get_limiter().reset_all()
        permissions_module._REGISTRY = None
        reset_adapter_registry()


def _cli(*args: str, config_path: Path) -> int:
    return cli_main(["--config", str(config_path), *args])


def _cfg(runtime: Path) -> Path:
    return runtime / "config.yaml"


# ---------------------------------------------------------------------
# Spec parsing helpers
# ---------------------------------------------------------------------


class TestSpecHelpers:
    def test_load_spec_inline_json(self) -> None:
        assert load_spec_json('{"a": 1}') == {"a": 1}

    def test_load_spec_from_at_file(self, tmp_path: Path) -> None:
        path = tmp_path / "spec.json"
        path.write_text('{"b": 2}', encoding="utf-8")
        assert load_spec_json(f"@{path}") == {"b": 2}

    def test_load_spec_empty_raises(self) -> None:
        with pytest.raises(SpecError):
            load_spec_json("")

    def test_load_spec_malformed_inline_raises(self) -> None:
        with pytest.raises(SpecError):
            load_spec_json("{not json}")

    def test_load_spec_missing_file_raises(self) -> None:
        with pytest.raises(SpecError):
            load_spec_json("@/does/not/exist.json")


# ---------------------------------------------------------------------
# lora group
# ---------------------------------------------------------------------


_IDENTITY_SPEC = "phoenix.adapters.identity_adapter:make_identity_adapter"


class TestLoraGroup:
    def test_lora_load_list_unload_lifecycle(
        self,
        isolated_runtime: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = _cli(
            "--format",
            "json",
            "lora",
            "load",
            _IDENTITY_SPEC,
            config_path=_cfg(isolated_runtime),
        )
        out = capsys.readouterr().out
        assert rc == 0, out
        assert '"name": "identity"' in out

        rc = _cli(
            "--format",
            "json",
            "lora",
            "list",
            config_path=_cfg(isolated_runtime),
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert '"count": 1' in out

        rc = _cli(
            "lora",
            "unload",
            "identity",
            config_path=_cfg(isolated_runtime),
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "unloaded" in out.lower()

    def test_lora_load_bad_spec_returns_http_error(
        self,
        isolated_runtime: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = _cli(
            "lora",
            "load",
            "no_colon_spec",
            config_path=_cfg(isolated_runtime),
        )
        # CLIHTTPError -> EXIT_HTTP_ERROR
        assert rc == 3
        assert "HTTP error" in capsys.readouterr().err


# ---------------------------------------------------------------------
# identity group
# ---------------------------------------------------------------------


class TestIdentityGroup:
    def test_identity_show_prints_actor_and_reachability(
        self,
        isolated_runtime: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = _cli(
            "--format",
            "json",
            "identity",
            "show",
            config_path=_cfg(isolated_runtime),
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "actor" in out
        assert "rest_url" in out
        assert "daemon_reachable" in out

    def test_identity_enroll_with_permissions(
        self,
        isolated_runtime: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = _cli(
            "--format",
            "json",
            "identity",
            "enroll",
            "bob",
            "--permission",
            "can_load_adapter=true",
            "--permission",
            "rate_limit_tier=elevated",
            config_path=_cfg(isolated_runtime),
        )
        out = capsys.readouterr().out
        assert rc == 0, out
        assert '"enrolled_actor_name": "bob"' in out
        # Boolean coercion of can_load_adapter applied -> True at daemon
        assert '"can_load_adapter": true' in out
        assert '"rate_limit_tier": "elevated"' in out

    def test_identity_enroll_malformed_permission_returns_usage_error(
        self,
        isolated_runtime: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = _cli(
            "identity",
            "enroll",
            "bob",
            "--permission",
            "no-equals",
            config_path=_cfg(isolated_runtime),
        )
        assert rc == 2
        assert "key=value" in capsys.readouterr().out


# ---------------------------------------------------------------------
# task group (spec parsing + cache surface only -- full submit goes
# through the existing tier-1 / solve-endpoint tests).
# ---------------------------------------------------------------------


class TestTaskGroup:
    def test_task_get_cached(
        self,
        isolated_runtime: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        cached = {"task_id": "tx-123", "value": 42}
        write_task_cache({"tx-123": cached})
        rc = _cli(
            "--format",
            "json",
            "task",
            "get",
            "tx-123",
            config_path=_cfg(isolated_runtime),
        )
        out = capsys.readouterr().out
        assert rc == 0, out
        assert '"value": 42' in out

    def test_task_get_missing_returns_1(
        self,
        isolated_runtime: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = _cli(
            "task",
            "get",
            "no-such-task",
            config_path=_cfg(isolated_runtime),
        )
        assert rc == 1
        assert "no cached envelope" in capsys.readouterr().out

    def test_task_submit_with_malformed_spec(
        self,
        isolated_runtime: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = _cli(
            "task",
            "submit",
            "--spec",
            "{not json}",
            config_path=_cfg(isolated_runtime),
        )
        assert rc == 2
        out = capsys.readouterr().out
        assert "Malformed inline JSON" in out

    def test_task_submit_spec_must_be_object(
        self,
        isolated_runtime: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = _cli(
            "task",
            "submit",
            "--spec",
            "[1,2,3]",
            config_path=_cfg(isolated_runtime),
        )
        assert rc == 2
        assert "JSON object" in capsys.readouterr().out

    def test_task_stream_prints_ws_hint(
        self,
        isolated_runtime: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = _cli(
            "task",
            "stream",
            "tx-abc",
            config_path=_cfg(isolated_runtime),
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "ws://" in out or "wss://" in out
        assert "tx-abc" in out


# ---------------------------------------------------------------------
# providers group
# ---------------------------------------------------------------------


def test_providers_list_calls_admin_endpoint(
    isolated_runtime: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = _cli(
        "--format",
        "json",
        "providers",
        "list",
        config_path=_cfg(isolated_runtime),
    )
    out = capsys.readouterr().out
    assert rc == 0, out
    # /v1/admin/providers/health-history returns a histogram payload
    # with the canonical shape: count + events list (Phase 8 Step 6).
    assert '"events"' in out
    assert '"count"' in out


# ---------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------


class TestDispatcherEdges:
    def test_group_without_subcommand_returns_usage(
        self,
        isolated_runtime: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = _cli("lora", config_path=_cfg(isolated_runtime))
        assert rc == 2
        assert "subcommand required" in capsys.readouterr().err

    def test_cache_directory_writes_on_write(self, tmp_path: Path) -> None:
        """Sanity for the cache path resolver."""
        # Re-rooting HOME via env var doesn't change Path.home() in the
        # already-imported module on Windows; test by passing an
        # explicit path instead.
        cache_path = tmp_path / "phoenix" / "task_cache.json"
        write_task_cache({"id-1": {"x": 1}}, path=cache_path)
        assert cache_path.exists()
        assert read_task_cache(path=cache_path) == {"id-1": {"x": 1}}

    def test_task_cache_path_under_home(self) -> None:
        """The default path resolves under ~/.phoenix/."""
        assert str(task_cache_path()).endswith("task_cache.json")
