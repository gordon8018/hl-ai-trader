from __future__ import annotations

from numbers import Real
from typing import Any, Dict


def _coerce_numeric(value: Any, default: float | None = None) -> tuple[bool, float]:
    if isinstance(value, bool):
        return False, 0.0
    if isinstance(value, Real):
        return True, float(value)
    try:
        return True, float(value)
    except (TypeError, ValueError):
        if default is None:
            return False, 0.0
        return True, default


def evaluate_and_decide(metrics: Dict[str, Any], thresholds: Dict[str, Any]) -> Dict[str, str]:
    ok_drawdown, drawdown = _coerce_numeric(metrics.get("drawdown"), None)
    ok_reject_rate, reject_rate = _coerce_numeric(metrics.get("reject_rate"), None)
    ok_slippage, slippage_bps = _coerce_numeric(metrics.get("slippage_bps"), None)
    ok_max_drawdown, max_drawdown = _coerce_numeric(thresholds.get("max_drawdown"), 0.2)
    ok_max_reject_rate, max_reject_rate = _coerce_numeric(thresholds.get("max_reject_rate"), 0.05)
    ok_max_slippage_bps, max_slippage_bps = _coerce_numeric(thresholds.get("max_slippage_bps"), 8.0)

    if not all(
        [
            ok_drawdown,
            ok_reject_rate,
            ok_slippage,
            ok_max_drawdown,
            ok_max_reject_rate,
            ok_max_slippage_bps,
        ]
    ):
        return {"action": "rollback", "reason": "invalid_input"}

    if drawdown > max_drawdown:
        return {"action": "rollback", "reason": "drawdown_breach"}
    if reject_rate > max_reject_rate:
        return {"action": "rollback", "reason": "reject_rate_breach"}
    if slippage_bps > max_slippage_bps:
        return {"action": "rollback", "reason": "slippage_breach"}
    return {"action": "promote", "reason": "gate_pass"}
