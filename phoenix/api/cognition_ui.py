"""Mobile control panel for the Step 5c cognition harness (Phase 13 Step 5c).

A small same-origin web UI (PWA) served by the Phoenix daemon plus the JSON
endpoints it calls, so an operator can drive `audit / adapt / evaluate / train`
from a phone (over Tailscale) or a desktop browser — the same capabilities the
``phoenix cognition`` CLI exposes, calling the same ``cognition_wobble`` library
in-process.

**Auth (single-user model).** Browsers can't do Phoenix's per-request HMAC, and
these endpoints never mint an actor for an unsigned caller. A request is admitted
only when one of these holds (see :func:`_gate`):

1. ``PHOENIX_UI_TOKEN`` is set on the daemon and the request sends a matching
   ``X-Phoenix-UI-Token`` header (constant-time compare). When the token is set
   it is always required. The desktop shortcut
   (``scripts/phoenix_cognition_launch.ps1``) generates a random token per
   launch for the daemon it starts and hands it to the page in the URL
   *fragment* (``/cognition#token=...``), which browsers never send to a
   server; ``app.js`` moves it into ``sessionStorage`` and strips it from the
   address bar. The phone-over-Tailscale flow sets the same variable by hand.
2. No token is configured and the request carries a valid signed
   ``Authorization: Phoenix-Actor`` header (scripts, the CLI).

Nothing else admits a request: no peer address, ``Host`` or ``Origin`` is a
credential, and there is no header-less mode. (A short-lived
``PHOENIX_UI_LOOPBACK_NO_TOKEN`` opt-in was replaced by the per-launch token on
2026-09-16; the variable is ignored.) A present but invalid ``Authorization``
header is always a 401. The UI token opens ``/v1/cognition/*`` only, never an
actor for any other route.

**Long-running train** runs in an in-process background job; the UI polls
``GET /v1/cognition/jobs/{id}``.
"""

from __future__ import annotations

import hmac
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from phoenix.identity.bootstrap import IdentityError, require_actor

_STATIC_DIR = Path(__file__).resolve().parent.parent / "ui" / "static"

cognition_ui_router = APIRouter(tags=["Cognition UI"])

# In-process job table for long-running train. Single-process daemon; a plain
# dict + lock is sufficient (no cross-process durability needed for v1).
_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# auth + path safety


UI_TOKEN_ENV = "PHOENIX_UI_TOKEN"
UI_TOKEN_HEADER = "X-Phoenix-UI-Token"


def _gate(authorization: str | None, ui_token: str | None) -> None:
    """Admit a cognition-UI request or raise HTTP 401 (rules in the module docstring)."""
    signed = False
    if authorization is not None and authorization.strip():
        try:
            require_actor(authorization)
        except IdentityError as exc:
            raise HTTPException(status_code=401, detail=f"identity error: {exc}") from exc
        signed = True

    expected = os.environ.get(UI_TOKEN_ENV)
    if expected:
        supplied = (ui_token or "").encode("utf-8")
        if not hmac.compare_digest(supplied, expected.encode("utf-8")):
            raise HTTPException(status_code=401, detail=f"missing or invalid {UI_TOKEN_HEADER}")
        return

    if signed:
        return
    raise HTTPException(
        status_code=401,
        detail=(
            f"cognition UI requires authentication: start the daemon with {UI_TOKEN_ENV} set "
            f"and send it as {UI_TOKEN_HEADER} (the desktop shortcut does this with a fresh "
            "token per launch), or send a signed Phoenix-Actor Authorization header."
        ),
    )


def _safe_path(raw: str) -> Path:
    """Resolve a UI-supplied path, confined to ``PHOENIX_CORPUS_DIR`` when that is set.

    With the sandbox set (the desktop shortcut always sets it), a relative path
    such as ``felm_pairs.jsonl`` or ``models/gbm.txt`` is resolved against the
    sandbox directory, not the daemon's working directory, so the UI's
    placeholders work as typed. The result, after ``..`` and symlinks are
    resolved, must lie inside the sandbox; anything else is a 403 that names
    the directory. Unset → relative to the daemon's working directory, any path
    the process can access (the directory ``GET /v1/cognition/corpora``
    reports in both cases).
    """
    expanded = Path(raw).expanduser()
    sandbox = os.environ.get("PHOENIX_CORPUS_DIR")
    if not sandbox:
        return expanded.resolve()
    root = Path(sandbox).expanduser().resolve()
    # An absolute ``expanded`` replaces ``root`` in the join, so it is checked as given.
    p = (root / expanded).resolve()
    if root not in p.parents and p != root:
        raise HTTPException(
            status_code=403,
            detail=(
                f"path outside PHOENIX_CORPUS_DIR ({root}): {raw}. Use a path inside that "
                "directory; a relative path is resolved there."
            ),
        )
    return p


# ---------------------------------------------------------------------------
# request models


class AuditRequest(BaseModel):
    corpus: str
    min_per_class: int = 28


class AdaptRequest(BaseModel):
    dataset: Literal["felm", "sac3"]
    path: str
    out: str
    source_tag: str | None = None
    no_prelabel: bool = False


class EvaluateRequest(BaseModel):
    corpus: str
    model: str | None = None
    stub: bool = False
    threshold: float = 0.70
    confusion: bool = False


class TrainRequest(BaseModel):
    corpus: str
    out: str
    version: str = "gbm-v1.0.0"


# ---------------------------------------------------------------------------
# static page


@cognition_ui_router.get("/cognition", include_in_schema=False)
def cognition_index() -> FileResponse:
    """Serve the control-panel single-page app."""
    index = _STATIC_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="cognition UI not installed")
    return FileResponse(index)


# ---------------------------------------------------------------------------
# JSON endpoints


@cognition_ui_router.get("/v1/cognition/corpora")
def list_corpora(
    authorization: str | None = Header(default=None),
    x_phoenix_ui_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """List ``*.jsonl`` files under ``PHOENIX_CORPUS_DIR`` (default: cwd)."""
    _gate(authorization, x_phoenix_ui_token)
    root = Path(os.environ.get("PHOENIX_CORPUS_DIR", str(Path.cwd()))).expanduser().resolve()
    files = sorted(str(p) for p in root.glob("*.jsonl")) if root.is_dir() else []
    return {"dir": str(root), "files": files}


@cognition_ui_router.post("/v1/cognition/audit")
def audit(
    req: AuditRequest,
    authorization: str | None = Header(default=None),
    x_phoenix_ui_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _gate(authorization, x_phoenix_ui_token)
    from cognition_wobble.audit import audit_corpus
    from cognition_wobble.corpus import load_corpus

    try:
        examples = load_corpus(_safe_path(req.corpus))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    report: dict[str, Any] = audit_corpus(examples, min_per_class=req.min_per_class)
    return report


@cognition_ui_router.post("/v1/cognition/adapt")
def adapt(
    req: AdaptRequest,
    authorization: str | None = Header(default=None),
    x_phoenix_ui_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _gate(authorization, x_phoenix_ui_token)
    from cognition_wobble.corpus import count_by_class, write_corpus
    from cognition_wobble.datasets import from_felm, from_sac3
    from cognition_wobble.disagreement_types import GRADED_CLASSES
    from cognition_wobble.jsonl import read_jsonl_records

    path = _safe_path(req.path)
    if not path.exists():
        raise HTTPException(status_code=400, detail=f"input not found: {req.path}")
    # Same loader as the CLI: skips blank/# lines, enforces object records, and
    # reports `path line N` errors so UI + CLI behave identically.
    try:
        records = read_jsonl_records(path)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if req.dataset == "felm":
        report = from_felm(records, source_tag=req.source_tag or "FELM")
    else:
        report = from_sac3(
            records, source_tag=req.source_tag or "SAC3", prelabel_from_votes=not req.no_prelabel
        )
    if not report.examples:
        raise HTTPException(
            status_code=400, detail=f"adapted 0 pairs from {report.n_input} records"
        )
    try:
        write_corpus(
            _safe_path(req.out), report.examples, header_lines=(f"{req.dataset.upper()}-adapted",)
        )
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"could not write {req.out}: {exc}") from exc

    counts = count_by_class(report.examples)
    return {
        "out": req.out,
        "emitted": report.n_emitted,
        "input": report.n_input,
        "skipped": report.n_skipped,
        "per_class": {c.value: counts[c] for c in GRADED_CLASSES if counts[c]},
    }


@cognition_ui_router.post("/v1/cognition/evaluate")
def evaluate_corpus(
    req: EvaluateRequest,
    authorization: str | None = Header(default=None),
    x_phoenix_ui_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _gate(authorization, x_phoenix_ui_token)
    from cognition_wobble.acceptance import check_gate
    from cognition_wobble.corpus import load_corpus
    from cognition_wobble.eval import evaluate
    from phoenix.providers.cognition.errors import MissingOptionalDependency

    try:
        examples = load_corpus(_safe_path(req.corpus))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if req.stub:
        from cognition_wobble.classifier import AlwaysUnclassifiedClassifier

        classifier: Any = AlwaysUnclassifiedClassifier()
    elif req.model:
        from cognition_wobble.classifier_gbm import GBMClassifier

        try:
            classifier = GBMClassifier(model_path=_safe_path(req.model))
        except MissingOptionalDependency as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        raise HTTPException(status_code=400, detail="provide 'model' or set 'stub': true")

    report = evaluate(classifier, examples)
    gate = check_gate(report, threshold=req.threshold)
    out: dict[str, Any] = {
        "macro_f1": report.macro_f1,
        "gate_passed": gate.passed,
        "threshold": req.threshold,
        "n_graded": report.n_graded_examples,
        "per_class": {
            cls.value: {
                "precision": m.precision,
                "recall": m.recall,
                "f1": m.f1,
                "support": m.support,
            }
            for cls, m in report.per_class.items()
        },
    }
    if req.confusion:
        out["confusion"] = {
            f"{g.value}->{p.value}": n for (g, p), n in report.confusion_matrix.items()
        }
    return out


def _run_train_job(job_id: str, corpus: str, out: str, version: str) -> None:
    """Background worker: train the GBM and record the result in ``_JOBS``."""
    try:
        from cognition_wobble.corpus import load_corpus
        from cognition_wobble.training import train_gbm

        examples = load_corpus(Path(corpus))
        result = train_gbm(examples, model_path=Path(out), version=version)
        done: dict[str, Any] = {
            "status": "done",
            "model": str(result.model_path),
            "meta": str(result.meta_path),
            "trained_examples": result.n_train,
            "version": version,
        }
        with _JOBS_LOCK:
            _JOBS[job_id] = done
    except Exception as exc:  # noqa: BLE001 - surface any training failure to the poller
        with _JOBS_LOCK:
            _JOBS[job_id] = {"status": "error", "error": str(exc), "error_type": type(exc).__name__}


@cognition_ui_router.post("/v1/cognition/train")
def train(
    req: TrainRequest,
    authorization: str | None = Header(default=None),
    x_phoenix_ui_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Start a background training job; poll ``/v1/cognition/jobs/{id}``."""
    _gate(authorization, x_phoenix_ui_token)
    corpus = _safe_path(req.corpus)
    out = _safe_path(req.out)
    job_id = f"job_{uuid.uuid4().hex}"
    with _JOBS_LOCK:
        _JOBS[job_id] = {"status": "running"}
    threading.Thread(
        target=_run_train_job, args=(job_id, str(corpus), str(out), req.version), daemon=True
    ).start()
    return {"job_id": job_id, "status": "running"}


@cognition_ui_router.get("/v1/cognition/jobs/{job_id}")
def job_status(
    job_id: str,
    authorization: str | None = Header(default=None),
    x_phoenix_ui_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _gate(authorization, x_phoenix_ui_token)
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job {job_id}")
    return {"job_id": job_id, **job}


def register_cognition_ui(app: FastAPI) -> None:
    """Mount the static UI + include the cognition-UI router on ``app``."""
    if _STATIC_DIR.is_dir():
        app.mount(
            "/cognition/static",
            StaticFiles(directory=str(_STATIC_DIR)),
            name="cognition-static",
        )
    app.include_router(cognition_ui_router)


__all__ = ["cognition_ui_router", "register_cognition_ui"]
