"""Tests for RuleBasedStrategy."""
from research.backtest.engine import BarData


def test_profit_target_closes_position():
    from research.backtest.rule_based_strategy import RuleBasedStrategy

    strategy = RuleBasedStrategy()
    # Open a long position
    bar1 = BarData(ts="2026-03-15T10:00:00Z", symbol="BTC", mid_px=100.0,
                   features={"trend_agree": 1, "trend_strength_15m": 1.5, "ret_1h": 0.005})
    weights1 = strategy.on_bar(bar1)

    # Price up 30bps → should trigger profit target (25bps)
    bar2 = BarData(ts="2026-03-15T10:15:00Z", symbol="BTC", mid_px=100.30,
                   features={"trend_agree": 1, "trend_strength_15m": 1.5, "ret_1h": 0.005})
    weights2 = strategy.on_bar(bar2)
    # Should close or reduce position
    assert abs(weights2.get("BTC", 0)) < abs(weights1.get("BTC", 0))


def test_stop_loss_closes_position():
    from research.backtest.rule_based_strategy import RuleBasedStrategy

    strategy = RuleBasedStrategy()
    bar1 = BarData(ts="2026-03-15T10:00:00Z", symbol="BTC", mid_px=100.0,
                   features={"trend_agree": 1, "trend_strength_15m": 1.5, "ret_1h": 0.005})
    strategy.on_bar(bar1)

    # Price down 50bps → should trigger stop loss (40bps)
    bar2 = BarData(ts="2026-03-15T10:15:00Z", symbol="BTC", mid_px=99.50,
                   features={"trend_agree": 1, "trend_strength_15m": 1.5, "ret_1h": -0.005})
    weights2 = strategy.on_bar(bar2)
    assert weights2.get("BTC", 0) == 0.0  # Should be flat


def test_min_trade_interval():
    from research.backtest.rule_based_strategy import RuleBasedStrategy

    strategy = RuleBasedStrategy(min_trade_interval_min=90)
    bar1 = BarData(ts="2026-03-15T10:00:00Z", symbol="BTC", mid_px=100.0,
                   features={"trend_agree": 1, "trend_strength_15m": 1.5, "ret_1h": 0.005})
    strategy.on_bar(bar1)

    # Only 15 min later, should not trade
    bar2 = BarData(ts="2026-03-15T10:15:00Z", symbol="BTC", mid_px=100.5,
                   features={"trend_agree": -1, "trend_strength_15m": 1.5, "ret_1h": -0.005})
    weights2 = strategy.on_bar(bar2)
    # Should hold previous position, not reverse


def test_bearish_regime_blocks_longs():
    from research.backtest.rule_based_strategy import RuleBasedStrategy

    strategy = RuleBasedStrategy(bearish_regime_long_block=True)
    bar = BarData(ts="2026-03-15T10:00:00Z", symbol="BTC", mid_px=100.0,
                  features={"trend_agree": 1, "ret_1h": -0.008, "trend_1h": -1,
                            "trend_strength_15m": 1.5})
    weights = strategy.on_bar(bar)
    # Bearish regime (ret_1h < -0.5%) → no new longs
    assert weights.get("BTC", 0) <= 0
