from __future__ import annotations

from typing import Any, Dict, List


def _coerce_float_section(section: Any, keys: List[str]) -> tuple[bool, Dict[str, float]]:
    if not isinstance(section, dict):
        return False, {}

    out: Dict[str, float] = {}
    for key in keys:
        value = section.get(key)
        try:
            out[key] = float(value)
        except (TypeError, ValueError):
            return False, {}
    return True, out


def evaluate_promotion_gate(
    candidate: Dict[str, Any],
    baseline: Dict[str, Any],
    thresholds: Dict[str, Any],
) -> Dict[str, Any]:
    reasons: List[str] = []

    ok_candidate, candidate_vals = _coerce_float_section(
        candidate, ["score_total", "max_drawdown", "reject_rate", "slippage_bps"]
    )
    if not ok_candidate:
        reasons.append("invalid_candidate")

    ok_baseline, baseline_vals = _coerce_float_section(
        baseline, ["score_total", "max_drawdown", "reject_rate", "slippage_bps"]
    )
    if not ok_baseline:
        reasons.append("invalid_baseline")

    ok_thresholds, threshold_vals = _coerce_float_section(
        thresholds, ["max_dd_delta", "max_reject_rate", "max_slippage_bps"]
    )
    if not ok_thresholds:
        reasons.append("invalid_thresholds")

    if reasons:
        return {"pass": False, "reasons": reasons}

    if candidate_vals["score_total"] <= baseline_vals["score_total"]:
        reasons.append("score_not_improved")

    dd_delta = candidate_vals["max_drawdown"] - baseline_vals["max_drawdown"]
    if dd_delta > threshold_vals["max_dd_delta"]:
        reasons.append("drawdown_regression")

    if candidate_vals["reject_rate"] > threshold_vals["max_reject_rate"]:
        reasons.append("reject_rate_too_high")

    if candidate_vals["slippage_bps"] > threshold_vals["max_slippage_bps"]:
        reasons.append("slippage_too_high")

    return {"pass": len(reasons) == 0, "reasons": reasons}
