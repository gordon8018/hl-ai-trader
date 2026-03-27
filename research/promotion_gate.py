from __future__ import annotations

from typing import Any, Dict, List


def evaluate_promotion_gate(
    candidate: Dict[str, Any],
    baseline: Dict[str, Any],
    thresholds: Dict[str, Any],
) -> Dict[str, Any]:
    reasons: List[str] = []

    if float(candidate["score_total"]) <= float(baseline["score_total"]):
        reasons.append("score_not_improved")

    dd_delta = float(candidate["max_drawdown"]) - float(baseline["max_drawdown"])
    if dd_delta > float(thresholds["max_dd_delta"]):
        reasons.append("drawdown_regression")

    if float(candidate["reject_rate"]) > float(thresholds["max_reject_rate"]):
        reasons.append("reject_rate_too_high")

    if float(candidate["slippage_bps"]) > float(thresholds["max_slippage_bps"]):
        reasons.append("slippage_too_high")

    return {"pass": len(reasons) == 0, "reasons": reasons}
