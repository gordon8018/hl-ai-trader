"""CLI: Run backtests with different strategies."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import polars as pl


def load_bars_from_parquet(data_dir: Path, timeframe: str = "15m"):
    """Load Parquet files and convert to BarData sequence."""
    from research.backtest.engine import BarData

    feature_dir = data_dir / f"features_{timeframe}"
    files = sorted(feature_dir.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No parquet files in {feature_dir}")

    df = pl.concat([pl.read_parquet(f) for f in files]).sort(["ts", "symbol"])

    bars = []
    for row in df.iter_rows(named=True):
        features = {k: v for k, v in row.items()
                    if k not in ("ts", "symbol", "mid_px", "top_depth_usd", "funding_rate")
                    and v is not None and isinstance(v, (int, float))}
        bars.append(BarData(
            ts=row["ts"],
            symbol=row["symbol"],
            mid_px=row.get("mid_px", 0.0) or 0.0,
            top_depth_usd=row.get("top_depth_usd", 500000.0) or 500000.0,
            funding_rate=row.get("funding_rate", 0.0) or 0.0,
            features=features,
        ))
    return bars


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run backtest")
    parser.add_argument("--strategy", required=True,
                        choices=["rule_based", "single_factor", "weighted_factor"],
                        help="Strategy type")
    parser.add_argument("--factor", help="Factor name (for single_factor)")
    parser.add_argument("--factors", help="Factor:weight pairs (for weighted_factor)")
    parser.add_argument("--data-dir", default="research/data/parquet")
    parser.add_argument("--output-dir", default="research/reports/backtest")
    parser.add_argument("--initial-capital", type=float, default=10000.0)
    parser.add_argument("--walk-forward", help="Walk-forward params: train=7d,test=2d")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    bars = load_bars_from_parquet(data_dir)
    print(f"Loaded {len(bars)} bars")

    # Build strategy
    if args.strategy == "rule_based":
        from research.backtest.rule_based_strategy import RuleBasedStrategy
        strategy = RuleBasedStrategy()
    elif args.strategy == "single_factor":
        from research.backtest.strategies import SingleFactorStrategy
        strategy = SingleFactorStrategy(factor_name=args.factor)
    elif args.strategy == "weighted_factor":
        factor_weights = {}
        for pair in args.factors.split(","):
            name, weight = pair.split(":")
            factor_weights[name.strip()] = float(weight)
        from research.backtest.strategies import WeightedFactorStrategy
        strategy = WeightedFactorStrategy(factor_weights=factor_weights)
    else:
        raise ValueError(f"Unknown strategy: {args.strategy}")

    # Run backtest
    from research.backtest.engine import BacktestEngine
    from research.backtest.cost_model import CryptoPerpCostModel
    from research.backtest.scorer import Scorer

    engine = BacktestEngine(cost_model=CryptoPerpCostModel())
    result = engine.run(strategy=strategy, bars=bars, initial_capital=args.initial_capital)

    # Score
    scorer = Scorer()
    metrics = scorer.score(result.equity_curve, result.trades)

    # Save results
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output_dir) / f"{args.strategy}_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "summary.json", "w") as f:
        json.dump(metrics, f, indent=2)

    eq_df = pl.DataFrame({"equity": result.equity_curve})
    eq_df.write_parquet(out_dir / "equity_curve.parquet")

    if len(result.trades) > 0:
        result.trades.write_parquet(out_dir / "trades.parquet")

    print(f"\nResults saved to {out_dir}/")
    print(f"  Composite Score: {metrics['composite_score']:.1f}/100")
    print(f"  Sharpe: {metrics['sharpe']:.2f}")
    print(f"  Max Drawdown: {metrics['max_drawdown']:.2%}")
    print(f"  Win Rate: {metrics['win_rate']:.2%}")
    print(f"  Net PnL: {metrics['net_pnl_pct']:.2%}")


if __name__ == "__main__":
    main()
