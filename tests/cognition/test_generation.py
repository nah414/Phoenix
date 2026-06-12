"""Tests for the Step 5c pair-generation harness (mock providers; no API keys)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from cognition_wobble.disagreement_types import CognitionDisagreementType
from cognition_wobble.generation import (
    PairSpec,
    ResponseVariant,
    generate_pair,
    generate_pairs,
    interpretive_specs,
    refusal_specs,
    stylistic_specs,
    tool_choice_specs,
)
from phoenix.providers.cognition.errors import (
    CognitionContentPolicyError,
    CognitionTimeoutError,
)
from phoenix.providers.cognition.types import (
    CognitionResult,
    Prompt,
    TokenUsage,
    Tool,
    ToolCall,
)


class _FakeProvider:
    """Protocol-structural fake returning a canned result (Pattern C)."""

    def __init__(
        self,
        *,
        provider_id: str = "fake",
        model: str = "m",
        text: str = "a response",
        tool_calls: list[ToolCall] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.model = model
        self._text = text
        self._tool_calls = tool_calls or []
        self._raises = raises
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        prompt: Prompt,
        *,
        max_tokens: int,
        temperature: float,
        tools: list[Tool] | None = None,
        stream: bool = False,
    ) -> CognitionResult:
        self.calls.append({"system": prompt.system, "tools": tools})
        if self._raises is not None:
            raise self._raises
        return CognitionResult(
            text=self._text,
            tool_calls=self._tool_calls,
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            latency_ms=1.0,
            provider_fingerprint=f"{self.provider_id}|{self.model}",
        )


def _prompt(text: str = "q") -> Prompt:
    return Prompt(system=None, messages=[{"role": "user", "content": text}])


def test_generate_pair_is_unlabeled_placeholder() -> None:
    pa = _FakeProvider(provider_id="a", text="answer A")
    pb = _FakeProvider(provider_id="b", text="answer B")
    spec = PairSpec(
        prompt=_prompt(),
        variant_a=ResponseVariant(provider_index=0),
        variant_b=ResponseVariant(provider_index=1),
        target_class_hint="refusal_divergence",
    )
    ex = generate_pair([pa, pb], spec)  # type: ignore[list-item]
    # gold is a PLACEHOLDER, not a real label
    assert ex.gold_class is CognitionDisagreementType.UNCLASSIFIED
    assert ex.response_a.text == "answer A"
    assert ex.response_b.text == "answer B"
    assert "UNLABELED" in ex.annotation_notes
    assert "refusal_divergence" in ex.annotation_notes


def test_refusal_specs_use_two_providers() -> None:
    specs = refusal_specs([("p1", "n1"), ("p2", "n2")])
    assert len(specs) == 2
    assert specs[0].variant_a.provider_index == 0
    assert specs[0].variant_b.provider_index == 1
    assert specs[0].target_class_hint == "refusal_divergence"


def test_interpretive_specs_target_hint() -> None:
    specs = interpretive_specs([("ambiguous?", "polysemy")])
    assert specs[0].target_class_hint == "interpretive_divergence"


def test_stylistic_applies_system_overrides() -> None:
    provider = _FakeProvider(text="rendered")
    specs = stylistic_specs([("explain X", "fact")], register_a="be casual", register_b="be formal")
    generate_pair([provider], specs[0])  # type: ignore[list-item]
    # both calls hit provider 0, with the two register system prompts
    assert provider.calls[0]["system"] == "be casual"
    assert provider.calls[1]["system"] == "be formal"


def test_tool_choice_exposes_tools_to_one_side_only() -> None:
    provider = _FakeProvider(text="x")
    tools = [
        Tool(name="web_search", description="search", input_schema={"type": "object"}),
    ]
    specs = tool_choice_specs([("weather?", "tool eligible")], tools=tools)
    generate_pair([provider], specs[0])  # type: ignore[list-item]
    assert provider.calls[0]["tools"] == tools  # variant_a gets tools
    assert provider.calls[1]["tools"] is None  # variant_b does not


def test_policy_block_becomes_refusal_result() -> None:
    refuser = _FakeProvider(
        provider_id="strict",
        raises=CognitionContentPolicyError("blocked", reason_code="safety"),
    )
    answerer = _FakeProvider(provider_id="lenient", text="here is the answer")
    spec = refusal_specs([("gray area", "n")])[0]
    ex = generate_pair([refuser, answerer], spec)  # type: ignore[list-item]
    # the refused side is synthesized into a refusal-text result
    assert "can't help" in ex.response_a.text.lower()
    assert ex.response_b.text == "here is the answer"


def test_generate_pairs_skips_on_non_policy_error() -> None:
    good = _FakeProvider(text="ok")
    flaky = _FakeProvider(raises=CognitionTimeoutError("timeout"))
    specs = [
        PairSpec(
            prompt=_prompt(),
            variant_a=ResponseVariant(provider_index=0),
            variant_b=ResponseVariant(provider_index=0),
            target_class_hint="stylistic_divergence",
        ),
        PairSpec(
            prompt=_prompt(),
            variant_a=ResponseVariant(provider_index=1),  # flaky -> raises
            variant_b=ResponseVariant(provider_index=0),
            target_class_hint="stylistic_divergence",
        ),
    ]
    report = generate_pairs([good, flaky], specs)  # type: ignore[list-item]
    assert len(report.pairs) == 1
    assert report.n_attempted == 2
    assert report.n_skipped == 1
    assert "CognitionTimeoutError" in report.skip_reasons[0]


def test_out_of_range_provider_index_raises_descriptive_indexerror() -> None:
    provider = _FakeProvider()
    spec = PairSpec(
        prompt=_prompt(),
        variant_a=ResponseVariant(provider_index=0),
        variant_b=ResponseVariant(provider_index=5),  # out of range for 1 provider
        target_class_hint="stylistic_divergence",
    )
    with pytest.raises(IndexError, match="provider_index 5 out of range"):
        generate_pair([provider], spec)  # type: ignore[list-item]


def test_generate_pairs_skips_out_of_range_index() -> None:
    provider = _FakeProvider(text="ok")
    specs = [
        PairSpec(
            prompt=_prompt(),
            variant_a=ResponseVariant(provider_index=0),
            variant_b=ResponseVariant(provider_index=9),  # bad -> skipped, not fatal
            target_class_hint="stylistic_divergence",
        ),
        PairSpec(
            prompt=_prompt(),
            variant_a=ResponseVariant(provider_index=0),
            variant_b=ResponseVariant(provider_index=0),
            target_class_hint="stylistic_divergence",
        ),
    ]
    report = generate_pairs([provider], specs)  # type: ignore[list-item]
    assert len(report.pairs) == 1  # second spec ok; first skipped
    assert report.n_skipped == 1
    assert "IndexError" in report.skip_reasons[0]


# --- generate CLI _load_seeds (loaded from the script file) ---

_GEN_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "generate_cognition_pairs.py"


def _load_gen_cli() -> Any:
    spec = importlib.util.spec_from_file_location("gen_cli", _GEN_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_load_seeds_rejects_malformed_json(tmp_path: Path) -> None:
    gen = _load_gen_cli()
    p = tmp_path / "seeds.jsonl"
    p.write_text('{"prompt": "ok"}\n{bad json}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        gen._load_seeds(p, None)


def test_load_seeds_requires_prompt_field(tmp_path: Path) -> None:
    gen = _load_gen_cli()
    p = tmp_path / "seeds.jsonl"
    p.write_text('{"notes": "no prompt"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="prompt"):
        gen._load_seeds(p, None)
