"""Kill switch with persist-across-restart, refuse-to-start posture.

Per architecture v1 Section 8.3 + Section 11.5.1 (RESOLVED 2026-05-08):
the kill switch persists across daemon restart and the daemon refuses
to start when the kill switch is engaged. This is the explicit
disposition: emergencies are exactly the state that should NOT
silently lift on a side-effect restart.

Phase 6a shipped the JSON-file backend. Phase 6b Step 2 adds
*write-through* to a :class:`StateBackend` (typically SQLite): when a
backend is injected via :func:`set_store_backend`, writes go to both
the JSON file (Phase 6a-style) and the backend; reads prefer the
backend with a JSON fallback. The kill switch fails *closed* on any
read mismatch -- if either source reports engaged, the gate refuses
to proceed. Phase 7 promotes the backend to source of truth and
removes the JSON path.

The safety gate checks the kill-switch state at Stage 0 of its
9-stage pipeline; engaged state raises :class:`KillSwitchEngaged`
which the front door maps to HTTP 503.

The dev-ops admin endpoint (Phase 8) lets ops engage / release; Phase
6a ships a minimal CLI script ``scripts/kill_switch.py`` for ops
convenience.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from phoenix.identity.keystore import _keystore_dir

if TYPE_CHECKING:
    from phoenix.state.backend_protocol import StateBackend

_KILL_SWITCH_FILENAME = "kill_switch.json"


class KillSwitchEngaged(Exception):
    """The kill switch is engaged; refusing new requests.

    Carries the engagement metadata so audit logs see who flipped it
    and why. Phase 8's admin endpoint reads the same metadata.
    """

    def __init__(
        self,
        message: str,
        *,
        engaged_at_utc: str | None = None,
        engaged_by: str | None = None,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.engaged_at_utc = engaged_at_utc
        self.engaged_by = engaged_by
        self.reason = reason


@dataclass(frozen=True)
class KillSwitchState:
    """Phase 6a kill-switch state shape.

    Fields:
        engaged: ``True`` when the switch is flipped; ``False`` when
            normal operations are allowed.
        engaged_at_utc: ISO 8601 UTC of the flip; None when never
            engaged or after release.
        engaged_by: Actor name that flipped the switch (Phase 8 admin
            endpoint records this; Phase 6a may write
            ``"phoenix-cli"`` for ops-script invocations).
        reason: Free-form reason string (Section 8.3 audit log).
    """

    engaged: bool = False
    engaged_at_utc: str | None = None
    engaged_by: str | None = None
    reason: str | None = None


def _kill_switch_path() -> Path:
    return _keystore_dir() / _KILL_SWITCH_FILENAME


class KillSwitchStore:
    """JSON-file-backed kill-switch state with optional Phase 6b
    write-through to a :class:`StateBackend`.

    Phase 6a behavior is preserved when ``backend`` is ``None`` (the
    default): all reads / writes go to JSON only. Phase 6b Step 4's
    factory injects a backend via :func:`set_store_backend`, after
    which writes go to both sources and reads prefer the backend.

    Fail-closed: when both sources are present and disagree on
    ``engaged``, :meth:`read` returns the engaged variant (carrying
    whichever source's metadata fired). This guarantees that a
    desync caused by power loss between the two writes never
    silently *disengages* the kill switch.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        backend: StateBackend | None = None,
    ) -> None:
        self._path = path if path is not None else _kill_switch_path()
        self._lock = threading.Lock()
        self._backend = backend

    def set_backend(self, backend: StateBackend | None) -> None:
        """Inject (or clear) the write-through backend. Phase 6b Step
        4's factory calls this once at daemon startup."""
        with self._lock:
            self._backend = backend

    def read(self) -> KillSwitchState:
        """Read current state.

        When no backend is wired, returns the JSON file's content
        (disengaged default if absent or corrupt). With a backend
        wired, reads both sources and applies the fail-closed rule:
        if either reports engaged, return engaged; if they agree,
        return that.
        """
        with self._lock:
            json_state = self._read_json_locked()
            if self._backend is None:
                return json_state
            backend_state = self._read_backend_locked()
        return _fail_closed_merge(json_state, backend_state)

    def engage(self, *, by: str, reason: str) -> KillSwitchState:
        """Flip the kill switch. Writes to JSON first (Phase 6a
        durability), then backend (if wired). Returns the new state."""
        new_state = KillSwitchState(
            engaged=True,
            engaged_at_utc=datetime.now(timezone.utc).isoformat(),
            engaged_by=by,
            reason=reason,
        )
        self._write(new_state)
        return new_state

    def release(self) -> KillSwitchState:
        """Release the kill switch. Returns the new disengaged state."""
        new_state = KillSwitchState()
        self._write(new_state)
        return new_state

    def assert_disengaged(self) -> None:
        """Safety-gate Stage 0: refuse-to-proceed when engaged.

        Raises :class:`KillSwitchEngaged` carrying the engagement
        metadata so the front door's HTTP 503 response includes the
        reason.
        """
        state = self.read()
        if state.engaged:
            raise KillSwitchEngaged(
                (
                    f"Kill switch engaged: {state.reason!r} "
                    f"(by {state.engaged_by!r} at {state.engaged_at_utc}). "
                    f"Phoenix is refusing new requests until the switch is "
                    f"released via 'phoenix admin kill-switch release' "
                    f"(Phase 8 endpoint) or by manual deletion of "
                    f"~/.phoenix/runtime/kill_switch.json (Phase 6a fallback)."
                ),
                engaged_at_utc=state.engaged_at_utc,
                engaged_by=state.engaged_by,
                reason=state.reason,
            )

    def _write(self, state: KillSwitchState) -> None:
        """Write to JSON first (Phase 6a durability), then to backend
        if wired. Write order matters for SAFETY: a power loss between
        the two writes leaves JSON updated and backend stale; the
        fail-closed read merge then prefers the engaged variant, so
        either ordering is safe as long as the read merge runs."""
        with self._lock:
            self._write_json_locked(state)
            if self._backend is not None:
                self._backend.set_kill_switch_state(asdict(state))

    def _read_json_locked(self) -> KillSwitchState:
        """JSON read; caller holds ``self._lock``."""
        if not self._path.exists():
            return KillSwitchState()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return KillSwitchState(
                engaged=bool(data.get("engaged", False)),
                engaged_at_utc=data.get("engaged_at_utc"),
                engaged_by=data.get("engaged_by"),
                reason=data.get("reason"),
            )
        except (OSError, ValueError, TypeError):
            return KillSwitchState()

    def _read_backend_locked(self) -> KillSwitchState:
        """Backend read; caller holds ``self._lock``. Backend's own
        lock handles its internal synchronization."""
        assert self._backend is not None
        record = self._backend.get_kill_switch_state()
        return KillSwitchState(
            engaged=bool(record.get("engaged", False)),
            engaged_at_utc=record.get("engaged_at_utc"),
            engaged_by=record.get("engaged_by"),
            reason=record.get("reason"),
        )

    def _write_json_locked(self, state: KillSwitchState) -> None:
        """Atomic tmp-file + rename JSON write; caller holds ``self._lock``."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(asdict(state), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(self._path)


def _fail_closed_merge(
    json_state: KillSwitchState, backend_state: KillSwitchState
) -> KillSwitchState:
    """Merge two kill-switch reads under the fail-closed rule.

    Returns the engaged variant if either source is engaged. When both
    are engaged we prefer the backend's metadata (newer per the
    JSON-first write order); when only one is engaged we return that
    one. When both agree on disengaged, return the disengaged state.
    """
    if backend_state.engaged:
        return backend_state
    if json_state.engaged:
        return json_state
    return KillSwitchState()


# Module-level singleton.
_STORE: KillSwitchStore | None = None


def get_store() -> KillSwitchStore:
    """Lazy module-level :class:`KillSwitchStore` singleton."""
    global _STORE
    if _STORE is None:
        _STORE = KillSwitchStore()
    return _STORE


def set_store_backend(backend: StateBackend | None) -> None:
    """Inject (or clear) the write-through backend on the singleton.

    Phase 6b Step 4's factory calls this once at daemon startup with
    the configured :class:`SQLiteStateBackend` (or
    :class:`PostgresStateBackend`). Tests injecting their own backend
    typically construct a fresh :class:`KillSwitchStore` instead.
    """
    get_store().set_backend(backend)
