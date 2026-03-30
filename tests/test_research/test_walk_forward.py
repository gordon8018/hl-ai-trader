"""Tests for walk-forward validation."""
import pytest


def test_generate_walk_forward_windows():
    from research.backtest.walk_forward import generate_windows

    # 20 days of data
    timestamps = [f"2026-03-{d:02d}T10:00:00Z" for d in range(1, 21)]
    windows = generate_windows(timestamps, train_days=7, test_days=2, step_days=2)

    assert len(windows) > 0
    for train_range, test_range in windows:
        # Train period should be before test period
        assert train_range[1] <= test_range[0]


def test_walk_forward_runner():
    from research.backtest.walk_forward import WalkForwardRunner
    from research.backtest.engine import BacktestEngine, BarData
    from research.backtest.cost_model import CryptoPerpCostModel
    from research.backtest.strategies import SingleFactorStrategy
    import numpy as np

    np.random.seed(42)
    bars = []
    for i in range(500):
        bars.append(BarData(
            ts=f"2026-03-{1 + i // 96:02d}T{(i % 96) // 4:02d}:{(i % 4) * 15:02d}:00Z",
            symbol="BTC",
            mid_px=100.0 + np.cumsum(np.random.randn(1))[0] * 0.1,
            features={"book_imbalance_l5": np.random.randn()},
        ))

    runner = WalkForwardRunner(
        engine=BacktestEngine(cost_model=CryptoPerpCostModel()),
        train_days=3,
        test_days=1,
        step_days=1,
    )

    strategy_factory = lambda: SingleFactorStrategy(factor_name="book_imbalance_l5")
    result = runner.run(strategy_factory, bars)

    assert "composite_score" in result
    assert "n_windows" in result
    assert result["n_windows"] > 0
