"""NATS connection management (Phase 6b Step 5).

Per architecture v1 Section 1 Decision 32: NATS JetStream is Phoenix's
queue backend regardless of deployment size. Per Decision 33: solo
installs run NATS as a sibling process to the daemon (managed by the
launcher script via :mod:`phoenix.queue.embedded_runner`); org
installs typically point at an existing NATS cluster via
``$NATS_URL``.

This module provides a thin async connection wrapper around
``nats.connect``. It does NOT implement pub/sub or JetStream semantics
-- those live in :mod:`phoenix.queue.task_queue` (Step 6).

**Install requirement:** the ``nats`` optional extra
(``pip install phoenix-middleware[nats]``) brings in ``nats-py>=2.6``.
SQLite-only deployments don't need NATS at all; the lazy import
pattern means this module imports without nats-py installed, and only
:func:`connect_nats` fails (with a clear ImportError naming the
extra) when actually called.

**URL resolution precedence:**

1. Explicit ``url`` argument to :func:`connect_nats`.
2. ``$PHOENIX_NATS_URL`` (canonical Phoenix env var).
3. ``$NATS_URL`` (legacy / community convention).
4. Default ``nats://127.0.0.1:4222`` (matches the embedded runner's
   default port).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nats.aio.client import Client as NATSClient


def _default_url() -> str:
    """Resolve the NATS URL from env vars or fall back to localhost."""
    return (
        os.environ.get("PHOENIX_NATS_URL") or os.environ.get("NATS_URL") or "nats://127.0.0.1:4222"
    )


async def connect_nats(url: str | None = None) -> NATSClient:
    """Open an async connection to NATS.

    Returns a ready-to-use :class:`nats.aio.client.Client`. Caller is
    responsible for closing via ``await nc.close()`` (or, in Step 6,
    via the task-queue's lifespan-managed singleton).

    Raises:
        ImportError: when the ``nats`` optional extra is not installed.
            Message names the install command so users get a clear
            recovery step.
    """
    try:
        import nats
    except ImportError as e:
        raise ImportError(
            "NATS support requires the `nats` extra. "
            "Install with: pip install phoenix-middleware[nats]"
        ) from e

    resolved = url if url is not None else _default_url()
    nc: NATSClient = await nats.connect(resolved)
    return nc
