"""Tests for strategy scorer."""
import numpy as np
import polars as pl
import pytest


def test_compute_sharpe():
    from research.backtest.scorer import compute_sharpe

    # Returns: 1% per bar, no volatility → high sharpe
    returns = np.ones(100) * 0.01
    sharpe = compute_sharpe(returns, bars_per_day=96)
    assert sharpe > 5.0  # Very high sharpe with no vol


def test_compute_max_drawdown():
    from research.backtest.scorer import compute_max_drawdown

    equity = [100, 105, 103, 108, 95, 110, 100]
    dd = compute_max_drawdown(equity)
    # Max DD: from 108 to 95 = (108-95)/108 ≈ 0.1204
    assert abs(dd - (108 - 95) / 108) < 0.01


def test_scorer_produces_0_to_100():
    from research.backtest.scorer import Scorer

    equity_curve = np.cumsum(np.random.randn(500) * 0.001) + 100
    equity_curve = equity_curve.tolist()
    trades = pl.DataFrame({
        "pnl": np.random.randn(50).tolist(),
    })

    scorer = Scorer()
    result = scorer.score(equity_curve, trades)

    assert "composite_score" in result
    assert 0 <= result["composite_score"] <= 100
    assert "sharpe" in result
    assert "max_drawdown" in result
    assert "win_rate" in result
