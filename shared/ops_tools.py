from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

from shared.schemas import Envelope, current_cycle_id

VALID_CTL_COMMANDS: Set[str] = {"HALT", "REDUCE_ONLY", "RESUME"}


def normalize_ctl_command(cmd: str) -> str:
    cmd_u = str(cmd).upper().strip()
    if cmd_u not in VALID_CTL_COMMANDS:
        raise ValueError(f"unsupported ctl command: {cmd}")
    return cmd_u


def build_ctl_message(cmd: str, reason: str = "", source: str = "ops.manual") -> Dict[str, Any]:
    cmd_u = normalize_ctl_command(cmd)
    env = Envelope(source=source, cycle_id=current_cycle_id())
    return {
        "env": env.model_dump(),
        "data": {"cmd": cmd_u, "reason": reason},
    }


def parse_utc_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def age_seconds(ts: Any, *, now: Optional[datetime] = None) -> Optional[float]:
    dt = parse_utc_ts(ts)
    if dt is None:
        return None
    cur = now or datetime.now(timezone.utc)
    return (cur - dt).total_seconds()


def pipeline_lag_issues(
    latest_ts_by_stream: Dict[str, Any],
    required_streams: Iterable[str],
    max_lag_sec: int,
) -> Dict[str, str]:
    issues: Dict[str, str] = {}
    for stream in required_streams:
        ts = latest_ts_by_stream.get(stream)
        if ts is None:
            issues[stream] = "missing"
            continue
        lag = age_seconds(ts)
        if lag is None:
            issues[stream] = "invalid_ts"
            continue
        if lag > max_lag_sec:
            issues[stream] = f"stale:{lag:.1f}s"
    return issues


def count_exec_statuses(reports: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for item in reports:
        status = item.get("status")
        if not isinstance(status, str):
            continue
        out[status] = out.get(status, 0) + 1
    return out


def percentile(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    qq = max(0.0, min(1.0, float(q)))
    arr = sorted(float(v) for v in values)
    idx = max(0, math.ceil(qq * len(arr)) - 1)
    return arr[idx]


def summarize_exec_quality(reports: Iterable[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    total = 0
    rejected = 0
    lats: List[float] = []
    for item in reports:
        total += 1
        status = item.get("status")
        if status == "REJECTED":
            rejected += 1
        lat = item.get("latency_ms")
        if isinstance(lat, (int, float)):
            lats.append(float(lat))
    reject_rate = None if total == 0 else rejected / total
    p95 = percentile(lats, 0.95)
    return {
        "total_reports": float(total),
        "rejected_reports": float(rejected),
        "reject_rate": reject_rate,
        "p95_latency_ms": p95,
    }


def _avg_map(data: Any) -> Optional[float]:
    if not isinstance(data, dict):
        return None
    vals = [float(v) for v in data.values() if isinstance(v, (int, float))]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _avg_abs_map(data: Any) -> Optional[float]:
    if not isinstance(data, dict):
        return None
    vals = [abs(float(v)) for v in data.values() if isinstance(v, (int, float))]
    if not vals:
        return None
    return sum(vals) / len(vals)


def summarize_market_feature_quality(features_payloads: Iterable[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    basis_abs, liq_avg, slippage_avg = [], [], []
    for payload in features_payloads:
        data = payload.get("data")
        if not isinstance(data, dict):
            continue
        b = _avg_abs_map(data.get("basis_bps"))
        if b is not None:
            basis_abs.append(b)
        l = _avg_map(data.get("liquidity_score"))
        if l is not None:
            liq_avg.append(l)
        s = _avg_map(data.get("slippage_bps_15m"))
        if s is not None:
            slippage_avg.append(s)
    return {
        "basis_bps_abs_avg": (sum(basis_abs) / len(basis_abs)) if basis_abs else None,
        "liquidity_score_avg": (sum(liq_avg) / len(liq_avg)) if liq_avg else None,
        "slippage_bps_15m_avg": (sum(slippage_avg) / len(slippage_avg)) if slippage_avg else None,
    }


def count_events(items: Iterable[Dict[str, Any]], key: str = "event") -> Dict[str, int]:
    out: Dict[str, int] = {}
    for item in items:
        event = item.get(key)
        if not isinstance(event, str):
            continue
        out[event] = out.get(event, 0) + 1
    return out


def evaluate_alert_thresholds(
    *,
    lag_issues: Dict[str, str],
    reject_rate: Optional[float],
    p95_latency_ms: Optional[float],
    dlq_events: int,
    max_reject_rate: float,
    max_p95_latency_ms: int,
    max_dlq_events: int,
    basis_bps_abs_avg: Optional[float] = None,
    max_basis_bps_abs_avg: Optional[float] = None,
    liquidity_score_avg: Optional[float] = None,
    min_liquidity_score_avg: Optional[float] = None,
    slippage_bps_15m_avg: Optional[float] = None,
    max_slippage_bps_15m_avg: Optional[float] = None,
) -> List[str]:
    reasons: List[str] = []
    if lag_issues:
        reasons.append("pipeline_lag")
    if reject_rate is not None and reject_rate > max_reject_rate:
        reasons.append(f"reject_rate>{max_reject_rate:.2f}")
    if p95_latency_ms is not None and p95_latency_ms > float(max_p95_latency_ms):
        reasons.append(f"p95_latency_ms>{max_p95_latency_ms}")
    if int(dlq_events) > int(max_dlq_events):
        reasons.append(f"dlq_events>{max_dlq_events}")
    if (
        basis_bps_abs_avg is not None
        and max_basis_bps_abs_avg is not None
        and basis_bps_abs_avg > max_basis_bps_abs_avg
    ):
        reasons.append(f"basis_bps_abs_avg>{max_basis_bps_abs_avg:.2f}")
    if (
        liquidity_score_avg is not None
        and min_liquidity_score_avg is not None
        and liquidity_score_avg < min_liquidity_score_avg
    ):
        reasons.append(f"liquidity_score_avg<{min_liquidity_score_avg:.2f}")
    if (
        slippage_bps_15m_avg is not None
        and max_slippage_bps_15m_avg is not None
        and slippage_bps_15m_avg > max_slippage_bps_15m_avg
    ):
        reasons.append(f"slippage_bps_15m_avg>{max_slippage_bps_15m_avg:.2f}")
    return reasons
