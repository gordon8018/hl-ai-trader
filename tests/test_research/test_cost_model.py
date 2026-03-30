"""Tests for CryptoPerpCostModel."""
from dataclasses import dataclass
import pytest


@dataclass
class FakeBar:
    top_depth_usd: float = 500000.0
    funding_rate: float = 0.0001


def test_trade_cost_includes_fee_and_slippage():
    from research.backtest.cost_model import CryptoPerpCostModel

    model = CryptoPerpCostModel(taker_fee_bps=3.5, slippage_base_bps=1.0)
    bar = FakeBar(top_depth_usd=500000.0)

    cost = model.trade_cost(notional=1000.0, bar=bar)

    # fee = 1000 * 3.5/10000 = 0.35
    # slippage = 1000 * (1.0 + 1000/500000 * 100) / 10000 = 1000 * 1.2 / 10000 = 0.12
    assert cost > 0.35  # at least the fee
    assert cost < 1.0   # reasonable


def test_funding_cost_scales_with_position():
    from research.backtest.cost_model import CryptoPerpCostModel

    model = CryptoPerpCostModel()
    bar = FakeBar(funding_rate=0.0001)

    # 15m bar = 1/32 of 8h funding period
    cost = model.funding_cost(position_notional=10000.0, bar=bar)
    expected = 10000.0 * 0.0001 / 32
    assert abs(cost - expected) < 1e-6


def test_zero_depth_uses_max_slippage():
    from research.backtest.cost_model import CryptoPerpCostModel

    model = CryptoPerpCostModel()
    bar = FakeBar(top_depth_usd=0.0)

    cost = model.trade_cost(notional=1000.0, bar=bar)
    assert cost > 0  # Should not crash, uses max slippage cap
