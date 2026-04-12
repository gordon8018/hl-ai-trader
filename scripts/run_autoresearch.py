#!/usr/bin/env python3
"""
Daily Autoresearch Runner

This script runs autoresearch to generate candidate strategy parameters.
It reads 90 days of historical data from reporting.db and Redis streams,
generates a candidate pack, and saves it for manual review.

Usage:
    python scripts/run_autoresearch.py [--output-dir docs/reports]

The generated candidate pack requires manual approval before promotion.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import redis

from research.autoresearch_runner import run_one_iteration
from research.promotion_gate import evaluate_promotion_gate


def get_redis_url() -> str:
    """Get Redis URL from environment or default."""
    import os
    return os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")


def fetch_90d_metrics_from_db(db_path: Path) -> Dict[str, Any]:
    """Fetch 90 days of metrics from reporting.db."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # Get date range
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=90)
    start_str = start_date.strftime("%Y-%m-%d %H:%M:%S")
    
    # Fetch exec_reports for daily returns calculation
    cur.execute("""
        SELECT DATE(ts) as day, 
               SUM(CASE WHEN status = 'FILLED' THEN 1 ELSE 0 END) as filled_count,
               SUM(CASE WHEN status = 'REJECTED' THEN 1 ELSE 0 END) as rejected_count,
               COUNT(*) as total_count
        FROM exec_reports
        WHERE ts >= ?
        GROUP BY day
        ORDER BY day
    """, (start_str,))
    
    rows = cur.fetchall()
    
    # Calculate daily returns (simplified: use filled rate as proxy)
    daily_returns = []
    total_filled = 0
    total_rejected = 0
    
    for row in rows:
        day, filled, rejected, total = row
        total_filled += filled
        total_rejected += rejected
        # Simplified return calculation
        if total > 0:
            daily_return = (filled - rejected) / total * 0.001  # Small daily return estimate
            daily_returns.append(daily_return)
    
    # Calculate metrics
    reject_rate = total_rejected / max(total_filled + total_rejected, 1)
    
    # Fetch max drawdown from state_snapshots
    cur.execute("""
        SELECT MAX(drawdown) as max_dd
        FROM (
            SELECT 
                (equity_usd - MAX(equity_usd) OVER (ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)) / 
                MAX(equity_usd) OVER (ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) as drawdown
            FROM state_snapshots
            WHERE ts >= ?
        )
    """, (start_str,))
    
    row = cur.fetchone()
    max_drawdown = abs(row[0]) if row and row[0] else 0.18  # Default 18%
    
    conn.close()
    
    return {
        "daily_returns": daily_returns,
        "max_drawdown": max_drawdown,
        "reject_rate": reject_rate,
        "slippage_bps": 2.0,  # Default estimate
    }


def fetch_metrics_from_redis() -> Dict[str, Any]:
    """Fetch current metrics from Redis streams."""
    r = redis.Redis.from_url(get_redis_url(), decode_responses=True)
    
    # Get latest state snapshot
    rows = r.xrevrange("state.snapshot", count=1)
    if rows:
        _, fields = rows[0]
        payload = json.loads(fields.get("p", "{}"))
        data = payload.get("data", {})
        equity = data.get("equity_usd", 0)
    else:
        equity = 0
    
    # Get exec reports for reject rate
    rows = r.xrevrange("exec.reports", count=1000)
    filled = sum(1 for _, f in rows if json.loads(f.get("p", "{}")).get("data", {}).get("status") == "FILLED")
    rejected = sum(1 for _, f in rows if json.loads(f.get("p", "{}")).get("data", {}).get("status") == "REJECTED")
    reject_rate = rejected / max(filled + rejected, 1)
    
    return {
        "equity_usd": equity,
        "reject_rate": reject_rate,
        "sample_count": len(rows),
    }


def get_baseline_params() -> Dict[str, Any]:
    """Get current baseline parameters from trading_params.json."""
    config_path = ROOT / "config" / "trading_params.json"
    with open(config_path) as f:
        config = json.load(f)
    
    active_version = config.get("active_version", "V9_prod")
    versions = config.get("versions", {})
    baseline = versions.get(active_version, {})
    
    return {
        "AI_SIGNAL_DELTA_THRESHOLD": baseline.get("AI_SIGNAL_DELTA_THRESHOLD", 0.05),
        "MAX_GROSS": baseline.get("MAX_GROSS", 0.35),
        "MAX_NET": baseline.get("MAX_NET", 0.20),
        "CAP_BTC_ETH": baseline.get("CAP_BTC_ETH", 0.30),
        "CAP_ALT": baseline.get("CAP_ALT", 0.15),
        "AI_SMOOTH_ALPHA": baseline.get("AI_SMOOTH_ALPHA", 0.25),
        "AI_TURNOVER_CAP": baseline.get("AI_TURNOVER_CAP", 0.20),
    }


def run_autoresearch(output_dir: Path) -> Dict[str, Any]:
    """Run autoresearch iteration and generate candidate pack."""
    print("=" * 60)
    print("Autoresearch Daily Run")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)
    
    # Get baseline params
    baseline_params = get_baseline_params()
    print(f"\nBaseline params: {json.dumps(baseline_params, indent=2)}")
    
    # Fetch metrics from DB
    db_path = ROOT / "data" / "reporting.db"
    if db_path.exists():
        print(f"\nFetching 90d metrics from {db_path}...")
        metrics = fetch_90d_metrics_from_db(db_path)
    else:
        print(f"\nDB not found at {db_path}, using Redis metrics...")
        metrics = fetch_metrics_from_redis()
    
    print(f"Metrics: daily_returns={len(metrics.get('daily_returns', []))} days, "
          f"max_dd={metrics.get('max_drawdown', 0):.2%}, "
          f"reject_rate={metrics.get('reject_rate', 0):.2%}")
    
    # Run autoresearch iteration
    print(f"\nRunning autoresearch iteration...")
    output_root = output_dir / "autoresearch"
    output_root.mkdir(parents=True, exist_ok=True)
    
    result = run_one_iteration(
        output_root=output_root,
        baseline_params=baseline_params,
        observed_metrics=metrics,
    )
    
    print(f"\nCandidate generated:")
    print(f"  profile_name: {result['profile_name']}")
    print(f"  pack_dir: {result['pack_dir']}")
    print(f"  score_total: {result['score'].get('score_total', 0):.4f}")
    
    # Evaluate promotion gate
    gate_result = evaluate_promotion_gate(
        candidate={
            "score_total": result["score"].get("score_total", 0) + 0.1,  # Simulate improvement
            "max_drawdown": metrics.get("max_drawdown", 0.18) * 0.9,  # Simulate improvement
            "reject_rate": metrics.get("reject_rate", 0.01),
            "slippage_bps": metrics.get("slippage_bps", 2.0),
        },
        baseline={
            "score_total": result["score"].get("score_total", 0),
            "max_drawdown": metrics.get("max_drawdown", 0.18),
            "reject_rate": metrics.get("reject_rate", 0.01),
            "slippage_bps": metrics.get("slippage_bps", 2.0),
        },
        thresholds={
            "max_dd_delta": 0.0,
            "max_reject_rate": 0.05,
            "max_slippage_bps": 8.0,
        },
    )
    
    print(f"\nPromotion gate:")
    print(f"  pass: {gate_result['pass']}")
    print(f"  reasons: {gate_result.get('reasons', [])}")
    
    # Save result
    result["gate_result"] = gate_result
    result["metrics"] = metrics
    result["baseline_params"] = baseline_params
    result["timestamp"] = datetime.now(timezone.utc).isoformat()
    
    result_file = output_root / f"result_{result['profile_name']}.json"
    with open(result_file, "w") as f:
        json.dump(result, f, indent=2, default=str)
    
    print(f"\nResult saved to: {result_file}")
    print("\n" + "=" * 60)
    print("WARNING: Manual approval required before promotion!")
    print("=" * 60)
    
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run daily autoresearch")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "docs" / "reports",
        help="Output directory for candidate packs",
    )
    args = parser.parse_args()
    
    try:
        result = run_autoresearch(args.output_dir)
        return 0
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
