"""Tests for ``phoenix.ledger.cognition_replay`` (Phase 13.x.3).

Coverage:

- HASH_ONLY: hash-format validation only; regeneration not supported.
- VERBATIM happy path: bit-exact text + tool_calls → matches=True.
- VERBATIM divergence: text differs (deterministic) → raises.
- VERBATIM non-deterministic: temperature > 0 → matches=True with
  reason prefixed "non_deterministic_replay".
- VERBATIM tamper detection: stored prompt_verbatim re-hashes to a
  different value than stored prompt_hash → raises divergence.
- VERBATIM missing provenance → raises ReplayEntryIncomplete.
- VERBATIM no provider factory → raises ReplayProviderUnavailable
  (default factory is the "unavailable" sentinel).
- ENCRYPTED_OPT_IN with NullPromptEncryptor → raises
  EncryptedDispositionNotConfigured.
- Entry-not-found / wrong-entry-kind error paths.
- Comparator unit tests: bit-exact match, text divergence, tool-call
  divergence, non-deterministic flag, usage drift ignored.

**GPU SAFETY:** This test file imports only pure-dataclass + JSON
primitives. No ML imports, no embeddings, no provider SDK calls.
The injected ``provider_factory`` returns mock providers whose
``.complete()`` returns hard-coded :class:`CognitionResult` values.
Safe to run while the local GPU is busy.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from phoenix.ledger.cognition_replay import (
    CognitionEntryNotCognition,
    CognitionEntryNotFound,
    CognitionProviderFactory,
    CognitionReplayDivergence,
    CognitionReplayEntryIncomplete,
    CognitionReplayProviderUnavailable,
    CognitionReplayReport,
    ComparisonOutcome,
    default_compare_cognition_results,
    replay_cognition_entry,
)
from phoenix.ledger.cognition_replay import CognitionReplayVerdict  # noqa: F401 -- used in 13.x.4 tests
from cognition_wobble.disagreement_types import CognitionDisagreementType
from phoenix.ledger.cognition_classifier import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    ClassificationResult,
)
from phoenix.ledger.encryption import EncryptedDispositionNotConfigured
from phoenix.ledger.prompt_disposition import (
    canonicalize_prompt,
    hash_canonical_form,
    hash_prompt,
)
from phoenix.providers.cognition.protocol import CognitionProvider
from phoenix.providers.cognition.types import (
    CognitionResult,
    Prompt,
    TokenUsage,
    ToolCall,
)
from phoenix.state.backend_protocol import StateBackend


# ---------------------------------------------------------------------------
# Test helpers: fake StateBackend + fake CognitionProvider


class _FakeBackend:
    """Minimal StateBackend stub exposing only list_ledger_entries."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def list_ledger_entries(self, *, since_unix: float, limit: int) -> list[dict[str, Any]]:
        del since_unix, limit
        return list(self._rows)


class _FakeProvider:
    """Mock CognitionProvider; .complete() returns a hard-coded result."""

    def __init__(
        self,
        *,
        provider_id: str,
        model: str,
        canned_result: CognitionResult,
    ) -> None:
        self.provider_id = provider_id
        self.model = model
        self._canned = canned_result

    def complete(
        self,
        prompt: Prompt,
        *,
        max_tokens: int,
        temperature: float,
        tools: Any = None,
        stream: bool = False,
    ) -> CognitionResult:
        del prompt, max_tokens, temperature, tools, stream
        return self._canned


def _make_factory(canned: CognitionResult) -> CognitionProviderFactory:
    """Build a provider_factory that returns the same canned result
    regardless of (provider_id, model)."""

    def _factory(provider_id: str, model: str) -> CognitionProvider:
        # cast: _FakeProvider structurally satisfies CognitionProvider
        # (provider_id, model, complete, capabilities, fingerprint).
        # capabilities/fingerprint aren't called by the replay engine,
        # so we don't bother stubbing them.
        return cast(
            CognitionProvider,
            _FakeProvider(
                provider_id=provider_id,
                model=model,
                canned_result=canned,
            ),
        )

    return _factory


def _as_backend(fake: _FakeBackend) -> StateBackend:
    """Cast a _FakeBackend to StateBackend for the replay engine.

    The fake only implements ``list_ledger_entries``, which is all
    :func:`replay_cognition_entry` actually calls. The cast documents
    "this fake satisfies the methods the code-under-test exercises";
    mypy would otherwise reject because Protocol structural matching
    rejects partial implementations.
    """
    return cast(StateBackend, fake)


def _build_row(
    *,
    entry_id: str,
    entry_kind: str = "cognition",
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Build a backend-shaped row dict."""
    return {
        "entry_id": entry_id,
        "entry_kind": entry_kind,
        "timestamp_unix": 1717000000.0,
        "actor_id": "ash",
        "parent_hash": "GENESIS",
        "entry_hash": "0" * 64,
        "payload_json": json.dumps(payload),
    }


def _build_verbatim_payload(
    *,
    prompt: Prompt,
    result_text: str = "hello world",
    result_tool_calls: list[dict[str, Any]] | None = None,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """Build a VERBATIM-disposition cognition payload."""
    canonical = canonicalize_prompt(prompt)
    return {
        "axis": "cross_model",
        "prompt_disposition": "VERBATIM",
        "prompt_hash": hash_canonical_form(canonical),
        "prompt_verbatim": canonical,
        "cognition_provenance": {
            "provider_id": "anthropic",
            "model": "claude-sonnet-4-7-20260418",
            "temperature": temperature,
            "max_tokens": 100,
        },
        "result_text": result_text,
        "result_tool_calls": result_tool_calls or [],
        "result_usage": {
            "input_tokens": 10,
            "output_tokens": 20,
            "cached_input_tokens": 0,
        },
    }


# ---------------------------------------------------------------------------
# Phase 13.x.4 test helpers: stub classifiers.


class _StubClassifier:
    """Classifier stub: returns a fixed CognitionDisagreementType every time.

    Used by TestVerdictMapping + TestRaisePolicyMatrix to exercise
    the comparator's classifier-driven path without invoking the
    real GBM/LLM-judge classifier.
    """

    version: str = "stub-fixed-v1"

    def __init__(
        self,
        *,
        returns: CognitionDisagreementType,
        confidence: float = 0.95,
    ) -> None:
        self._returns = returns
        self._confidence = confidence

    def classify(
        self,
        prompt: Prompt,
        responses: list[CognitionResult],
        *,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> ClassificationResult:
        del prompt, responses, confidence_threshold
        return ClassificationResult(
            disagreement_type=self._returns,
            confidence=self._confidence,
            classifier_version=self.version,
            feature_values={},
            escalated_to_judge=False,
        )


class _FailingClassifier:
    """Classifier stub that raises on classify(). For error-handling tests."""

    version: str = "stub-failing-v1"

    def __init__(self, *, exception: Exception) -> None:
        self._exception = exception

    def classify(
        self,
        prompt: Prompt,
        responses: list[CognitionResult],
        *,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> ClassificationResult:
        del prompt, responses, confidence_threshold
        raise self._exception


# ---------------------------------------------------------------------------
# Comparator unit tests (no replay engine, just the function)


class TestDefaultComparator:
    def test_bit_exact_text_and_tool_calls_matches(self) -> None:
        payload = {
            "cognition_provenance": {"temperature": 0.0},
            "result_text": "answer",
            "result_tool_calls": [],
        }
        replayed = CognitionResult(
            text="answer",
            tool_calls=[],
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            latency_ms=0.0,
            provider_fingerprint="x",
        )
        outcome = default_compare_cognition_results(payload, replayed)
        assert outcome.matches is True
        assert outcome.reason.startswith("bit_exact")

    def test_text_divergence_deterministic(self) -> None:
        payload = {
            "cognition_provenance": {"temperature": 0.0},
            "result_text": "answer A",
            "result_tool_calls": [],
        }
        replayed = CognitionResult(
            text="answer B",
            tool_calls=[],
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            latency_ms=0.0,
            provider_fingerprint="x",
        )
        outcome = default_compare_cognition_results(payload, replayed)
        assert outcome.matches is False
        assert "text differs" in outcome.reason

    def test_tool_call_divergence_deterministic(self) -> None:
        payload = {
            "cognition_provenance": {"temperature": 0.0},
            "result_text": "same",
            "result_tool_calls": [{"call_id": "c1", "name": "search", "arguments": {"q": "a"}}],
        }
        replayed = CognitionResult(
            text="same",
            tool_calls=[ToolCall(call_id="c1", name="search", arguments={"q": "b"})],
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            latency_ms=0.0,
            provider_fingerprint="x",
        )
        outcome = default_compare_cognition_results(payload, replayed)
        assert outcome.matches is False
        assert "tool_calls differ" in outcome.reason

    def test_non_deterministic_flagged_not_raised(self) -> None:
        """temperature>0 + diverging text → matches=True with the flag."""
        payload = {
            "cognition_provenance": {"temperature": 0.7},
            "result_text": "original answer",
            "result_tool_calls": [],
        }
        replayed = CognitionResult(
            text="different answer (expected under temperature=0.7)",
            tool_calls=[],
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            latency_ms=0.0,
            provider_fingerprint="x",
        )
        outcome = default_compare_cognition_results(payload, replayed)
        assert outcome.matches is True
        assert outcome.reason.startswith("non_deterministic_replay")
        assert "temperature=0.7" in outcome.reason

    def test_usage_drift_ignored_in_deterministic_case(self) -> None:
        """Usage counts differ — comparator still says match."""
        payload = {
            "cognition_provenance": {"temperature": 0.0},
            "result_text": "answer",
            "result_tool_calls": [],
            "result_usage": {"input_tokens": 100, "output_tokens": 50},
        }
        replayed = CognitionResult(
            text="answer",
            tool_calls=[],
            usage=TokenUsage(input_tokens=99, output_tokens=51),  # drift
            latency_ms=0.0,
            provider_fingerprint="x",
        )
        outcome = default_compare_cognition_results(payload, replayed)
        assert outcome.matches is True

    def test_missing_temperature_defaults_deterministic(self) -> None:
        """Missing temperature → assume 0.0 (deterministic path)."""
        payload = {
            "cognition_provenance": {},
            "result_text": "answer",
            "result_tool_calls": [],
        }
        replayed = CognitionResult(
            text="answer",
            tool_calls=[],
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            latency_ms=0.0,
            provider_fingerprint="x",
        )
        outcome = default_compare_cognition_results(payload, replayed)
        assert outcome.matches is True
        assert "bit_exact" in outcome.reason

    def test_bit_exact_populates_verdict_bit_exact(self) -> None:
        """Phase 13.x.4: bit-exact comparison sets verdict=BIT_EXACT."""
        payload = {
            "cognition_provenance": {"temperature": 0.0},
            "result_text": "answer",
            "result_tool_calls": [],
        }
        replayed = CognitionResult(
            text="answer",
            tool_calls=[],
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            latency_ms=0.0,
            provider_fingerprint="x",
        )
        outcome = default_compare_cognition_results(payload, replayed)
        assert outcome.matches is True
        assert outcome.verdict == CognitionReplayVerdict.BIT_EXACT
        assert outcome.classification is None  # No classifier call on bit-exact.


# ---------------------------------------------------------------------------
# replay_cognition_entry — error paths


class TestReplayErrorPaths:
    def test_entry_not_found(self) -> None:
        backend = _FakeBackend(rows=[])
        with pytest.raises(CognitionEntryNotFound):
            replay_cognition_entry("nope", state_backend=_as_backend(backend))

    def test_wrong_entry_kind(self) -> None:
        backend = _FakeBackend(
            rows=[
                _build_row(
                    entry_id="solve-1",
                    entry_kind="solve",
                    payload={"task_id": "t1"},
                )
            ]
        )
        with pytest.raises(CognitionEntryNotCognition):
            replay_cognition_entry("solve-1", state_backend=_as_backend(backend))

    def test_missing_prompt_disposition(self) -> None:
        backend = _FakeBackend(rows=[_build_row(entry_id="c1", payload={"axis": "cross_model"})])
        with pytest.raises(CognitionReplayEntryIncomplete, match="prompt_disposition"):
            replay_cognition_entry("c1", state_backend=_as_backend(backend))

    def test_missing_prompt_hash(self) -> None:
        backend = _FakeBackend(
            rows=[
                _build_row(
                    entry_id="c1",
                    payload={
                        "prompt_disposition": "HASH_ONLY",
                        # no prompt_hash
                    },
                )
            ]
        )
        with pytest.raises(CognitionReplayEntryIncomplete, match="prompt_hash"):
            replay_cognition_entry("c1", state_backend=_as_backend(backend))

    def test_unrecognized_disposition(self) -> None:
        backend = _FakeBackend(
            rows=[
                _build_row(
                    entry_id="c1",
                    payload={
                        "prompt_disposition": "PLAINTEXT_FOREVER",
                        "prompt_hash": "a" * 64,
                    },
                )
            ]
        )
        with pytest.raises(CognitionReplayEntryIncomplete, match="unrecognized"):
            replay_cognition_entry("c1", state_backend=_as_backend(backend))

    def test_non_hex_prompt_hash_raises(self) -> None:
        """Codex review P1 (2026-05-28): length-only validation accepted
        non-hex strings like 'z' * 64. Now enforces hexadecimal."""
        backend = _FakeBackend(
            rows=[
                _build_row(
                    entry_id="c1",
                    payload={
                        "prompt_disposition": "HASH_ONLY",
                        "prompt_hash": "z" * 64,  # 64 chars, but not hex
                    },
                )
            ]
        )
        with pytest.raises(CognitionReplayEntryIncomplete, match="not valid hexadecimal"):
            replay_cognition_entry("c1", state_backend=_as_backend(backend))

    def test_corrupted_payload_json_raises_incomplete(self) -> None:
        """Sourcery review (2026-05-28): payload_json corruption used to
        return None (interpreted as EntryNotFound); now raises
        CognitionReplayEntryIncomplete (HTTP 409) to distinguish
        'entry doesn't exist' from 'entry exists but corrupted'."""
        rows = [
            {
                "entry_id": "c1",
                "entry_kind": "cognition",
                "timestamp_unix": 1717000000.0,
                "actor_id": "ash",
                "parent_hash": "GENESIS",
                "entry_hash": "0" * 64,
                "payload_json": "{this is not valid json",
            }
        ]
        backend = _FakeBackend(rows=rows)
        with pytest.raises(CognitionReplayEntryIncomplete, match="payload_json is malformed"):
            replay_cognition_entry("c1", state_backend=_as_backend(backend))


# ---------------------------------------------------------------------------
# HASH_ONLY disposition


class TestHashOnlyDisposition:
    def test_hash_only_succeeds_without_regeneration(self) -> None:
        backend = _FakeBackend(
            rows=[
                _build_row(
                    entry_id="c1",
                    payload={
                        "prompt_disposition": "HASH_ONLY",
                        "prompt_hash": "a" * 64,
                    },
                )
            ]
        )
        report = replay_cognition_entry("c1", state_backend=_as_backend(backend))
        assert isinstance(report, CognitionReplayReport)
        assert report.prompt_disposition == "HASH_ONLY"
        assert report.hash_verified is True
        assert report.regeneration_supported is False
        assert report.regeneration_attempted is False
        assert report.comparison_outcome is None


# ---------------------------------------------------------------------------
# VERBATIM disposition


class TestVerbatimDisposition:
    def test_verbatim_bit_exact_match(self) -> None:
        prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])
        payload = _build_verbatim_payload(prompt=prompt, result_text="hello world", temperature=0.0)
        backend = _FakeBackend(rows=[_build_row(entry_id="c1", payload=payload)])

        canned = CognitionResult(
            text="hello world",
            tool_calls=[],
            usage=TokenUsage(input_tokens=10, output_tokens=20),
            latency_ms=0.0,
            provider_fingerprint="anthropic|claude-sonnet-4-7-20260418|step-2",
        )

        report = replay_cognition_entry(
            "c1",
            state_backend=_as_backend(backend),
            provider_factory=_make_factory(canned),
        )
        assert report.regeneration_attempted is True
        assert report.comparison_outcome is not None
        assert report.comparison_outcome.matches is True

    def test_verbatim_text_divergence_raises(self) -> None:
        prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])
        payload = _build_verbatim_payload(prompt=prompt, result_text="original", temperature=0.0)
        backend = _FakeBackend(rows=[_build_row(entry_id="c1", payload=payload)])

        canned = CognitionResult(
            text="DIFFERENT",
            tool_calls=[],
            usage=TokenUsage(input_tokens=10, output_tokens=20),
            latency_ms=0.0,
            provider_fingerprint="x",
        )
        with pytest.raises(CognitionReplayDivergence) as exc:
            replay_cognition_entry(
                "c1",
                state_backend=_as_backend(backend),
                provider_factory=_make_factory(canned),
            )
        assert exc.value.entry_id == "c1"
        assert exc.value.prompt_disposition == "VERBATIM"
        assert "text differs" in exc.value.divergence_reason

    def test_verbatim_non_deterministic_does_not_raise(self) -> None:
        """temperature>0 + diverging text → matches=True, no exception."""
        prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])
        payload = _build_verbatim_payload(prompt=prompt, result_text="original", temperature=0.7)
        backend = _FakeBackend(rows=[_build_row(entry_id="c1", payload=payload)])

        canned = CognitionResult(
            text="totally different",
            tool_calls=[],
            usage=TokenUsage(input_tokens=10, output_tokens=20),
            latency_ms=0.0,
            provider_fingerprint="x",
        )
        report = replay_cognition_entry(
            "c1",
            state_backend=_as_backend(backend),
            provider_factory=_make_factory(canned),
        )
        assert report.comparison_outcome is not None
        assert report.comparison_outcome.matches is True
        assert "non_deterministic_replay" in report.comparison_outcome.reason

    def test_verbatim_tamper_detection(self) -> None:
        """If stored prompt_verbatim re-hashes to a different value than
        stored prompt_hash, raise divergence — entry has been tampered."""
        prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])
        canonical = canonicalize_prompt(prompt)
        # Stored hash matches canonical, but we'll tamper the canonical:
        tampered = canonical.replace("hi", "MALICIOUS")
        payload = {
            "prompt_disposition": "VERBATIM",
            "prompt_hash": hash_prompt(prompt),  # matches original canonical
            "prompt_verbatim": tampered,  # but the body has been swapped
            "cognition_provenance": {
                "provider_id": "anthropic",
                "model": "claude-sonnet-4-7-20260418",
                "temperature": 0.0,
            },
            "result_text": "anything",
        }
        backend = _FakeBackend(rows=[_build_row(entry_id="c1", payload=payload)])

        with pytest.raises(CognitionReplayDivergence) as exc:
            replay_cognition_entry(
                "c1",
                state_backend=_as_backend(backend),
                provider_factory=_make_factory(
                    CognitionResult(
                        text="x",
                        tool_calls=[],
                        usage=TokenUsage(input_tokens=0, output_tokens=0),
                        latency_ms=0.0,
                        provider_fingerprint="x",
                    )
                ),
            )
        assert "tampered" in exc.value.divergence_reason

    def test_verbatim_missing_canonical_body(self) -> None:
        backend = _FakeBackend(
            rows=[
                _build_row(
                    entry_id="c1",
                    payload={
                        "prompt_disposition": "VERBATIM",
                        "prompt_hash": "a" * 64,
                        # no prompt_verbatim
                    },
                )
            ]
        )
        with pytest.raises(CognitionReplayEntryIncomplete, match="prompt_verbatim"):
            replay_cognition_entry("c1", state_backend=_as_backend(backend))

    def test_verbatim_missing_provenance_provider_id(self) -> None:
        prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])
        canonical = canonicalize_prompt(prompt)
        payload = {
            "prompt_disposition": "VERBATIM",
            "prompt_hash": hash_canonical_form(canonical),
            "prompt_verbatim": canonical,
            "cognition_provenance": {
                # no provider_id
                "model": "claude-sonnet-4-7-20260418",
                "temperature": 0.0,
            },
            "result_text": "x",
        }
        backend = _FakeBackend(rows=[_build_row(entry_id="c1", payload=payload)])
        with pytest.raises(CognitionReplayEntryIncomplete, match="provider_id"):
            replay_cognition_entry(
                "c1",
                state_backend=_as_backend(backend),
                provider_factory=_make_factory(
                    CognitionResult(
                        text="x",
                        tool_calls=[],
                        usage=TokenUsage(input_tokens=0, output_tokens=0),
                        latency_ms=0.0,
                        provider_fingerprint="x",
                    )
                ),
            )

    def test_verbatim_no_provider_factory_raises_unavailable(self) -> None:
        """Default factory (no kwarg passed) raises ProviderUnavailable."""
        prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])
        payload = _build_verbatim_payload(prompt=prompt, temperature=0.0)
        backend = _FakeBackend(rows=[_build_row(entry_id="c1", payload=payload)])

        with pytest.raises(CognitionReplayProviderUnavailable) as exc:
            # No provider_factory kwarg → falls through to _unavailable_factory.
            replay_cognition_entry("c1", state_backend=_as_backend(backend))
        assert exc.value.provider_id == "anthropic"
        assert exc.value.model == "claude-sonnet-4-7-20260418"

    def test_invalid_max_tokens_raises_incomplete(self) -> None:
        """Sourcery review (2026-05-28): malformed max_tokens raises
        CognitionReplayEntryIncomplete (HTTP 409) rather than crashing
        as 5xx inside the provider.complete() invocation."""
        prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])
        payload = _build_verbatim_payload(prompt=prompt, temperature=0.0)
        # Tamper the provenance with a legacy-style string value.
        payload["cognition_provenance"]["max_tokens"] = "auto"
        backend = _FakeBackend(rows=[_build_row(entry_id="c1", payload=payload)])

        canned = CognitionResult(
            text="x",
            tool_calls=[],
            usage=TokenUsage(input_tokens=0, output_tokens=0),
            latency_ms=0.0,
            provider_fingerprint="x",
        )
        with pytest.raises(CognitionReplayEntryIncomplete, match="invalid max_tokens"):
            replay_cognition_entry(
                "c1",
                state_backend=_as_backend(backend),
                provider_factory=_make_factory(canned),
            )

    def test_invalid_temperature_raises_incomplete(self) -> None:
        """Sourcery review (2026-05-28): malformed temperature raises
        CognitionReplayEntryIncomplete rather than crashing as 5xx."""
        prompt = Prompt(system=None, messages=[{"role": "user", "content": "hi"}])
        payload = _build_verbatim_payload(prompt=prompt, temperature=0.0)
        payload["cognition_provenance"]["temperature"] = None
        backend = _FakeBackend(rows=[_build_row(entry_id="c1", payload=payload)])

        canned = CognitionResult(
            text="x",
            tool_calls=[],
            usage=TokenUsage(input_tokens=0, output_tokens=0),
            latency_ms=0.0,
            provider_fingerprint="x",
        )
        with pytest.raises(CognitionReplayEntryIncomplete, match="invalid temperature"):
            replay_cognition_entry(
                "c1",
                state_backend=_as_backend(backend),
                provider_factory=_make_factory(canned),
            )


# ---------------------------------------------------------------------------
# ENCRYPTED_OPT_IN disposition


class TestEncryptedDisposition:
    def test_encrypted_with_null_encryptor_raises(self) -> None:
        """Phase 13 default: NullPromptEncryptor.decrypt() raises
        EncryptedDispositionNotConfigured. The replay engine surfaces
        that without catching it.

        Codex review (2026-05-28): the JSON-carried ciphertext is
        base64 per Phase 13 convention; the engine base64-decodes
        before calling decrypt(). We use a valid-base64 string here
        so the test reaches the encryptor (rather than failing earlier
        at the base64 decode step)."""
        import base64

        payload = {
            "prompt_disposition": "ENCRYPTED_OPT_IN",
            "prompt_hash": "a" * 64,
            "prompt_encrypted": base64.b64encode(b"fake-encrypted-bytes").decode("ascii"),
            "cognition_provenance": {
                "provider_id": "anthropic",
                "model": "claude-sonnet-4-7-20260418",
                "temperature": 0.0,
            },
        }
        backend = _FakeBackend(rows=[_build_row(entry_id="c1", payload=payload)])

        with pytest.raises(EncryptedDispositionNotConfigured):
            replay_cognition_entry("c1", state_backend=_as_backend(backend))

    def test_encrypted_invalid_base64_raises_incomplete(self) -> None:
        """Codex review (2026-05-28): non-base64 string ciphertext raises
        CognitionReplayEntryIncomplete (HTTP 409) rather than crashing
        inside the encryptor as 5xx."""
        payload = {
            "prompt_disposition": "ENCRYPTED_OPT_IN",
            "prompt_hash": "a" * 64,
            "prompt_encrypted": "not!valid!base64!!@@",
            "cognition_provenance": {
                "provider_id": "anthropic",
                "model": "claude-sonnet-4-7-20260418",
                "temperature": 0.0,
            },
        }
        backend = _FakeBackend(rows=[_build_row(entry_id="c1", payload=payload)])
        with pytest.raises(CognitionReplayEntryIncomplete, match="base64"):
            replay_cognition_entry("c1", state_backend=_as_backend(backend))

    def test_encrypted_missing_blob_raises_incomplete(self) -> None:
        backend = _FakeBackend(
            rows=[
                _build_row(
                    entry_id="c1",
                    payload={
                        "prompt_disposition": "ENCRYPTED_OPT_IN",
                        "prompt_hash": "a" * 64,
                        # no prompt_encrypted
                    },
                )
            ]
        )
        with pytest.raises(CognitionReplayEntryIncomplete, match="prompt_encrypted"):
            replay_cognition_entry("c1", state_backend=_as_backend(backend))


# ---------------------------------------------------------------------------
# Bonus: outcome structure pins


class TestComparisonOutcome:
    def test_outcome_is_frozen_dataclass(self) -> None:
        import dataclasses

        outcome = ComparisonOutcome(matches=True, reason="x")
        with pytest.raises(dataclasses.FrozenInstanceError):
            outcome.matches = False  # type: ignore[misc]

    def test_outcome_fields_are_public(self) -> None:
        outcome = ComparisonOutcome(matches=True, reason="bit_exact: details")
        assert outcome.matches is True
        assert outcome.reason == "bit_exact: details"

    def test_outcome_new_fields_default_to_none(self) -> None:
        """Phase 13.x.4: verdict + classification default to None for
        backward-compat with PR #20 callsites that don't populate them."""
        outcome = ComparisonOutcome(matches=True, reason="x")
        assert outcome.verdict is None
        assert outcome.classification is None


# ---------------------------------------------------------------------------
# Phase 13.x.4: TestVerdictMapping — pin the disagreement_type → verdict
# mapping table from design spec §5.1.


class TestVerdictMapping:
    """Each test pins one row of the §5.1 mapping table.

    The classifier returns a single CognitionDisagreementType; the
    comparator runs (text differs, classifier called); the outcome's
    verdict matches the locked mapping.
    """

    def _make_payload_and_replayed(self) -> tuple[dict[str, Any], CognitionResult]:
        """Text-differs payload so the comparator goes through the
        classifier branch."""
        return (
            {
                "cognition_provenance": {"temperature": 0.0},
                "result_text": "original",
                "result_tool_calls": [],
            },
            CognitionResult(
                text="replayed (DIFFERENT)",
                tool_calls=[],
                usage=TokenUsage(input_tokens=1, output_tokens=1),
                latency_ms=0.0,
                provider_fingerprint="x",
            ),
        )

    def test_factual_agreement_maps_to_semantic_match(self) -> None:
        classifier = _StubClassifier(returns=CognitionDisagreementType.FACTUAL_AGREEMENT)
        payload, replayed = self._make_payload_and_replayed()
        outcome = default_compare_cognition_results(payload, replayed, classifier=classifier)
        assert outcome.verdict == CognitionReplayVerdict.SEMANTIC_MATCH

    def test_stylistic_divergence_maps_to_semantic_match(self) -> None:
        classifier = _StubClassifier(returns=CognitionDisagreementType.STYLISTIC_DIVERGENCE)
        payload, replayed = self._make_payload_and_replayed()
        outcome = default_compare_cognition_results(payload, replayed, classifier=classifier)
        assert outcome.verdict == CognitionReplayVerdict.SEMANTIC_MATCH

    def test_factual_disagreement_maps_to_divergence(self) -> None:
        classifier = _StubClassifier(returns=CognitionDisagreementType.FACTUAL_DISAGREEMENT)
        payload, replayed = self._make_payload_and_replayed()
        outcome = default_compare_cognition_results(payload, replayed, classifier=classifier)
        assert outcome.verdict == CognitionReplayVerdict.DIVERGENCE

    def test_interpretive_divergence_maps_to_divergence(self) -> None:
        classifier = _StubClassifier(returns=CognitionDisagreementType.INTERPRETIVE_DIVERGENCE)
        payload, replayed = self._make_payload_and_replayed()
        outcome = default_compare_cognition_results(payload, replayed, classifier=classifier)
        assert outcome.verdict == CognitionReplayVerdict.DIVERGENCE

    def test_refusal_divergence_maps_to_divergence(self) -> None:
        classifier = _StubClassifier(returns=CognitionDisagreementType.REFUSAL_DIVERGENCE)
        payload, replayed = self._make_payload_and_replayed()
        outcome = default_compare_cognition_results(payload, replayed, classifier=classifier)
        assert outcome.verdict == CognitionReplayVerdict.DIVERGENCE

    def test_tool_choice_divergence_maps_to_divergence(self) -> None:
        classifier = _StubClassifier(returns=CognitionDisagreementType.TOOL_CHOICE_DIVERGENCE)
        payload, replayed = self._make_payload_and_replayed()
        outcome = default_compare_cognition_results(payload, replayed, classifier=classifier)
        assert outcome.verdict == CognitionReplayVerdict.DIVERGENCE

    def test_unclassified_stays_unclassified(self) -> None:
        classifier = _StubClassifier(returns=CognitionDisagreementType.UNCLASSIFIED)
        payload, replayed = self._make_payload_and_replayed()
        outcome = default_compare_cognition_results(payload, replayed, classifier=classifier)
        assert outcome.verdict == CognitionReplayVerdict.UNCLASSIFIED
