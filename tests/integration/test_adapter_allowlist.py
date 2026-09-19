"""Adapter-loader module allowlist (follow-up PHX-FU3, 2026-09-18).

``POST /v1/adapters`` used to hand ``spec`` straight to
``importlib.import_module`` and call the named attribute, so any signed
actor holding ``can_load_adapter`` could import (and call a zero-arg
callable from) any module on the daemon's path -- ``os``,
``subprocess``, anything installed.

The loader now refuses, **before any import**, every module outside:

- Phoenix's own adapter package, ``phoenix.adapters`` (minus the
  subsystem's own machinery: loader, registry, sandbox, ...), and
- the namespaces an operator lists in ``PHOENIX_ADAPTER_ALLOWLIST``
  (comma-separated dotted prefixes).

The REST surface answers a refused spec with 403
``adapter_module_not_allowed``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from os import getcwd  # noqa: F401  -- re-export probe for the __module__ check
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import phoenix  # noqa: F401  -- triggers sys.path injection
from phoenix.adapters import IdentityAdapter
from phoenix.adapters import errors as adapter_errors
from phoenix.adapters import loader as adapter_loader
from phoenix.adapters import (
    get_registry as get_adapter_registry,
    load_adapter,
    make_identity_adapter,
    reset_registry as reset_adapter_registry,
)
from phoenix.adapters.errors import AdapterError
from phoenix.api.routes import app
from tests._signed_actor import signed_client

_ENV = "PHOENIX_ADAPTER_ALLOWLIST"
_IDENTITY_SPEC = "phoenix.adapters.identity_adapter:make_identity_adapter"
_CANARY_MODULE = "tests.integration._adapter_import_canary"
_THIS_MODULE = "tests.integration.test_adapter_allowlist"

# Modules outside every allowlist. Each factory is harmless if a
# regression lets it run: getcwd() returns a str and Popen() without
# arguments raises TypeError.
_FOREIGN_SPECS = ["os:getcwd", "subprocess:Popen", "subprocess:getoutput"]


def make_allowlisted_identity() -> IdentityAdapter:
    """A legitimate adapter factory defined in a test module.

    Loadable only when the operator allowlists this module's namespace.
    """
    return make_identity_adapter(name="allowlisted-identity")


@pytest.fixture(autouse=True)
def clean_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No operator allowlist unless a test sets one; empty registry."""
    monkeypatch.delenv(_ENV, raising=False)
    reset_adapter_registry()
    try:
        yield
    finally:
        reset_adapter_registry()


@pytest.fixture
def import_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every module the adapter loader asks importlib for."""
    imported: list[str] = []
    real_import: Callable[..., Any] = adapter_loader.importlib.import_module

    def spy(name: str, package: str | None = None) -> Any:
        imported.append(name)
        return real_import(name, package)

    monkeypatch.setattr(adapter_loader, "importlib", SimpleNamespace(import_module=spy))
    return imported


@pytest.fixture
def isolated_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Per-test fresh runtime for the REST tests (mirrors test_adapters_step3)."""
    runtime = tmp_path / "phoenix_runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PHOENIX_SQLITE_DB_PATH", str(runtime / "state.db"))
    monkeypatch.setenv("PHOENIX_AUDIT_DIR", str(runtime / "audit"))
    monkeypatch.setenv("PHOENIX_KILL_SWITCH_PATH", str(runtime / "kill_switch.json"))

    from phoenix.audit import reset_emitter
    from phoenix.ledger import reset_ledger
    from phoenix.safety import kill_switch as ks_module
    from phoenix.safety.rate_limiter import get_limiter
    from phoenix.state import reset_state_backend

    def _reset() -> None:
        reset_emitter()
        reset_ledger()
        reset_state_backend()
        ks_module._STORE = None
        get_limiter().reset_all()

    _reset()
    try:
        yield runtime
    finally:
        _reset()


# ---------------------------------------------------------------------
# Loader: foreign modules are refused before any import
# ---------------------------------------------------------------------


@pytest.mark.parametrize("spec", _FOREIGN_SPECS)
def test_foreign_module_refused_without_import(spec: str, import_spy: list[str]) -> None:
    with pytest.raises(AdapterError) as excinfo:
        load_adapter(spec)
    assert import_spy == [], f"loader imported {import_spy} for refused spec {spec!r}"
    assert isinstance(excinfo.value, adapter_errors.AdapterSpecNotAllowed)
    assert excinfo.value.module_path == spec.partition(":")[0]
    assert _ENV in str(excinfo.value)
    assert get_adapter_registry().list_adapters() == []


def test_refused_module_never_reaches_sys_modules() -> None:
    """End-to-end proof, independent of how the loader imports."""
    assert _CANARY_MODULE not in sys.modules
    with pytest.raises(AdapterError):
        load_adapter(f"{_CANARY_MODULE}:make_adapter")
    assert _CANARY_MODULE not in sys.modules


def test_allowlist_prefix_matches_whole_components_only(
    monkeypatch: pytest.MonkeyPatch, import_spy: list[str]
) -> None:
    """``tests.integ`` must not admit ``tests.integration``."""
    monkeypatch.setenv(_ENV, "tests.integ")
    with pytest.raises(AdapterError) as excinfo:
        load_adapter(f"{_THIS_MODULE}:make_allowlisted_identity")
    assert isinstance(excinfo.value, adapter_errors.AdapterSpecNotAllowed)
    assert import_spy == []


def test_lookalike_of_builtin_namespace_is_refused(import_spy: list[str]) -> None:
    """``phoenix.adapters_evil`` is not under ``phoenix.adapters``."""
    with pytest.raises(AdapterError) as excinfo:
        load_adapter("phoenix.adapters_evil:make")
    assert isinstance(excinfo.value, adapter_errors.AdapterSpecNotAllowed)
    assert import_spy == []


@pytest.mark.parametrize(
    "spec",
    [
        "phoenix.adapters.registry:reset_registry",
        "phoenix.adapters:reset_registry",
        "phoenix.adapters.sandbox:_restricted_env",
    ],
)
def test_adapter_subsystem_machinery_is_not_loadable(spec: str) -> None:
    """The package's own plumbing is not an adapter.

    ``reset_registry`` takes no arguments; before the allowlist, loading
    it as a "factory" silently emptied the registry.
    """
    load_adapter(_IDENTITY_SPEC)
    with pytest.raises(AdapterError) as excinfo:
        load_adapter(spec)
    assert isinstance(excinfo.value, adapter_errors.AdapterSpecNotAllowed)
    assert [r.adapter.name for r in get_adapter_registry().list_adapters()] == ["identity"]


def test_callable_reexported_from_foreign_module_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An allowlisted module can't launder a callable defined elsewhere.

    This module does ``from os import getcwd``; the factory's defining
    module (``nt`` / ``posix``) is not allowlisted, so it is not called.
    """
    monkeypatch.setenv(_ENV, _THIS_MODULE)
    with pytest.raises(AdapterError) as excinfo:
        load_adapter(f"{_THIS_MODULE}:getcwd")
    assert isinstance(excinfo.value, adapter_errors.AdapterSpecNotAllowed)


@pytest.mark.parametrize("spec", [".relative:make", "phoenix..adapters:make", "a-b.c:make"])
def test_malformed_module_path_is_a_spec_error(spec: str, import_spy: list[str]) -> None:
    """Not a dotted identifier path -> AdapterError (400), never a TypeError (500)."""
    with pytest.raises(AdapterError):
        load_adapter(spec)
    assert import_spy == []


# ---------------------------------------------------------------------
# Loader: legitimate adapters still load
# ---------------------------------------------------------------------


def test_builtin_identity_adapter_still_loads(import_spy: list[str]) -> None:
    record = load_adapter(_IDENTITY_SPEC)
    assert record.adapter.name == "identity"
    assert import_spy == ["phoenix.adapters.identity_adapter"]


def test_configured_allowlist_entry_admits_its_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV, " some.other.pkg , tests.integration ")
    record = load_adapter(f"{_THIS_MODULE}:make_allowlisted_identity")
    assert record.adapter.name == "allowlisted-identity"


def test_test_module_factory_refused_without_configured_entry() -> None:
    with pytest.raises(AdapterError) as excinfo:
        load_adapter(f"{_THIS_MODULE}:make_allowlisted_identity")
    assert isinstance(excinfo.value, adapter_errors.AdapterSpecNotAllowed)


# ---------------------------------------------------------------------
# REST surface
# ---------------------------------------------------------------------


@pytest.mark.parametrize("spec", _FOREIGN_SPECS)
def test_post_foreign_module_returns_403(
    spec: str, isolated_runtime: Path, import_spy: list[str]
) -> None:
    with signed_client(app) as client:
        resp = client.post("/v1/adapters", json={"spec": spec})
    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "adapter_module_not_allowed"
    assert detail["module"] == spec.partition(":")[0]
    assert _ENV in detail["message"]
    assert import_spy == []
    with signed_client(app) as client:
        assert client.get("/v1/adapters").json()["count"] == 0


def test_post_builtin_identity_adapter_still_returns_200(isolated_runtime: Path) -> None:
    with signed_client(app) as client:
        resp = client.post("/v1/adapters", json={"spec": _IDENTITY_SPEC})
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "identity"
