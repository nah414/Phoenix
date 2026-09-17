"""Tests for the cognition control-panel endpoints (FastAPI TestClient)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from phoenix.api.routes import app
from tests._signed_actor import signed_client

_REPO = Path(__file__).resolve().parents[2]
_FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_corpus.jsonl"
_FELM = _REPO / "samples" / "step5c" / "felm_sample.jsonl"


_STATIC = _REPO / "phoenix" / "ui" / "static"
_LAUNCHER = _REPO / "scripts" / "phoenix_cognition_launch.ps1"


@pytest.fixture(autouse=True)
def _ui_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No UI token, no corpus sandbox and no obsolete flag unless a test sets one."""
    for name in ("PHOENIX_UI_TOKEN", "PHOENIX_UI_LOOPBACK_NO_TOKEN", "PHOENIX_CORPUS_DIR"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def client() -> TestClient:
    """Signed as adam; with no UI token configured the signed actor is accepted."""
    return signed_client(app)


def test_serves_the_spa(client: TestClient) -> None:
    r = client.get("/cognition")
    assert r.status_code == 200
    assert "Phoenix Cognition" in r.text
    assert client.get("/cognition/static/app.js").status_code == 200


def test_audit_ready_on_synthetic(client: TestClient) -> None:
    r = client.post("/v1/cognition/audit", json={"corpus": str(_FIXTURE)})
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    assert body["graded_total"] == 240


def test_audit_missing_corpus_is_400(client: TestClient, tmp_path: Path) -> None:
    r = client.post("/v1/cognition/audit", json={"corpus": str(tmp_path / "nope.jsonl")})
    assert r.status_code == 400


def test_evaluate_stub_fails_gate(client: TestClient) -> None:
    r = client.post("/v1/cognition/evaluate", json={"corpus": str(_FIXTURE), "stub": True})
    assert r.status_code == 200
    assert r.json()["gate_passed"] is False


def test_adapt_felm_sample(client: TestClient, tmp_path: Path) -> None:
    out = tmp_path / "felm.jsonl"
    r = client.post(
        "/v1/cognition/adapt",
        json={"dataset": "felm", "path": str(_FELM), "out": str(out)},
    )
    assert r.status_code == 200
    assert r.json()["emitted"] == 6
    assert out.exists()


def test_adapt_rejects_non_object_line_with_line_number(client: TestClient, tmp_path: Path) -> None:
    # The UI shares the CLI's JSONL loader, so a non-object record is a 400 that
    # names the offending line (not a silent decode of arbitrary JSON values).
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"prompt": "ok"}\n[1, 2, 3]\n', encoding="utf-8")
    r = client.post(
        "/v1/cognition/adapt",
        json={"dataset": "felm", "path": str(bad), "out": str(tmp_path / "o.jsonl")},
    )
    assert r.status_code == 400
    assert "line 2" in r.json()["detail"]


def test_ui_token_gate(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIX_UI_TOKEN", "s3cret")
    # no token header -> 401, even though the client sends a signed actor
    assert client.post("/v1/cognition/audit", json={"corpus": str(_FIXTURE)}).status_code == 401
    # wrong token -> 401
    wrong = client.post(
        "/v1/cognition/audit",
        json={"corpus": str(_FIXTURE)},
        headers={"X-Phoenix-UI-Token": "s3cre"},
    )
    assert wrong.status_code == 401
    # correct token -> ok
    r = client.post(
        "/v1/cognition/audit",
        json={"corpus": str(_FIXTURE)},
        headers={"X-Phoenix-UI-Token": "s3cret"},
    )
    assert r.status_code == 200


def test_ui_token_alone_admits_a_browser_without_an_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The browser flow: no Authorization header, just the daemon's UI token."""
    monkeypatch.setenv("PHOENIX_UI_TOKEN", "per-launch-token")
    browser = TestClient(app, base_url="http://127.0.0.1:8003")
    r = browser.post(
        "/v1/cognition/audit",
        json={"corpus": str(_FIXTURE)},
        headers={"X-Phoenix-UI-Token": "per-launch-token"},
    )
    assert r.status_code == 200
    assert browser.get("/v1/cognition/corpora").status_code == 401


def test_signed_actor_is_accepted_when_no_token_is_configured(client: TestClient) -> None:
    assert client.get("/v1/cognition/corpora").status_code == 200


@pytest.mark.parametrize("flag", ["1", "true"])
def test_no_header_and_no_token_is_401_whatever_the_obsolete_flag(
    monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    """``PHOENIX_UI_LOOPBACK_NO_TOKEN`` has no effect, even for a loopback browser."""
    monkeypatch.setenv("PHOENIX_UI_LOOPBACK_NO_TOKEN", flag)
    browser = TestClient(app, base_url="http://127.0.0.1:8003", client=("127.0.0.1", 50000))
    r = browser.post("/v1/cognition/audit", json={"corpus": str(_FIXTURE)})
    assert r.status_code == 401
    assert "PHOENIX_UI_TOKEN" in r.json()["detail"]


def test_served_app_js_takes_the_launch_token_from_the_fragment(client: TestClient) -> None:
    """Guards the browser half of the per-launch token handoff.

    The token arrives as ``/cognition#token=...`` (a fragment is never sent to
    the server), is kept in sessionStorage rather than localStorage, is
    stripped from the address bar, and goes out as ``X-Phoenix-UI-Token``.
    """
    js = client.get("/cognition/static/app.js").text
    assert "window.location.hash" in js
    assert 'startsWith("token=")' in js
    after = js.split("const launchToken = takeTokenFromFragment();")[1]
    # The launch token's only use is the sessionStorage write (never localStorage).
    assert "if (launchToken) writeStore(sessionStorage, TOKEN_KEY, launchToken);" in after
    assert after.count("launchToken") == 2
    assert "history.replaceState(" in js
    assert 'headers["X-Phoenix-UI-Token"] = token' in js


def test_launcher_hands_a_random_per_launch_token_in_the_fragment() -> None:
    """Guards the launcher half: CSPRNG token, fragment not query, confined corpus dir."""
    ps1 = _LAUNCHER.read_text(encoding="utf-8")
    assert "System.Security.Cryptography.RandomNumberGenerator" in ps1
    assert "$env:PHOENIX_UI_TOKEN = $token" in ps1
    assert '"$url#token=$token"' in ps1
    assert "?token=" not in ps1
    assert "$env:PHOENIX_CORPUS_DIR = Join-Path $phoenixHome" in ps1
    # The removed header-less loopback mode is never switched on.
    assert "$env:PHOENIX_UI_LOOPBACK_NO_TOKEN" not in ps1
    # A running daemon without a token is reported instead of opening a 401 panel.
    assert "WITHOUT a cognition UI token" in ps1


@pytest.fixture
def sandbox(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """``PHOENIX_CORPUS_DIR`` as the desktop shortcut sets it, holding the fixture corpora.

    The daemon's working directory is deliberately a different directory, as with
    the launcher (``-WorkingDirectory`` is the repo, the sandbox is under the
    user's Phoenix data dir).
    """
    root = tmp_path / "phoenix-home" / "corpora"
    root.mkdir(parents=True)
    (root / "corpus.jsonl").write_bytes(_FIXTURE.read_bytes())
    (root / "felm_sample.jsonl").write_bytes(_FELM.read_bytes())
    workdir = tmp_path / "daemon-cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("PHOENIX_CORPUS_DIR", str(root))
    return root


def test_sandbox_resolves_a_bare_filename_inside_the_corpus_dir(
    client: TestClient, sandbox: Path
) -> None:
    """The UI placeholders (``corpus.jsonl``, ``felm_pairs.jsonl``) work as typed."""
    audit = client.post("/v1/cognition/audit", json={"corpus": "corpus.jsonl"})
    assert audit.status_code == 200, audit.text
    assert audit.json()["graded_total"] == 240

    evaluate = client.post("/v1/cognition/evaluate", json={"corpus": "corpus.jsonl", "stub": True})
    assert evaluate.status_code == 200, evaluate.text

    adapt = client.post(
        "/v1/cognition/adapt",
        json={"dataset": "felm", "path": "felm_sample.jsonl", "out": "out/felm_pairs.jsonl"},
    )
    assert adapt.status_code == 200, adapt.text
    assert adapt.json()["emitted"] == 6
    assert (sandbox / "out" / "felm_pairs.jsonl").is_file()
    # Nothing was written relative to the daemon's working directory.
    assert not (Path.cwd() / "out").exists()

    corpora = client.get("/v1/cognition/corpora").json()
    assert corpora["dir"] == str(sandbox.resolve())


@pytest.mark.parametrize(
    "escape",
    ["../outside.jsonl", "sub/../../outside.jsonl", "..", "absolute-outside"],
)
def test_sandbox_still_refuses_paths_outside_and_names_the_dir(
    client: TestClient, sandbox: Path, escape: str
) -> None:
    outside = sandbox.parent / "outside.jsonl"
    outside.write_bytes(_FIXTURE.read_bytes())
    raw = str(outside) if escape == "absolute-outside" else escape

    audit = client.post("/v1/cognition/audit", json={"corpus": raw})
    assert audit.status_code == 403, audit.text
    detail = audit.json()["detail"]
    assert "path outside PHOENIX_CORPUS_DIR" in detail
    assert str(sandbox.resolve()) in detail

    adapt = client.post(
        "/v1/cognition/adapt",
        json={"dataset": "felm", "path": "felm_sample.jsonl", "out": raw},
    )
    assert adapt.status_code == 403, adapt.text
    assert str(sandbox.resolve()) in adapt.json()["detail"]
    train = client.post("/v1/cognition/train", json={"corpus": "corpus.jsonl", "out": raw})
    assert train.status_code == 403, train.text
    assert outside.read_bytes() == _FIXTURE.read_bytes()


def test_without_sandbox_a_relative_path_uses_the_working_directory(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "corpus.jsonl").write_bytes(_FIXTURE.read_bytes())
    monkeypatch.chdir(tmp_path)
    r = client.post("/v1/cognition/audit", json={"corpus": "corpus.jsonl"})
    assert r.status_code == 200, r.text
    assert client.get("/v1/cognition/corpora").json()["dir"] == str(tmp_path.resolve())


def test_served_panel_shows_where_relative_paths_resolve(client: TestClient) -> None:
    assert 'id="corpus-dir"' in client.get("/cognition").text
    js = client.get("/cognition/static/app.js").text
    assert 'const { dir, files } = await api("/v1/cognition/corpora");' in js
    assert "dirEl.textContent" in js


def test_train_job_runs_to_completion(client: TestClient, tmp_path: Path) -> None:
    pytest.importorskip("lightgbm")
    out = tmp_path / "gbm.txt"
    r = client.post("/v1/cognition/train", json={"corpus": str(_FIXTURE), "out": str(out)})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    assert r.json()["status"] == "running"

    job = {"status": "running"}
    for _ in range(100):  # up to ~20s
        job = client.get(f"/v1/cognition/jobs/{job_id}").json()
        if job["status"] != "running":
            break
        time.sleep(0.2)
    assert job["status"] == "done", job
    assert out.exists()
    assert job["trained_examples"] == 240


def test_unknown_job_is_404(client: TestClient) -> None:
    assert client.get("/v1/cognition/jobs/job_nope").status_code == 404
