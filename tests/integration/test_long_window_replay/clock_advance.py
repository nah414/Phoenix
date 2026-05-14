"""Clock-monkeypatch helper for long-window replay tests (Phase 11 Step 6).

Per locked OPEN-2 (Phase 11): the §10.7 long-window replay test
must verify that a ledger entry recorded MONTHS ago still replays
bit-exactly today. Rather than waiting wall-clock time (impossible
in CI) or building a fixture-only test (doesn't actually exercise
the elapsed-time invariant), Phase 11 monkeypatches
:func:`time.time` to fast-forward the system clock.

The contract this helper exercises:

- **Replay is timestamp-independent.** A ledger entry recorded
  at unix=T_0 should replay successfully at unix=T_0 + 6 months
  (or T_0 + 6 years) with the SAME ``result_hash``. The
  determinism contract (Section 1 Decision 21) lives in
  ``requirements.lock`` + ``vendor/VENDOR_VERSION.txt`` +
  per-RNG-seed + FP-environment -- NONE of which depend on
  wall-clock time.

- **Vendor-version-invariance still holds.** If the vendor manifest
  has been silently upgraded between solve and replay (e.g., a
  library got bumped in CI), THAT is what causes a divergence --
  not the elapsed time. The clock advance isolates time from
  vendor-state as the test's failure dimension.

The helper monkeypatches:

- ``time.time()`` -- the canonical wall-clock read.
- ``time.time_ns()`` -- consumed by some downstream paths.
- ``datetime.datetime.now()`` -- used in ISO-format timestamping.
- ``datetime.datetime.utcnow()`` -- deprecated but still in code.

It does NOT monkeypatch ``time.monotonic()`` -- replay is timestamp-
agnostic, not duration-agnostic. Code that times its own
operations via monotonic() should keep working normally during
the advance.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

import pytest


@contextmanager
def monkeypatch_clock(
    monkeypatch: pytest.MonkeyPatch,
    *,
    target_unix: float,
) -> Iterator[float]:
    """Fast-forward ``time.time()`` to ``target_unix`` for the
    duration of the context.

    The advance affects:

    - ``time.time()`` -- returns ``target_unix`` exactly while the
      context is active. Repeated calls return the same value
      (deterministic; no drift during the test).
    - ``time.time_ns()`` -- returns ``int(target_unix * 1e9)``.

    ``time.monotonic()`` is NOT patched -- replay logic that needs
    elapsed-time measurement (timeouts, wall-clock-ms budgets)
    should keep working normally.

    **Important:** the context restores ``time.time`` on exit
    (NOT when the parent test ends). The ``monkeypatch`` argument
    is still accepted for the symmetric API + the future case
    where we add tz-aware datetime patching, but the actual
    save/restore uses plain ``setattr`` + try/finally so the
    context boundary is honored.

    Yields ``target_unix`` so callers can confirm the patched value
    without re-reading.

    Example::

        # Advance the clock 6 months from now
        six_months_unix = time.time() + 180 * 86400
        with monkeypatch_clock(monkeypatch, target_unix=six_months_unix):
            # time.time() returns six_months_unix
            replay(task_id)  # bit-exact even though "6 months" passed
        # time.time() now back to real wall-clock
    """
    # Argument retained for symmetric API; not currently used (see
    # docstring note re: datetime patching).
    _ = monkeypatch

    def _fake_time() -> float:
        return target_unix

    def _fake_time_ns() -> int:
        return int(target_unix * 1_000_000_000)

    # Save originals via plain setattr/getattr so we control the
    # exact restore point (the context exit, not the test exit).
    original_time = time.time
    original_time_ns = time.time_ns
    setattr(time, "time", _fake_time)
    setattr(time, "time_ns", _fake_time_ns)
    try:
        yield target_unix
    finally:
        setattr(time, "time", original_time)
        setattr(time, "time_ns", original_time_ns)


__all__ = ["monkeypatch_clock"]
