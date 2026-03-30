"""Experiment evaluation for parameter optimization."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CryptoEvaluator:
    """Evaluate backtest results against quality thresholds."""

    thresholds: dict[str, Any] = field(default_factory=lambda: {
        "sharpe": 1.0,
        "max_drawdown": 0.10,
        "win_rate": 0.45,
        "profit_factor": 1.3,
        "net_pnl_positive": True,
    })

    def evaluate(self, result: dict[str, Any]) -> dict[str, Any]:
        """Evaluate a backtest result. Returns result with is_good flag."""
        checks_passed = 0
        total_checks = len(self.thresholds)

        if result.get("sharpe", 0) >= self.thresholds["sharpe"]:
            checks_passed += 1
        if result.get("max_drawdown", 1) <= self.thresholds["max_drawdown"]:
            checks_passed += 1
        if result.get("win_rate", 0) >= self.thresholds["win_rate"]:
            checks_passed += 1
        if result.get("profit_factor", 0) >= self.thresholds["profit_factor"]:
            checks_passed += 1
        if result.get("net_pnl_pct", 0) > 0:
            checks_passed += 1

        result["is_good"] = checks_passed >= 4  # at least 4/5
        result["quality_score"] = result.get("sharpe", 0) - 2.0 * result.get("max_drawdown", 0)
        result["checks_passed"] = checks_passed
        return result
