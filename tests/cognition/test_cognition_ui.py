"""Tests for the cognition control-panel endpoints (FastAPI TestClient)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from phoenix.api.routes import app

_REPO = Path(__file__).resolve().parents[2]
_FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_corpus.jsonl"
_FELM = _REPO / "samples" / "step5c" / "felm_sample.jsonl"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


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
    # no token header -> 401
    assert client.post("/v1/cognition/audit", json={"corpus": str(_FIXTURE)}).status_code == 401
    # correct token -> ok
    r = client.post(
        "/v1/cognition/audit",
        json={"corpus": str(_FIXTURE)},
        headers={"X-Phoenix-UI-Token": "s3cret"},
    )
    assert r.status_code == 200


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
