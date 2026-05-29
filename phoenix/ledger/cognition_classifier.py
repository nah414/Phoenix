"""Cognition classifier registry + ship default (Phase 13.x.4).

Per Phase 13.x.4 design spec (2026-05-28): the cognition classifier is
pluggable via a global registry that mirrors
:mod:`phoenix.ledger.encryption`'s pattern. The ship default is
:class:`AlwaysUnclassifiedClassifier` from
:mod:`cognition_wobble.classifier`; ops swap in a real classifier
(typically Phase 13 Step 5b's hybrid GBM+LLM-judge) at daemon
startup via :func:`set_cognition_classifier`.

The registry is **read-dominated**: ops call
:func:`set_cognition_classifier` once at daemon startup; every replay
read calls :func:`get_cognition_classifier`. The module-global write
is atomic under the GIL (single reference assignment); the pattern
does not need explicit locking.

Test-isolation: :func:`reset_cognition_classifier` restores the ship
default. The :mod:`tests.cognition.test_cognition_classifier_registry`
autouse fixture pattern is the recommended way to keep tests isolated.
"""

from __future__ import annotations

# Re-exports from vendor so callers import from one place.
from cognition_wobble.classifier import (
    AlwaysUnclassifiedClassifier,
    ClassificationResult,
    CognitionClassifier,
    DEFAULT_CONFIDENCE_THRESHOLD,
)

__all__ = [
    "AlwaysUnclassifiedClassifier",
    "ClassificationResult",
    "CognitionClassifier",
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "get_cognition_classifier",
    "reset_cognition_classifier",
    "set_cognition_classifier",
]


# Module-level singleton. Read-dominated; reference write is GIL-atomic.
_classifier: CognitionClassifier = AlwaysUnclassifiedClassifier()


def get_cognition_classifier() -> CognitionClassifier:
    """Return the currently registered classifier.

    Ships with :class:`AlwaysUnclassifiedClassifier`. Daemon startup
    swaps in a real classifier via :func:`set_cognition_classifier`.
    """
    return _classifier


def set_cognition_classifier(c: CognitionClassifier) -> None:
    """Register ``c`` as the active classifier.

    Typically called once at daemon startup with a real classifier
    impl. Reference assignment is atomic under the GIL; no lock
    needed.
    """
    global _classifier
    _classifier = c


def reset_cognition_classifier() -> None:
    """Restore the ship default :class:`AlwaysUnclassifiedClassifier`.

    Test helper. Production should not call this; daemon shutdown
    discards the module anyway.
    """
    global _classifier
    _classifier = AlwaysUnclassifiedClassifier()
