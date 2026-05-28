"""Cognition-entry replay engine (Phase 13.x.3).

Per Phase 13 build guide §4.8 + 13-D2: a cognition ledger entry with
``prompt_disposition=VERBATIM`` can be replayed by re-invoking the
original cognition provider with the stored canonical prompt and
comparing the regenerated output against the stored result. For
deterministic providers (temperature=0, fixed seed where supported),
the comparison is bit-exact; for non-deterministic providers, the
replay flags ``non_deterministic_replay``.

This module is **separate from** :mod:`phoenix.ledger.replay_engine`
(physics-solve replay) because the two have different keying + flow:

- Physics ``replay(task_id)`` — one entry per solve, looked up by
  ``payload.task_id``; reconstructs ``PhysicsTask`` + restores
  environment + re-runs ``solve()`` pipeline.
- Cognition ``replay_cognition_entry(entry_id)`` — axis-level entries
  (multiple per task), looked up by ``entry_id``; reads
  ``cognition_provenance`` to identify the provider + model + sampling
  params; re-invokes the provider via injected factory; compares.

**Disposition dispatch (13-D2):**

- ``HASH_ONLY`` (default) — verify the stored ``prompt_hash`` is
  present + well-formed. Cannot regenerate output (the prompt body
  isn't stored). Report has ``regeneration_supported=False``.
- ``VERBATIM`` — reconstruct :class:`Prompt` from the stored canonical
  JSON, re-invoke the provider via the injected factory, compare
  against the stored result using a user-supplied comparison function.
- ``ENCRYPTED_OPT_IN`` — decrypt via the configured
  :class:`PromptEncryptor`; if no encryptor configured (Phase 13
  default), raises :class:`EncryptedDispositionNotConfigured`. Once
  the customer-key-management ceremony lands, this path mirrors
  ``VERBATIM``.

**Provider factory seam:**

This module does NOT import cognition adapter classes directly —
that would hard-code SDK dependencies + API-key handling into the
ledger module. Instead, the caller (admin endpoint, test) injects a
``CognitionProviderFactory`` callable that maps
``(provider_id, model) → CognitionProvider``. The default factory
raises :class:`CognitionReplayProviderUnavailable` for every input,
forcing the caller to inject a real one.

**SAFETY:** Replay reads the ledger; the read itself is permission-free
(admin endpoint above this module enforces ``is_admin``). The
**re-invocation** of the cognition provider via the factory is a
real provider call that consumes API quota + may incur cost; the
admin endpoint enforces the rate limit + cost ceiling separately.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from cognition_wobble.disagreement_types import CognitionDisagreementType

from phoenix.ledger.encryption import (
    EncryptedDispositionNotConfigured,
    get_prompt_encryptor,
)
from phoenix.ledger.prompt_disposition import (
    PromptDisposition,
    canonicalize_prompt,
    hash_canonical_form,
)

if TYPE_CHECKING:
    from phoenix.ledger.cognition_classifier import (
        ClassificationResult,
        CognitionClassifier,
    )
    from phoenix.ledger.entry_types import LedgerEntry
    from phoenix.providers.cognition.protocol import CognitionProvider
    from phoenix.providers.cognition.types import CognitionResult
    from phoenix.state.backend_protocol import StateBackend


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase 13.x.4 — verdict enum + mapping dict.


class CognitionReplayVerdict(Enum):
    """The four-level outcome of a cognition replay comparison.

    Per Phase 13.x.4 design spec:

    - :attr:`BIT_EXACT` — text + tool_calls byte-for-byte match; no
      classifier call needed (perf optimization).
    - :attr:`SEMANTIC_MATCH` — text differs but classifier confirms
      semantic equivalence (e.g., model-version update producing
      equivalent prose).
    - :attr:`DIVERGENCE` — classifier confident the outputs differ in
      substance (factual disagreement, refusal asymmetry, tool-choice
      divergence, etc.).
    - :attr:`UNCLASSIFIED` — classifier could not confidently
      categorize (or the classifier crashed; see
      :class:`CognitionReplayDivergence` reason prefix).
    """

    BIT_EXACT = "bit_exact"
    SEMANTIC_MATCH = "semantic_match"
    DIVERGENCE = "divergence"
    UNCLASSIFIED = "unclassified"


# Locked mapping from classifier output to verdict. Per Phase 13.x.4
# design spec §5.1: the six classified disagreement types collapse
# to SEMANTIC_MATCH or DIVERGENCE; UNCLASSIFIED stays as its own
# verdict (the calibrated escape hatch).
MAP_DISAGREEMENT_TYPE_TO_VERDICT: dict[CognitionDisagreementType, CognitionReplayVerdict] = {
    CognitionDisagreementType.FACTUAL_AGREEMENT: CognitionReplayVerdict.SEMANTIC_MATCH,
    CognitionDisagreementType.STYLISTIC_DIVERGENCE: CognitionReplayVerdict.SEMANTIC_MATCH,
    CognitionDisagreementType.FACTUAL_DISAGREEMENT: CognitionReplayVerdict.DIVERGENCE,
    CognitionDisagreementType.INTERPRETIVE_DIVERGENCE: CognitionReplayVerdict.DIVERGENCE,
    CognitionDisagreementType.REFUSAL_DIVERGENCE: CognitionReplayVerdict.DIVERGENCE,
    CognitionDisagreementType.TOOL_CHOICE_DIVERGENCE: CognitionReplayVerdict.DIVERGENCE,
    CognitionDisagreementType.UNCLASSIFIED: CognitionReplayVerdict.UNCLASSIFIED,
}


# ---------------------------------------------------------------------------
# Typed errors. Mirrors phoenix.ledger.replay_engine's pattern.


class CognitionReplayError(Exception):
    """Base class for cognition-replay-engine errors. Maps to HTTP 5xx
    at the admin endpoint."""


class CognitionEntryNotFound(CognitionReplayError):
    """No ledger entry exists with the requested ``entry_id``.

    Maps to HTTP 404."""


class CognitionEntryNotCognition(CognitionReplayError):
    """The found entry exists but its ``entry_kind`` is not
    ``"cognition"``.

    Maps to HTTP 400. Use :func:`phoenix.ledger.replay_engine.replay`
    for physics solves; this module is cognition-specific."""


class CognitionReplayEntryIncomplete(CognitionReplayError):
    """The entry lacks fields the cognition replay engine requires:
    ``prompt_disposition``, ``cognition_provenance``, or — for
    VERBATIM/ENCRYPTED_OPT_IN — the corresponding body column.

    Maps to HTTP 409."""


class CognitionReplayProviderUnavailable(CognitionReplayError):
    """The factory could not produce a provider for the entry's
    recorded ``(provider_id, model)``.

    Common causes: the optional SDK isn't installed in the replay
    environment; the API key for the original provider isn't
    configured; the provider has been retired since the original
    solve. Maps to HTTP 503."""

    def __init__(self, *, provider_id: str, model: str, reason: str) -> None:
        self.provider_id = provider_id
        self.model = model
        self.reason = reason
        super().__init__(
            f"Cannot construct cognition provider for replay "
            f"(provider_id={provider_id!r}, model={model!r}): {reason}"
        )


class CognitionReplayDivergence(CognitionReplayError):
    """The regenerated output diverged from the stored result.

    Carries enough context for the admin caller to surface in the
    audit log + post-incident review. The ``divergence_reason`` is
    free-form text from the user-supplied comparison function so it
    can carry the specific shape of the mismatch (text differs at
    byte N, tool-call count mismatch, etc.).

    Maps to HTTP 500."""

    def __init__(
        self,
        *,
        entry_id: str,
        prompt_disposition: str,
        divergence_reason: str,
    ) -> None:
        self.entry_id = entry_id
        self.prompt_disposition = prompt_disposition
        self.divergence_reason = divergence_reason
        super().__init__(
            f"Cognition replay diverged for entry_id={entry_id!r} "
            f"(disposition={prompt_disposition}): {divergence_reason}"
        )


# ---------------------------------------------------------------------------
# Provider-factory seam.


CognitionProviderFactory = Callable[[str, str], "CognitionProvider"]
"""Caller-injected factory: ``(provider_id, model) → CognitionProvider``.

The default :data:`_unavailable_factory` raises for every input. The
admin endpoint that exposes ``POST /v1/admin/replay/cognition/{entry_id}``
(Phase 13.x.3 follow-up) wires a real factory at request time, pulling
API keys from the environment per the Phase 13 SAFETY contract.
"""


def _unavailable_factory(provider_id: str, model: str) -> CognitionProvider:
    """Default factory: every call raises. Tests + production override."""
    raise CognitionReplayProviderUnavailable(
        provider_id=provider_id,
        model=model,
        reason=(
            "no CognitionProviderFactory configured; the replay caller "
            "must inject a factory that knows how to construct providers "
            "for the (provider_id, model) recorded in the entry."
        ),
    )


# ---------------------------------------------------------------------------
# Result-comparison seam. **Adam authors this** (see docstring).


@dataclass(frozen=True)
class ComparisonOutcome:
    """The result of comparing an original cognition entry to a replay.

    Fields (Phase 13.x.3 original):
        matches: True iff the replay matches the original per the
            comparison policy chosen by the caller.
        reason: Free-form description of WHY they match or differ.

    Fields (Phase 13.x.4 additions; optional for back-compat):
        verdict: The 4-level :class:`CognitionReplayVerdict` produced
            by the default comparator when a classifier is available.
            ``None`` for custom comparators that don't classify.
        classification: The full :class:`ClassificationResult` from
            the classifier, when one was called. ``None`` when the
            comparator returned BIT_EXACT (no classifier call) or
            when a custom comparator chose not to populate it.
    """

    matches: bool
    reason: str
    verdict: CognitionReplayVerdict | None = None
    classification: ClassificationResult | None = None


CognitionResultComparator = Callable[[dict[str, Any], "CognitionResult"], ComparisonOutcome]
"""Caller-injected comparator: compares stored entry payload to
fresh :class:`CognitionResult` from a replay invocation.

The original is passed as the raw JSON-parsed payload dict (rather
than a reconstructed :class:`CognitionResult`) because the entry
payload contains additional provenance fields that might inform the
comparison (e.g., the original ``provider_fingerprint`` for sanity-
checking the replay actually hit the same provider/model)."""


# ---------------------------------------------------------------------------
# Typed report.


@dataclass(frozen=True)
class CognitionReplayReport:
    """The result of a cognition-entry replay attempt.

    Fields:
        entry_id: The cognition entry that was replayed.
        prompt_disposition: The stored ``PromptDisposition`` value.
        hash_verified: True iff the stored ``prompt_hash`` is
            well-formed and (for VERBATIM/ENCRYPTED) matches the hash
            of the reconstructed canonical form.
        regeneration_supported: True iff the disposition allows
            re-invocation of the provider. False for HASH_ONLY.
        regeneration_attempted: True iff a provider call was actually
            made (False for HASH_ONLY, False for VERBATIM when no
            factory was registered, etc.).
        comparison_outcome: For attempts that ran, the comparison
            result. None for HASH_ONLY or when regeneration was
            skipped.
        wall_clock_ms: How long the replay took, informational.
    """

    entry_id: str
    prompt_disposition: str
    hash_verified: bool
    regeneration_supported: bool
    regeneration_attempted: bool
    comparison_outcome: ComparisonOutcome | None
    wall_clock_ms: float


# ---------------------------------------------------------------------------
# Internal helpers.


def _find_entry_by_id(
    entry_id: str,
    *,
    state_backend: StateBackend | None,
) -> LedgerEntry | None:
    """Linear-scan the ledger for an entry by ``entry_id``.

    Phase 13.x v1: linear scan, matching the physics replay engine's
    approach. v1.x adds an index on ``entry_id`` when chain depth
    justifies it.
    """
    from phoenix.ledger.entry_types import LedgerEntry
    from phoenix.state import get_state_backend

    backend = state_backend if state_backend is not None else get_state_backend()
    rows = backend.list_ledger_entries(since_unix=0.0, limit=1_000_000)
    for row in rows:
        if str(row.get("entry_id", "")) == entry_id:
            try:
                payload = json.loads(row["payload_json"])
            except (json.JSONDecodeError, TypeError) as exc:
                # Sourcery review (2026-05-28): an entry that exists but
                # has unparseable payload_json is a corruption case, NOT
                # a missing-entry case. Conflating them (by returning
                # None here and letting the caller raise
                # CognitionEntryNotFound) masks data corruption as
                # "doesn't exist." Raise the typed corruption error
                # instead so the admin endpoint can surface a clear
                # 409 "invalid payload_json" rather than a misleading 404.
                raise CognitionReplayEntryIncomplete(
                    f"entry_id={entry_id!r} payload_json is malformed "
                    f"(corruption or partial write): {exc}"
                ) from exc
            return LedgerEntry(
                entry_id=str(row["entry_id"]),
                entry_kind=str(row["entry_kind"]),
                timestamp_unix=float(row["timestamp_unix"]),
                actor_id=str(row["actor_id"]),
                parent_hash=str(row["parent_hash"]),
                entry_hash=str(row["entry_hash"]),
                payload=payload,
            )
    return None


def _reconstruct_prompt_from_canonical(canonical_json: str) -> Any:
    """Parse a canonical-form JSON string back into a :class:`Prompt`.

    The canonical form is ``{"system": ..., "messages": [...]}`` per
    :func:`phoenix.ledger.prompt_disposition.canonicalize_prompt`.
    Round-tripping should reproduce the original prompt shape modulo
    the deliberate normalizations (whitespace, key-order).
    """
    from phoenix.providers.cognition.types import Prompt

    parsed = json.loads(canonical_json)
    return Prompt(
        system=parsed.get("system"),
        messages=list(parsed.get("messages", [])),
    )


def _extract_sampling_params(
    *,
    entry_id: str,
    provenance: dict[str, Any],
) -> tuple[int, float]:
    """Parse ``max_tokens`` + ``temperature`` from a cognition entry's provenance.

    Sourcery review (2026-05-28): the previous direct
    ``int(provenance.get("max_tokens", 1024))`` raised
    :class:`TypeError`/:class:`ValueError` on malformed legacy values
    (e.g. ``"auto"``, ``None``), turning a data issue into an
    unstructured 5xx. This helper centralizes the defensive parsing so
    both VERBATIM + ENCRYPTED_OPT_IN handlers stay consistent and
    surface :class:`CognitionReplayEntryIncomplete` (HTTP 409) on
    malformed provenance rather than crashing.
    """
    raw_max_tokens = provenance.get("max_tokens", 1024)
    try:
        max_tokens = int(raw_max_tokens)
    except (TypeError, ValueError) as exc:
        raise CognitionReplayEntryIncomplete(
            f"entry_id={entry_id!r} cognition_provenance has invalid "
            f"max_tokens={raw_max_tokens!r}; expected an integer."
        ) from exc
    raw_temperature = provenance.get("temperature", 0.0)
    try:
        temperature = float(raw_temperature)
    except (TypeError, ValueError) as exc:
        raise CognitionReplayEntryIncomplete(
            f"entry_id={entry_id!r} cognition_provenance has invalid "
            f"temperature={raw_temperature!r}; expected a float."
        ) from exc
    return max_tokens, temperature


def _decode_encrypted_blob(
    *,
    entry_id: str,
    raw_blob: Any,
) -> bytes:
    """Normalize a stored ``prompt_encrypted`` value to raw ciphertext bytes.

    Codex review (P2, 2026-05-28): when an encrypted blob arrives via
    JSON-serialized payload (as opposed to a direct SQLite BLOB read),
    it's necessarily a string — and the Phoenix convention for binary
    in JSON is base64 (matches Phase 6a's keystore convention). The
    previous code did ``encrypted_blob.encode("utf-8")``, which gave
    the UTF-8 bytes of the *base64 string* rather than the ciphertext —
    decryption would fail under a real (non-mock) encryptor.

    Phase 13 convention (locked here):

    - Strings → base64-decoded
    - bytes/bytearray → used as-is

    Raises :class:`CognitionReplayEntryIncomplete` on base64-decode
    failure rather than crashing later inside the encryptor.
    """
    if isinstance(raw_blob, (bytes, bytearray)):
        return bytes(raw_blob)
    if not isinstance(raw_blob, str):
        raise CognitionReplayEntryIncomplete(
            f"entry_id={entry_id!r} prompt_encrypted is {type(raw_blob).__name__}; "
            f"expected bytes or base64-encoded string."
        )
    import base64 as _base64
    import binascii as _binascii

    try:
        return _base64.b64decode(raw_blob, validate=True)
    except (_binascii.Error, ValueError) as exc:
        raise CognitionReplayEntryIncomplete(
            f"entry_id={entry_id!r} prompt_encrypted is a string but not "
            f"valid base64 (Phase 13 convention for JSON-serialized "
            f"ciphertext): {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Result comparison — Phase 13.x.4 classifier-driven default comparator.
#
# This is the load-bearing design call for VERBATIM replay semantics.
# Phase 13.x.3 shipped a binary comparator; Phase 13.x.4 extends it
# to a 4-level verdict ladder (BIT_EXACT / SEMANTIC_MATCH /
# DIVERGENCE / UNCLASSIFIED) driven by a pluggable classifier.


def _compute_matches(
    verdict: CognitionReplayVerdict,
    temperature: float,
) -> bool:
    """Phase 13.x.4 raise-policy matrix (§5.2):

    +----------------+---------+---------+
    | verdict        | temp=0  | temp>0  |
    +================+=========+=========+
    | BIT_EXACT      | True    | True    |
    | SEMANTIC_MATCH | True    | True    |
    | DIVERGENCE     | False   | False   |
    | UNCLASSIFIED   | False   | True    |
    +----------------+---------+---------+
    """
    if verdict in (
        CognitionReplayVerdict.BIT_EXACT,
        CognitionReplayVerdict.SEMANTIC_MATCH,
    ):
        return True
    if verdict is CognitionReplayVerdict.DIVERGENCE:
        return False
    if verdict is CognitionReplayVerdict.UNCLASSIFIED:
        return temperature > 0.0
    # Unreachable per the 4-value enum; defense-in-depth.
    return False


def _build_unclassified_outcome(
    *,
    text_match: bool,
    tool_calls_match: bool,
    original_text: str,
    replayed_text: str,
    original_tool_calls_n: int,
    replayed_tool_calls_n: int,
    temperature: float,
    reason_prefix: str,
) -> ComparisonOutcome:
    """Build an UNCLASSIFIED outcome (classifier failure or invalid result).

    Preserves PR #20's reason substrings AND PREFIX so existing tests
    pass. Specifically:

    - When ``temperature > 0``, the reason starts with
      ``"non_deterministic_replay: temperature=..."`` (preserves PR #20's
      ``startswith("non_deterministic_replay")`` invariant).
    - When ``temperature == 0``, the reason starts with the supplied
      ``reason_prefix`` (e.g., ``"classifier_failure: ExcType(...)"``).
    - The ``reason_prefix`` is always present in the reason string (as
      a substring even when not leading), so ops can grep for
      ``"classifier_failure:"`` to find these cases regardless of temp.
    """
    parts: list[str] = []
    if temperature > 0.0:
        # PR #20 compat: non_deterministic_replay must lead when temp>0
        # for downstream `startswith("non_deterministic_replay")` checks.
        parts.append(f"non_deterministic_replay: temperature={temperature}")
        parts.append(reason_prefix)
    else:
        parts.append(reason_prefix)
    parts.append(f"verdict={CognitionReplayVerdict.UNCLASSIFIED.value}")
    if not text_match:
        parts.append(
            f"text differs (original_len={len(original_text)}, replayed_len={len(replayed_text)})"
        )
    if not tool_calls_match:
        parts.append(
            f"tool_calls differ (original_n={original_tool_calls_n}, "
            f"replayed_n={replayed_tool_calls_n})"
        )
    return ComparisonOutcome(
        matches=_compute_matches(CognitionReplayVerdict.UNCLASSIFIED, temperature),
        reason="; ".join(parts),
        verdict=CognitionReplayVerdict.UNCLASSIFIED,
        classification=None,
    )


def _payload_to_cognition_result(payload: dict[str, Any]) -> CognitionResult:
    """Reconstruct a CognitionResult from a cognition ledger payload.

    Used by the classifier-driven comparator path: classify() expects
    a list of responses; we feed it [original, replayed].
    """
    from phoenix.providers.cognition.types import CognitionResult, TokenUsage, ToolCall

    text = str(payload.get("result_text", ""))
    tc_raw = payload.get("result_tool_calls") or []
    tool_calls = [
        ToolCall(
            call_id=str(tc.get("call_id", "")),
            name=str(tc.get("name", "")),
            arguments=tc.get("arguments", {}),
        )
        for tc in tc_raw
    ]
    usage_raw = payload.get("result_usage") or {}
    usage = TokenUsage(
        input_tokens=int(usage_raw.get("input_tokens", 0)),
        output_tokens=int(usage_raw.get("output_tokens", 0)),
        cached_input_tokens=int(usage_raw.get("cached_input_tokens", 0)),
    )
    provenance = payload.get("cognition_provenance") or {}
    fingerprint = f"{provenance.get('provider_id', '')}|{provenance.get('model', '')}|replay"
    return CognitionResult(
        text=text,
        tool_calls=tool_calls,
        usage=usage,
        latency_ms=0.0,
        provider_fingerprint=fingerprint,
        prompt_cache_hit=None,
    )


def default_compare_cognition_results(
    original_payload: dict[str, Any],
    replayed: CognitionResult,
    *,
    classifier: CognitionClassifier | None = None,
) -> ComparisonOutcome:
    """Default comparator for VERBATIM-disposition cognition replays.

    **Policy (Phase 13.x.4 four-level verdict, locked 2026-05-28):**

    The 4-level :class:`CognitionReplayVerdict` ladder:

    - ``BIT_EXACT`` — text + tool_calls match byte-for-byte. No
      classifier call (perf optimization).
    - ``SEMANTIC_MATCH`` — text differs but classifier confirms
      equivalence (e.g., model version producing equivalent prose).
    - ``DIVERGENCE`` — classifier confident the outputs differ in
      substance.
    - ``UNCLASSIFIED`` — classifier could not confidently categorize
      OR the classifier itself raised (reason carries the prefix
      ``classifier_failure:`` in the latter case).

    The ``matches`` field follows §5.2's raise-policy matrix (see
    :func:`_compute_matches`).

    Args:
        original_payload: The cognition entry's parsed payload dict.
        replayed: The fresh :class:`CognitionResult` from re-invoking
            the provider.
        classifier: Optional :class:`CognitionClassifier` to use for
            the text-differs case. When ``None``, falls through to
            :func:`get_cognition_classifier` (which returns the ship
            default :class:`AlwaysUnclassifiedClassifier` until
            ``set_cognition_classifier`` is called).

    Returns:
        :class:`ComparisonOutcome` with ``matches``, ``reason``,
        ``verdict``, and ``classification`` populated.
    """
    from phoenix.ledger.cognition_classifier import get_cognition_classifier

    # Resolve effective classifier (kwarg overrides global).
    effective_classifier = classifier if classifier is not None else get_cognition_classifier()

    # Determine non-determinism from the recorded sampling params.
    provenance = original_payload.get("cognition_provenance") or {}
    try:
        temperature = float(provenance.get("temperature", 0.0))
    except (TypeError, ValueError):
        temperature = 0.0

    # Pull original artifacts. Missing keys → empty defaults so the
    # comparator never raises KeyError on partial entries.
    original_text = str(original_payload.get("result_text", ""))
    original_tool_calls_raw = original_payload.get("result_tool_calls") or []

    # Normalize the replayed tool_calls.
    replayed_tool_calls = [
        {
            "call_id": tc.call_id,
            "name": tc.name,
            "arguments": tc.arguments,
        }
        for tc in replayed.tool_calls
    ]

    text_match = replayed.text == original_text
    tool_calls_match = list(original_tool_calls_raw) == replayed_tool_calls

    # Bit-exact branch: early return, no classifier call.
    if text_match and tool_calls_match:
        return ComparisonOutcome(
            matches=True,
            reason=(
                f"bit_exact: text + tool_calls match "
                f"(temperature={temperature}; usage drift not compared)"
            ),
            verdict=CognitionReplayVerdict.BIT_EXACT,
            classification=None,
        )

    # Text-differs branch: invoke the classifier (with defensive try).
    # Reconstruct prompt for classifier from the canonical form in payload.
    try:
        canonical_json = str(original_payload.get("prompt_verbatim", "") or "{}")
        prompt = _reconstruct_prompt_from_canonical(canonical_json)
    except Exception:
        # If reconstruction fails, classifier still gets *some* prompt;
        # use an empty Prompt as fallback so classify() can run.
        from phoenix.providers.cognition.types import Prompt

        prompt = Prompt(system=None, messages=[])

    # Reconstruct original CognitionResult for the classifier.
    original_response = _payload_to_cognition_result(original_payload)

    # Classify (defense: catch any exception, fall back to UNCLASSIFIED).
    classification: ClassificationResult | None
    try:
        classification = effective_classifier.classify(
            prompt=prompt,
            responses=[original_response, replayed],
        )
        verdict = MAP_DISAGREEMENT_TYPE_TO_VERDICT.get(
            classification.disagreement_type,
            CognitionReplayVerdict.UNCLASSIFIED,  # Defense-in-depth.
        )
    except Exception as exc:
        log.warning(
            "default_compare_cognition_results: classifier.classify() raised",
            exc_info=True,
        )
        return _build_unclassified_outcome(
            text_match=text_match,
            tool_calls_match=tool_calls_match,
            original_text=original_text,
            replayed_text=replayed.text,
            original_tool_calls_n=len(list(original_tool_calls_raw)),
            replayed_tool_calls_n=len(replayed_tool_calls),
            temperature=temperature,
            reason_prefix=f"classifier_failure: {type(exc).__name__}({str(exc)[:80]})",
        )

    # Compute matches per §5.2 raise-policy matrix.
    matches = _compute_matches(verdict, temperature)

    # Build reason. PRESERVE existing PR #20 substring patterns:
    #   "non_deterministic_replay"    when temperature > 0 (kept as PREFIX
    #                                 for PR #20 startswith() check compat)
    #   "text differs (...)"          when text doesn't match
    #   "tool_calls differ (...)"     when tool_calls don't match
    #   "temperature=X.X"             when temperature > 0
    # plus add the 13.x.4 verdict + classifier info.
    reason_parts: list[str] = []
    # PR #20 compat: when temperature > 0, lead with "non_deterministic_replay:"
    # so the existing startswith() check still passes.
    if temperature > 0.0:
        reason_parts.append(f"non_deterministic_replay: temperature={temperature}")
    reason_parts.append(f"verdict={verdict.value}")
    if not text_match:
        reason_parts.append(
            f"text differs (original_len={len(original_text)}, replayed_len={len(replayed.text)})"
        )
    if not tool_calls_match:
        reason_parts.append(
            f"tool_calls differ (original_n={len(list(original_tool_calls_raw))}, "
            f"replayed_n={len(replayed_tool_calls)})"
        )
    reason_parts.append(
        f"classifier: version={classification.classifier_version} "
        f"confidence={classification.confidence:.2f}"
    )
    reason = "; ".join(reason_parts)

    return ComparisonOutcome(
        matches=matches,
        reason=reason,
        verdict=verdict,
        classification=classification,
    )


# ---------------------------------------------------------------------------
# Replay entry point.


def replay_cognition_entry(
    entry_id: str,
    *,
    provider_factory: CognitionProviderFactory | None = None,
    comparator: CognitionResultComparator | None = None,
    classifier: CognitionClassifier | None = None,  # NEW (Phase 13.x.4)
    state_backend: StateBackend | None = None,
) -> CognitionReplayReport:
    """Replay a single cognition ledger entry by ``entry_id``.

    Args:
        entry_id: The cognition entry's UUID4 (from
            ``ledger_entries.entry_id``).
        provider_factory: Caller-injected factory mapping
            ``(provider_id, model) → CognitionProvider``. When
            ``None``, the default :data:`_unavailable_factory` is
            used (all replay attempts raise
            :class:`CognitionReplayProviderUnavailable` for VERBATIM
            entries; HASH_ONLY entries still verify hash without
            needing a factory).
        comparator: Comparison function. When ``None``,
            :func:`default_compare_cognition_results` is used.
        state_backend: Optional explicit backend (tests).

    Raises:
        CognitionEntryNotFound: no entry with the given id.
        CognitionEntryNotCognition: entry exists but isn't a
            cognition entry.
        CognitionReplayEntryIncomplete: entry lacks required fields.
        CognitionReplayProviderUnavailable: factory couldn't construct
            a provider for the entry's recorded (provider_id, model).
        CognitionReplayDivergence: comparator returned matches=False.
        EncryptedDispositionNotConfigured: entry is ENCRYPTED_OPT_IN
            but no :class:`PromptEncryptor` is configured.
    """
    t_start = time.perf_counter()

    entry = _find_entry_by_id(entry_id, state_backend=state_backend)
    if entry is None:
        raise CognitionEntryNotFound(f"No ledger entry with entry_id={entry_id!r}.")

    if entry.entry_kind != "cognition":
        raise CognitionEntryNotCognition(
            f"entry_id={entry_id!r} has entry_kind={entry.entry_kind!r}; "
            f"use phoenix.ledger.replay_engine.replay() for physics solves."
        )

    payload = entry.payload
    disposition_raw = str(payload.get("prompt_disposition", ""))
    if not disposition_raw:
        raise CognitionReplayEntryIncomplete(
            f"entry_id={entry_id!r} lacks prompt_disposition; predates Phase 13 Step 8."
        )

    prompt_hash_stored = payload.get("prompt_hash")
    if not isinstance(prompt_hash_stored, str) or len(prompt_hash_stored) != 64:
        raise CognitionReplayEntryIncomplete(
            f"entry_id={entry_id!r} prompt_hash missing or malformed "
            f"(expected 64-char hex SHA-256)."
        )
    # Codex review (P1, 2026-05-28): length-only validation accepts non-hex
    # strings (e.g., "z" * 64), letting malformed hashes report
    # hash_verified=True. Enforce hex characters before treating as valid.
    try:
        int(prompt_hash_stored, 16)
    except ValueError as exc:
        raise CognitionReplayEntryIncomplete(
            f"entry_id={entry_id!r} prompt_hash is not valid hexadecimal "
            f"(got {prompt_hash_stored[:16]}...); expected 64-char hex SHA-256."
        ) from exc

    factory = provider_factory if provider_factory is not None else _unavailable_factory
    # Phase 13.x.4: wrap the comparator so the classifier kwarg flows through.
    # Custom comparators that don't take a classifier kwarg are still supported
    # because we only inject the kwarg into the default_compare_cognition_results
    # path; custom comparators receive only (original_payload, replayed).
    compare: CognitionResultComparator
    if comparator is None:
        from functools import partial

        compare = partial(default_compare_cognition_results, classifier=classifier)
    else:
        compare = comparator

    try:
        disposition = PromptDisposition(disposition_raw)
    except ValueError as exc:
        raise CognitionReplayEntryIncomplete(
            f"entry_id={entry_id!r} has unrecognized prompt_disposition={disposition_raw!r}."
        ) from exc

    # Branch on disposition.
    if disposition is PromptDisposition.HASH_ONLY:
        return _replay_hash_only(entry_id, prompt_hash_stored, t_start)

    if disposition is PromptDisposition.VERBATIM:
        return _replay_verbatim(
            entry_id=entry_id,
            payload=payload,
            prompt_hash_stored=prompt_hash_stored,
            factory=factory,
            compare=compare,
            t_start=t_start,
        )

    if disposition is PromptDisposition.ENCRYPTED_OPT_IN:
        return _replay_encrypted(
            entry_id=entry_id,
            payload=payload,
            prompt_hash_stored=prompt_hash_stored,
            factory=factory,
            compare=compare,
            t_start=t_start,
        )

    # Unreachable per the PromptDisposition enum's three values + the
    # parse above; defense-in-depth.
    raise CognitionReplayEntryIncomplete(f"Unhandled prompt_disposition={disposition_raw!r}.")


def _replay_hash_only(
    entry_id: str,
    prompt_hash_stored: str,
    t_start: float,
) -> CognitionReplayReport:
    """HASH_ONLY: the prompt body isn't stored. Verify the hash is
    well-formed; report that regeneration isn't supported."""
    wall_clock_ms = (time.perf_counter() - t_start) * 1000.0
    # The hash being a 64-char hex string is the only thing we can
    # verify without the prompt body. Real cryptographic verification
    # requires the body, which HASH_ONLY by design doesn't have.
    return CognitionReplayReport(
        entry_id=entry_id,
        prompt_disposition=PromptDisposition.HASH_ONLY.value,
        hash_verified=True,
        regeneration_supported=False,
        regeneration_attempted=False,
        comparison_outcome=None,
        wall_clock_ms=wall_clock_ms,
    )


def _replay_verbatim(
    *,
    entry_id: str,
    payload: dict[str, Any],
    prompt_hash_stored: str,
    factory: CognitionProviderFactory,
    compare: CognitionResultComparator,
    t_start: float,
) -> CognitionReplayReport:
    """VERBATIM: rehydrate canonical prompt, re-invoke provider, compare."""
    canonical = payload.get("prompt_verbatim")
    if not isinstance(canonical, str) or not canonical:
        raise CognitionReplayEntryIncomplete(
            f"entry_id={entry_id!r} disposition=VERBATIM but prompt_verbatim "
            f"is missing or not a string."
        )

    # Verify the stored canonical re-hashes to the stored prompt_hash.
    # If this fails the entry has been tampered with at rest.
    rehashed = hash_canonical_form(canonical)
    if rehashed != prompt_hash_stored:
        raise CognitionReplayDivergence(
            entry_id=entry_id,
            prompt_disposition=PromptDisposition.VERBATIM.value,
            divergence_reason=(
                f"stored prompt_verbatim re-hashes to {rehashed[:16]}... "
                f"but stored prompt_hash is {prompt_hash_stored[:16]}... — "
                f"entry has been tampered with at rest"
            ),
        )

    provenance = payload.get("cognition_provenance") or {}
    provider_id = str(provenance.get("provider_id", ""))
    model = str(provenance.get("model", ""))
    if not provider_id or not model:
        raise CognitionReplayEntryIncomplete(
            f"entry_id={entry_id!r} cognition_provenance lacks provider_id "
            f"or model (got provider_id={provider_id!r}, model={model!r})."
        )

    # Inject the factory; default raises CognitionReplayProviderUnavailable.
    provider = factory(provider_id, model)

    # Reconstruct the prompt + re-invoke.
    prompt = _reconstruct_prompt_from_canonical(canonical)
    # Use the recorded sampling params; these are what made the original
    # invocation reproducible. Defensive parsing per Sourcery review
    # (2026-05-28): malformed legacy values now raise
    # CognitionReplayEntryIncomplete (409) instead of crashing as 5xx.
    max_tokens, temperature = _extract_sampling_params(entry_id=entry_id, provenance=provenance)

    replayed = provider.complete(
        prompt,
        max_tokens=max_tokens,
        temperature=temperature,
    )

    outcome = compare(payload, replayed)
    wall_clock_ms = (time.perf_counter() - t_start) * 1000.0

    if not outcome.matches:
        raise CognitionReplayDivergence(
            entry_id=entry_id,
            prompt_disposition=PromptDisposition.VERBATIM.value,
            divergence_reason=outcome.reason,
        )

    return CognitionReplayReport(
        entry_id=entry_id,
        prompt_disposition=PromptDisposition.VERBATIM.value,
        hash_verified=True,
        regeneration_supported=True,
        regeneration_attempted=True,
        comparison_outcome=outcome,
        wall_clock_ms=wall_clock_ms,
    )


def _replay_encrypted(
    *,
    entry_id: str,
    payload: dict[str, Any],
    prompt_hash_stored: str,
    factory: CognitionProviderFactory,
    compare: CognitionResultComparator,
    t_start: float,
) -> CognitionReplayReport:
    """ENCRYPTED_OPT_IN: decrypt via PromptEncryptor, then mirror VERBATIM.

    Phase 13 default: :class:`NullPromptEncryptor` raises
    :class:`EncryptedDispositionNotConfigured`. Once the
    customer-key-management ceremony lands and a real encryptor is
    installed, this path proceeds identically to VERBATIM after
    decryption."""
    encrypted_blob = payload.get("prompt_encrypted")
    if encrypted_blob is None:
        raise CognitionReplayEntryIncomplete(
            f"entry_id={entry_id!r} disposition=ENCRYPTED_OPT_IN but prompt_encrypted is missing."
        )

    # Decrypt via the configured encryptor; raises
    # EncryptedDispositionNotConfigured for the Phase 13 default.
    # Normalize the stored blob to raw ciphertext bytes — string values
    # are base64 per Phase 13 convention (Codex review 2026-05-28).
    encryptor = get_prompt_encryptor()
    encrypted_bytes = _decode_encrypted_blob(entry_id=entry_id, raw_blob=encrypted_blob)

    canonical = encryptor.decrypt(encrypted_bytes)

    # From here, mirror VERBATIM's flow.
    rehashed = hash_canonical_form(canonical)
    if rehashed != prompt_hash_stored:
        raise CognitionReplayDivergence(
            entry_id=entry_id,
            prompt_disposition=PromptDisposition.ENCRYPTED_OPT_IN.value,
            divergence_reason=(
                f"decrypted prompt re-hashes to {rehashed[:16]}... but "
                f"stored prompt_hash is {prompt_hash_stored[:16]}..."
            ),
        )

    provenance = payload.get("cognition_provenance") or {}
    provider_id = str(provenance.get("provider_id", ""))
    model = str(provenance.get("model", ""))
    if not provider_id or not model:
        raise CognitionReplayEntryIncomplete(
            f"entry_id={entry_id!r} cognition_provenance lacks provider_id or model after decrypt."
        )

    provider = factory(provider_id, model)
    prompt = _reconstruct_prompt_from_canonical(canonical)
    # Defensive sampling-param parsing (Sourcery review 2026-05-28).
    max_tokens, temperature = _extract_sampling_params(entry_id=entry_id, provenance=provenance)

    replayed = provider.complete(
        prompt,
        max_tokens=max_tokens,
        temperature=temperature,
    )

    outcome = compare(payload, replayed)
    wall_clock_ms = (time.perf_counter() - t_start) * 1000.0

    if not outcome.matches:
        raise CognitionReplayDivergence(
            entry_id=entry_id,
            prompt_disposition=PromptDisposition.ENCRYPTED_OPT_IN.value,
            divergence_reason=outcome.reason,
        )

    return CognitionReplayReport(
        entry_id=entry_id,
        prompt_disposition=PromptDisposition.ENCRYPTED_OPT_IN.value,
        hash_verified=True,
        regeneration_supported=True,
        regeneration_attempted=True,
        comparison_outcome=outcome,
        wall_clock_ms=wall_clock_ms,
    )


# Re-export hooks for callers that want to reference these names from
# this module (e.g., import EncryptedDispositionNotConfigured from
# phoenix.ledger.cognition_replay in the admin endpoint without a
# second import line). Listed in __all__ so they're discoverable.
__all_reexports__ = (canonicalize_prompt, EncryptedDispositionNotConfigured)


__all__ = [
    "CognitionEntryNotCognition",
    "CognitionEntryNotFound",
    "CognitionReplayDivergence",
    "CognitionReplayEntryIncomplete",
    "CognitionReplayError",
    "CognitionReplayProviderUnavailable",
    "CognitionReplayReport",
    "CognitionReplayVerdict",
    "CognitionResultComparator",
    "CognitionProviderFactory",
    "ComparisonOutcome",
    "MAP_DISAGREEMENT_TYPE_TO_VERDICT",
    "default_compare_cognition_results",
    "replay_cognition_entry",
]
