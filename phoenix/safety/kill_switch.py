"""Kill switch with persist-across-restart, refuse-to-start posture.

Per architecture v1 Section 8.3 + Section 11.5.1 (RESOLVED 2026-05-08):
the kill switch persists across daemon restart and the daemon refuses
to start when the kill switch is engaged. This is the explicit
disposition: emergencies are exactly the state that should NOT
silently lift on a side-effect restart.

Phase 6a ships the JSON-file backend. Phase 6b's SQLite backend swaps
in via the :class:`StateBackend` Protocol (Step 4).

The safety gate (Step 5) checks the kill-switch state at Stage 0 of
its 9-stage pipeline; engaged state raises :class:`KillSwitchEngaged`
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

from phoenix.identity.keystore import _keystore_dir

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
    """JSON-file-backed kill-switch state.

    Phase 6a synchronous, single-process. Phase 6b's
    :class:`StateBackend` Protocol takes over via the abstract
    :func:`get_kill_switch_state` / :func:`set_kill_switch_state`
    methods.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else _kill_switch_path()
        self._lock = threading.Lock()

    def read(self) -> KillSwitchState:
        """Read current state. Returns disengaged default if file is
        absent or corrupt (Phase 6a; Phase 6b logs a structured
        warning on corruption)."""
        with self._lock:
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

    def engage(self, *, by: str, reason: str) -> KillSwitchState:
        """Flip the kill switch. Returns the new state for audit log
        lookup."""
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
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(asdict(state), indent=2, sort_keys=True),
                encoding="utf-8",
            )
            tmp.replace(self._path)


# Module-level singleton.
_STORE: KillSwitchStore | None = None


def get_store() -> KillSwitchStore:
    """Lazy module-level :class:`KillSwitchStore` singleton."""
    global _STORE
    if _STORE is None:
        _STORE = KillSwitchStore()
    return _STORE
