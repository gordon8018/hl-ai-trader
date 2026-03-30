"""Tests for SingleFactorStrategy and WeightedFactorStrategy."""
from research.backtest.engine import BarData


def test_single_factor_strategy_goes_long_on_positive():
    from research.backtest.strategies import SingleFactorStrategy

    strategy = SingleFactorStrategy(
        factor_name="book_imbalance_l5",
        long_threshold=0.5,
        short_threshold=-0.5,
        max_weight=0.3,
    )

    bar = BarData(ts="T1", symbol="BTC", mid_px=100.0,
                  features={"book_imbalance_l5": 0.8})
    weights = strategy.on_bar(bar)
    assert weights["BTC"] > 0  # Should go long


def test_single_factor_strategy_goes_short_on_negative():
    from research.backtest.strategies import SingleFactorStrategy

    strategy = SingleFactorStrategy(
        factor_name="book_imbalance_l5",
        long_threshold=0.5,
        short_threshold=-0.5,
        max_weight=0.3,
    )

    bar = BarData(ts="T1", symbol="BTC", mid_px=100.0,
                  features={"book_imbalance_l5": -0.8})
    weights = strategy.on_bar(bar)
    assert weights["BTC"] < 0  # Should go short


def test_weighted_factor_strategy():
    from research.backtest.strategies import WeightedFactorStrategy

    strategy = WeightedFactorStrategy(
        factor_weights={"book_imbalance_l5": 0.6, "aggr_delta_5m": 0.4},
        long_threshold=0.3,
        short_threshold=-0.3,
        max_weight=0.2,
    )

    bar = BarData(ts="T1", symbol="BTC", mid_px=100.0,
                  features={"book_imbalance_l5": 0.8, "aggr_delta_5m": 0.5})
    weights = strategy.on_bar(bar)
    assert weights["BTC"] > 0  # Both factors positive → long
