
from decimal import Decimal

import pytest

from src import pricing

MODEL = "gemini-3.5-flash-lite"


def test_known_model_rates():
    rates = pricing.get_rates(MODEL)

    assert rates is not None
    assert rates.input_per_million == Decimal("0.30")
    assert rates.output_per_million == Decimal("2.50")


def test_model_name_normalization():
    assert pricing.get_rates(f"models/{MODEL}") == pricing.get_rates(MODEL)
    assert pricing.get_rates(MODEL.upper()) == pricing.get_rates(MODEL)


def test_unknown_model_has_no_rates():
    assert pricing.get_rates("unknown-model") is None


def test_typical_call_with_explicit_zero_thinking_tokens():
    cost = pricing.estimate_cost_usd(MODEL, 1000, 500, 0)

    assert cost == Decimal("0.00155000")


def test_thinking_tokens_are_billed_at_output_rate():
    cost = pricing.estimate_cost_usd(MODEL, 1000, 500, 200)

    assert cost == Decimal("0.00205000")


def test_zero_token_usage_has_zero_cost():
    assert pricing.estimate_cost_usd(MODEL, 0, 0, 0) == Decimal("0E-8")


def test_missing_thinking_tokens_means_unknown_cost():
    assert pricing.estimate_cost_usd(MODEL, 1000, 500) is None


def test_missing_prompt_tokens_means_unknown_cost():
    assert pricing.estimate_cost_usd(MODEL, None, 500, 0) is None


def test_missing_output_tokens_means_unknown_cost():
    assert pricing.estimate_cost_usd(MODEL, 1000, None, 0) is None


def test_unknown_model_means_unknown_cost():
    assert pricing.estimate_cost_usd("unknown-model", 1000, 500, 0) is None


@pytest.mark.parametrize(
    "value",
    [-1, 1.5, True, "100", None],
)
def test_invalid_token_counts_are_rejected(value):
    assert pricing.estimate_cost_usd(MODEL, value, 500, 0) is None
    assert pricing.estimate_cost_usd(MODEL, 1000, value, 0) is None


def test_invalid_thinking_token_count_is_rejected():
    assert pricing.estimate_cost_usd(MODEL, 1000, 500, -1) is None


def test_estimate_note_distinguishes_estimate_from_actual_billing():
    assert "Not the provider's" in pricing.ESTIMATE_NOTE
    assert "Free-tier" in pricing.ESTIMATE_NOTE
