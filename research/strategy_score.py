from __future__ import annotations

from math import sqrt
from numbers import Real
from statistics import mean, pstdev
from typing import Any, Dict, List


def _sharpe(daily_returns: List[float]) -> float:
    if not daily_returns:
        return 0.0
    sigma = pstdev(daily_returns) if len(daily_returns) > 1 else 0.0
    if sigma <= 1e-12:
        return 0.0
    return (mean(daily_returns) / sigma) * sqrt(252.0)


def _normalize_daily_returns(daily_returns: Any) -> List[float]:
    if isinstance(daily_returns, (str, bytes, bytearray, dict)):
        return []
    if not hasattr(daily_returns, "__iter__"):
        return []

    out: List[float] = []
    for item in daily_returns:
        if isinstance(item, Real) and not isinstance(item, bool):
            out.append(float(item))
    return out


def _coerce_scalar(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, Real):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def evaluate_candidate(metrics: Dict[str, Any]) -> Dict[str, float]:
    returns = _normalize_daily_returns(metrics.get("daily_returns", []))

    max_drawdown = abs(_coerce_scalar(metrics.get("max_drawdown", 1.0), 1.0))
    reject_rate = _coerce_scalar(metrics.get("reject_rate", 1.0), 1.0)
    slippage_bps = _coerce_scalar(metrics.get("slippage_bps", 999.0), 999.0)

    sharpe = _sharpe(returns)
    calmar = (mean(returns) * 252.0) / max(max_drawdown, 1e-6) if returns else 0.0
    penalty_dd = max_drawdown * 3.0
    penalty_exec = reject_rate * 2.0 + (slippage_bps / 100.0)
    score_total = sharpe + calmar - penalty_dd - penalty_exec

    return {
        "score_total": score_total,
        "score_sharpe": sharpe,
        "score_calmar": calmar,
        "penalty_dd": penalty_dd,
        "penalty_exec": penalty_exec,
    }
