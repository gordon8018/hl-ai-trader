from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4
from pathlib import Path
from typing import Any, Dict

from research.candidate_pack import write_candidate_pack
from research.strategy_score import evaluate_candidate


def _coerce_threshold(value: Any, default: float = 0.05) -> float:
    try:
        if isinstance(value, bool):
            raise TypeError
        return float(value)
    except (TypeError, ValueError):
        return default


def _propose_params(baseline: Dict[str, Any]) -> Dict[str, Any]:
    proposed = dict(baseline)
    current = _coerce_threshold(proposed.get("AI_SIGNAL_DELTA_THRESHOLD", 0.05))
    proposed["AI_SIGNAL_DELTA_THRESHOLD"] = max(0.02, round(current - 0.01, 4))
    return proposed


def run_one_iteration(
    output_root: Path,
    baseline_params: Dict[str, Any],
    observed_metrics: Dict[str, Any],
) -> Dict[str, Any]:
    candidate_params = _propose_params(baseline_params)
    score = evaluate_candidate(observed_metrics)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    profile_name = f"V9_ar_{stamp}_{uuid4().hex[:8]}"
    pack_dir = write_candidate_pack(
        output_dir=output_root,
        profile_name=profile_name,
        candidate_params=candidate_params,
        metrics=score,
        baseline_params=baseline_params,
        risk_notes="manual approval required before promotion",
    )
    return {"profile_name": profile_name, "pack_dir": str(pack_dir), "score": score}
