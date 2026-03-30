"""Trading cost model for Hyperliquid perpetual futures."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CryptoPerpCostModel:
    """Depth-aware cost model with continuous funding charges."""

    taker_fee_bps: float = 3.5
    maker_fee_bps: float = 1.0
    slippage_base_bps: float = 1.0
    slippage_impact_coeff: float = 100.0  # notional/depth multiplier
    max_slippage_bps: float = 20.0
    funding_interval_hours: float = 8.0

    @property
    def _bars_per_funding(self) -> float:
        """Number of 15m bars per funding interval."""
        return self.funding_interval_hours * 4  # 4 bars per hour

    def trade_cost(self, notional: float, bar: Any) -> float:
        """Total cost for executing a trade: fee + slippage."""
        fee = abs(notional) * self.taker_fee_bps / 10000

        depth = getattr(bar, "top_depth_usd", 0)
        if depth > 0:
            impact_bps = self.slippage_base_bps + abs(notional) / depth * self.slippage_impact_coeff
        else:
            impact_bps = self.max_slippage_bps
        impact_bps = min(impact_bps, self.max_slippage_bps)
        slippage = abs(notional) * impact_bps / 10000

        return fee + slippage

    def funding_cost(self, position_notional: float, bar: Any) -> float:
        """Funding cost for holding a position through one 15m bar."""
        rate = getattr(bar, "funding_rate", 0)
        return abs(position_notional) * abs(rate) / self._bars_per_funding
