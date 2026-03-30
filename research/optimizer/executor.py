"""Experiment executor for parameter optimization (stub)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ExperimentExecutor:
    """Execute backtest experiments with given parameters.

    Currently a stub. Will be upgraded to use subprocess calls to
    research/scripts/run_backtest.py with multi-worker parallelism.
    """

    data_dir: str = "research/data/parquet"
    num_workers: int = 2

    def run_batch(self, params_batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Run a batch of experiments. Returns list of result dicts."""
        results = []
        for params in params_batch:
            # Stub: return empty result. Real implementation will call run_backtest.py
            results.append({
                "sharpe": 0.0,
                "max_drawdown": 0.0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "net_pnl_pct": 0.0,
                "composite_score": 0.0,
            })
        return results
