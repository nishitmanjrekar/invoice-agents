from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

ESTIMATE_NOTE = (
    "Estimated from published standard-tier list prices. Not the provider's "
    "final billed amount. Free-tier usage may have no charge."
)


@dataclass(frozen=True)
class Rates:
    input_per_million: Decimal
    output_per_million: Decimal
    version: str
    source: str
    verified_on: str


PRICING = {
    "gemini-3.5-flash-lite": Rates(
        input_per_million=Decimal("0.30"),
        output_per_million=Decimal("2.50"),
        version="gemini-3.5-flash-lite/standard/2026-11-19",
        source="https://ai.google.dev/gemini-api/docs/pricing",
        verified_on="2026-11-19",
    ),
}

_MILLION = Decimal("1000000")
_PRECISION = Decimal("0.00000001")


def _valid_token_count(value):
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    )


def get_rates(model):
    """Return rates for a known model name, otherwise None."""
    if not isinstance(model, str) or not model.strip():
        return None

    key = model.strip().lower()
    if key.startswith("models/"):
        key = key.removeprefix("models/")

    return PRICING.get(key)


def estimate_cost_usd(
    model,
    prompt_tokens,
    output_tokens,
    thinking_tokens=None,
):
    """Return a Decimal estimate, or None if required usage is unknown.

    Thinking tokens are billed at the output-token rate. An explicit zero is
    valid. None means the provider did not report the count, so no estimate
    is produced.
    """
    rates = get_rates(model)

    if rates is None:
        return None

    if not _valid_token_count(prompt_tokens):
        return None

    if not _valid_token_count(output_tokens):
        return None

    if not _valid_token_count(thinking_tokens):
        return None

    input_cost = (
        Decimal(prompt_tokens) * rates.input_per_million / _MILLION
    )
    billed_output = output_tokens + thinking_tokens
    output_cost = (
        Decimal(billed_output) * rates.output_per_million / _MILLION
    )

    return (input_cost + output_cost).quantize(
        _PRECISION,
        rounding=ROUND_HALF_UP,
    )
