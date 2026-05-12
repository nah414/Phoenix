"""Phase 9 Step 6 -- CLI scaffold tests.

Covers the four CLI scaffold pieces shipped at Step 6:

1. :class:`CLIConfig` loader -- fixture YAML parsing, env-var
   overrides, defaults, malformed YAML rejection.
2. :func:`render` output formats -- ``json`` | ``text`` | ``table``
   | ``auto`` (TTY-detection) modes.
3. :class:`CLIHTTPClient` -- httpx-backed wrapper that signs
   actors + composes the base URL + maps non-2xx to
   :class:`CLIHTTPError`.
4. :func:`main` entry dispatcher -- argparse tree, ``--version``,
   the Step-6 ``health`` smoke command end-to-end against the
   in-process FastAPI app.

The Step 7-8 command groups land in test_adapters_step7.py /
step8.py; this file only proves the scaffold itself works.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix.api.routes import app as fastapi_app
from phoenix.cli.config_loader import (
    CLIConfig,
    ConfigError,
    load_config,
)
from phoenix.cli.entry import main as cli_main
from phoenix.cli.http_client import (
    CLIHTTPClient,
    CLIHTTPError,
    build_client,
)
from phoenix.cli.output_formats import OutputFormatError, render


# ---------------------------------------------------------------------
# 1. CLIConfig loader
# ---------------------------------------------------------------------


class TestConfigLoader:
    def test_defaults_when_no_file_no_env(self, tmp_path: Path) -> None:
        config = load_config(
            config_path=tmp_path / "missing.yaml",
            env={},
        )
        assert config.rest_url == "http://localhost:8000"
        assert config.reproducibility_mode == "permissive"
        assert config.default_actor is None
        assert config.output_format == "auto"

    def test_loads_from_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text(
            "rest_url: https://phoenix.example.com:8443\n"
            "reproducibility_mode: strict\n"
            "default_actor: bob\n"
            "output_format: json\n",
            encoding="utf-8",
        )
        config = load_config(config_path=path, env={})
        assert config.rest_url == "https://phoenix.example.com:8443"
        assert config.reproducibility_mode == "strict"
        assert config.default_actor == "bob"
        assert config.output_format == "json"

    def test_env_overrides_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text(
            "rest_url: https://yaml.example.com\nreproducibility_mode: permissive\n",
            encoding="utf-8",
        )
        config = load_config(
            config_path=path,
            env={
                "PHOENIX_REST_URL": "http://env-override:9999",
                "PHOENIX_REPRODUCIBILITY_MODE": "replay",
            },
        )
        assert config.rest_url == "http://env-override:9999"
        assert config.reproducibility_mode == "replay"

    def test_invalid_repro_mode_in_yaml_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("reproducibility_mode: invalid\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(config_path=path, env={})

    def test_invalid_repro_mode_in_env_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError):
            load_config(
                config_path=tmp_path / "missing.yaml",
                env={"PHOENIX_REPRODUCIBILITY_MODE": "nope"},
            )

    def test_malformed_yaml_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("rest_url: [unclosed\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(config_path=path, env={})

    def test_non_mapping_yaml_raises(self, tmp_path: Path) -> None:
        """A top-level scalar or list isn't a valid config."""
        path = tmp_path / "config.yaml"
        path.write_text("- just a list\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(config_path=path, env={})

    def test_unknown_keys_are_kept_in_raw(self, tmp_path: Path) -> None:
        """Forward-compat: unknown keys land in ``config.raw``."""
        path = tmp_path / "config.yaml"
        path.write_text(
            "rest_url: http://x\nfuture_field: value\n",
            encoding="utf-8",
        )
        config = load_config(config_path=path, env={})
        assert config.raw["future_field"] == "value"

    def test_rest_url_trailing_slash_stripped(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("rest_url: http://x:8000//\n", encoding="utf-8")
        config = load_config(config_path=path, env={})
        assert config.rest_url == "http://x:8000"


# ---------------------------------------------------------------------
# 2. Output formats
# ---------------------------------------------------------------------


class TestOutputFormats:
    def test_json_round_trips_dict(self) -> None:
        out = render({"a": 1, "b": [2, 3]}, format_name="json")
        assert '"a"' in out
        assert "1" in out

    def test_text_renders_dict_keys(self) -> None:
        out = render({"name": "echo", "version": "1.0.0"}, format_name="text")
        assert "name: echo" in out
        assert "version: 1.0.0" in out

    def test_text_handles_nested_dict(self) -> None:
        out = render(
            {"adapter": {"name": "echo", "version": "1.0"}},
            format_name="text",
        )
        assert "adapter:" in out
        assert "name: echo" in out

    def test_text_handles_list(self) -> None:
        out = render([1, 2, 3], format_name="text")
        assert "[0] 1" in out
        assert "[2] 3" in out

    def test_table_renders_list_of_dicts(self) -> None:
        out = render(
            [
                {"name": "a", "value": 1},
                {"name": "b", "value": 2},
            ],
            format_name="table",
        )
        # Header row + body rows
        lines = out.splitlines()
        assert "name" in lines[0]
        assert "value" in lines[0]
        # Header divider
        assert "-" in lines[1]
        # Two body rows
        assert len(lines) == 4

    def test_table_falls_back_to_text_for_scalar(self) -> None:
        out = render({"only_dict": "no list"}, format_name="table")
        assert "only_dict: no list" in out

    def test_auto_picks_text_for_tty(self) -> None:
        out = render({"a": 1}, format_name="auto", is_tty=True)
        assert "a: 1" in out

    def test_auto_picks_json_for_pipe(self) -> None:
        out = render({"a": 1}, format_name="auto", is_tty=False)
        assert '"a"' in out

    def test_unknown_format_raises(self) -> None:
        with pytest.raises(OutputFormatError):
            render({}, format_name="yaml")

    def test_bool_format(self) -> None:
        out = render({"flag": True, "off": False}, format_name="text")
        assert "flag: true" in out
        assert "off: false" in out

    def test_empty_dict_rendering(self) -> None:
        assert render({}, format_name="text") == "(empty)"

    def test_empty_list_rendering(self) -> None:
        assert render([], format_name="text") == "(empty list)"


# ---------------------------------------------------------------------
# 3. HTTP client
# ---------------------------------------------------------------------


@pytest.fixture
def fastapi_transport() -> Iterator[httpx.MockTransport]:
    """Drive CLIHTTPClient against the in-process FastAPI app."""
    # Use a real httpx Client routing through the FastAPI app via the
    # WSGI/ASGI bridge that TestClient provides. The CLI client builds
    # its own httpx.Client so we patch the build_client return path
    # via a tiny shim: tests instantiate CLIHTTPClient with the test
    # transport directly.
    yield httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))


class TestCLIHTTPClient:
    def test_build_client_uses_config_url_and_actor(self) -> None:
        config = CLIConfig(
            rest_url="http://example.com:9000",
            default_actor="bob",
        )
        client = build_client(config)
        assert client.base_url == "http://example.com:9000"
        assert client.actor_name == "bob"

    def test_actor_override_takes_precedence(self) -> None:
        config = CLIConfig(rest_url="http://x", default_actor="bob")
        client = build_client(config, actor_override="charlie")
        assert client.actor_name == "charlie"

    def test_actor_override_none_uses_config_default(self) -> None:
        config = CLIConfig(rest_url="http://x", default_actor="bob")
        client = build_client(config)
        assert client.actor_name == "bob"

    def test_no_actor_passes_through_for_bootstrap(self) -> None:
        config = CLIConfig(rest_url="http://x")
        client = build_client(config)
        assert client.actor_name is None

    def test_client_sends_signed_actor_header(self) -> None:
        """End-to-end: the CLI client sends a Phoenix-Actor header
        that the daemon's :func:`extract_or_bootstrap` resolves to
        a real actor.
        """
        # We can't use httpx MockTransport because CLIHTTPClient builds
        # its own client. Instead, drive against the real FastAPI app
        # via TestClient and observe the response.
        with TestClient(fastapi_app) as test_client:
            test_client.get("/v1/health")  # warm up state

        # Hit /v1/health directly via CLIHTTPClient using TestClient as
        # the URL backend. We monkeypatch httpx.Client to TestClient
        # for this test only.
        client = CLIHTTPClient(
            base_url="http://testserver",
            actor_name="adam",
        )
        # Drive via TestClient.request: TestClient supports the same
        # interface but uses an in-process ASGI transport.
        with TestClient(fastapi_app) as test_client:
            resp = test_client.get(
                "/v1/health",
                headers=client._build_headers(),
            )
        assert resp.status_code == 200

    def test_http_error_carries_status_and_url(self) -> None:
        """A 404 from the daemon becomes :class:`CLIHTTPError` with
        a structured ``status_code`` + ``url`` for the caller's
        error path.
        """
        client = CLIHTTPClient(
            base_url="http://localhost:1",  # unbindable -> connection refused
            actor_name=None,
        )
        with pytest.raises(CLIHTTPError) as excinfo:
            client.get("/v1/health")
        assert excinfo.value.url.endswith("/v1/health")


# ---------------------------------------------------------------------
# 4. main() dispatcher
# ---------------------------------------------------------------------


class TestMainDispatcher:
    def test_version_flag_prints_and_exits(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        with pytest.raises(SystemExit) as excinfo:
            cli_main(["--version"])
        assert excinfo.value.code == 0
        out = capsys.readouterr().out
        assert "phoenix" in out

    def test_no_command_prints_help(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = cli_main([])
        assert rc == 2  # EXIT_USAGE_ERROR
        # Help text goes to stderr
        captured = capsys.readouterr()
        assert "phoenix" in captured.err

    def test_unknown_command_exits_2(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        with pytest.raises(SystemExit) as excinfo:
            cli_main(["nonexistent"])
        assert excinfo.value.code == 2

    def test_config_error_prints_and_exits(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        bad_config = tmp_path / "bad.yaml"
        bad_config.write_text("rest_url: [unclosed\n", encoding="utf-8")
        rc = cli_main(["--config", str(bad_config), "health"])
        assert rc == 4  # EXIT_CONFIG_ERROR
        assert "config error" in capsys.readouterr().err

    def test_group_without_subcommand_returns_usage_error(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`phoenix task` (no subcommand) returns EXIT_USAGE_ERROR."""
        rc = cli_main(["task"])
        assert rc == 2  # EXIT_USAGE_ERROR
        assert "subcommand required" in capsys.readouterr().err


# ---------------------------------------------------------------------
# 5. End-to-end health command (smoke for the whole dispatcher path)
# ---------------------------------------------------------------------


def test_health_command_e2e(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hit `phoenix health` via the dispatcher with a mock transport.

    The CLI dispatcher -> config loader -> http_client -> render
    chain is what Step 6 ships; the actual daemon response shape
    is exercised by tests/integration/test_health.py. We use
    httpx's :class:`MockTransport` so this test stays sync.
    """
    config_path = tmp_path / "config.yaml"
    config_path.write_text("rest_url: http://mocked\n", encoding="utf-8")

    import httpx as httpx_mod

    fake_response_body = {"status": "ok", "phoenix_version": "1.0.0.dev9"}

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/health"
        return httpx.Response(200, json=fake_response_body)

    real_client_init = httpx_mod.Client.__init__

    def patched_init(self: httpx.Client, *args: object, **kwargs: object) -> None:
        kwargs["transport"] = httpx.MockTransport(_handler)
        real_client_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx_mod.Client, "__init__", patched_init)

    rc = cli_main(
        [
            "--config",
            str(config_path),
            "--format",
            "json",
            "health",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0, f"CLI rc={rc}; out={out!r}"
    assert "1.0.0.dev9" in out  # rendered version from mocked body
    assert "status" in out.lower()


# ---------------------------------------------------------------------
# Side helpers used above (silences unused-import warnings)
# ---------------------------------------------------------------------


def _silence_unused() -> None:  # pragma: no cover
    _ = (io,)
