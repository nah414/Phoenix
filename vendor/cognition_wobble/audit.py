"""Corpus class-balance audit (shared by the CLI console + the UI endpoint).

The audit answers "is this corpus ready to train?" — per-class counts against the
~28-per-graded-class floor, exact-duplicate detection, and a ready/not-ready
verdict. Both ``phoenix cognition audit`` (the CLI) and ``POST /v1/cognition/audit``
(the mobile control panel) call :func:`audit_corpus`, so the logic lives here in
one shippable place rather than inside a script.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cognition_wobble.corpus import count_by_class
from cognition_wobble.disagreement_types import GRADED_CLASSES, CognitionDisagreementType

if TYPE_CHECKING:
    from cognition_wobble.eval import CalibrationExample

__all__ = ["DEFAULT_MIN_PER_GRADED_CLASS", "MIN_GRADED_TOTAL", "audit_corpus"]

DEFAULT_MIN_PER_GRADED_CLASS = 28
MIN_GRADED_TOTAL = 200


def audit_corpus(
    examples: list[CalibrationExample],
    *,
    min_per_class: int = DEFAULT_MIN_PER_GRADED_CLASS,
) -> dict[str, Any]:
    """Return a structured class-balance report for ``examples``.

    Keys: ``graded_total``, ``floor``, ``per_class`` (all six graded classes),
    ``unclassified``, ``under_floor`` (class values below the floor),
    ``duplicates`` (exact-duplicate row count), and ``ready`` (no under-floor
    class and ``graded_total >= 200``).
    """
    counts = count_by_class(examples)
    graded_total = sum(counts[c] for c in GRADED_CLASSES)
    under = [c.value for c in GRADED_CLASSES if counts[c] < min_per_class]

    seen: dict[tuple[str, str, str], int] = {}
    for ex in examples:
        key = (
            "".join(str(m.get("content", "")) for m in ex.prompt.messages),
            ex.response_a.text,
            ex.response_b.text,
        )
        seen[key] = seen.get(key, 0) + 1
    duplicates = sum(v - 1 for v in seen.values() if v > 1)

    return {
        "graded_total": graded_total,
        "floor": min_per_class,
        "per_class": {c.value: counts[c] for c in GRADED_CLASSES},
        "unclassified": counts[CognitionDisagreementType.UNCLASSIFIED],
        "under_floor": under,
        "duplicates": duplicates,
        "ready": not under and graded_total >= MIN_GRADED_TOTAL,
    }
