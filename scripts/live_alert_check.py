#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import redis

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.ops_tools import (
    pipeline_lag_issues,
    summarize_exec_quality,
    count_events,
    evaluate_alert_thresholds,
)


REQUIRED_STREAMS = [
    "md.features.1m",
    "alpha.target",
    "risk.approved",
    "exec.reports",
    "state.snapshot",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Check basic live alert thresholds from Redis streams.")
    p.add_argument("--redis-url", default=os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"))
    p.add_argument("--window-minutes", type=int, default=10)
    p.add_argument("--scan-limit", type=int, default=5000)
    p.add_argument("--max-lag-sec", type=int, default=180)
    p.add_argument("--max-reject-rate", type=float, default=0.70)
    p.add_argument("--max-p95-latency-ms", type=int, default=5000)
    p.add_argument("--max-dlq-events", type=int, default=0)
    return p.parse_args()


def decode_payload(fields: Dict[str, Any]) -> Dict[str, Any]:
    raw = fields.get("p")
    if not isinstance(raw, str):
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def latest_env_ts(r: redis.Redis, stream: str) -> str | None:
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


def stream_entries_since(
    r: redis.Redis,
    stream: str,
    since: datetime,
    *,
    limit: int,
) -> List[Dict[str, Any]]:
    ms = int(since.timestamp() * 1000)
    start_id = f"{ms}-0"
    rows = r.xrange(stream, min=start_id, max="+", count=limit)  # type: ignore[arg-type]
    out: List[Dict[str, Any]] = []
    for _, fields in rows:
        payload = decode_payload(fields)
        if payload:
            out.append(payload)
    return out


def main() -> int:
    args = parse_args()
    r = redis.Redis.from_url(args.redis_url, decode_responses=True)

    now = datetime.now(timezone.utc)
    since = now - timedelta(minutes=args.window_minutes)

    latest_by_stream = {s: latest_env_ts(r, s) for s in REQUIRED_STREAMS}
    lag_issues = pipeline_lag_issues(latest_by_stream, REQUIRED_STREAMS, args.max_lag_sec)

    exec_payloads = stream_entries_since(r, "exec.reports", since, limit=args.scan_limit)
    exec_reports = [p.get("data", {}) for p in exec_payloads if isinstance(p.get("data"), dict)]
    quality = summarize_exec_quality(exec_reports)

    audit_payloads = stream_entries_since(r, "audit.logs", since, limit=args.scan_limit)
    event_counts = count_events(audit_payloads, key="event")
    dlq_events = int(event_counts.get("dlq", 0))

    reasons = evaluate_alert_thresholds(
        lag_issues=lag_issues,
        reject_rate=quality.get("reject_rate"),
        p95_latency_ms=quality.get("p95_latency_ms"),
        dlq_events=dlq_events,
        max_reject_rate=args.max_reject_rate,
        max_p95_latency_ms=args.max_p95_latency_ms,
        max_dlq_events=args.max_dlq_events,
    )

    print("=== Live Alert Check ===")
    print("latest_env_ts:", latest_by_stream)
    print("lag_issues:", lag_issues if lag_issues else "none")
    print("exec_quality:", quality)
    print("audit_event_counts:", event_counts if event_counts else "none")
    print("thresholds:", {
        "max_reject_rate": args.max_reject_rate,
        "max_p95_latency_ms": args.max_p95_latency_ms,
        "max_dlq_events": args.max_dlq_events,
        "max_lag_sec": args.max_lag_sec,
    })
    if reasons:
        print("ALERTS:", reasons)
        print("RESULT: FAIL")
        return 2
    print("ALERTS: none")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
