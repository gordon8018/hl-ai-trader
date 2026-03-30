"""Integration test: export synthetic data → evaluate factors → run backtest."""
import numpy as np
import polars as pl
import pytest
from pathlib import Path


@pytest.fixture
def research_dir(tmp_path):
    """Create synthetic 15m feature data."""
    features_dir = tmp_path / "features_15m"
    features_dir.mkdir(parents=True)

    np.random.seed(42)
    n_bars = 200
    total = n_bars * 2  # 2 symbols

    symbols = ["BTC", "ETH"] * n_bars
    ts = []
    for i in range(n_bars):
        for s in ["BTC", "ETH"]:
            ts.append(f"2026-03-{15 + i // 96:02d}T{(i % 96) // 4:02d}:{(i % 4) * 15:02d}:00Z")

    base_btc = 65000 + np.cumsum(np.random.randn(n_bars) * 50)
    base_eth = 3200 + np.cumsum(np.random.randn(n_bars) * 25)
    mid_px = []
    for i in range(n_bars):
        mid_px.append(base_btc[i])
        mid_px.append(base_eth[i])

    df = pl.DataFrame({
        "ts": ts,
        "symbol": symbols,
        "mid_px": mid_px,
        "book_imbalance_l5": (np.random.randn(total) * 0.3).tolist(),
        "spread_bps": (np.random.rand(total) * 5).tolist(),
        "funding_rate": (np.random.randn(total) * 0.0001).tolist(),
        "top_depth_usd": (np.random.rand(total) * 1000000 + 100000).tolist(),
        "aggr_delta_5m": (np.random.randn(total) * 100).tolist(),
    })

    from research.data.exporter import build_forward_returns
    df = build_forward_returns(df, horizons=[1, 2, 4])
    df.write_parquet(features_dir / "2026-03-15.parquet")
    return tmp_path


def test_factor_evaluation_pipeline(research_dir):
    from research.factors.evaluator import evaluate_all_factors

    df = pl.read_parquet(research_dir / "features_15m" / "2026-03-15.parquet")
    summary = evaluate_all_factors(
        df,
        factor_cols=["book_imbalance_l5", "spread_bps", "aggr_delta_5m"],
        return_cols=["ret_fwd_15m", "ret_fwd_30m", "ret_fwd_1h"],
    )
    assert len(summary) == 9  # 3 factors × 3 horizons
    assert "icir" in summary.columns


def test_backtest_pipeline(research_dir):
    from research.scripts.run_backtest import load_bars_from_parquet
    from research.backtest.engine import BacktestEngine
    from research.backtest.cost_model import CryptoPerpCostModel
    from research.backtest.strategies import SingleFactorStrategy
    from research.backtest.scorer import Scorer

    bars = load_bars_from_parquet(research_dir)
    assert len(bars) > 0

    strategy = SingleFactorStrategy(factor_name="book_imbalance_l5")
    engine = BacktestEngine(cost_model=CryptoPerpCostModel())
    result = engine.run(strategy=strategy, bars=bars, initial_capital=10000.0)

    assert len(result.equity_curve) == len(bars)

    scorer = Scorer()
    metrics = scorer.score(result.equity_curve, result.trades)
    assert "composite_score" in metrics
    assert 0 <= metrics["composite_score"] <= 100
