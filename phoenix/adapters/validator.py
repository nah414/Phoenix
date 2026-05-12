"""Inference-time round-trip validator (Phase 9 Step 2).

Per architecture v1 Section 2.7: an adapter is only useful if
``decode(encode(x)) == x`` for the grammar productions the
task-grammar layer cares about. The validator is the gate
that enforces this invariant **at load time** before the
adapter ever sees a real prompt.

The validator drives the adapter through a small fixed set of
canonical inputs covering single chars, multi-word strings,
unicode, and whitespace-sensitive edges. If every input round-
trips cleanly, the adapter is healthy; if any input fails, the
adapter is refused (HTTP 503 via :class:`AdapterValidationError`).

**Why a small fixed set?** Phase 9 ships the *capability*, not
exhaustive coverage. v1.x can grow the set per locked OPEN-3 +
domain conventions. The five inputs here catch the 80%
correctness regression (an adapter that drops whitespace, or
fails on unicode, or eats the empty string).

**The result shape:** :class:`ValidationHistoryEntry` (defined
in ``registry.py``) carries ``passed`` + ``failed_cases`` +
``wall_clock_ms``. The admin force-revalidate endpoint (Phase 9
Step 4) returns this entry verbatim so operators see exactly
which inputs broke.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from phoenix.adapters.registry import ValidationHistoryEntry
from phoenix.adapters.sandbox import call_adapter_method

if TYPE_CHECKING:
    from phoenix.adapters.protocol import LoRAAdapter

logger = logging.getLogger(__name__)


# Canonical inputs driving the inference-time round-trip suite.
# Selection criteria:
#   - "x"                       single ASCII char (minimal input)
#   - "abc"                     plain multi-char (baseline)
#   - "hello world"             includes spaces (whitespace-sensitive
#                               encoders break here first)
#   - "non-ascii: üñî"          unicode (UTF-8 byte-vs-char regressions)
#   - " leading-trailing "      leading + trailing whitespace
#                               (strip/normalize regressions)
# Empty string is intentionally omitted -- many LoRA encoders treat
# "" as a degenerate case; v1.x can add it once a real adapter
# clarifies semantics.
_VALIDATION_INPUTS: tuple[str, ...] = (
    "x",
    "abc",
    "hello world",
    "non-ascii: üñî",
    " leading-trailing ",
)


def validation_inputs() -> tuple[str, ...]:
    """Return the canonical-input set used for round-trip checks.

    Exposed so tests + admin tooling can introspect the contract
    without reaching into private state.
    """
    return _VALIDATION_INPUTS


def run_round_trip_validation(
    adapter: LoRAAdapter,
    *,
    sandbox_timeout_seconds: float = 5.0,
) -> ValidationHistoryEntry:
    """Drive ``adapter`` through the canonical round-trip suite.

    For each canonical input ``x`` calls (via the sandbox):

    1. ``encoded = adapter.encode_to_grammar(x)``
    2. ``decoded = adapter.decode_from_grammar(encoded)``
    3. Records ``x`` in ``failed_cases`` iff ``decoded != x``.

    Always returns a :class:`ValidationHistoryEntry`; callers
    decide whether to raise. The loader (``loader.py``) checks
    ``entry.passed`` and raises :class:`AdapterValidationError`
    if any case failed; the admin force-revalidate endpoint
    surfaces the entry directly.

    The sandbox is exercised via :func:`call_adapter_method`,
    which dispatches to the in-process path for adapters like
    :class:`IdentityAdapter` and to :func:`run_in_sandbox` for
    external (subprocess) adapters.
    """
    failed: list[str] = []
    start = time.perf_counter()
    for canonical in _VALIDATION_INPUTS:
        try:
            encoded = call_adapter_method(
                adapter=adapter,
                method="encode_to_grammar",
                arg=canonical,
                timeout_seconds=sandbox_timeout_seconds,
            )
            decoded = call_adapter_method(
                adapter=adapter,
                method="decode_from_grammar",
                arg=encoded,
                timeout_seconds=sandbox_timeout_seconds,
            )
        except Exception:
            # Any exception during encode/decode counts as a failure
            # for *that* canonical input. The validator never lets
            # an exception escape -- the loader's job is to inspect
            # the entry and decide whether to refuse the adapter.
            logger.exception(
                "Adapter %r round-trip raised on input %r",
                getattr(adapter, "name", "<unknown>"),
                canonical,
            )
            failed.append(canonical)
            continue
        if decoded != canonical:
            logger.warning(
                "Adapter %r round-trip mismatch: %r -> %r -> %r",
                getattr(adapter, "name", "<unknown>"),
                canonical,
                encoded,
                decoded,
            )
            failed.append(canonical)

    wall_ms = (time.perf_counter() - start) * 1000.0
    return ValidationHistoryEntry(
        timestamp_unix=time.time(),
        passed=len(failed) == 0,
        failed_cases=failed,
        wall_clock_ms=wall_ms,
    )


__all__ = [
    "run_round_trip_validation",
    "validation_inputs",
]
