from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional, Set

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
