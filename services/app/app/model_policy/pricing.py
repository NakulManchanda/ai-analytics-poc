"""Versioned price table. Costs are estimates unless the proxy reports a billed cost."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

PRICING_VERSION = "2026-10-bedrock-nova-v2"
# USD per million (input, output) tokens, Bedrock on-demand (matches LiteLLM's model cost map).
MODEL_RATES: Mapping[str, tuple[float, float]] = MappingProxyType(
    {
        "amazon.nova-micro-v1:0": (0.035, 0.140),
        "amazon.nova-pro-v1:0": (0.80, 3.20),
    }
)
# Which Bedrock model each proxy alias targets. Must equal `model_list` in
# infra/litellm/config.yaml (the deployed proxy's config too): tests/test_litellm_config_pricing.py
# fails when the two drift, so remapping an alias cannot silently misprice it.
ALIAS_MODELS: Mapping[str, str] = MappingProxyType(
    {
        "taxi-classifier": "amazon.nova-micro-v1:0",
        "taxi-planner": "amazon.nova-pro-v1:0",
        "taxi-answer": "amazon.nova-micro-v1:0",
    }
)


def _rate(alias: str) -> tuple[float, float] | None:
    """Rate for a mapped alias, or for an alias that is itself a priced model id (the direct
    Bedrock path). Anything else is unpriced: no guess, so a configured USD cap fails closed.
    """
    return MODEL_RATES.get(ALIAS_MODELS.get(alias, alias))


@dataclass(frozen=True)
class PricingTable:
    version: str
    rates: Mapping[
        str, tuple[float, float]
    ]  # alias -> (input, output) USD per 1M tokens

    def estimate(
        self, alias: str, input_tokens: int, output_tokens: int
    ) -> float | None:
        """Estimated USD, or None when the alias is not priced (never a silent 0)."""
        rate = self.rates.get(alias)
        if rate is None:
            return None
        return (input_tokens * rate[0] + output_tokens * rate[1]) / 1_000_000


def default_pricing(aliases: tuple[str, ...]) -> PricingTable:
    return PricingTable(
        PRICING_VERSION,
        MappingProxyType({a: rate for a in aliases if (rate := _rate(a)) is not None}),
    )
