"""Gemini model pricing data for cost estimation.

Prices are in USD per 1 million tokens, sourced from:
https://ai.google.dev/gemini-api/docs/pricing
"""
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """Pricing info for a single Gemini model (USD per 1M tokens)."""

    input_price: float
    output_price: float


# Pricing per 1M tokens (standard tier, prompts <= 200k tokens).
# Updated from https://ai.google.dev/gemini-api/docs/pricing – Feb 2026.
MODEL_PRICING_MAP: Final[dict[str, ModelPricing]] = {
    # Gemini 3
    "gemini-3-pro":             ModelPricing(input_price=2.00,  output_price=12.00),
    "gemini-3-flash":           ModelPricing(input_price=0.50,  output_price=3.00),
    # Gemini 2.5
    "gemini-2.5-pro":           ModelPricing(input_price=1.25,  output_price=10.00),
    "gemini-2.5-pro-preview":   ModelPricing(input_price=1.25,  output_price=10.00),
    "gemini-2.5-flash":         ModelPricing(input_price=0.30,  output_price=2.50),
    "gemini-2.5-flash-preview": ModelPricing(input_price=0.30,  output_price=2.50),
    "gemini-2.5-flash-lite":    ModelPricing(input_price=0.10,  output_price=0.40),
    # Gemini 2.0
    "gemini-2.0-flash":         ModelPricing(input_price=0.10,  output_price=0.40),
    "gemini-2.0-flash-lite":    ModelPricing(input_price=0.10,  output_price=0.40),
}

# Fallback pricing when the model name isn't in the map.
DEFAULT_PRICING: Final[ModelPricing] = ModelPricing(input_price=0.50, output_price=3.00)
