"""Comprehensive strategy scoring with weighted composite metrics."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
import polars as pl


def compute_sharpe(returns: np.ndarray | Sequence[float], bars_per_day: int = 96, risk_free: float = 0.0) -> float:
    """Annualized Sharpe ratio. 96 bars/day = 15-min bars × 24h."""
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    if len(r) < 2:
        return 0.0
    std_r = np.std(r)
    if std_r < 1e-12:
        # Zero volatility: cap at large positive if mean > 0, else 0
        return 10.0 if np.mean(r) > 0 else 0.0
    excess = r - risk_free / (bars_per_day * 365)
    return float(np.mean(excess) / std_r * np.sqrt(bars_per_day * 365))


def compute_sortino(returns: np.ndarray | Sequence[float], bars_per_day: int = 96) -> float:
    """Annualized Sortino ratio (downside deviation only)."""
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    if len(r) < 2:
        return 0.0
    downside = r[r < 0]
    if len(downside) < 1 or np.std(downside) < 1e-12:
        return 0.0 if np.mean(r) <= 0 else 10.0  # cap
    return float(np.mean(r) / np.std(downside) * np.sqrt(bars_per_day * 365))


def compute_max_drawdown(equity: Sequence[float]) -> float:
    """Maximum drawdown as a fraction (0 to 1)."""
    eq = np.asarray(equity, dtype=float)
    if len(eq) < 2:
        return 0.0
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / np.where(peak > 0, peak, 1.0)
    return float(np.max(dd))


def compute_calmar(returns: np.ndarray | Sequence[float], equity: Sequence[float], bars_per_day: int = 96) -> float:
    """Calmar ratio: annualized return / max drawdown."""
    r = np.asarray(returns, dtype=float)
    ann_ret = float(np.mean(r) * bars_per_day * 365)
    mdd = compute_max_drawdown(equity)
    if mdd < 1e-10:
        return 0.0 if ann_ret <= 0 else 10.0
    return min(float(ann_ret / mdd), 10.0)


@dataclass
class Scorer:
    """Weighted composite scoring system for strategy evaluation."""

    weights: dict[str, float] = field(default_factory=lambda: {
        "sharpe": 0.25,
        "sortino": 0.15,
        "calmar": 0.10,
        "win_rate": 0.15,
        "profit_factor": 0.15,
        "net_pnl": 0.10,
        "max_drawdown": 0.10,
    })

    def score(self, equity_curve: Sequence[float], trades: pl.DataFrame) -> dict[str, float]:
        """Compute all metrics and weighted composite score (0-100)."""
        eq = np.asarray(equity_curve, dtype=float)
        returns = np.diff(eq) / eq[:-1] if len(eq) > 1 else np.array([])

        # Individual metrics
        sharpe = compute_sharpe(returns)
        sortino = compute_sortino(returns)
        mdd = compute_max_drawdown(equity_curve)
        calmar = compute_calmar(returns, equity_curve)

        # Trade-level metrics
        pnl_col = trades["pnl"].to_numpy() if "pnl" in trades.columns else np.array([])
        win_rate = float(np.mean(pnl_col > 0)) if len(pnl_col) > 0 else 0.0

        gross_profit = float(np.sum(pnl_col[pnl_col > 0])) if len(pnl_col) > 0 else 0.0
        gross_loss = abs(float(np.sum(pnl_col[pnl_col < 0]))) if len(pnl_col) > 0 else 1.0
        profit_factor = gross_profit / gross_loss if gross_loss > 1e-10 else 0.0

        net_pnl = float(eq[-1] - eq[0]) / eq[0] if len(eq) > 1 and eq[0] > 0 else 0.0

        # Normalize to 0-100
        normalized = {
            "sharpe": np.clip(sharpe / 3.0 * 100, 0, 100),        # 3.0 → 100
            "sortino": np.clip(sortino / 4.0 * 100, 0, 100),      # 4.0 → 100
            "calmar": np.clip(calmar / 5.0 * 100, 0, 100),        # 5.0 → 100
            "win_rate": win_rate * 100,                             # 1.0 → 100
            "profit_factor": np.clip(profit_factor / 3.0 * 100, 0, 100),  # 3.0 → 100
            "net_pnl": np.clip((net_pnl + 0.5) / 1.0 * 100, 0, 100),     # +50% → 100
            "max_drawdown": np.clip((1 - mdd / 0.5) * 100, 0, 100),       # 0%→100, 50%→0
        }

        composite = sum(normalized[k] * self.weights[k] for k in self.weights)

        return {
            "composite_score": float(composite),
            "sharpe": sharpe,
            "sortino": sortino,
            "calmar": calmar,
            "max_drawdown": mdd,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "net_pnl_pct": net_pnl,
            "total_trades": len(pnl_col),
        }
