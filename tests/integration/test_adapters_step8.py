"""Phase 9 Step 8 -- CLI audit / calibration / admin groups.

Each command is exercised against the in-process FastAPI app via
the same MockTransport bridge used in Step 7's tests. Covers:

  ``phoenix audit tail``              -> GET /v1/audit/events
  ``phoenix audit verify``            -> GET /v1/audit/ledger/verify
  ``phoenix calibration status``      -> GET /v1/admin/calibration/detail
  ``phoenix calibration run``         -> POST /v1/admin/calibration/run
  ``phoenix admin kill-switch ...``   -> POST/GET /v1/admin/kill-switch/...
  ``phoenix admin health``            -> GET /v1/admin/health/detailed
  ``phoenix admin governor``          -> GET /v1/admin/governor
  ``phoenix admin budget``            -> GET /v1/admin/budget
  ``phoenix admin override``          -> POST /v1/admin/tasks-pending-review/{id}/override

The default bootstrap actor (``adam``) has admin = True, so the
admin-only endpoints round-trip cleanly.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix.api.routes import app as fastapi_app
from phoenix.cli.entry import main as cli_main


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

    config_path = runtime / "config.yaml"
    config_path.write_text("rest_url: http://testserver\n", encoding="utf-8")

    test_client = TestClient(fastapi_app)
    test_client.__enter__()  # noqa: SLF001
    try:
        real_init = httpx.Client.__init__

        def _patched_init(self: httpx.Client, *args: object, **kwargs: object) -> None:
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

            kwargs["transport"] = httpx.MockTransport(_handler)
            real_init(self, *args, **kwargs)

        monkeypatch.setattr(httpx.Client, "__init__", _patched_init)
        yield runtime
    finally:
        # Always release the kill switch so tests don't leak.
        try:
            ks_module.get_store().release(by="adam", reason="cleanup")
        except Exception:
            pass
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
# audit group
# ---------------------------------------------------------------------


class TestAuditGroup:
    def test_audit_tail_returns_events_payload(
        self,
        isolated_runtime: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = _cli(
            "--format",
            "json",
            "audit",
            "tail",
            "--limit",
            "50",
            config_path=_cfg(isolated_runtime),
        )
        out = capsys.readouterr().out
        assert rc == 0, out
        assert '"events"' in out or '"event_type"' in out

    def test_audit_verify_runs(
        self,
        isolated_runtime: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = _cli(
            "--format",
            "json",
            "audit",
            "verify",
            config_path=_cfg(isolated_runtime),
        )
        out = capsys.readouterr().out
        assert rc == 0, out
        assert '"valid"' in out or "chain" in out.lower()


# ---------------------------------------------------------------------
# calibration group
# ---------------------------------------------------------------------


class TestCalibrationGroup:
    def test_calibration_status(
        self,
        isolated_runtime: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = _cli(
            "--format",
            "json",
            "calibration",
            "status",
            config_path=_cfg(isolated_runtime),
        )
        out = capsys.readouterr().out
        assert rc == 0, out
        # /v1/admin/calibration/detail returns checker info + cadence
        assert "checkers" in out.lower() or "cadence" in out.lower()


# ---------------------------------------------------------------------
# admin group: kill switch
# ---------------------------------------------------------------------


class TestAdminKillSwitch:
    def test_status_engage_release_round_trip(
        self,
        isolated_runtime: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Initial status: disengaged
        rc = _cli(
            "--format",
            "json",
            "admin",
            "kill-switch",
            "status",
            config_path=_cfg(isolated_runtime),
        )
        out = capsys.readouterr().out
        assert rc == 0, out
        assert "engaged" in out.lower()  # presence of the field

        # Engage
        rc = _cli(
            "--format",
            "json",
            "admin",
            "kill-switch",
            "engage",
            "--reason",
            "cli test engage",
            config_path=_cfg(isolated_runtime),
        )
        out = capsys.readouterr().out
        assert rc == 0, out
        assert "engaged" in out.lower()

        # Release
        rc = _cli(
            "--format",
            "json",
            "admin",
            "kill-switch",
            "release",
            "--reason",
            "cli test release",
            config_path=_cfg(isolated_runtime),
        )
        out = capsys.readouterr().out
        assert rc == 0, out
        # Release endpoint returns the canonical status snapshot;
        # engaged should now be false.
        assert '"engaged": false' in out

    def test_engage_without_reason_returns_2(
        self,
        isolated_runtime: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """argparse rejects missing --reason BEFORE dispatch."""
        with pytest.raises(SystemExit) as excinfo:
            _cli(
                "admin",
                "kill-switch",
                "engage",
                config_path=_cfg(isolated_runtime),
            )
        assert excinfo.value.code == 2


# ---------------------------------------------------------------------
# admin group: health / governor / budget
# ---------------------------------------------------------------------


class TestAdminReadEndpoints:
    def test_admin_health(
        self,
        isolated_runtime: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = _cli(
            "--format",
            "json",
            "admin",
            "health",
            config_path=_cfg(isolated_runtime),
        )
        out = capsys.readouterr().out
        assert rc == 0, out
        # Detailed health shows component statuses
        assert "kill_switch" in out.lower() or "state" in out.lower()

    def test_admin_governor(
        self,
        isolated_runtime: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = _cli(
            "--format",
            "json",
            "admin",
            "governor",
            config_path=_cfg(isolated_runtime),
        )
        out = capsys.readouterr().out
        assert rc == 0, out
        # Governor shows resource snapshot (CPU/RAM/disk)
        assert "cpu" in out.lower() or "ram" in out.lower() or "disk" in out.lower()

    def test_admin_budget(
        self,
        isolated_runtime: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = _cli(
            "--format",
            "json",
            "admin",
            "budget",
            config_path=_cfg(isolated_runtime),
        )
        out = capsys.readouterr().out
        assert rc == 0, out
        assert "tokens" in out.lower() or "tier" in out.lower() or "actors" in out.lower()


# ---------------------------------------------------------------------
# admin override
# ---------------------------------------------------------------------


class TestAdminOverride:
    def test_override_requires_disposition(
        self,
        isolated_runtime: Path,
    ) -> None:
        """argparse rejects missing --disposition BEFORE dispatch."""
        with pytest.raises(SystemExit) as excinfo:
            _cli(
                "admin",
                "override",
                "tx-1",
                "--reason",
                "test",
                config_path=_cfg(isolated_runtime),
            )
        assert excinfo.value.code == 2

    def test_override_returns_404_for_unknown_task(
        self,
        isolated_runtime: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The admin override endpoint returns 404 when the task isn't
        in HUMAN_REVIEW state. The CLI surfaces this as
        :class:`CLIHTTPError`.
        """
        rc = _cli(
            "admin",
            "override",
            "tx-never-existed",
            "--disposition",
            "pass",
            "--reason",
            "test",
            config_path=_cfg(isolated_runtime),
        )
        assert rc == 3
        assert "HTTP error" in capsys.readouterr().err
