"""Versioned price table. Costs are estimates unless the proxy reports a billed cost."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

PRICING_VERSION = "2026-10-bedrock-nova-micro-v1"
# USD per million (input, output) tokens; Nova Micro on-demand, same rates as bedrock_budget.
_NOVA_MICRO = (0.035, 0.140)


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
        PRICING_VERSION, MappingProxyType({a: _NOVA_MICRO for a in aliases})
    )
