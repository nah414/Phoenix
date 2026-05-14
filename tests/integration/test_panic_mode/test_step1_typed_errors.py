"""Phase 11 Step 1 -- typed-error contracts + harness scaffolding tests.

Pins the three typed errors the §10.7 acceptance gate depends on:

- :class:`QueueUnavailable` (Phase 11 Step 1) -- new in
  ``phoenix/queue/errors.py``; surfaces from
  :meth:`TaskQueue.publish_submit` and
  :meth:`TaskQueue.subscribe_submit` when ``setup_streams`` was
  not called.
- :class:`StateBackendUnavailable` (Phase 11 Step 1) -- new in
  ``phoenix/state/errors.py``; surfaces from
  :meth:`SQLiteStateBackend._require_conn` (and Postgres
  equivalent) when the backend is closed.
- :class:`DriftStateUnavailable` (Phase 6b) -- pre-existing in
  ``phoenix/verification/drift_state.py``; surfaces from
  :func:`read_drift_state` when telemetry is unavailable.

The Steps 2-5 panic-mode tests assert on these typed errors;
Step 1 tests assert the contracts (construction, raise sites,
isinstance) before the higher-level tests rely on them.

Also pins the conftest fixtures (``isolated_runtime``,
``kill_state_backend``, ``kill_drift_state``, ``kill_queue``)
behave correctly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import phoenix  # noqa: F401  -- triggers sys.path injection
from tests.integration.test_panic_mode.conftest import (
    kill_drift_state,
    kill_queue,
    kill_state_backend,
)


pytestmark = pytest.mark.acceptance


# ---------------------------------------------------------------------
# 1. Typed-error class shapes
# ---------------------------------------------------------------------


class TestQueueUnavailable:
    def test_can_construct_without_subject(self) -> None:
        from phoenix.queue.errors import QueueUnavailable

        err = QueueUnavailable("connection refused")
        assert str(err) == "connection refused"
        assert err.subject is None

    def test_carries_subject_attribute(self) -> None:
        from phoenix.queue.errors import QueueUnavailable

        err = QueueUnavailable("no servers", subject="phoenix.tasks.submit.batch_realtime")
        assert err.subject == "phoenix.tasks.submit.batch_realtime"

    def test_is_an_exception(self) -> None:
        from phoenix.queue.errors import QueueUnavailable

        with pytest.raises(QueueUnavailable):
            raise QueueUnavailable("test")

    def test_module_import_path(self) -> None:
        """phoenix.queue.errors.QueueUnavailable is the canonical location."""
        from phoenix.queue.errors import QueueUnavailable

        assert QueueUnavailable.__module__ == "phoenix.queue.errors"


class TestStateBackendUnavailable:
    def test_can_construct_with_default_backend_kind(self) -> None:
        from phoenix.state.errors import StateBackendUnavailable

        err = StateBackendUnavailable("connection closed")
        assert err.backend_kind == "unknown"

    def test_carries_backend_kind_attribute(self) -> None:
        from phoenix.state.errors import StateBackendUnavailable

        err = StateBackendUnavailable("db locked", backend_kind="sqlite")
        assert err.backend_kind == "sqlite"

    def test_module_import_path(self) -> None:
        from phoenix.state.errors import StateBackendUnavailable

        assert StateBackendUnavailable.__module__ == "phoenix.state.errors"


class TestDriftStateUnavailable:
    """Pre-existing typed error -- pin the contract that Phase 11
    relies on hasn't drifted.
    """

    def test_carries_unavailable_detectors_list(self) -> None:
        from phoenix.verification.drift_state import DriftStateUnavailable

        err = DriftStateUnavailable(
            "tier1 detector crashed",
            unavailable_detectors=["tier1_analytical"],
        )
        assert err.unavailable_detectors == ["tier1_analytical"]

    def test_empty_unavailable_detectors_defaults_to_list(self) -> None:
        from phoenix.verification.drift_state import DriftStateUnavailable

        err = DriftStateUnavailable("stale snapshot")
        assert err.unavailable_detectors == []


# ---------------------------------------------------------------------
# 2. Raise-site contracts (typed errors fire from the right boundaries)
# ---------------------------------------------------------------------


class TestSQLiteBackendRaisesTypedError:
    def test_closed_backend_raises_state_backend_unavailable(self, tmp_path: Path) -> None:
        from phoenix.state.errors import StateBackendUnavailable
        from phoenix.state.sqlite_backend import SQLiteStateBackend

        backend = SQLiteStateBackend(db_path=tmp_path / "state.db")
        backend.close()
        with pytest.raises(StateBackendUnavailable) as excinfo:
            backend.get_kill_switch_state()
        assert excinfo.value.backend_kind == "sqlite"
        assert "closed" in str(excinfo.value).lower()


class TestTaskQueueRaisesTypedError:
    def test_publish_without_setup_raises_queue_unavailable(self) -> None:
        """``publish_submit`` before ``setup_streams`` -> QueueUnavailable."""
        import asyncio

        from phoenix.queue.errors import QueueUnavailable
        from phoenix.queue.task_queue import TaskQueue

        # TaskQueue takes a NATS client; the publish path fails BEFORE
        # touching it (the _js None check fires first), so a placeholder
        # is sufficient.
        class _StubNatsClient:
            pass

        queue = TaskQueue(nats_client=_StubNatsClient())  # type: ignore[arg-type]
        # _js stays None until setup_streams() is called

        async def _go() -> None:
            await queue.publish_submit("batch_realtime", b"payload")

        with pytest.raises(QueueUnavailable) as excinfo:
            asyncio.run(_go())
        assert excinfo.value.subject == "phoenix.tasks.submit.batch_realtime"


# ---------------------------------------------------------------------
# 3. Conftest fixtures behave correctly
# ---------------------------------------------------------------------


class TestKillStateBackendFixture:
    def test_closes_singleton_so_next_op_raises(self, isolated_runtime: Path) -> None:
        from phoenix.state import get_state_backend
        from phoenix.state.errors import StateBackendUnavailable

        # Backend is alive before the kill context manager
        get_state_backend().get_kill_switch_state()

        with kill_state_backend():
            with pytest.raises(StateBackendUnavailable):
                get_state_backend().get_kill_switch_state()

    def test_resets_cleanly_so_subsequent_ops_work(self, isolated_runtime: Path) -> None:
        from phoenix.state import get_state_backend

        with kill_state_backend():
            pass  # kill + restore

        # After the context manager exits, a fresh backend is available
        # (reset_state_backend was called).
        state = get_state_backend().get_kill_switch_state()
        assert state["engaged"] is False


class TestKillDriftStateFixture:
    def test_monkeypatches_read_drift_state(
        self,
        isolated_runtime: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Go through module attribute access so the monkeypatch
        # actually takes effect (Phase 8 Step 5 gotcha: a local
        # `from x import y` binding won't see the patched function).
        from phoenix.verification import drift_state as drift_module
        from phoenix.verification.drift_state import DriftStateUnavailable

        # Before: read_drift_state returns healthy
        result = drift_module.read_drift_state()
        assert result.state == "healthy"

        with kill_drift_state(monkeypatch):
            with pytest.raises(DriftStateUnavailable) as excinfo:
                drift_module.read_drift_state()
            assert "tier1_analytical" in excinfo.value.unavailable_detectors


class TestKillQueueFixture:
    def test_monkeypatches_publish_submit(
        self,
        isolated_runtime: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import asyncio

        from phoenix.queue.errors import QueueUnavailable
        from phoenix.queue.task_queue import TaskQueue

        class _StubNatsClient:
            pass

        queue = TaskQueue(nats_client=_StubNatsClient())  # type: ignore[arg-type]

        async def _publish() -> None:
            await queue.publish_submit("batch_realtime", b"payload")

        with kill_queue(monkeypatch):
            with pytest.raises(QueueUnavailable):
                asyncio.run(_publish())


# ---------------------------------------------------------------------
# 4. Acceptance marker is registered
# ---------------------------------------------------------------------


def test_acceptance_marker_registered() -> None:
    """The @pytest.mark.acceptance marker is registered in pyproject.toml.

    If unregistered, pytest emits a PytestUnknownMarkWarning. This
    test confirms the marker is in the canonical list.
    """
    import tomllib

    from pathlib import Path

    pyproject = (
        Path(__file__).resolve().parents[3] / "pyproject.toml"
    )  # tests/integration/test_panic_mode/test_step1.py -> repo root
    with open(pyproject, "rb") as f:
        config = tomllib.load(f)
    markers = config["tool"]["pytest"]["ini_options"]["markers"]
    marker_names = [m.split(":")[0] for m in markers]
    assert "acceptance" in marker_names
