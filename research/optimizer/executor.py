"""Experiment executor: runs backtests with given parameters."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research.backtest.cost_model import CryptoPerpCostModel
from research.backtest.engine import BacktestEngine
from research.backtest.scorer import Scorer


@dataclass
class ExperimentExecutor:
    """Execute backtest experiments in-process."""

    data_dir: str = "research/data/parquet"
    num_workers: int = 1  # Reserved for future parallel execution
    _bars: list | None = None
    _bars_loaded: bool = False

    def _ensure_bars(self) -> list:
        """Lazy-load bars from parquet (cached)."""
        if not self._bars_loaded:
            from research.scripts.run_backtest import load_bars_from_parquet
            try:
                self._bars = load_bars_from_parquet(Path(self.data_dir))
                self._bars_loaded = True
            except FileNotFoundError:
                self._bars = []
                self._bars_loaded = True
        return self._bars or []

    def run_single(self, params: dict[str, Any]) -> dict[str, Any]:
        """Run a single backtest with given parameters and return metrics."""
        bars = self._ensure_bars()
        if not bars:
            return {"status": "error", "error": "No data available"}

        start = time.time()

        try:
            # Build strategy from params
            strategy = self._build_strategy(params)

            # Build cost model with params overrides
            cost_model = CryptoPerpCostModel()

            # Run backtest
            engine = BacktestEngine(cost_model=cost_model)
            result = engine.run(strategy=strategy, bars=bars, initial_capital=10000.0)

            # Score
            scorer = Scorer()
            metrics = scorer.score(result.equity_curve, result.trades)
            metrics["status"] = "ok"
            metrics["elapsed_s"] = round(time.time() - start, 2)
            metrics["params"] = params

            return metrics

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "elapsed_s": round(time.time() - start, 2),
                "params": params,
                "sharpe": 0.0,
                "max_drawdown": 0.0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "net_pnl_pct": 0.0,
                "composite_score": 0.0,
            }

    def run_batch(self, params_batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Run a batch of experiments sequentially."""
        return [self.run_single(params) for params in params_batch]

    def _build_strategy(self, params: dict[str, Any]):
        """Build a strategy object from params dict."""
        strategy_type = params.get("strategy_type", "weighted_factor")

        if strategy_type == "single_factor":
            from research.backtest.strategies import SingleFactorStrategy
            return SingleFactorStrategy(
                factor_name=params.get("factor_name", "book_imbalance_l5"),
                long_threshold=params.get("confidence_threshold", 0.5),
                short_threshold=-params.get("confidence_threshold", 0.5),
                max_weight=params.get("max_gross", 0.3),
            )
        elif strategy_type == "rule_based":
            from research.backtest.rule_based_strategy import RuleBasedStrategy
            return RuleBasedStrategy(
                profit_target_bps=params.get("profit_target_bps", 25.0),
                stop_loss_bps=params.get("stop_loss_bps", 40.0),
                min_trade_interval_min=int(params.get("min_trade_interval_min", 90)),
                max_trades_per_day=int(params.get("max_trades_per_day", 15)),
                position_max_age_min=int(params.get("position_max_age_min", 30)),
                max_weight=params.get("max_gross", 0.3),
                bearish_regime_long_block=params.get("bearish_regime_long_block", True),
                bearish_ret_1h_threshold=params.get("bearish_ret_1h_threshold", -0.005),
                trending_strength_threshold=params.get("trending_strength_threshold", 1.0),
            )
        else:  # weighted_factor
            from research.backtest.strategies import WeightedFactorStrategy
            factor_weights = params.get("factor_weights", {"book_imbalance_l5": 0.5, "aggr_delta_5m": 0.5})
            return WeightedFactorStrategy(
                factor_weights=factor_weights,
                long_threshold=params.get("confidence_threshold", 0.3),
                short_threshold=-params.get("confidence_threshold", 0.3),
                max_weight=params.get("max_gross", 0.2),
            )
