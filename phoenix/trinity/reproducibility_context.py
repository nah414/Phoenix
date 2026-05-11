"""Per-request reproducibility-snapshot scratchpad (Phase 7 Step 7).

The pipeline (:mod:`phoenix.trinity.pipeline`) captures the
deterministic environment before delegating to the verification gate.
The gate's post-solve ledger composer
(:mod:`phoenix.ledger.solve_composer`) needs to read that snapshot to
stamp it on the :class:`SolveEntry`. Rather than thread the snapshot
through every layer (and bloat the gate's method signatures with a
parameter that's irrelevant 99% of the time), Phase 7 Step 7 uses a
thread-local mapping keyed by ``task.request_id``.

**Why thread-local + request_id key, not a single module-level
variable:** Phoenix's daemon serves concurrent requests on multiple
worker threads. Two solves running in parallel must NOT see each
other's snapshots. A bare module-level variable would race; a
thread-local keyed by request_id keeps each request's snapshot
isolated to the thread that captured it.

**Why not :mod:`contextvars`:** Python's :mod:`contextvars` are
asyncio-aware but Phoenix's pipeline is synchronous (the FastAPI
async layer wraps sync solves in ``asyncio.to_thread``). Plain
:class:`threading.local` matches the pipeline's actual concurrency
model and stays portable.

**Lifecycle:** :meth:`set_snapshot` is called by the pipeline at
solve start; :meth:`get_snapshot` is called by the composer at
solve end; :meth:`clear_snapshot` is called by the pipeline in its
``finally`` block so a misbehaving gate can't leak snapshot data
across requests.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phoenix._internal.reproducibility import EnvSnapshot


# Per-thread dict so concurrent solves on different threads stay
# isolated. Each thread can hold at most one snapshot per request_id;
# typical request_id is unique per solve so collisions don't happen.
_local = threading.local()


def _store() -> dict[str, EnvSnapshot]:
    """Return (or lazily initialize) this thread's snapshot store."""
    if not hasattr(_local, "snapshots"):
        _local.snapshots = {}
    return _local.snapshots  # type: ignore[no-any-return]


def set_snapshot(request_id: str, snap: EnvSnapshot) -> None:
    """Record an :class:`EnvSnapshot` for ``request_id`` on this thread.

    The pipeline calls this immediately after
    :func:`capture_environment` for ``strict`` and ``replay`` modes.
    """
    _store()[request_id] = snap


def get_snapshot(request_id: str) -> EnvSnapshot | None:
    """Return the snapshot for ``request_id``, or ``None`` if unset.

    The composer calls this when building the :class:`SolveEntry`.
    ``None`` signals ``default`` mode (no snapshot was captured), and
    the composer leaves ``environment_snapshot`` as an empty dict.
    """
    return _store().get(request_id)


def clear_snapshot(request_id: str) -> None:
    """Drop the snapshot for ``request_id``.

    The pipeline calls this in its ``finally`` block so a per-request
    snapshot never leaks past the solve that captured it.
    """
    _store().pop(request_id, None)


def clear_all() -> None:
    """Drop every snapshot held by this thread.

    Test isolation helper -- production code uses
    :func:`clear_snapshot` per request.
    """
    _store().clear()


__all__ = [
    "clear_all",
    "clear_snapshot",
    "get_snapshot",
    "set_snapshot",
]
