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
from typing import TYPE_CHECKING, Any

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
    from phoenix.ledger.entry_types import LedgerEntry
    from phoenix.providers.cognition.protocol import CognitionProvider
    from phoenix.providers.cognition.types import CognitionResult
    from phoenix.state.backend_protocol import StateBackend


log = logging.getLogger(__name__)


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

    Fields:
        matches: True iff the replay matches the original per the
            comparison policy chosen by the caller.
        reason: Free-form description of WHY they match (when matches=
            True, typically "bit-exact match" or
            "match modulo usage drift") or why they differ. Surfaced
            to the admin audit log + the API response so the operator
            can understand what the comparison found.
    """

    matches: bool
    reason: str


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
            except (json.JSONDecodeError, TypeError):
                return None
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


# ---------------------------------------------------------------------------
# Result comparison — TO BE AUTHORED BY ADAM.
#
# See the docstring below for context + the three reasonable approaches.
# This function is the load-bearing design call for VERBATIM replay
# semantics; once it's pinned, the rest of the engine flows mechanically.


def default_compare_cognition_results(
    original_payload: dict[str, Any],
    replayed: CognitionResult,
) -> ComparisonOutcome:
    """Default comparator for VERBATIM-disposition cognition replays.

    **Policy (Phase 13.x.3 binary version, locked 2026-05-21):**

    - Compare ``text`` byte-for-byte AND ``tool_calls`` (call_id +
      name + arguments dict) byte-for-byte.
    - **Ignore ``usage``** because token counts can drift slightly
      across provider API versions / tokenizer updates without any
      semantic change to the output. The integrity story for usage
      lives in the per-entry hashchain, not in replay verification.
    - For **non-deterministic** invocations (``temperature > 0`` per
      the recorded ``cognition_provenance``), return ``matches=True``
      with reason prefixed ``"non_deterministic_replay:"`` per
      Phase 13 build guide §4.8. The new output is preserved
      alongside the original for forensic review; we do NOT raise
      :class:`CognitionReplayDivergence` because text divergence is
      *expected* when ``temperature > 0``.

    **[v1.1.x TODO]** The richer 3-level verdict
    (``bit_exact`` / ``semantic_match`` / ``divergence``) that
    integrates with :class:`cognition_wobble.classifier.CognitionClassifier`
    is tracked as Phase 13.x.4. The classifier-backed
    "semantic_match" verdict will catch provider-side benign drift
    (e.g., model version update producing equivalent prose) WITHOUT
    flagging it as divergence. The 13.x.4 upgrade is additive: the
    callsite signature for this function does not change; the
    returned :class:`ComparisonOutcome` gains optional fields.

    Args:
        original_payload: The cognition entry's parsed payload dict.
            Reads ``result_text``, ``result_tool_calls``, and
            ``cognition_provenance.temperature``. Missing keys default
            to empty/zero.
        replayed: The fresh :class:`CognitionResult` from re-invoking
            the provider.

    Returns:
        :class:`ComparisonOutcome` with ``matches`` and a human-
        readable ``reason`` suitable for the admin audit log.
    """
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

    # Normalize the replayed CognitionResult.tool_calls to the same
    # dict shape used in the payload (matches what
    # cognition_wobble.eval.serialize_tool_calls writes).
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

    # Non-deterministic case: text drift is expected; do not raise.
    if temperature > 0.0:
        return ComparisonOutcome(
            matches=True,
            reason=(
                f"non_deterministic_replay: temperature={temperature} "
                f"(text_match={text_match}, tool_calls_match={tool_calls_match}); "
                f"new output preserved for forensic review per build guide §4.8"
            ),
        )

    # Deterministic case (temperature == 0): demand bit-exact text +
    # tool_calls. Usage is informational only.
    if text_match and tool_calls_match:
        return ComparisonOutcome(
            matches=True,
            reason=(
                f"bit_exact: text + tool_calls match "
                f"(temperature={temperature}; usage drift not compared)"
            ),
        )

    # Real deterministic divergence — build a precise reason.
    diffs: list[str] = []
    if not text_match:
        diffs.append(
            f"text differs (original_len={len(original_text)}, replayed_len={len(replayed.text)})"
        )
    if not tool_calls_match:
        diffs.append(
            f"tool_calls differ (original_n={len(list(original_tool_calls_raw))}, "
            f"replayed_n={len(replayed_tool_calls)})"
        )
    return ComparisonOutcome(
        matches=False,
        reason=f"deterministic replay divergence: {'; '.join(diffs)}",
    )


# ---------------------------------------------------------------------------
# Replay entry point.


def replay_cognition_entry(
    entry_id: str,
    *,
    provider_factory: CognitionProviderFactory | None = None,
    comparator: CognitionResultComparator | None = None,
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

    factory = provider_factory if provider_factory is not None else _unavailable_factory
    compare = comparator if comparator is not None else default_compare_cognition_results

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
    # invocation reproducible.
    max_tokens = int(provenance.get("max_tokens", 1024))
    temperature = float(provenance.get("temperature", 0.0))

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
    encryptor = get_prompt_encryptor()
    if isinstance(encrypted_blob, str):
        # SQLite returns BLOB as bytes; JSON encoding may have turned
        # it into a string (e.g., base64). The encryptor seam takes
        # bytes; tests typically pass bytes directly.
        encrypted_bytes = encrypted_blob.encode("utf-8")
    else:
        encrypted_bytes = bytes(encrypted_blob)

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
    max_tokens = int(provenance.get("max_tokens", 1024))
    temperature = float(provenance.get("temperature", 0.0))

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
    "CognitionResultComparator",
    "CognitionProviderFactory",
    "ComparisonOutcome",
    "default_compare_cognition_results",
    "replay_cognition_entry",
]
