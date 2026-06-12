"""Multi-provider PAIR-GENERATION harness for the Step 5c corpus.

Produces candidate ``(prompt, response_a, response_b)`` pairs by running >= 2
cognition providers (or one provider under two configurations) on purpose-built
prompts, targeting the four under-represented classes the source datasets don't
cover (REFUSAL / TOOL_CHOICE / INTERPRETIVE / STYLISTIC divergence).

**Candidates, not labels.** Generation does NOT assign a gold class — model
behavior is not guaranteed (a "refusal" prompt may yield two refusals, or none).
Each emitted pair carries a *placeholder* ``gold_class=UNCLASSIFIED`` and a
``target_class_hint`` in its notes; the LLM-judge labeler
(:mod:`cognition_wobble.annotation`) or a human assigns the real label. This is
the honest division of labor from ``docs/planning/STEP5C_CORPUS_PLAN.md``.

**Injectable + testable.** The core functions take a ``list[CognitionProvider]``
(the structural Protocol), so unit tests inject fake providers returning canned
``CognitionResult``s — no API keys, no SDKs, no network. The CLI
(``scripts/generate_cognition_pairs.py``) constructs the real adapters.

**Refusals arrive two ways:** as refusal text in ``CognitionResult.text``, or as
a raised :class:`CognitionContentPolicyError` (the adapter mapped the provider's
policy block). :func:`_call_variant` catches the latter and synthesizes a
refusal-text result so the refusal feature fires either way.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from cognition_wobble.disagreement_types import CognitionDisagreementType
from cognition_wobble.eval import CalibrationExample
from phoenix.providers.cognition.errors import (
    CognitionContentPolicyError,
    CognitionError,
)
from phoenix.providers.cognition.types import (
    CognitionResult,
    Prompt,
    TokenUsage,
)

if TYPE_CHECKING:
    from phoenix.providers.cognition.protocol import CognitionProvider
    from phoenix.providers.cognition.types import Tool

__all__ = [
    "ResponseVariant",
    "PairSpec",
    "GenerationReport",
    "generate_pair",
    "generate_pairs",
    "refusal_specs",
    "interpretive_specs",
    "stylistic_specs",
    "tool_choice_specs",
]

_ZERO_USAGE = TokenUsage(input_tokens=0, output_tokens=0)


@dataclass(frozen=True)
class ResponseVariant:
    """How to elicit one side of a pair from the provider list.

    Fields:
        provider_index: Index into the ``providers`` list passed to
            :func:`generate_pair`. Two variants with different indices run
            different providers (REFUSAL / INTERPRETIVE); two with the same
            index run one provider under different configs (TOOL_CHOICE /
            STYLISTIC).
        system_override: When set, replaces the prompt's ``system`` for this
            variant (used to render the same fact in different registers).
        tools: Tools exposed to this variant (used to make one side
            tool-eligible while the other is not).
    """

    provider_index: int
    system_override: str | None = None
    tools: list[Tool] | None = None


@dataclass(frozen=True)
class PairSpec:
    """A single pair-generation instruction."""

    prompt: Prompt
    variant_a: ResponseVariant
    variant_b: ResponseVariant
    source_tag: str = "phoenix-generated"
    target_class_hint: str = ""
    notes: str = ""


@dataclass(frozen=True)
class GenerationReport:
    """Outcome of a :func:`generate_pairs` run."""

    pairs: list[CalibrationExample]
    n_attempted: int
    skip_reasons: list[str] = field(default_factory=list)

    @property
    def n_skipped(self) -> int:
        return len(self.skip_reasons)


def _refusal_result(provider_id: str, reason: str | None) -> CognitionResult:
    """Synthesize a refusal-text result from an API-level policy block."""
    detail = f" ({reason})" if reason else ""
    return CognitionResult(
        text=f"I can't help with that request.{detail}",
        tool_calls=[],
        usage=_ZERO_USAGE,
        latency_ms=0.0,
        provider_fingerprint=f"{provider_id}|policy-refusal",
    )


def _call_variant(
    providers: list[CognitionProvider],
    variant: ResponseVariant,
    prompt: Prompt,
    *,
    max_tokens: int,
    temperature: float,
) -> CognitionResult:
    """Run one variant. Converts an API-level policy block into a refusal result;
    re-raises any other :class:`CognitionError` for the caller to handle."""
    provider = providers[variant.provider_index]
    eff_prompt = (
        replace(prompt, system=variant.system_override)
        if variant.system_override is not None
        else prompt
    )
    try:
        return provider.complete(
            eff_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=variant.tools,
        )
    except CognitionContentPolicyError as exc:
        return _refusal_result(provider.provider_id, exc.reason_code)


def generate_pair(
    providers: list[CognitionProvider],
    spec: PairSpec,
    *,
    max_tokens: int = 512,
    temperature: float = 0.7,
) -> CalibrationExample:
    """Run both variants of ``spec`` and build an UNLABELED calibration example.

    The returned example has ``gold_class=UNCLASSIFIED`` as a placeholder; the
    real class is assigned later by the judge labeler or a human. Raises
    :class:`IndexError` (with a descriptive message) if a variant's
    ``provider_index`` is out of range, and propagates any non-policy
    :class:`CognitionError` from a provider call.
    """
    n = len(providers)
    for name, variant in (("variant_a", spec.variant_a), ("variant_b", spec.variant_b)):
        if not 0 <= variant.provider_index < n:
            raise IndexError(
                f"{name} provider_index {variant.provider_index} out of range "
                f"for {n} provider(s)"
            )
    resp_a = _call_variant(providers, spec.variant_a, spec.prompt,
                           max_tokens=max_tokens, temperature=temperature)
    resp_b = _call_variant(providers, spec.variant_b, spec.prompt,
                           max_tokens=max_tokens, temperature=temperature)
    hint = f"[target={spec.target_class_hint}] " if spec.target_class_hint else ""
    note = f"{hint}UNLABELED (awaiting judge/human label). {spec.notes}".strip()
    return CalibrationExample(
        prompt=spec.prompt,
        response_a=resp_a,
        response_b=resp_b,
        gold_class=CognitionDisagreementType.UNCLASSIFIED,
        source_dataset=spec.source_tag,
        annotation_notes=note,
    )


def generate_pairs(
    providers: list[CognitionProvider],
    specs: list[PairSpec],
    *,
    max_tokens: int = 512,
    temperature: float = 0.7,
) -> GenerationReport:
    """Generate pairs for every spec, skipping (not failing) on provider errors.

    A pair is skipped (not fatal) when a non-policy :class:`CognitionError` is
    raised (e.g. rate limit, timeout, auth) OR a variant's ``provider_index`` is
    out of range (:class:`IndexError`) — the reason is recorded in
    :attr:`GenerationReport.skip_reasons` so a CLI can surface it, and the rest
    of the batch still runs. Policy blocks are NOT skips: they become refusal
    results (often exactly what a REFUSAL target wants).
    """
    pairs: list[CalibrationExample] = []
    skips: list[str] = []
    for i, spec in enumerate(specs):
        try:
            pairs.append(
                generate_pair(providers, spec, max_tokens=max_tokens, temperature=temperature)
            )
        except (IndexError, CognitionError) as exc:
            skips.append(f"spec[{i}] ({spec.target_class_hint}): {type(exc).__name__}: {exc}")
    return GenerationReport(pairs=pairs, n_attempted=len(specs), skip_reasons=skips)


# ---------------------------------------------------------------------------
# Per-class spec builders. Each takes seed prompts (text + notes) and encodes the
# elicitation strategy from the corpus plan. They DON'T call providers — they
# build PairSpecs that generate_pairs() runs.


def _as_prompt(text: str) -> Prompt:
    return Prompt(system=None, messages=[{"role": "user", "content": text}])


def refusal_specs(
    prompts: list[tuple[str, str]],
    *,
    provider_a: int = 0,
    provider_b: int = 1,
) -> list[PairSpec]:
    """Same gray-area prompt to two different providers; one may refuse.

    ``prompts`` is a list of ``(prompt_text, notes)``. Use borderline-but-benign
    capability/policy prompts where providers are likely to disagree on refusal;
    do NOT seed genuinely harmful content — the refusal *asymmetry* is the
    signal, not the payload.
    """
    return [
        PairSpec(
            prompt=_as_prompt(text),
            variant_a=ResponseVariant(provider_index=provider_a),
            variant_b=ResponseVariant(provider_index=provider_b),
            target_class_hint="refusal_divergence",
            notes=notes,
        )
        for text, notes in prompts
    ]


def interpretive_specs(
    prompts: list[tuple[str, str]],
    *,
    provider_a: int = 0,
    provider_b: int = 1,
) -> list[PairSpec]:
    """Same ambiguous/polysemous prompt to two providers; they may read it
    differently. Seed with deliberately ambiguous prompts (polysemy,
    context-free pronouns)."""
    return [
        PairSpec(
            prompt=_as_prompt(text),
            variant_a=ResponseVariant(provider_index=provider_a),
            variant_b=ResponseVariant(provider_index=provider_b),
            target_class_hint="interpretive_divergence",
            notes=notes,
        )
        for text, notes in prompts
    ]


def stylistic_specs(
    prompts: list[tuple[str, str]],
    *,
    register_a: str,
    register_b: str,
    provider_index: int = 0,
) -> list[PairSpec]:
    """Same prompt rendered by one provider in two registers (e.g. terse/casual
    vs. formal/verbose) via system-prompt overrides. The facts should match; the
    presentation diverges."""
    return [
        PairSpec(
            prompt=_as_prompt(text),
            variant_a=ResponseVariant(provider_index=provider_index, system_override=register_a),
            variant_b=ResponseVariant(provider_index=provider_index, system_override=register_b),
            target_class_hint="stylistic_divergence",
            notes=notes,
        )
        for text, notes in prompts
    ]


def tool_choice_specs(
    prompts: list[tuple[str, str]],
    *,
    tools: list[Tool],
    provider_index: int = 0,
) -> list[PairSpec]:
    """Same tool-eligible query run once WITH tools exposed and once WITHOUT, so
    one side may emit a tool call and the other must answer directly. Seed with
    tool-eligible questions (weather, search, calculation)."""
    return [
        PairSpec(
            prompt=_as_prompt(text),
            variant_a=ResponseVariant(provider_index=provider_index, tools=tools),
            variant_b=ResponseVariant(provider_index=provider_index, tools=None),
            target_class_hint="tool_choice_divergence",
            notes=notes,
        )
        for text, notes in prompts
    ]
