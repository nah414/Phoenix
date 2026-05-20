"""Tests for the cognition pricing v2 loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from phoenix.pricing.v2 import (
    CognitionPricingError,
    CognitionPricingRecord,
    load_cognition_pricing,
)


def test_load_default_pricing_returns_dict() -> None:
    """The shipped JSON loads without error."""
    pricing = load_cognition_pricing()
    assert isinstance(pricing, dict)
    assert len(pricing) > 0


def test_load_anthropic_models_present() -> None:
    """Initial pricing table includes all three Anthropic 4.x tiers."""
    pricing = load_cognition_pricing()
    anthropic_keys = [k for k in pricing if k[0] == "anthropic"]
    assert len(anthropic_keys) >= 3


def test_load_returns_pricing_records() -> None:
    """Each value is a CognitionPricingRecord."""
    pricing = load_cognition_pricing()
    for rec in pricing.values():
        assert isinstance(rec, CognitionPricingRecord)


def test_load_anthropic_sonnet_rate() -> None:
    """Sanity-check the Sonnet 4.7 rate: $3 in / $15 out per 1M tokens."""
    pricing = load_cognition_pricing()
    sonnet_key = next(
        (k for k in pricing if k[0] == "anthropic" and "sonnet" in k[1]),
        None,
    )
    assert sonnet_key is not None
    rec = pricing[sonnet_key]
    assert rec.usd_per_1m_input_tokens == 3.0
    assert rec.usd_per_1m_output_tokens == 15.0


def test_missing_file_raises_typed_error(tmp_path: Path) -> None:
    """A nonexistent path raises CognitionPricingError."""
    bogus = tmp_path / "does-not-exist.json"
    with pytest.raises(CognitionPricingError):
        load_cognition_pricing(bogus)


def test_malformed_json_raises_typed_error(tmp_path: Path) -> None:
    """A JSON-parse error raises CognitionPricingError."""
    f = tmp_path / "bad.json"
    f.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(CognitionPricingError):
        load_cognition_pricing(f)


def test_missing_providers_key_raises_typed_error(tmp_path: Path) -> None:
    """A JSON missing the 'providers' key raises CognitionPricingError."""
    f = tmp_path / "no-providers.json"
    f.write_text(json.dumps({"_metadata": {}}), encoding="utf-8")
    with pytest.raises(CognitionPricingError):
        load_cognition_pricing(f)


def test_load_with_minimal_record(tmp_path: Path) -> None:
    """A row missing the optional discount factors defaults to 1.0."""
    f = tmp_path / "minimal.json"
    f.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "provider": "test",
                        "model": "test-model",
                        "usd_per_1m_input_tokens": 1.0,
                        "usd_per_1m_output_tokens": 2.0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    pricing = load_cognition_pricing(f)
    rec = pricing[("test", "test-model")]
    assert rec.prompt_cache_discount_factor == 1.0
    assert rec.batch_discount_factor == 1.0
    assert rec.vision_token_multiplier == 1.0


def test_load_default_factors_in_valid_ranges() -> None:
    """All shipped discount factors are non-negative and at most 1.0."""
    pricing = load_cognition_pricing()
    for rec in pricing.values():
        assert 0.0 <= rec.prompt_cache_discount_factor <= 1.0
        assert 0.0 <= rec.batch_discount_factor <= 1.0
        assert rec.vision_token_multiplier >= 0.0
