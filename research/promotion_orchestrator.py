from __future__ import annotations

from typing import Any, Dict


def evaluate_and_decide(metrics: Dict[str, Any], thresholds: Dict[str, Any]) -> Dict[str, str]:
    if float(metrics.get("drawdown", 1.0)) > float(thresholds.get("max_drawdown", 0.2)):
        return {"action": "rollback", "reason": "drawdown_breach"}
    if float(metrics.get("reject_rate", 1.0)) > float(thresholds.get("max_reject_rate", 0.05)):
        return {"action": "rollback", "reason": "reject_rate_breach"}
    if float(metrics.get("slippage_bps", 999.0)) > float(thresholds.get("max_slippage_bps", 8.0)):
        return {"action": "rollback", "reason": "slippage_breach"}
    return {"action": "promote", "reason": "gate_pass"}
