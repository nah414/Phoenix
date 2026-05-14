"""Shared fixtures for long-window replay tests (Phase 11).

Same shape as :mod:`tests.integration.test_panic_mode.conftest`:
``isolated_runtime`` gives each test a fresh Phoenix runtime with
all singletons reset + env vars pointing at tmp_path. The clock
monkeypatch + the deterministic SolveEntry fixture builder live
in their own modules (``clock_advance`` + ``fixture_solve_entry``)
so the test bodies stay focused on the §10.7 acceptance assertion.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import phoenix  # noqa: F401  -- triggers sys.path injection


@pytest.fixture
def isolated_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[Path]:
    """Per-test fresh Phoenix runtime with all singletons reset."""
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
    from phoenix.trinity import reproducibility_context

    reset_emitter()
    reset_ledger()
    reset_state_backend()
    ks_module._STORE = None
    get_limiter().reset_all()
    permissions_module._REGISTRY = None
    reset_seams()
    reproducibility_context.clear_all()
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
        reproducibility_context.clear_all()
