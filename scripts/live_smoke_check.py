#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import redis

# Ensure repo root is on sys.path when running as a script.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.ops_tools import pipeline_lag_issues, count_exec_statuses, age_seconds


REQUIRED_STREAMS = [
    "md.features.1m",
    "alpha.target",
    "risk.approved",
    "exec.reports",
    "state.snapshot",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Live smoke checks for trading pipeline on Redis streams.")
    p.add_argument("--redis-url", default=os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    p.add_argument("--max-lag-sec", type=int, default=180, help="Maximum tolerated stream staleness")
    p.add_argument("--state-max-age-sec", type=int, default=60, help="Maximum tolerated state snapshot age")
    p.add_argument("--report-sample", type=int, default=200, help="How many recent exec.reports to sample")
    return p.parse_args()


def decode_payload(fields: Dict[str, Any]) -> Dict[str, Any]:
    raw = fields.get("p")
    if not isinstance(raw, str):
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def latest_env_ts(r: redis.Redis, stream: str) -> Optional[str]:
    rows = r.xrevrange(stream, count=1)  # type: ignore[arg-type]
    if not rows:
        return None
    _, fields = rows[0]
    payload = decode_payload(fields)
    env = payload.get("env", {})
    if isinstance(env, dict):
        ts = env.get("ts")
        if isinstance(ts, str):
            return ts
    return None


def recent_exec_reports(r: redis.Redis, count: int) -> List[Dict[str, Any]]:
    rows = r.xrevrange("exec.reports", count=count)  # type: ignore[arg-type]
    out: List[Dict[str, Any]] = []
    for _, fields in rows:
        payload = decode_payload(fields)
        data = payload.get("data")
        if isinstance(data, dict):
            out.append(data)
    return out


def main() -> int:
    args = parse_args()
    r = redis.Redis.from_url(args.redis_url, decode_responses=True)

    latest_by_stream = {s: latest_env_ts(r, s) for s in REQUIRED_STREAMS}
    lag_issues = pipeline_lag_issues(latest_by_stream, REQUIRED_STREAMS, args.max_lag_sec)

    state_issue = None
    state = None
    try:
        raw_state = r.get("latest.state.snapshot")
        if raw_state:
            state = json.loads(raw_state)
    except Exception as e:
        state_issue = f"state_read_error:{e}"

    if state is None and state_issue is None:
        state_issue = "state_missing"
    if state is not None:
        data = state.get("data", {}) if isinstance(state, dict) else {}
        health = data.get("health", {}) if isinstance(data, dict) else {}
        if isinstance(health, dict):
            if health.get("reconcile_ok") is False:
                state_issue = "reconcile_unhealthy"
            ts = health.get("last_reconcile_ts")
            lag = age_seconds(ts)
            if lag is None:
                state_issue = state_issue or "state_ts_invalid"
            elif lag > args.state_max_age_sec:
                state_issue = state_issue or f"state_stale:{lag:.1f}s"

    reports = recent_exec_reports(r, args.report_sample)
    status_counts = count_exec_statuses(reports)

    print("=== Live Smoke Check ===")
    print("latest_env_ts:", latest_by_stream)
    print("lag_issues:", lag_issues if lag_issues else "none")
    print("state_issue:", state_issue or "none")
    print("exec_report_status_counts:", status_counts if status_counts else "none")

    hard_fail = bool(lag_issues) or (state_issue is not None)
    if hard_fail:
        print("RESULT: FAIL")
        return 2
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
