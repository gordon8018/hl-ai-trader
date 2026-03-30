"""Tests for the backtest engine."""
import polars as pl
import numpy as np
import pytest


def test_backtest_engine_basic():
    """Engine should run a simple strategy and produce equity curve + trades."""
    from research.backtest.engine import BacktestEngine, BarData
    from research.backtest.cost_model import CryptoPerpCostModel

    # Create simple price data: BTC goes up
    bars = []
    for i in range(20):
        bars.append(BarData(
            ts=f"2026-03-15T{10 + i // 4:02d}:{(i % 4) * 15:02d}:00Z",
            symbol="BTC",
            mid_px=100.0 + i * 0.5,
            top_depth_usd=500000.0,
            funding_rate=0.0001,
            features={},
        ))

    class AlwaysLong:
        def on_bar(self, bar):
            return {"BTC": 0.5}  # 50% long BTC

    engine = BacktestEngine(cost_model=CryptoPerpCostModel())
    result = engine.run(
        strategy=AlwaysLong(),
        bars=bars,
        initial_capital=10000.0,
    )

    assert len(result.equity_curve) == 20
    assert result.equity_curve[-1] > result.equity_curve[0]  # Should profit
    assert len(result.trades) > 0  # At least the initial entry


def test_backtest_engine_costs_reduce_pnl():
    """With high fees, the strategy should perform worse."""
    from research.backtest.engine import BacktestEngine, BarData
    from research.backtest.cost_model import CryptoPerpCostModel

    bars = []
    for i in range(20):
        bars.append(BarData(
            ts=f"T{i}",
            symbol="BTC",
            mid_px=100.0,  # flat price
            top_depth_usd=500000.0,
            funding_rate=0.0001,
            features={},
        ))

    class Flipper:
        """Flip position every bar to generate maximum trading costs."""
        def __init__(self):
            self.long = True
        def on_bar(self, bar):
            self.long = not self.long
            return {"BTC": 0.5 if self.long else -0.5}

    engine = BacktestEngine(cost_model=CryptoPerpCostModel(taker_fee_bps=10.0))
    result = engine.run(strategy=Flipper(), bars=bars, initial_capital=10000.0)

    # Flat price + high fees + high turnover → loss
    assert result.equity_curve[-1] < 10000.0
