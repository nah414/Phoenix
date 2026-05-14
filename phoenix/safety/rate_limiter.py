"""Token-bucket rate limiter (in-memory).

Per architecture v1 Section 7.5 + Section 1 Decision 23: per-actor
rate limiting with variable cost weighting per endpoint. Phase 6a
ships in-memory only; buckets reset on daemon restart per Section 1
Decision 31. Phase 6b adds write-through to the state backend so
buckets survive restarts within their refill window.

Tier defaults:

============= =============== =====================================
Tier          Capacity        Refill rate
============= =============== =====================================
default       100 tokens      1 token/sec   (60/min sustained)
elevated      1000 tokens     16 tokens/sec (~1000/min sustained)
admin         unlimited       n/a
============= =============== =====================================

Endpoint costs (Section 7.5):

- ``GET /v1/health`` -- 0 tokens (free).
- ``GET /v1/identity/whoami`` -- 0 tokens.
- ``GET /v1/tasks/{id}`` -- 1 token.
- ``POST /v1/tasks`` at R1 / R2 / R3 / R4 / R5 -- 5 / 10 / 15 / 20 / 25 tokens.
- ``POST /v1/tasks/{id}/replay`` in replay mode -- 50 tokens.
- ``POST /v1/adapters`` (load LoRA) -- 10 tokens.
- ``POST /v1/identity/ws-token`` (WebSocket bearer) -- 1 token.

Phase 6a ships the cost catalogue + the algorithm; Phase 8 admin
extends with per-install tuning.
"""

from __future__ import annotations

import threading
import time
from typing import Any
from dataclasses import dataclass


# Tier capacity + refill (tokens, tokens/sec).
_TIER_CONFIGS: dict[str, tuple[int, float]] = {
    "default": (100, 1.0),
    "elevated": (1000, 16.0),
    "admin": (10**12, 10**12),  # effectively unlimited
}

# Endpoint cost catalogue. Keys are stable strings; the safety gate
# (Step 5) maps the actual route + rung to the cost key.
_COST_CATALOGUE: dict[str, int] = {
    "health": 0,
    "identity_whoami": 0,
    "tasks_get": 1,
    "tasks_submit_r1": 5,
    "tasks_submit_r2": 10,
    "tasks_submit_r3": 15,
    "tasks_submit_r4": 20,
    "tasks_submit_r5": 25,
    "tasks_replay": 50,
    "adapters_post": 10,
    "identity_enroll": 5,  # Phase 9 Step 5: admin-only, high-trust mutation
    "ws_token": 1,
    # Phase 8: admin endpoints. Read calls are 1 (matches tasks_get);
    # mutation calls (kill switch, quarantine/restore, override,
    # calibration run, adapter force-revalidate) are 5 -- not free,
    # so a malicious admin token can't engage/release the kill switch
    # in a tight loop and exhaust the audit chain.
    "admin.ping": 0,
    "admin.read": 1,
    "admin.mutate": 5,
}


@dataclass
class _Bucket:
    """Mutable per-actor bucket state."""

    capacity: int
    refill_rate: float  # tokens per second
    tokens: float
    last_refill_unix: float


class RateLimitExceeded(Exception):
    """Raised when an Actor's bucket can't cover the request cost.

    Carries ``retry_after_seconds`` so the front door can return an
    HTTP 429 with the standard ``Retry-After`` header.
    """

    def __init__(
        self,
        message: str,
        *,
        actor_name: str,
        cost: int,
        tokens_remaining: float,
        retry_after_seconds: float,
    ) -> None:
        super().__init__(message)
        self.actor_name = actor_name
        self.cost = cost
        self.tokens_remaining = tokens_remaining
        self.retry_after_seconds = retry_after_seconds


class RateLimiter:
    """Token-bucket rate limiter (in-memory, Phase 6a).

    Per actor: lazy-initialized bucket on first request. Refill on
    every check based on elapsed wall-clock time since last refill.
    Phase 6a uses ``time.monotonic()`` for the refill clock so wall-
    clock adjustments don't perturb buckets.
    """

    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def _ensure_bucket(self, actor_name: str, tier: str) -> _Bucket:
        """Get or create the bucket for ``actor_name``."""
        bucket = self._buckets.get(actor_name)
        capacity, refill_rate = _TIER_CONFIGS.get(tier, _TIER_CONFIGS["default"])
        if bucket is None:
            bucket = _Bucket(
                capacity=capacity,
                refill_rate=refill_rate,
                tokens=float(capacity),
                last_refill_unix=time.monotonic(),
            )
            self._buckets[actor_name] = bucket
        elif bucket.capacity != capacity or bucket.refill_rate != refill_rate:
            # Tier changed (e.g. admin promoted alice from default to
            # elevated); update the bucket's capacity + refill rate but
            # cap the current tokens at the new capacity.
            bucket.capacity = capacity
            bucket.refill_rate = refill_rate
            bucket.tokens = min(bucket.tokens, float(capacity))
        return bucket

    def _refill(self, bucket: _Bucket) -> None:
        """Apply elapsed-time refill, capped at capacity."""
        now = time.monotonic()
        elapsed = now - bucket.last_refill_unix
        if elapsed > 0:
            bucket.tokens = min(
                bucket.tokens + elapsed * bucket.refill_rate,
                float(bucket.capacity),
            )
            bucket.last_refill_unix = now

    def check_and_consume(
        self,
        actor_name: str,
        *,
        tier: str,
        cost: int,
    ) -> None:
        """Deduct ``cost`` tokens from ``actor_name``'s bucket.

        Raises :class:`RateLimitExceeded` when insufficient tokens.
        Cost ``0`` always succeeds (free endpoints) without touching
        the bucket.
        """
        if cost <= 0:
            return  # free endpoint; don't even refill.
        with self._lock:
            bucket = self._ensure_bucket(actor_name, tier)
            self._refill(bucket)
            if bucket.tokens >= cost:
                bucket.tokens -= cost
                return
            # Compute how long until the bucket refills enough for this
            # cost (assumes consistent refill rate).
            shortage = cost - bucket.tokens
            retry_after = shortage / bucket.refill_rate if bucket.refill_rate > 0 else float("inf")
            raise RateLimitExceeded(
                (
                    f"Actor {actor_name!r} rate limit exceeded "
                    f"(cost={cost}, tokens_remaining={bucket.tokens:.2f}, "
                    f"tier={tier!r}). Retry after {retry_after:.1f}s."
                ),
                actor_name=actor_name,
                cost=cost,
                tokens_remaining=bucket.tokens,
                retry_after_seconds=retry_after,
            )

    def reset(self, actor_name: str) -> None:
        """Refill ``actor_name``'s bucket to capacity. Phase 8 admin
        endpoint uses this for ops override."""
        with self._lock:
            bucket = self._buckets.get(actor_name)
            if bucket is not None:
                bucket.tokens = float(bucket.capacity)
                bucket.last_refill_unix = time.monotonic()

    def snapshot(self) -> list[dict[str, Any]]:
        """Return a per-actor bucket-state snapshot for the admin budget
        endpoint (Phase 8 Step 3).

        Each row carries:

        - ``actor_name`` (str)
        - ``capacity`` (int) -- bucket's total token capacity
        - ``tokens_remaining`` (float) -- current tokens (after refill
          to the snapshot moment)
        - ``refill_rate_per_second`` (float)
        - ``last_refill_unix`` (float) -- monotonic clock value, useful
          for relative age but not absolute wall-clock

        Snapshot is taken under the lock so concurrent
        ``check_and_consume`` calls don't see partial state. The
        snapshot is a copy; mutating the returned dicts does not
        affect the live buckets.
        """
        with self._lock:
            now = time.monotonic()
            snapshot: list[dict[str, Any]] = []
            for actor_name, bucket in self._buckets.items():
                # Refill projection (same math as :meth:`_refill`) so
                # the snapshot reflects "tokens RIGHT NOW", not "tokens
                # at last refill". We don't mutate the live bucket --
                # the next real check_and_consume will refill cleanly.
                elapsed = max(0.0, now - bucket.last_refill_unix)
                projected_tokens = min(
                    bucket.tokens + elapsed * bucket.refill_rate,
                    float(bucket.capacity),
                )
                snapshot.append(
                    {
                        "actor_name": actor_name,
                        "capacity": int(bucket.capacity),
                        "tokens_remaining": float(projected_tokens),
                        "refill_rate_per_second": float(bucket.refill_rate),
                        "last_refill_monotonic": float(bucket.last_refill_unix),
                    }
                )
            return snapshot

    def reset_all(self) -> None:
        """Reset all buckets. Phase 6a tests use this between cases."""
        with self._lock:
            self._buckets.clear()


def cost_for(action: str) -> int:
    """Look up the canonical cost for an action key.

    Unknown action keys return 1 token defensively (Phase 6a; Phase 8
    admin can override). The safety gate (Step 5) builds action keys
    from the route + tolerance.
    """
    return _COST_CATALOGUE.get(action, 1)


def cost_for_submit_at_rung(rung_name: str) -> int:
    """Convenience: map a RungDepth.name to its task-submit cost."""
    rung_to_action = {
        "R1_FLOOR": "tasks_submit_r1",
        "R2_CROSS_PRECISION": "tasks_submit_r2",
        "R3_TWO_AXES": "tasks_submit_r3",
        "R4_THREE_AXES": "tasks_submit_r4",
        "R5_REPLICATED": "tasks_submit_r5",
    }
    return cost_for(rung_to_action.get(rung_name, "tasks_submit_r3"))


# Module-level singleton.
_LIMITER: RateLimiter | None = None


def get_limiter() -> RateLimiter:
    """Lazy module-level :class:`RateLimiter` singleton."""
    global _LIMITER
    if _LIMITER is None:
        _LIMITER = RateLimiter()
    return _LIMITER
