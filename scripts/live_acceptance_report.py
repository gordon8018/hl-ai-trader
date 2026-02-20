#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import redis

# Ensure repo root is on sys.path when running as a script.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.live_acceptance import summarize_exec, evaluate_gates, render_markdown_report
from shared.ops_tools import pipeline_lag_issues, count_exec_statuses, age_seconds


REQUIRED_STREAMS = [
    "md.features.1m",
    "alpha.target",
    "risk.approved",
    "exec.reports",
    "state.snapshot",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate live small-capital acceptance report.")
    p.add_argument("--redis-url", default=os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    p.add_argument("--window-minutes", type=int, default=30)
    p.add_argument("--max-lag-sec", type=int, default=180)
    p.add_argument("--state-max-age-sec", type=int, default=60)
    p.add_argument("--scan-limit", type=int, default=5000)
    p.add_argument("--min-ack", type=int, default=1)
    p.add_argument("--max-rejected", type=int, default=0)
    p.add_argument("--max-error-events", type=int, default=0)
    p.add_argument("--require-terminal", action="store_true")
    p.add_argument("--output", default="", help="Optional output markdown path")
    return p.parse_args()


def decode_payload(fields: Dict[str, Any]) -> Dict[str, Any]:
    raw = fields.get("p")
    if not isinstance(raw, str):
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def stream_entries_since(
    r: redis.Redis,
    stream: str,
    since: datetime,
    *,
    limit: int,
) -> List[Tuple[str, Dict[str, Any]]]:
    # Use stream ID lower bound by epoch millis for efficient range scan.
    ms = int(since.timestamp() * 1000)
    start_id = f"{ms}-0"
    rows = r.xrange(stream, min=start_id, max="+", count=limit)  # type: ignore[arg-type]
    out: List[Tuple[str, Dict[str, Any]]] = []
    for msg_id, fields in rows:
        payload = decode_payload(fields)
        if payload:
            out.append((msg_id, payload))
    return out


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


def read_state_issue(r: redis.Redis, state_max_age_sec: int) -> str:
    raw = r.get("latest.state.snapshot")
    if not raw:
        return "state_missing"
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return "state_json_invalid"
    data = obj.get("data", {}) if isinstance(obj, dict) else {}
    health = data.get("health", {}) if isinstance(data, dict) else {}
    if not isinstance(health, dict):
        return "state_health_missing"
    if health.get("reconcile_ok") is False:
        return "reconcile_unhealthy"
    lag = age_seconds(health.get("last_reconcile_ts"))
    if lag is None:
        return "state_ts_invalid"
    if lag > state_max_age_sec:
        return f"state_stale:{lag:.1f}s"
    return "none"


def count_audit_events(entries: List[Tuple[str, Dict[str, Any]]]) -> Dict[str, int]:
    out = {"error": 0, "retry": 0, "dlq": 0}
    for _, payload in entries:
        event = payload.get("event")
        if not isinstance(event, str):
            continue
        if event in out:
            out[event] += 1
    return out


def cycle_coverage(entries_by_stream: Dict[str, List[Tuple[str, Dict[str, Any]]]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for stream, entries in entries_by_stream.items():
        cycles = set()
        for _, payload in entries:
            env = payload.get("env", {})
            if isinstance(env, dict):
                c = env.get("cycle_id")
                if isinstance(c, str):
                    cycles.add(c)
        out[stream] = len(cycles)
    return out


def default_output_path(now: datetime) -> Path:
    ts = now.strftime("%Y%m%dT%H%M%SZ")
    return Path("/Users/gordonyang/workspace/myprojects/hl-ai-trader/docs/reports") / f"live_acceptance_{ts}.md"


def main() -> int:
    args = parse_args()
    now = datetime.now(timezone.utc)
    since = now - timedelta(minutes=args.window_minutes)
    r = redis.Redis.from_url(args.redis_url, decode_responses=True)

    entries_by_stream: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    for s in REQUIRED_STREAMS + ["audit.logs"]:
        entries_by_stream[s] = stream_entries_since(r, s, since, limit=args.scan_limit)

    latest_by_stream = {s: latest_env_ts(r, s) for s in REQUIRED_STREAMS}
    lag_issues = pipeline_lag_issues(latest_by_stream, REQUIRED_STREAMS, args.max_lag_sec)
    state_issue = read_state_issue(r, args.state_max_age_sec)

    exec_reports = []
    for _, payload in entries_by_stream["exec.reports"]:
        data = payload.get("data")
        if isinstance(data, dict):
            exec_reports.append(data)
    status_counts = count_exec_statuses(exec_reports)
    exec_summary = summarize_exec(status_counts)

    audit_counts = count_audit_events(entries_by_stream["audit.logs"])
    passed, reasons = evaluate_gates(
        lag_issues=lag_issues,
        state_issue=state_issue,
        exec_summary=exec_summary,
        error_events=audit_counts["error"],
        min_ack=args.min_ack,
        max_rejected=args.max_rejected,
        max_error_events=args.max_error_events,
        require_terminal=args.require_terminal,
    )

    report = {
        "pass": passed,
        "fail_reasons": reasons,
        "generated_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "window_start": since.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "window_end": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "lag_issues": lag_issues,
        "state_issue": state_issue,
        "status_counts": status_counts,
        "exec_summary": exec_summary,
        "error_events": audit_counts["error"],
        "retry_events": audit_counts["retry"],
        "dlq_events": audit_counts["dlq"],
        "cycle_coverage": cycle_coverage({k: v for k, v in entries_by_stream.items() if k in REQUIRED_STREAMS}),
    }

    markdown = render_markdown_report(report)
    print(markdown)
    output = Path(args.output) if args.output else default_output_path(now)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown, encoding="utf-8")
    print(f"saved_report={output}")
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
